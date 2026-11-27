"""シーズン制 RPG の 1 日分の進行（もののふの状態・口入れ屋の依頼・戦闘・武具・シーズンの切り替え）。

もののふは毎日、決まった回数だけ行動する。場所は町だけで、町と町は陸路・航路でつながっている。
町の口入れ屋で依頼を受け、討伐なら依頼の道を行き来して物の怪を倒し、配達なら届け先の町へ向かう。
道を 1 行動進むごとに、道の地形に合った物の怪に出会ったり、宝箱を見つけたりする（乱数）。
物の怪は種類ごとにレベル（強さ）と性質（先手・毒・群れなど）があり、地方ごとに出る物の怪が違う。
依頼がなければ、となりの町へ旅をする。力尽きたら依頼は失敗になり、最後に寄った町で目を覚ます。
シーズンが変わると、武具（持ち物）以外は最初の状態に戻る。
乱数は「日付＋シーズン＋もののふの ID」を種にするので、同じ日に何度実行しても結果は同じ。
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
from datetime import date, timedelta
from pathlib import Path

import world as W

JOURNAL_DAYS = 30


def rarity_mark(world: dict, rarity: str) -> str:
  """武具のレアリティの印。items.json の rarities の順に ★・★★・★★★…（依頼のレアリティと同じく ★ から始まる）。"""
  rarities = world["rarities"]
  return "★" * (rarities.index(rarity) + 1) if rarity in rarities else ""


# ---- シーズン -------------------------------------------------------------------------
def season_of(world: dict, d: date) -> int:
  """d が何シーズン目か（start より前の日はシーズン 1 に含める）。"""
  cfg = world["season"]
  start = date.fromisoformat(cfg["start"])
  if d < start:
    return 1
  months = (d.year - start.year) * 12 + d.month - start.month - (1 if d.day < start.day else 0)
  return 1 + months // cfg["months"]


def season_range(world: dict, season: int) -> tuple[date, date]:
  """シーズンの最初と最後の日（シーズン 1 の最初は start。それより前の日もシーズン 1 に含める）。"""
  cfg = world["season"]
  start = date.fromisoformat(cfg["start"])

  def shift(months: int) -> date:
    y, m = divmod(start.month - 1 + months, 12)
    return date(start.year + y, m + 1, start.day)

  return shift((season - 1) * cfg["months"]), shift(season * cfg["months"]) - timedelta(days=1)


# ---- 状態 -----------------------------------------------------------------------------
GROWN = ("atk", "def", "spd", "dex")  # Lv1〜最大レベルの一覧（season.json の growth）で伸びる能力


def base_stats(world: dict, trait_id: str | None, level: int) -> dict[str, int]:
  """レベルで決まる基本の能力（装備なし）。HP は Lv1 の max_hp から毎レベル level_up.hp ずつ、
  攻撃・防御・素早さ・器用さは growth の一覧の値。特性の growth（倍率）は、Lv1 からの伸びにかける（四捨五入）。"""
  season = world["season"]
  mult = season["hero_traits"].get(trait_id, {}).get("growth", {})
  level = max(1, min(level, season["max_level"]))
  out = {"max_hp": int(season["hero"]["max_hp"] + season["level_up"]["hp"] * (level - 1) * mult.get("hp", 1) + 0.5)}
  for stat in GROWN:
    table = season["growth"][stat]
    out[stat] = int(table[0] + (table[level - 1] - table[0]) * mult.get(stat, 1) + 0.5)
  return out


def set_base_stats(world: dict, state: dict) -> None:
  """もののふの基本の能力を、今のレベルと特性に合わせる（レベルアップ・読み込みのとき）。"""
  base, hero = base_stats(world, state.get("trait"), state["hero"]["level"]), state["hero"]
  hero["max_hp"] = base["max_hp"]
  for stat in GROWN:
    hero[f"base_{stat}"] = base[stat]


def new_hero(world: dict) -> dict:
  h, base = world["season"]["hero"], base_stats(world, None, 1)
  return {
    "level": 1,
    "exp": 0,
    "max_hp": base["max_hp"],
    "hp": base["max_hp"],
    **{f"base_{stat}": base[stat] for stat in GROWN},
    "gold": h["gold"],
    "potions": h["potions"],
    "stored": 0,  # 溜めた行動力（町で休んで溜める。シーズンが変わると 0 に戻る）
  }


def stone_name(world: dict, area: str) -> str:
  info = world["areas"].get(area, {})
  return info.get("stone") or (f"{info['name']}の原石" if info.get("name") else "原石")


def new_season_stats() -> dict:
  return {"quests_done": 0, "quests_failed": 0, "kills": 0, "deaths": 0, "rares": []}


def start_town(world: dict, hero_id: str, season: int | None) -> str:
  """最初の町（もののふとシーズンごとに、map.json の starts からランダムに決める）。"""
  return random.Random(f"start-{season or 0}-{hero_id}").choice(world["starts"])


def pick_trait(world: dict, hero_id: str) -> str:
  """もののふを作るときにランダムで付ける特性。もののふの ID で決まる（シーズンが変わっても同じ）。"""
  return random.Random(f"trait-{hero_id}").choice(sorted(world["season"]["hero_traits"]))


def pick_personality(world: dict, hero_id: str) -> str | None:
  """もののふを作るときにランダムで付ける性格（season.json の personalities）。もののふの ID で決まる（シーズンが変わっても同じ）。"""
  table = world["season"].get("personalities") or {}
  return random.Random(f"personality-{hero_id}").choice(sorted(table)) if table else None


def personality(world: dict, state: dict) -> dict:
  """もののふの性格（season.json の personalities）。性格のないもののふ（シミュレーターなど）は空の dict（今までの動き。
  ただし配達は、性格のないもののふも Lv を見ない。配達の Lv はいつも仮の値なので、前から Lv で外れることはなかった）。"""
  return (world["season"].get("personalities") or {}).get(state.get("personality"), {})


def stored_max(world: dict) -> int:
  """溜められる行動力の上限（stored_days 日分）。"""
  cfg = world["season"]
  return cfg.get("stored_days", 0) * cfg["actions_per_day"]


def new_state(world: dict, hero_id: str = "h1", name: str = "もののふ") -> dict:
  town = start_town(world, hero_id, None)
  return {
    "id": hero_id,
    "name": name,
    "trait": pick_trait(world, hero_id),
    "personality": pick_personality(world, hero_id),
    "season": None,
    "season_day": 0,
    "day": 0,
    "last_date": None,
    "hero": new_hero(world),
    "inventory": [],  # 持っている装備（1 つずつ {"id": 武具, "skill": スキル}。部位ごとに carry の数まで）
    "stones": {},  # 地方ごとの原石の個数
    "storage": [],  # 蔵（どの町の口入れ屋でも出し入れできる。持てる数を超えた分）
    "place": town,
    "home_town": town,
    "road": None,
    "quest": None,
    "taken": [],
    "stay": None,  # 今の町に着いた日 {"town": 町, "since": 日付}（同じ町に stay_days_max 日いたら旅に出る）
    "season_stats": new_season_stats(),
    "last": None,
    "journal": [],
    "seasons": [],
  }


def load_state(world: dict, path: Path, hero_id: str = "h1", name: str = "もののふ") -> dict:
  """状態を読む。足りない項目は補い、world/ から消えた場所・武具・依頼の参照は安全な値に戻す。
  前の形式（一本道の RPG）の状態なら、新しい形式で最初から作り直す。"""
  try:
    loaded = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return new_state(world, hero_id, name)
  except json.JSONDecodeError as e:
    raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}") from e
  if "season" not in loaded:  # 前の形式
    return new_state(world, loaded.get("id", hero_id), loaded.get("name", name))
  state = {**new_state(world, hero_id, name), **loaded}
  state["hero"] = {**new_hero(world), **state["hero"]}
  if state.get("trait") not in world["season"]["hero_traits"]:  # 前の形式・world/ から消えた特性は、付け直す
    state["trait"] = pick_trait(world, state["id"])
  if state.get("personality") not in (world["season"].get("personalities") or {}):  # 前の形式・消えた性格は付け直す
    state["personality"] = pick_personality(world, state["id"])
  hero = state["hero"]
  if not isinstance(hero.get("stored"), int) or isinstance(hero["stored"], bool):
    hero["stored"] = 0
  hero["stored"] = max(0, min(hero["stored"], stored_max(world)))
  state["hero"]["level"] = max(1, min(state["hero"]["level"], world["season"]["max_level"]))
  set_base_stats(world, state)  # 能力の伸び方が変わっても、今のレベルと特性に合わせる
  state["season_stats"] = {**new_season_stats(), **state["season_stats"]}
  places = world["places"]
  fallback = start_town(world, state["id"], state["season"])
  state["inventory"] = [x for x in carried(state) if x["id"] in world["items"]]
  state["storage"] = [x for x in stored(state) if x["id"] in world["items"]]
  state["season_stats"]["rares"] = [i for i in state["season_stats"]["rares"] if i in world["items"]]
  if state["place"] not in world["towns"]:
    state.update(place=fallback, road=None)
  if state["home_town"] not in world["towns"]:  # 開放していない町も、戻る町にはしない
    state["home_town"] = fallback
  road = state["road"]
  if road and (road.get("to") not in places or (state["place"], road["to"]) not in world["roads"]):
    state["road"] = None
  quest = state["quest"]
  if quest and not quest_ok(world, quest):
    state["quest"] = None
    if quest.get("name"):  # 黙って消さず、次に進めた日の日誌に残す
      state.setdefault("inbox", []).append(f"受けていた「{quest['name']}」は、決まりが変わったので取り下げられた")
  if state["quest"]:
    quest = state["quest"]
    renew_quest_texts(world, quest)
    rank = quest.get("rank", 0)
    if isinstance(quest.get("reward"), dict) and isinstance(rank, int) and 1 <= rank <= len(world["quest_ranks"]):
      quest["reward"]["item"] = None if quest.get("party") else world["quest_ranks"][rank - 1]["item"]
  stay = state.get("stay")
  try:  # 今の町に着いた日（壊れていたら数え直す）
    ok = isinstance(stay, dict) and stay.get("town") == state["place"] and bool(date.fromisoformat(stay["since"]))
  except (KeyError, TypeError, ValueError):
    ok = False
  if not ok:
    state["stay"] = None
  hp_max = stats(world, state)["max_hp"]
  state["hero"]["hp"] = min(state["hero"]["hp"], hp_max)
  return state


# 物の怪の前の名前 → 今の名前（#253）。名前を変える前に貼られた・受けられた依頼の文を直す（長い名前から順に置き換える）
OLD_NAMES = {
  "ケンタウロス": "鎌鼬",
  "溶岩スライム": "釣瓶火",
  "リザードマン": "河童",
  "吸血コウモリ": "野衾",
  "草原の大蛇": "野槌",
  "荒くれ野牛": "輪入道",
  "グリフォン": "以津真天",
  "森オオカミ": "山犬",
  "岩ゴーレム": "だいだらぼっち",
  "雪原の白熊": "鬼熊",
  "密林の大蛇": "うわばみ",
  "砂漠の盗賊": "砂狐",
  "雪男の牙": "雪女の簪",
  "大ネズミ": "鉄鼠",
  "ゴブリン": "小鬼",
  "人食い花": "人面樹",
  "霜の巨人": "霜の大入道",
  "サハギン": "七人ミサキ",
  "火トカゲ": "火鼠",
  "沼トカゲ": "大山椒魚",
  "毒ガエル": "大蝦蟇",
  "砂サソリ": "砂蠍",
  "岩トカゲ": "石蜥蜴",
  "暴れ猪": "猪笹王",
  "野盗団": "野伏せり",
  "オーク": "赤鬼",
  "オーガ": "青鬼",
  "大蜘蛛": "女郎蜘蛛",
  "氷の精": "雪ん子",
  "大ダコ": "蛸入道",
  "炎の鳥": "青鷺火",
  "沼ヒル": "山蛭",
  "沼蛭": "山蛭",  # 森に住むので名前を変えた（#269）
  "泥人形": "泥田坊",
  "ヒドラ": "蛟",
  "野犬": "送り犬",
  "地竜": "落ち武者",
  "山賊": "狒々",
  "雷鳥": "雷獣",
  "大鷲": "大天狗",
  "雪男": "雪女",
  "氷狼": "雪狼",
  "海賊": "船幽霊",
}
_OLD_NAMES_RE = re.compile("|".join(re.escape(k) for k in OLD_NAMES))


def renew_quest_texts(world: dict, quest: dict) -> None:
  """依頼の名前・説明・荷物を、今の world/ の言葉に合わせる。町の決まった依頼は quests.json から作り直し、
  共通の依頼は、物の怪の前の名前（OLD_NAMES）を今の名前に置き換える。"""
  template = next(
    (q for q in world["town_quests"].get(quest.get("town"), []) if q["id"] == quest.get("template")), None
  )
  if template is not None:
    quest.update(name=template["name"], text=template.get("text", ""))
    return
  for key in ("name", "text", "cargo"):
    if isinstance(quest.get(key), str):
      quest[key] = _OLD_NAMES_RE.sub(lambda m: OLD_NAMES[m.group(0)], quest[key])


QUEST_TYPES = ("hunt", "deliver", "patrol")


def hunt_steps_ok(quest: dict) -> bool:
  """討伐の段取りが、進み（progress）と合っているか。段取りが空なのに倒し終えていない、討伐の段取りが残っているのに
  倒し終えている・残っていないのに倒し終えていない、などは壊れている（前に高札と段取りを共有していたころに壊れた依頼）。
  討伐の段取りの行動力は問わない（前の形式の「1 体 1 行動」の段取りも、そのまま 1 行動で残りを連戦する）。"""
  steps, progress, count = quest.get("steps"), quest.get("progress", 0), quest.get("count")
  if not isinstance(steps, list) or not steps or not isinstance(count, int) or not isinstance(progress, int):
    return False
  if not all(isinstance(p, list) and len(p) == 2 and isinstance(p[1], int) and p[1] > 0 for p in steps):
    return False
  hunting = any(phase == "hunt" for phase, _ in steps)
  return progress < count if hunting else progress >= count


def party_size(world: dict, monster: dict) -> int:
  """何人で受ける依頼か（大物・主は season.json の party。ほかは 1 人。#72）。"""
  party = world["season"].get("party") or {}
  if monster.get("boss"):
    return party.get("boss", 1)
  if monster.get("elite"):
    return party.get("elite", 1)
  return 1


def boss_town(world: dict, monster: dict) -> str | None:
  """主の依頼を出す町（主のいる霊場への山道がつながる、開放した町）。"""
  return W.boss_town(world["trails"], world["towns"], monster.get("boss"))


def elite_ok(world: dict, quest: dict) -> bool:
  """大物・主の討伐が、今の決まりで作られた依頼か（受けられるレベルが物の怪のレベルで、倒す数が町の依頼と同じで、
  人数・★・必要な行動力・達成報酬が今の決まり）。決まりを変える前に貼られた・受けられた依頼は捨てる
  （#297 で ★・行動力・報酬を変えた雪女の依頼など）。大物・主でなければ True。"""
  target = world["monsters"].get(quest.get("monster"), {})
  if not target.get("elite") and not target.get("boss"):
    return True
  if target.get("boss"):
    count, rank = 1, len(world["quest_ranks"])
    ok = quest.get("place") == target["boss"] and quest.get("report") == boss_town(world, target)
  else:
    template = next(
      (q for q in world["town_quests"].get(quest.get("town"), []) if q["id"] == quest.get("template")), None
    )
    count, rank = (template["count"], template["rank"]) if template else (None, None)
    ok = template is not None and (quest.get("report"), quest.get("place")) in world["roads"]
  if not ok:
    return False
  cost = sum(n for _, n in hunt_steps(world, quest["report"], quest["place"]))
  gold = world["quest_reward"]["party_gold"]["boss" if target.get("boss") else "elite"]
  return (
    quest.get("rank") == rank
    and quest.get("cost") == cost
    and (quest.get("reward") or {}).get("gold") == gold
    and quest.get("min_level") == target["level"]
    and quest.get("count") == count
    and quest.get("level") == [target["level"], target["level"]]
    and quest.get("party", 1) == party_size(world, target)
  )


def quest_ok(world: dict, quest: dict, steps: bool = True) -> bool:
  """依頼の参照先が今の world/ にあるか（行き先・報告先・依頼を出した町は開放した町、討伐は報告先からの道と物の怪）。
  steps が True なら、討伐の段取りが壊れていないかも確かめる。"""
  towns = set(world["towns"])
  boss = world["monsters"].get(quest.get("monster") or "", {}).get("boss") if quest.get("type") == "hunt" else None
  if quest.get("type") not in QUEST_TYPES or (quest.get("place") not in towns and not boss):
    return False
  for key in ("report", "town"):
    if quest.get(key) is not None and quest[key] not in towns:
      return False
  if quest["type"] == "hunt":  # 前の形式（段取りのない討伐）は捨てる
    target = world["monsters"].get(quest.get("monster"))
    return (
      target is not None
      and ((quest.get("report"), quest["place"]) in world["roads"] or bool(target.get("boss")))  # 主は霊場へ
      and elite_ok(world, quest)
      and (hunt_steps_ok(quest) if steps else isinstance(quest.get("steps"), list))
    )
  return True


def save_state(state: dict, path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---- 装備と能力値 ----------------------------------------------------------------------
def item_score(world: dict, item: dict) -> float:
  """武具の強さ（点数。season.json の item_points）。装備を選ぶ・買うときに比べる。"""
  return W.item_points(item, world["season"]["item_points"])


def minus_points(world: dict, item: dict) -> float:
  """武具のマイナスの能力の点数（0 以上。損のない武具は 0）。"""
  weights = world["season"]["item_points"]["weights"]
  return -sum(weights.get(k, 0) * v for k, v in item.items() if k in W.REWARD_STATS and isinstance(v, int) and v < 0)


def instances(value) -> list[dict]:
  """持ち物・蔵を、1 つずつの {"id", "skill"} の一覧にする（前の形式 {ID: 個数} も読める。テストでも使う）。"""
  if isinstance(value, dict):
    return [{"id": k, "skill": None} for k, n in value.items() for _ in range(n if isinstance(n, int) and n > 0 else 1)]
  if not isinstance(value, list):
    return []
  out = []
  for x in value:
    if isinstance(x, str):
      out.append({"id": x, "skill": None})
    elif isinstance(x, dict) and isinstance(x.get("id"), str):
      out.append({"id": x["id"], "skill": x.get("skill") if isinstance(x.get("skill"), str) else None})
  return out


def carried(state: dict) -> list[dict]:
  """持っている装備（前の形式なら、ここで 1 つずつの一覧に直す）。"""
  if not (
    isinstance(state.get("inventory"), list)
    and all(isinstance(x, dict) and isinstance(x.get("id"), str) for x in state["inventory"])
  ):
    state["inventory"] = instances(state.get("inventory"))
  return state["inventory"]


def stored(state: dict) -> list[dict]:
  """蔵の装備。"""
  if not (
    isinstance(state.get("storage"), list)
    and all(isinstance(x, dict) and isinstance(x.get("id"), str) for x in state["storage"])
  ):
    state["storage"] = instances(state.get("storage"))
  return state["storage"]


def owned(state: dict) -> list[dict]:
  """持っている装備と蔵の装備（どこにあっても自分のもの）。"""
  return carried(state) + stored(state)


def owns(state: dict, iid: str) -> bool:
  return any(x["id"] == iid for x in owned(state))


def carry_limit(world: dict, slot: str) -> int:
  """部位ごとに持てる数（season.json の carry）。"""
  return world["season"].get("carry", {}).get(slot, 99)


def equipment_skills(world: dict, instance: dict) -> tuple[str | None, str | None]:
  """装備1つの固定スキルと追加スキル。以前の追加枠に同じスキルがあれば、固定枠で効く。"""
  fixed = world["items"][instance["id"]].get("fixed_skill")
  extra = instance.get("skill")
  return fixed, extra if extra != fixed else None


def equipment_skill_note(world: dict, instance: dict) -> str:
  """日誌と武具の表示に使う、装備ごとのスキルの札。"""
  skills = world.get("skills") or {}
  fixed_id, extra_id = equipment_skills(world, instance)
  fixed, extra = skills.get(fixed_id), skills.get(extra_id)
  text = f"〔固定 {fixed['name']}〕" if fixed else ""
  if extra:
    label = "追加 " if fixed else ""
    text += f"〔{label}{extra['name']}〕"
  return text


def gear_stat(world: dict, state: dict | None) -> str | None:
  """同じ点数の装備を選ぶときに重んじる能力（#244）。特性の得意（strong_stat）・不得意（weak_stat）を、
  性格の扱い方（gear: grow＝伸ばす・cover＝補う・balance＝バランス）で選ぶ。なければ None（バランス）。"""
  if not state:
    return None
  trait = hero_trait(world, state)
  how = personality(world, state).get("gear", "balance")
  return trait.get("strong_stat") if how == "grow" else trait.get("weak_stat") if how == "cover" else None


def gear_key(world: dict, item: dict, state: dict | None = None) -> tuple:
  """装備の優先順（大きいほど先）: ★ の多さ → 点数 → 重んじる能力（gear_stat）の上がり方 → マイナスの少なさ → 安さ。
  （#237: ★ の多い装備を優先する。#244: 同じ点数なら、特性と性格で選ぶ。値段の高さでは比べない）"""
  rank = {r: i for i, r in enumerate(world["rarities"])}
  stat = gear_stat(world, state)
  value = item.get(stat, 0) if stat else 0  # stat は装備の能力の名前（hp・atk・def・spd・dex）
  return (rank[item["rarity"]], item_score(world, item), value, -minus_points(world, item), -item.get("price", 0))


def worn(world: dict, state: dict) -> dict[str, dict | None]:
  """部位ごとに、ふだん着ける装備（なければ None）。★ の多いもの → 点数の高いもの（gear_key）、すべて同じなら
  性格の優先スキルの付いたもの → ID の順。戦いの武器と御守は、戦いごとに選び直す（pick_gear）。"""
  best: dict[str, dict | None] = {slot: None for slot in W.SLOTS}
  best_key: dict[str, tuple] = {}
  for x in sorted(carried(state), key=lambda x: (x["id"], x.get("skill") or "")):
    item = world["items"][x["id"]]
    fixed, extra = equipment_skills(world, x)
    k = (gear_key(world, item, state), -prefer_index(world, state, x), bool(fixed and extra))
    if best[item["slot"]] is None or k > best_key[item["slot"]]:
      best[item["slot"]], best_key[item["slot"]] = x, k
  return best


def equipment(world: dict, state: dict) -> dict[str, str | None]:
  """部位ごとに、ふだん着ける装備の ID（なければ None。worn）。"""
  return {slot: (x["id"] if x else None) for slot, x in worn(world, state).items()}


def hero_trait(world: dict, state: dict) -> dict:
  return world["season"]["hero_traits"].get(state.get("trait"), {})


def stats(world: dict, state: dict, held: dict[str, str | None] | None = None) -> dict[str, int]:
  """装備を足した能力値（素早さは 1 より下にはならない）。特性は、レベルで決まる基本の能力に効く（base_stats）。
  held を渡すと、その部位だけ持ち替えた能力（{"weapon": ID か None}。戦いで武器を持ち替えたとき）。"""
  hero = state["hero"]
  worn = [world["items"][i] for i in (equipment(world, state) | (held or {})).values() if i]
  return {
    "atk": hero["base_atk"] + sum(i.get("atk", 0) for i in worn),
    "def": hero["base_def"] + sum(i.get("def", 0) for i in worn),
    "max_hp": hero["max_hp"] + sum(i.get("hp", 0) for i in worn),
    "spd": max(1, hero["base_spd"] + sum(i.get("spd", 0) for i in worn)),
    "dex": max(0, hero["base_dex"] + sum(i.get("dex", 0) for i in worn)),
  }


def attack_element(world: dict, weapon: str | None, accessory: str | None) -> str | None:
  """武器 weapon（None は素手）と御守 accessory で攻めるときの属性。
  武器に属性がなければ、御守の属性（なければ None）。"""
  items = world["items"]
  if weapon and items[weapon].get("element"):
    return items[weapon]["element"]
  return items[accessory].get("element") if accessory else None


def element_rate(world: dict, attack: str | None, guard: str | None) -> float:
  """属性の相性の倍率。攻める属性が守る属性に強ければ strong、守る属性が攻める属性に強ければ weak、ほかは 1。"""
  elements, rates = world["elements"], world["season"]["element_rates"]
  if not attack or not guard or attack == guard or attack not in elements or guard not in elements:
    return 1.0  # 属性なし・同じ属性どうしは 1 倍
  if elements[attack]["strong"] == guard:
    return rates["strong"]
  if elements[guard]["strong"] == attack:
    return rates["weak"]
  return 1.0


def exp_need(world: dict, level: int) -> int:
  up = world["season"]["level_up"]
  return round(up["exp_base"] * level ** up["exp_power"])


def quest_level(world: dict, quest: dict) -> int | None:
  """依頼のレベル。討伐は狙う物の怪のレベル。配達は Lv を持たない（None。選ぶときも Lv を見ない。#150）。"""
  target = world["monsters"].get(quest.get("monster")) if quest.get("type") == "hunt" else None
  return target["level"] if target else None


# ---- 口入れ屋の依頼 ---------------------------------------------------------------------
def board(world: dict, town: str, d: date, season: int) -> list[dict]:
  """町 town に、日付 d に新しく出る依頼（枠をすべて埋めるとき）。"""
  return draw_quests(world, town, d, season, quests_per_day(world, town))


def draw_quests(
  world: dict, town: str, d: date, season: int, n: int, exclude: set | None = None, levels: list | None = None
) -> list[dict]:
  """町 town に、日付 d に新しく出す依頼を n 件まで作る（全員共通。日付と町で決まる）。
  枠ごとにレアリティ（★〜）を重みで選び、その町の依頼から選ぶ。その町に当たったレアリティの依頼がなければ
  1 つ下に落とす。町の依頼は高札に 1 度に 1 つだけ出て（exclude は高札に残っている町の依頼の ID）、
  足りない枠には共通の依頼（templates）が入る。
  levels（その町にいるもののふたちの今の Lv）を渡すと、何人かで受ける大物の依頼は、その町に受けられるもののふが
  人数分いるときだけ出す（#72・#297）。"""
  ranks = world["quest_ranks"]
  rng = random.Random(f"board-{d.isoformat()}-{season}-{town}")

  def ready(q: dict) -> bool:  # 大物の依頼を受けられるもののふが、人数分いるか
    target = world["monsters"].get(q.get("monster") or "", {})
    if levels is None or q["type"] != "hunt" or not target.get("elite"):
      return True
    return sum(lv >= target["level"] for lv in levels) >= party_size(world, target)

  own = [q for q in world["town_quests"].get(town, []) if q["id"] not in (exclude or set()) and ready(q)]
  quests = []
  for i in range(n):
    rank = rng.choices(range(1, len(ranks) + 1), [r["weight"] for r in ranks])[0]
    while True:
      mine = [q for q in own if q["rank"] == rank]
      common = [
        t for t in world["quests"] if rank <= computed_max(world, t)
      ]  # 区切りの数 + 1（★★★）までのどの ★ にもなれる
      if mine or common or rank == 1:
        break
      rank -= 1
    if mine and (not common or rng.random() < 0.8):  # 町の依頼を優先する
      q = rng.choice(mine)
      own.remove(q)
      quest = make_town_quest(world, q, town, d)
    else:
      quest = make_quest(world, rng.choices(common, [t["weight"] for t in common])[0], town, d, rng, rank)
    if quest:
      quest["id"] = f"{d.isoformat()}-{town}-{i}"
      quests.append(quest)
  return quests


def quests_per_day(world: dict, town: str) -> int:
  """町の口入れ屋の規模（高札の依頼の枠の数。map.json の quests_per_day。どの町にも書く）。"""
  return world["places"][town]["quests_per_day"]


# ---- 口入れ屋の高札（全員で共有。.state/guild.json に保存して、日をまたいで持ち越す）----------------------
# 高札の依頼は、町ごとの枠（quests_per_day）に貼られ、次のどれかの状態になる:
#   open（受付中）: 誰でも受けられる。誰も受けないまま quest_open_days 日たつと、新しい依頼に入れ替わる
#   held（受注中）: もののふが受けて、こなしている（ほかのもののふは受けられない。期限はない）
#   done（達成）/ failed（失敗）: 翌日の朝、達成なら新しい依頼に入れ替わり、失敗なら同じ依頼が受付中に戻る
GUILD_FIELDS = ("posted", "status", "holder", "returned", "members", "arrived", "gather_until")
# 何人かで受ける依頼（party。#72）は、受けたもののふを members、狩り場（霊場）に着いたもののふを arrived に入れる。
# 人数がそろうまでは open（受付中）のまま。最初の 1 人が受けた日から gather_days 日（gather_until まで）に、全員が狩り場に
# そろって戦えなければ流れる（受注中でも。仲間の 1 人が進められなくなったときに、ほかの仲間が待ち続けないように）。
# そろったら held。物の怪を倒したら done、全員が倒れたら failed（翌朝、受付中に戻る）。


def new_guild() -> dict:
  return {"season": None, "towns": {}, "refreshed": {}}


def town_levels(states) -> dict[str, list[int]]:
  """町ごとの、その町にいるもののふの Lv（大物の依頼を高札に出すかの抽選に使う。#297）。
  道の途中のもののふ（road がある）は、どの町にもいないとして数えない。別の依頼を受けているもののふは数える。"""
  levels: dict[str, list[int]] = {}
  for st in states:
    if not st.get("road") and isinstance(st.get("place"), str):
      levels.setdefault(st["place"], []).append(st["hero"]["level"])
  return {town: sorted(lvs) for town, lvs in sorted(levels.items())}


def last_open_day(world: dict, entry: dict) -> date:
  """受付中の依頼を受けられる最後の日（貼られた日から quest_open_days 日）。"""
  return date.fromisoformat(entry["posted"]) + timedelta(days=world["season"]["quest_open_days"] - 1)


def refresh_guild(world: dict, guild: dict, d: date, season: int, towns: list[str] | None = None) -> None:
  """日付 d の朝の高札にする（towns を省くと全部の町。町ごとに、その日の最初の 1 回だけ）。
  達成された依頼と、受付が終わった依頼をはがし、失敗した依頼は受付中に戻して、空いた枠に新しい依頼を貼る。
  シーズンが変わったら全部貼り直す。高札より前の日（遅れているもののふがさかのぼって進めるとき）は貼り替えない
  （同じ依頼の ID が二重にできたり、今の高札が消えたりしないように）。"""
  if guild.get("season") is not None and season < guild["season"]:
    return
  if guild.get("season") != season:
    guild.update(season=season, towns={}, refreshed={}, elite_kills=0, awake=[])
  boards, refreshed = guild.setdefault("towns", {}), guild.setdefault("refreshed", {})
  for town in [t for t in boards if t not in world["towns"]]:
    del boards[town]
    refreshed.pop(town, None)
  for town in towns or world["towns"]:
    if refreshed.get(town, "") >= d.isoformat():
      continue
    kept = []
    for e in boards.get(town, []):
      if e.get("status") == "failed":
        e.update(status="open", holder=None, posted=d.isoformat(), returned=True)
        if e.get("party", 1) > 1:
          e.update(members=[], arrived=[], gather_until=None)
        if quest_ok(world, e, steps=False):  # 受付に戻すときは、段取り・行動力・報酬を最初の状態に作り直す
          finish_quest(world, e, e["town"])
      # 高札の段取りは見ない（受けるときに作り直す。受注中の進みはもののふの状態のほう）
      if e.get("status") not in ("open", "held") or not quest_ok(world, e, steps=False):
        continue  # 達成された依頼（と、world/ から消えた依頼）ははがす
      if e.get("gather_until") and e["status"] in ("open", "held") and date.fromisoformat(e["gather_until"]) < d:
        continue  # 期限までに全員がそろって戦えなかったので流れた（受けていたもののふは、自分の番で引き返す）
      if e["status"] == "open" and not e.get("members") and last_open_day(world, e) < d:
        continue  # 誰も受けないまま受付が終わった
      renew_quest_texts(world, e)  # 物の怪の名前が変わったら、文も合わせる
      if e["status"] == "open":  # 物の怪のレベルが変わったら、依頼のレベルも合わせる
        lv = quest_level(world, e)
        e["level"] = [lv, lv] if lv is not None else None
      kept.append(e)
    exclude = {e["template"] for e in kept}
    slots = max(0, quests_per_day(world, town) - sum(not str(e["template"]).startswith("boss-") for e in kept))
    levels = guild.get("levels")  # 町ごとの、その町にいるもののふの Lv（ないときは人数を問わない）
    new = draw_quests(world, town, d, season, slots, exclude, None if levels is None else levels.get(town, []))
    for mid in guild.get("awake") or []:  # 目覚めた主の依頼（高札の枠とは別に貼る）
      target = world["monsters"].get(mid, {})
      if f"boss-{mid}" not in exclude and boss_town(world, target) == town:
        boss = make_boss_quest(world, mid, d)
        if boss:
          new.append(boss)
    for i, q in enumerate(new):
      if not str(q["template"]).startswith("boss-"):
        q["id"] = f"{d.isoformat()}-{town}-{i}"
      q.update(posted=d.isoformat(), status="open", holder=None, returned=False)
      if q.get("party", 1) > 1:
        q.update(members=[], arrived=[], gather_until=None)
    boards[town] = kept + new
    refreshed[town] = d.isoformat()


def guild_entry(guild: dict, quest: dict) -> dict | None:
  """もののふが持っている依頼の、高札の上の依頼。"""
  return next((e for e in guild.get("towns", {}).get(quest.get("town"), []) if e["id"] == quest.get("id")), None)


def release_orphans(guild: dict, states: dict[str, dict], keep: set | frozenset = frozenset()) -> int:
  """受注中なのに、受けたもののふがもうその依頼を持っていない（引退した・状態が戻ったなど）依頼を失敗にする
  （翌日、受付中に戻る）。states はもののふの ID → 状態。keep のもののふ（状態を読めなかったなど）の依頼はそのまま。
  戻した件数を返す。"""
  n = 0
  for entries in guild.get("towns", {}).values():
    for e in entries:
      if e.get("party", 1) > 1:  # 何人かの依頼: いなくなったもののふだけを外す（人数が欠けたら受付中に戻す）
        if e["status"] not in ("open", "held"):
          continue
        mine = [
          h
          for h in e.get("members") or []
          if h in keep or ((states.get(h) or {}).get("quest") or {}).get("id") == e["id"]
        ]
        if mine != (e.get("members") or []):
          n += len(e["members"]) - len(mine)
          e["members"] = mine
          e["arrived"] = [h for h in e.get("arrived") or [] if h in mine]
          e["status"] = "open"
          if not mine:
            e["gather_until"] = None
        continue
      state = states.get(e.get("holder"))
      if (
        e["status"] == "held"
        and e.get("holder") not in keep
        and (state is None or (state.get("quest") or {}).get("id") != e["id"])
      ):
        e["status"] = "failed"
        n += 1
  return n


HUNT_TRIP = 1  # 討伐の狩り場までの行動回数（狩り場は町の近く。道の長さによらず 1。#297）


def hunt_steps(world: dict, town: str, to: str) -> list[list]:
  """討伐の段取り: 行き（狩り場まで）→ 討伐（何体いても 1 行動で連戦）→ 帰り。[[段取り, 残りの行動力], ...]。
  町の近くの狩り場（通常の討伐・大物）へは行き帰り 1 行動ずつ。霊場（主）へは、山道の行動回数そのまま行き帰りする。"""
  if (town, to) in world["roads"]:
    trip = HUNT_TRIP
  else:
    trip = next(r["distance"] for r in world["trails"] if {r["from"], r["to"]} == {town, to})
  return [["go", trip], ["hunt", 1], ["back", trip]]


def finish_quest(world: dict, quest: dict, town: str) -> dict:
  """必要な行動力と、報酬（1 行動あたりの報酬 × 必要な行動力 × レアリティの倍率）をつける（期限はない）。
  討伐も配達も、行動力が同じなら報酬も同じ（討伐は別に、1 体ごとの戦闘の経験値とお金が入る）。
  何人かで受ける大物・主の達成報酬のお金は、★や行動力によらない決まった額（quests.json の reward の party_gold。#297）。"""
  lvl, rank = quest_level(world, quest), quest["rank"]
  base, cfg = world["quest_reward"], world["quest_ranks"][rank - 1]
  if quest["type"] == "hunt":
    quest["steps"] = hunt_steps(world, town, quest["place"])
    target = world["monsters"].get(quest["monster"], {})
    if target.get("elite") or target.get("boss"):  # 大物・主は、物の怪のレベル以上のもののふだけが、何人かで受ける
      quest["min_level"] = lvl
      quest["party"] = party_size(world, target)
    quest["away"] = 0  # 町から何行動ぶん離れているか（地図に描く位置）
    cost = sum(n for _, n in quest["steps"])
  else:
    cost = world["dist"][town][quest["place"]]
  gold = round(base["gold"] * cost * cfg["reward"] * (base.get("deliver_rate", 1) if quest["type"] == "deliver" else 1))
  if quest.get("party"):
    gold = base["party_gold"]["boss" if target.get("boss") else "elite"]
  quest.update(
    town=town,
    progress=0,
    level=[lvl, lvl] if lvl is not None else None,
    deadline=None,
    cost=cost,
    reward={
      "exp": round(base["exp"] * cost * cfg["reward"]),
      "gold": gold,
      "item": None if quest.get("party") else cfg["item"],
    },
  )
  return quest


def make_town_quest(world: dict, q: dict, town: str, d: date) -> dict:
  """町の依頼（quests.json の towns）から、高札に出す依頼を作る。"""
  if q["type"] == "hunt":
    quest = {"type": "hunt", "place": q["to"], "report": town, "monster": q["monster"], "count": q["count"]}
  else:
    quest = {"type": "deliver", "place": q["to"], "report": q["to"], "monster": None, "count": 1}
  quest.update(template=q["id"], name=q["name"], rank=q["rank"], text=q.get("text", ""))
  return finish_quest(world, quest, town)


def make_boss_quest(world: dict, mid: str, d: date) -> dict | None:
  """主の討伐の依頼（主が目覚めたら、霊場への山道がつながる町に出す。#72）。"""
  target = world["monsters"][mid]
  town = boss_town(world, target)
  if town is None:
    return None
  name = f"{world['places'][target['boss']]['name']}の{target['name']}"
  quest = {"type": "hunt", "place": target["boss"], "report": town, "monster": mid, "count": 1}
  quest.update(template=f"boss-{mid}", name=name, rank=len(world["quest_ranks"]), text=target.get("text", ""))
  quest = finish_quest(world, quest, town)
  quest["id"] = f"{d.isoformat()}-{town}-boss-{mid}"
  return quest


def hunt_rank(world: dict, level: int, count: int) -> int:
  """共通の討伐の ★ の数。点数 = 物の怪の Lv ×（数 + 1）を、quests.json の hunt_ranks の区切りと比べる
  （[15, 40] なら 15 以下は ★、40 以下は ★★、それより上は ★★★）。"""
  score = level * (count + 1)
  return 1 + sum(1 for limit in world["hunt_ranks"] if score > limit)


def rank_hunt(world: dict, t: dict, town: str, rank: int, rng: random.Random) -> dict | None:
  """★ が rank になる共通の討伐（町から出る道の 1 本で、その道に出る物の怪を数体）。道を均等に選び、
  その道で rank になる物の怪の Lv を均等に選び、同じ Lv の中は道の重み（地方と地形の割合）で物の怪を選び、数は rank になるものから均等に選ぶ。
  どの道でも作れなければ、★ を 1 つずつ下げて探し、それでもなければ上げて探す（どの ★ でも作れなければ None）。"""
  low, high = t["count"]
  top = len(world["hunt_ranks"]) + 1
  for r in [*range(rank, 0, -1), *range(rank + 1, top + 1)]:  # ★ を下げて探し、なければ上げて探す
    combos = [
      (other, mid, w, n)
      for other in world["neighbors"][town]
      for mid, w in world["road_info"][(town, other)]["monsters"]
      for n in range(low, high + 1)
      if hunt_rank(world, world["monsters"][mid]["level"], n) == r
    ]
    if not combos:
      continue
    other = rng.choice(sorted({c[0] for c in combos}))
    here = [c for c in combos if c[0] == other]
    level = rng.choice(sorted({world["monsters"][c[1]]["level"] for c in here}))
    pool = {c[1]: c[2] for c in here if world["monsters"][c[1]]["level"] == level}
    mid = rng.choices(sorted(pool), [pool[m] for m in sorted(pool)])[0]
    count = rng.choice([c[3] for c in here if c[1] == mid])
    hs = world.get("hunts") or {}
    places = hs.get("places", {}).get(town, []) + hs.get("places", {}).get("_any", [])
    texts = hs.get("texts", [])
    name = world["monsters"][mid]["name"]
    text = ""
    if places and len(texts) >= r and texts[r - 1]:  # 町の困りごとと、★ ごとの頼み方から説明を作る
      text = rng.choice(texts[r - 1]).format(place=rng.choice(places), monster=name, count=count)
    return {
      "type": "hunt",
      "place": other,
      "report": town,
      "monster": mid,
      "count": count,
      "rank": r,
      "name": f"{name}の討伐",
      "text": text,
    }
  return None


def computed_max(world: dict, t: dict) -> int:
  """共通の依頼の、いちばん多い ★ の数（★ は計算で決める）。"""
  limits = world["hunt_ranks"] if t["type"] == "hunt" else (world.get("deliveries") or {}).get("ranks", [])
  return len(limits) + 1


def deliver_rank(world: dict, distance: int, danger: int) -> int:
  """配達の ★ の数。点数 = 距離 ×（危なさ + 1）を、quests.json の deliveries の ranks の区切りと比べる
  （[4, 8] なら 4 以下は ★、8 以下は ★★、それより上は ★★★）。"""
  score = distance * (danger + 1)
  return 1 + sum(1 for limit in world["deliveries"]["ranks"] if score > limit)


def rank_deliver(world: dict, town: str, rank: int, rng: random.Random) -> dict | None:
  """★ が rank になる配達（届け先は season.json の deliver_range 以内の町）。届け先を均等に選び、危なさは
  rank になるものから均等に選ぶ。荷物は、町の荷物とどの町でも使う荷物（_any）のうち、危なさに合うものから選ぶ。
  作れなければ ★ を 1 つずつ下げて探し、それでもなければ上げて探す（どの ★ でも作れなければ None）。"""
  cfg, dist, dv = world["season"], world["dist"][town], world["deliveries"]
  towns = [p for p in world["towns"] if p != town and dist.get(p, cfg["deliver_range"] + 1) <= cfg["deliver_range"]]
  top = len(dv["ranks"]) + 1
  for r in [*range(rank, 0, -1), *range(rank + 1, top + 1)]:  # ★ を下げて探し、なければ上げて探す
    combos = [(to, d) for to in towns for d in range(len(dv["dangers"])) if deliver_rank(world, dist[to], d) == r]
    if not combos:
      continue
    to = rng.choice(sorted({c[0] for c in combos}))
    danger = rng.choice([d for t, d in combos if t == to])
    cargo = rng.choice(dv["cargo"].get(town, [[]] * len(dv["dangers"]))[danger] + dv["cargo"]["_any"][danger])
    text = rng.choice(dv["dangers"][danger]["texts"]).format(cargo=cargo, to=world["places"][to]["name"])
    quest = {"type": "deliver", "place": to, "report": to, "monster": None, "count": 1, "rank": r}
    quest.update(danger=danger, cargo=cargo, name=f"{cargo}の配達", text=text)
    if dv["dangers"][danger]["min_level"]:
      quest["min_level"] = dv["dangers"][danger]["min_level"]  # 危ない配達は、この Lv から受けられる
    return quest
  return None


def make_quest(world: dict, t: dict, town: str, d: date, rng: random.Random, rank: int | None = None) -> dict | None:
  """共通の依頼。★ は計算で決める（rank はほしい ★）。討伐は物の怪の Lv と数、配達は距離と危なさで決まり、
  討伐は町から出る道の 1 本で、その道に出る物の怪を倒す。配達は近くの町へ、町の荷物を届ける。"""
  if t["type"] == "hunt":
    quest = rank_hunt(world, t, town, rank or 1, rng)
  else:
    quest = rank_deliver(world, town, rank or 1, rng)
  if not quest:
    return None
  quest["template"] = t["id"]
  return finish_quest(world, quest, town)


def quest_gap(quest: dict, level: int) -> int:
  """依頼の Lv ともののふの Lv の差。配達は Lv を見ない（道中の物の怪はもののふの Lv で決まり、危なさは ★ に入っている。#228）ので 0。"""
  if quest.get("type") == "deliver" or not quest.get("level"):  # Lv を持たない依頼（配達・旅）は、自分と同じ Lv
    return 0
  return quest["level"][0] - level


def suitable(quest: dict, level: int, levels: list | None = None) -> bool:
  """受けられる依頼か。levels（性格の [下, 上]。もののふの Lv との差。None は下限なし）があれば、依頼の Lv がその範囲のものだけ。
  なければ、依頼のレベルが自分のレベル＋3 を超える、強すぎる依頼だけを避ける（弱すぎる依頼も受ける）。
  配達は Lv を見ない（いつも範囲に入る）。大物の討伐は、もののふのレベルが物の怪のレベル以上のときだけ受ける（min_level）。"""
  if quest.get("type") == "deliver":  # 配達は Lv を見ない（どんな範囲でも入る）
    return True
  low, high = levels if levels else (None, 3)
  gap = quest_gap(quest, level)
  return (low is None or gap >= low) and gap <= high and level >= quest.get("min_level", 0)


def level_gap(quest: dict, level: int, levels: list) -> int:
  """依頼の Lv が、性格の範囲（levels。もののふの Lv との差 [下, 上]、下は None で下限なし）からどれだけ外れているか（範囲内は 0）。"""
  if quest.get("type") == "deliver":  # 配達はいつも範囲に入る
    return 0
  low, high = levels
  gap = quest_gap(quest, level)
  return max(0 if low is None else low - gap, gap - high, 0)


QUEST_ORDER = {  # 性格の依頼の選び方（quest.order）。小さいほうを先に選ぶ。配達の Lv はもののふの Lv と同じに数える
  "level_high": lambda q, lv: -quest_gap(q, lv),
  "level_low": lambda q, lv: quest_gap(q, lv),
  "rank_high": lambda q, lv: -q.get("rank", 1),
  "rank_low": lambda q, lv: q.get("rank", 1),
}


def encounters(world: dict, monsters: list, level: int, window: list | None = None) -> list:
  """道で出会う物の怪（[ID, 重み], ...）のうち、もののふの Lv に合うもの。Lv が「もののふの Lv + encounter_levels」の範囲
  （season.json。[-3, 1] なら Lv −3〜+1）の物の怪だけにする。範囲に 1 体もいなければ、範囲にいちばん近い Lv の物の怪。
  地方と地形ごとの割合は道のまま（地方をまたぐ道は半分ずつ、地方の中は道の地形の割合。残った物の怪で、その地方・地形の重みを
  分け直す。残った物の怪のいない地方・地形は出ない）。
  window（[下, 上]）を渡すと、encounter_levels の代わりにその範囲を使う（散策の強い物の怪）。"""
  low, high = (level + d for d in (window or world["season"]["encounter_levels"]))
  lv = world["monsters"]

  def gap(mid: str) -> int:
    return max(low - lv[mid]["level"], lv[mid]["level"] - high, 0)

  near = min(gap(mid) for mid, _ in monsters)
  kept = [(mid, w) for mid, w in monsters if gap(mid) == near]

  def home(mid: str) -> tuple:  # 物の怪の住む地方と地形
    return lv[mid].get("area"), lv[mid]["terrain"]

  total: dict[tuple, float] = {}  # 地方・地形ごとの重み（道のまま）と、残った物の怪の重み
  left: dict[tuple, float] = {}
  for mid, w in monsters:
    total[home(mid)] = total.get(home(mid), 0) + w
  for mid, w in kept:
    left[home(mid)] = left.get(home(mid), 0) + w
  return [(mid, w * total[home(mid)] / left[home(mid)]) for mid, w in kept]


def patrol(world: dict, state: dict, rng: random.Random) -> dict:
  """合う依頼がないときの旅（報酬なし）。となりの町のどれかへ向かう。"""
  to = rng.choice(world["neighbors"][state["place"]])
  return {
    "type": "patrol",
    "name": "旅",
    "template": None,
    "town": None,
    "place": to,
    "report": None,
    "monster": None,
    "count": 1,
    "progress": 0,
    "deadline": None,
    "reward": None,
    "cost": world["roads"][(state["place"], to)],
  }


def quest_detail(world: dict, quest: dict) -> str:
  places = world["places"]
  place = places[quest["place"]]["name"]
  if quest["type"] == "hunt" and places[quest["place"]]["type"] == "dungeon":
    return f"{place}の{world['monsters'][quest['monster']]['name']}を討つ"
  if quest["type"] == "hunt":
    return (
      f"{places[quest['report']]['name']}と{place}の間の道で"
      f"{world['monsters'][quest['monster']]['name']}を{quest['count']}体討伐"
    )
  if quest["type"] == "deliver":
    return f"{place}へ{quest.get('cargo', '荷物')}を届ける"
  return f"{place}へ旅をする"


def quest_text(world: dict, quest: dict) -> str:
  """依頼の表示。レアリティと名前があれば「★★ 砂狐の討伐（〜を 6 体討伐）」の形にする。"""
  detail = quest_detail(world, quest)
  if quest["type"] == "patrol":
    return detail
  rank = quest.get("rank", 1)
  marks = world["quest_ranks"]
  mark = marks[rank - 1]["mark"] if 1 <= rank <= len(marks) else ""
  return f"{mark} {quest.get('name', '依頼')}（{detail}）"


# ---- スキルの順位（#235） ---------------------------------------------------------------
def counters_monster(sk: dict | None, monster: dict) -> bool:
  counter = sk.get("counter") if sk else None
  if not counter:
    return False
  return counter.get("trait") == monster.get("trait") or (
    counter.get("element") is True and bool(monster.get("element"))
  )


def prefer_index(world: dict, state: dict, x: dict) -> int:
  """装備 x のスキルが、性格の優先スキル（skill.prefer の部位の一覧）の何番目か（0 が一番）。優先スキルでなければ 99。"""
  slot = world["items"][x["id"]]["slot"]
  prefer = personality(world, state).get("skill", {}).get("prefer", {}).get(slot, [])
  _, extra = equipment_skills(world, x)
  return prefer.index(extra) if extra in prefer else 99


# ---- 1 日分を進める -------------------------------------------------------------------
class Day:
  """1 日分の出来事を記録しながら進める。"""

  def __init__(
    self,
    world: dict,
    state: dict,
    today: date,
    rng: random.Random,
    guild: dict | None = None,
    others: dict | None = None,
  ):
    self.world, self.state, self.today, self.rng = world, state, today, rng
    self.cfg = world["season"]
    self.hero = state["hero"]
    self.events: list[str] = []
    self.fallen = False
    self.strolled = False
    self.reported = False  # 今日、依頼を報告したか（長くいた町から旅に出るのは、次の日の朝にする）
    self.gear: list | None = None  # 戦いの中で着けている装備（スキルを見る。戦いの外は None）
    self.crit = False  # 最後の攻撃が会心の一撃だったか
    # 踏ん張り・身代わりを今日もう使ったか（1 日 1 回。仲間の番で一緒に戦うときも数えるので、状態に残す）
    used = state.get("used_today") or {}
    same_day = used.get("date") == today.isoformat()
    self.endured = bool(same_day and used.get("endured"))
    self.decoyed = bool(same_day and used.get("decoyed"))
    self.hunting = False  # 討伐の連戦の途中か（スキルの息継ぎ）
    self.smithed = False  # 今日、鍛冶屋でスキルを付けたか（1 日 1 回）
    self.forged = bool(same_day and used.get("forged"))  # 今日、★★★★武具を生成したか（1 日 1 回）
    self.rested = False
    self.guild = guild if guild is not None else new_guild()  # 全員で共有する高札（None なら、このもののふだけの高札）
    self.others = others or {}  # ほかのもののふの状態（ID → 状態。何人かで受ける依頼で、一緒に戦う。#72）
    self.waiting = False  # 今日、仲間を待ったか（待つ日は溜めた行動力を使わない）

  def log(self, text: str) -> None:
    self.events.append(text)

  def remember(self, flag: str) -> None:
    """1 日 1 回のスキル（踏ん張り・身代わり）を使ったことを、状態に残す（同じ日に仲間の番で戦うときも使えない）。"""
    used = self.state.get("used_today") or {}
    if used.get("date") != self.today.isoformat():
      used = {"date": self.today.isoformat()}
    used[flag] = True
    self.state["used_today"] = used

  @property
  def stats(self) -> dict:
    return stats(self.world, self.state)

  # 戦闘 ----------------------------------------------------------------------------
  def damage(
    self, attack: int, guard: int, dex: int = 0, divisor: float | None = None, multiplier: float | None = None
  ) -> int:
    """1 回の攻撃のダメージ。器用さ（dex）÷ critical.dex_divisor の確率で会心の一撃（multiplier 倍）になる。
    もののふも物の怪も同じ。会心があるので、能力の差が小さいと勝ち負けに幅が出る。
    divisor・multiplier を渡すと、会心の確率・倍率を変える（スキルの心眼・会心の心得・守りの心得）。
    会心が出たかは self.crit に残す（スキルの悟り）。"""
    hit = max(1, attack - guard // 2 + self.rng.randint(-2, 2))
    crit = self.cfg["critical"]
    rate = dex / (divisor or crit["dex_divisor"])
    self.crit = rate > 0 and self.rng.random() < rate  # 会心がなければ乱数を使わない
    if self.crit:
      return W.half_up(hit * (multiplier or crit["multiplier"]))
    return hit

  # スキル ----------------------------------------------------------------------------
  def worn_set(self) -> list[dict]:
    """ふだん着けている装備（部位ごとに 1 つ）。"""
    return [x for x in worn(self.world, self.state).values() if x]

  def skill(self, sid: str, pool: list | None = None):
    """スキル sid が、着けている装備に付いていれば、その value（value がなければ True）。付いていなければ None。
    pool を渡すとその装備から探す。渡さなければ、戦いの中ならその戦いの装備（self.gear）、それ以外はふだんの装備。"""
    sk = self.world.get("skills", {}).get(sid)
    if not sk:
      return None
    gear = pool if pool is not None else (self.gear if self.gear is not None else self.worn_set())
    if any(x and sid in equipment_skills(self.world, x) for x in gear):
      return sk.get("value", True)
    return None

  def price(self, base: int) -> int:
    """店・旅籠・鍛冶屋の値段（スキルの値切りで安くなる。切り上げ）。"""
    cut = self.skill("haggle")
    return math.ceil(base * cut) if cut else base

  def pick_weapon(self, monster: dict, guard: int) -> tuple[str | None, float, dict[str, int], str | None]:
    """pick_gear の、武器と御守を ID にしたもの。"""
    w, rate, s, acc = self.pick_gear(monster, guard)
    return (w["id"] if w else None), rate, s, (acc["id"] if acc else None)

  def pick_gear(self, monster: dict, guard: int) -> tuple[dict | None, float, dict[str, int], dict | None]:
    """この物の怪に、1 回の攻撃の番でいちばんダメージの大きい武器（持ち物から持ち替える）と、属性の倍率と、
    その武器を持ったときの能力（stats。攻撃・素早さだけでなく、武器の HP・防御・器用さも持ち替えたものになる）と、
    その戦いで着ける御守。ダメージは、物の怪の守り（性質で変わったあと）・属性・2 回攻撃・会心の平均まで考える
    （武器のスキルの鎧通し・弱点突き・二の太刀・会心の心得も入れる）。
    武器を持たない（None）ほうが効くとき（苦手な属性の武器しかないとき）は、武器を持たずに戦う。
    攻める属性は武器の属性。武器に属性がなければ（素手も）、御守の属性（attack_element）。
    御守は、ふだんの御守（worn）のまま戦う。ただし、物の怪の弱点を突ける属性の御守か、物の怪の性質に効くスキル（対策）の
    御守を持っていれば、その戦いだけ着け替える。ふだんの御守の属性が物の怪に苦手なときは、属性のない御守に着け替えるか、外す。
    候補の中からどれにするかは、物の怪の性質に効くスキルの数（#234）→ 与えるダメージで決める（同じなら、ふだんの御守・武器）。"""
    items, crit = self.world["items"], self.cfg["critical"]
    usual = worn(self.world, self.state)
    usual_acc, usual_wpn, enemy = usual["accessory"], usual["weapon"], monster.get("element")
    mine = sorted(carried(self.state), key=lambda x: (x["id"], x.get("skill") or ""))
    accessories = [usual_acc]
    for x in mine:  # 弱点を突ける属性・性質に効くスキルの御守
      if items[x["id"]]["slot"] == "accessory" and x is not usual_acc:
        if element_rate(self.world, items[x["id"]].get("element"), enemy) > 1 or self.counters(x, monster):
          accessories.append(x)
    if usual_acc and element_rate(self.world, items[usual_acc["id"]].get("element"), enemy) < 1:  # 苦手な相手
      accessories += [x for x in mine if items[x["id"]]["slot"] == "accessory" and not items[x["id"]].get("element")]
      accessories.append(None)
    helmet = usual["helmet"]
    divisor = self.skill("focus", [helmet]) or crit["dex_divisor"]
    best = None
    for acc in accessories:
      for w in [*(x for x in mine if items[x["id"]]["slot"] == "weapon"), None]:
        wid, aid = (w["id"] if w else None), (acc["id"] if acc else None)
        s = stats(self.world, self.state, {"weapon": wid, "accessory": aid})
        rate = element_rate(self.world, attack_element(self.world, wid, aid), enemy)
        if rate > 1 and self.skill("weak_point", [w]):
          rate = self.skill("weak_point", [w])
        need = self.skill("combo", [w]) or 2
        hits = 2 if s["spd"] >= need * monster.get("spd", s["spd"]) else 1
        mult = self.skill("crit_art", [w]) or crit["multiplier"]
        pierce = self.skill("pierce", [w]) if monster.get("trait") == "armored" else None
        crits = 1 + (mult - 1) * s["dex"] / divisor
        damage = max(1, s["atk"] - guard // 2) * rate * hits * crits * (pierce or 1)
        weapon_counter = 0 if pierce else self.counters(w, monster)
        key = (weapon_counter + self.counters(acc, monster), damage, acc is usual_acc, w is usual_wpn)
        if best is None or key > best[0]:
          best = (key, w, rate, s, acc)
    return best[1], best[2], best[3], best[4]

  def counters(self, x: dict | None, monster: dict) -> int:
    """装備 x の固定・追加スキルのどちらかが、物の怪の性質・属性に効く対策なら1。"""
    if x is None:
      return 0
    skills = self.world.get("skills", {})
    return int(any(counters_monster(skills.get(sid), monster) for sid in equipment_skills(self.world, x)))

  def battle(self, monster: dict) -> bool:
    """勝てば True。丸薬が尽きて HP が 0 になったら False（1 人で戦う。何人かで戦うのは party_battle）。
    物の怪の性質（trait）・属性の相性（武器の属性と物の怪の属性）・素早さで戦い方が変わる。
    素早いほうが先に攻撃し、相手の 2 倍以上素早ければ 2 回攻撃する（性質「先手」は、素早さに関係なく物の怪が先）。
    もののふは戦いのたびに、持ち物の中からいちばん効く武器に持ち替える（能力は、持ち替えた武器のもの）。
    装備のスキル（#233）: 武器と御守はその戦いで選んだもの、頭と胴はふだんのもので効く。"""
    return party_battle([self], monster)

  def ready(self, monster: dict, effect: dict) -> dict:
    """戦いの支度: その戦いの装備を選んで self.gear に入れ、このもののふから見た物の怪の値（性質のスキルで変わる守り・
    攻撃・攻撃の回数）と、このもののふの攻撃の回数・先に動くかなどをまとめる（party_battle が使う）。"""
    atk, guard, hits = effect["atk"], effect["guard"], effect["hits"]
    weapon, rate, s, accessory = self.pick_gear(monster, guard)
    usual = worn(self.world, self.state)
    self.gear = [weapon, accessory, usual["helmet"], usual["shield"]]
    trait, sk = monster.get("trait"), self.skill
    if trait == "fierce" and sk("deflect"):
      atk = monster["atk"]
    if trait == "swarm" and sk("swarm_guard"):
      hits = max(1, hits - 1)
    spd = s["spd"]
    mspd = monster.get("spd", spd)
    return {
      "day": self,
      "weapon": weapon,
      "accessory": accessory,
      "rate": rate,
      "pierce": sk("pierce") if trait == "armored" else None,
      "s": s,
      "guard": guard,  # このもののふが攻めるときの物の怪の守り
      "atk": atk,  # 物の怪がこのもののふを攻めるときの攻撃
      "hits": hits * (2 if mspd >= 2 * spd else 1),  # 物の怪がこのもののふを攻める回数
      "hero_hits": 2 if spd >= (sk("combo") or 2) * mspd else 1,
      "first": (trait != "first_strike" or bool(sk("insight"))) and spd >= mspd,
      "first_move": bool(sk("first_move")),
      "spd": spd,
      "mspd": mspd,
      "divisor": sk("focus"),
      "mult": sk("crit_art"),
      "taken_mult": sk("crit_guard"),
      "taken": 0,
      "max_hp": self.stats["max_hp"],
      "killing_crit": False,
      "down": False,
    }

  def win(self, monster: dict, c: dict) -> None:
    """物の怪を倒したあと（経験値・お金・スキル・毒・盗み）。c は ready の支度。
    何人かで戦ったときは、経験値は全員が満額、お金は物の怪のお金を人数で割った額（切り捨て。合計が物の怪のお金を超えない。#72）。"""
    hero, trait, sk = self.hero, monster.get("trait"), self.skill
    items, usual_wpn = self.world["items"], equipment(self.world, self.state)["weapon"]
    rate, weapon, accessory = c["rate"], c["weapon"], c["accessory"]
    exp = monster["exp"]
    gold = monster["gold"] // c["party"]
    if sk("keen_eye"):
      gold = W.half_up(gold * sk("keen_eye"))
    hero["gold"] += gold
    self.state["season_stats"]["kills"] += 1
    note = ""
    if rate > 1:
      if weapon and items[weapon["id"]].get("element"):  # 弱点を突いたのは武器の属性
        note = f"（{items[weapon['id']]['name']}で弱点を突いた）" if weapon["id"] != usual_wpn else "（弱点を突いた）"
      elif accessory and accessory["id"] != equipment(self.world, self.state)["accessory"]:  # 着け替えた御守の属性
        note = f"（{items[accessory['id']]['name']}で弱点を突いた）"
      else:
        note = "（弱点を突いた）"
    for factor, ok in (
      (sk("study"), True),
      (sk("training"), monster.get("level", 0) > hero["level"]),
      (sk("hardship"), c["taken"] >= c["max_hp"] * 0.5),  # 受けたダメージの合計（途中の丸薬を差し引かない）
      (sk("inspiration"), c.get("killer") and c["killing_crit"]),  # とどめの一撃が会心（悟り）
    ):
      if factor and ok:
        exp *= factor
    exp = W.half_up(exp)
    self.log(f"{self.monster_name(monster)}を倒した{note} (+{exp}EXP)")
    if sk("lifesteal"):
      self.heal(sk("lifesteal"))
    if sk("breath") and hero["hp"] < c["max_hp"] * 0.3:
      self.heal(sk("breath"))
    if sk("second_wind") and self.hunting:
      self.heal(sk("second_wind"))
    if trait == "poison" and not sk("antidote"):  # 毒: 倒したあとも HP が減る（0 にはならない）
      lost = min(hero["hp"] - 1, max(1, self.stats["max_hp"] // 10))
      if lost > 0:
        hero["hp"] -= lost
        self.log(f"毒で HP が {lost} 減った")
        self.use_potion()
    elif trait == "thief" and not sk("anti_theft") and hero["gold"] > 0 and self.rng.random() < 0.4:  # 盗み
      stolen = min(hero["gold"], self.rng.randint(5, 20))
      hero["gold"] -= stolen
      self.log(f"逃げ際に {stolen}文盗まれた")
    self.gain_exp(exp)

  def guard_skills(self, dmg: int, monster: dict, spd: int, mspd: int) -> int:
    """受けるダメージに、守りのスキルをかける（魔除け・受け太刀・平常心・身代わり・金剛）。"""
    sk, hero = self.skill, self.hero
    if sk("element_guard") and monster.get("element"):
      dmg = W.half_up(dmg * sk("element_guard"))
    if sk("parry") and spd >= mspd:
      dmg -= sk("parry")
    if sk("calm") and hero["hp"] < self.stats["max_hp"] * 0.3:
      dmg -= sk("calm")
    if sk("decoy") and not self.decoyed and dmg > 0:  # 身代わり: 1 日 1 回、受けたダメージを 0
      self.decoyed = True
      self.remember("decoyed")
      self.log("身代わりが攻撃を受けた")
      return 0
    return max(0 if sk("sturdy") else 1, dmg)

  def heal(self, rate: float) -> None:
    """最大 HP の rate 回復する（最大 HP は超えない）。"""
    max_hp = self.stats["max_hp"]
    self.hero["hp"] = min(max_hp, self.hero["hp"] + max(1, int(max_hp * rate)))

  def spend_potion(self) -> None:
    """丸薬を 1 個使う（スキルの薬の節約なら、ときどき減らない）。"""
    saver = self.skill("potion_saver")
    if saver and self.rng.random() < saver:
      self.log("丸薬を節約した")
      return
    self.hero["potions"] -= 1

  def use_potion(self) -> None:
    """性格の potion_below（HP の割合）を下回ったら、丸薬を 1 個使う（最大 HP の heal_rate ぶん回復。最大 HP は超えない）。"""
    below, hero = personality(self.world, self.state).get("potion_below"), self.hero
    max_hp = self.stats["max_hp"]
    if below is not None and hero["potions"] and 0 < hero["hp"] < max_hp * below:
      self.spend_potion()
      hero["hp"] = min(max_hp, hero["hp"] + int(max_hp * (self.skill("revive_art") or self.cfg["potion"]["heal_rate"])))
      self.log(f"丸薬を使った（HP {hero['hp']}/{max_hp}）")

  def monster_name(self, monster: dict) -> str:
    trait = self.world["traits"].get(monster.get("trait"), {})
    return f"{monster['name']}〔{trait['name']}〕" if trait else monster["name"]

  def gain_exp(self, exp: int) -> None:
    hero = self.hero
    if hero["level"] >= self.cfg["max_level"]:
      hero["exp"] = 0  # 最大レベルでは経験値をためない
      return
    hero["exp"] += exp
    while hero["level"] < self.cfg["max_level"] and hero["exp"] >= exp_need(self.world, hero["level"]):
      hero["exp"] -= exp_need(self.world, hero["level"])
      hero["level"] += 1
      set_base_stats(self.world, self.state)
      hero["hp"] = self.stats["max_hp"]
      self.log(f"レベルが上がった！ Lv.{hero['level']}")
    if hero["level"] >= self.cfg["max_level"]:
      hero["exp"] = 0

  # 武具 -------------------------------------------------------------------------
  def draw(self, table: list | None, town: str | None = None) -> str | None:
    """ドロップ表から抽選する（特性「強運」なら、外れたときに 1 度だけ引き直す）。"""
    got = self.draw_once(table, town)
    if got is None and table and hero_trait(self.world, self.state).get("luck"):
      got = self.draw_once(table, town)
    return got

  def draw_once(self, table: list | None, town: str | None = None) -> str | None:
    """ドロップ表から抽選する。珍しさが当たったら、その珍しさの武具から均等に選ぶ。
    common は、開放した地域のもの（開放した町の店で売っている品）だけ。
    レア（common 以外）は、指定した町に対応する武具（items.json の towns）だけ。
    season.json の rare_drops が false なら、レアが当たっても何も出ない（配布を止めている）。
    その町にない珍しさが当たったら、1 つ下の珍しさに落として選び直す（どの町にも rare は必ずある）。"""
    if not table:
      return None
    keys, weights = zip(*table)
    key = self.rng.choices(keys, weights)[0]
    if key == W.NONE:
      return None
    items, region = self.world["items"], self.world["region_items"]
    if not self.cfg.get("rare_drops", True) and (items[key]["rarity"] if key in items else key) != W.COMMON:
      return None  # レアの配布は止めている（season.json の rare_drops）。当たっても何も出ない

    def here(iid: str) -> bool:  # 出せる武具か（common は開放した地域のもの、レアはその町のもの）
      item = items[iid]
      return iid in region if item["rarity"] == W.COMMON else town in item.get("towns", [])

    if key in items:  # 武具の ID を直接書いたとき
      return key if here(key) else None
    rarities = self.world["rarities"]
    for rarity in reversed(rarities[rarities.index(W.COMMON) : rarities.index(key) + 1]):
      pool = sorted(i for i, it in items.items() if it["rarity"] == rarity and here(i))
      if pool:
        return self.rng.choice(pool)
      if rarity == rarities[rarities.index(W.COMMON) + 1]:
        return None  # rare もない（宝箱など、町のない抽選）なら何も出ない
    return None

  def random_item_skill(self, iid: str) -> str | None:
    item = self.world["items"][iid]
    pool = sorted(
      sid for sid, sk in self.world["skills"].items() if sk["slot"] == item["slot"] and sid != item.get("fixed_skill")
    )
    return self.rng.choice(pool) if pool else None

  def obtain(self, iid: str, source: str, skill: str | None = None) -> None:
    item = self.world["items"][iid]
    before = equipment(self.world, self.state)[item["slot"]]
    instance = {"id": iid, "skill": skill}
    carried(self.state).append(instance)
    mark = rarity_mark(self.world, item["rarity"])
    if item["rarity"] != "common":
      self.state["season_stats"]["rares"].append(iid)
    text = f"{source}{mark}「{item['name']}」を手に入れた"
    if equipment(self.world, self.state)[item["slot"]] == iid and before != iid:
      text += "（装備した）"
    text += equipment_skill_note(self.world, instance)
    self.log(text + ("！" if item["rarity"] != W.COMMON else ""))

  def obtain_stones(self, town: str, count: int, source: str) -> None:
    area = self.world["places"][town].get("area") or ""
    stones = self.state.setdefault("stones", {})
    stones[area] = stones.get(area, 0) + count
    self.log(f"{source}{stone_name(self.world, area)}を {count} つ手に入れた")

  # 町 --------------------------------------------------------------------------------
  def visit_town(self, town: str) -> None:
    """町に着いた: 依頼の報告と買い物（HP は旅籠で休憩しないと回復しない）。"""
    state, hero = self.state, self.hero
    state["home_town"] = town
    self.mark_stay(town)
    quest = state["quest"]
    if quest and quest["type"] in ("deliver", "patrol") and quest["place"] == town:
      quest["progress"] = quest["count"]
    if quest and quest["report"] == town and quest["progress"] >= quest["count"]:
      self.report(quest)
    if not personality(self.world, state):  # 性格のないもののふだけ、町に着くと全回復する（性格があれば旅籠で休憩する）
      hero["hp"] = self.stats["max_hp"]
    self.shop(town)
    self.arrange(town, state["quest"])
    self.smith(town)
    self.forge_legendary(town)
    self.arrange(town, state["quest"])

  def forge_legendary(self, town: str) -> None:
    """地方の名工の鍛冶屋で、原石 10 個と代金を使って ★★★★ 武具を生成する（1 日 1 回）。"""
    if self.forged or self.state["place"] != town or not self.world["places"][town].get("legendary_forge"):
      return
    area = self.world["places"][town].get("area")
    stones = self.state.setdefault("stones", {})
    pool = sorted(
      iid
      for iid, item in self.world["items"].items()
      if item.get("forge_town") == town and item["rarity"] == "legendary"
    )
    if len(pool) != 4 or stones.get(area, 0) < 10:
      return
    cost = self.price(10_000)
    potion, inn = self.cfg["potion"], self.price(self.cfg["inn"]["price"])
    reserve = inn + self.price(potion["price"]) * potion["keep"]
    if self.hero["gold"] < cost + reserve:
      return
    iid = self.rng.choice(pool)
    stones[area] -= 10
    if not stones[area]:
      del stones[area]
    self.hero["gold"] -= cost
    self.forged = True
    self.remember("forged")
    stone = stone_name(self.world, area)
    self.obtain(iid, f"{self.world['places'][town]['name']}の鍛冶屋で{stone}を打ち", None)
    self.log(f"{stone}を 10 個使った（-{cost}文）")

  def arrange(self, town: str, quest: dict | None = None) -> None:
    """町の口入れ屋で、持ち物と蔵を入れ替える（部位ごとに carry の数まで持ち、残りは蔵。#232・#234）。
    持つ順は carry_key（依頼の物の怪に効く対策スキル → 性格の欲しいスキル → 装備の強さ）。
    2 つ以上持てる部位（武器・御守）は、いちばん強い装備（ふだん着けるもの）を必ず持つ。"""
    state, items = self.state, self.world["items"]
    foes = self.quest_foes(quest)
    pool = owned(state)
    keep, store, dropped = [], [], 0
    for slot in W.SLOTS:
      mine = sorted(
        (x for x in pool if items[x["id"]]["slot"] == slot), key=lambda x: self.carry_key(x, foes), reverse=True
      )
      n = carry_limit(self.world, slot)
      if n > 1 and mine:  # ふだん着ける装備（★ の多いもの）は必ず持つ
        strongest = max(mine, key=lambda x: (gear_key(self.world, items[x["id"]], state), x["id"]))
        mine = [strongest] + [x for x in mine if x is not strongest]
      for x in mine:  # 同じ装備のスキルなしは、2 つ目から手放す（持ち物・蔵が増え続けないように）
        if (
          self.world["items"][x["id"]]["rarity"] != "legendary"
          and not equipment_skills(self.world, x)[1]
          and any(y["id"] == x["id"] and not equipment_skills(self.world, y)[1] for y in keep + store)
        ):
          dropped += 1
        elif sum(items[y["id"]]["slot"] == slot for y in keep) < n:
          keep.append(x)
        else:
          store.append(x)
    moved = len([x for x in store if any(x is y for y in carried(state))])
    state["inventory"], state["storage"] = keep, store
    if moved:
      self.log(f"{self.world['places'][town]['name']}の口入れ屋の蔵に、装備を {moved} つ預けた")
    if dropped:
      self.log(f"同じ装備を {dropped} つ手放した")

  def carry_key(self, x: dict, foes: list[dict] | None = None) -> tuple:
    """持っておく装備の順（大きいほうを先に持つ）: 依頼の物の怪（foes）に効く対策スキルの割合（依頼があるときだけ）
    → 弱点を突ける属性の割合（依頼がなければ開放した道の物の怪で見る）（属性の武器・御守を蔵にしまい込まない）→ 性格の優先スキル
    （prefer の前のほうが先）→ 装備の優先順（gear_key）→ ID。"""
    hit = sum(self.counters(x, m) for m in foes) / len(foes) if foes else 0.0
    element = self.world["items"][x["id"]].get("element")
    area = foes or self.region_foes()  # 属性は、依頼がなければ開放した道の物の怪で見る（属性の武器をしまい込まない）
    weak = (
      sum(element_rate(self.world, element, m.get("element")) > 1 for m in area) / len(area)
      if area and element
      else 0.0
    )
    want = -prefer_index(self.world, self.state, x)
    fixed, extra = equipment_skills(self.world, x)
    return (
      hit,
      weak,
      want,
      gear_key(self.world, self.world["items"][x["id"]], self.state),
      bool(fixed and extra),
      x["id"],
    )

  def region_foes(self) -> list[dict]:
    """開放した道に出る物の怪（依頼のないときの入れ替えに使う）。"""
    monsters = self.world["monsters"]
    return [monsters[mid] for info in self.world["road_info"].values() for mid, _ in info["monsters"]]

  def quest_foes(self, quest: dict | None) -> list[dict]:
    """依頼で戦う物の怪（討伐はその物の怪、配達は通る道の物の怪）。入れ替えでスキルを選ぶのに使う（#234）。"""
    if not quest:
      return []
    monsters = self.world["monsters"]
    if quest.get("type") == "hunt":
      return [monsters[quest["monster"]]] if quest.get("monster") in monsters else []
    if quest.get("type") != "deliver" or quest.get("town") not in self.world["towns"]:
      return []
    path = [quest["town"]]
    while path[-1] != quest["place"] and len(path) <= len(self.world["towns"]):  # 最短経路（world の next）
      path.append(self.world["next"][path[-1]][quest["place"]])
    foes = []
    for a, b in zip(path, path[1:], strict=False):
      info = self.world["road_info"].get((a, b))
      foes += [monsters[mid] for mid, _ in (info or {}).get("monsters", [])]
    return foes

  def smith(self, town: str) -> None:
    """町の鍛冶屋で、装備にスキルを付ける（1 日 1 回。行動力は使わない。#233・#235・#237）。
    性格の skill（from: 付ける装備の珍しさの下限、margin: 余裕の倍率、prefer: 部位ごとの優先スキル）で決める。
    持っている装備を優先順（★ の多いもの → 点数の高いもの）に見て、追加枠に優先スキルのない最初の装備に付ける。
    付けるスキルは武具の固定スキルを除く同部位の候補からランダム。
    余裕 = 手元のお金 − 旅籠代 − 丸薬 keep 個分 が、代金の margin 倍以上あるとき。"""
    cfg = personality(self.world, self.state).get("skill")
    prices, skills = self.world.get("skill_prices") or {}, self.world.get("skills") or {}
    if not cfg or self.smithed or not skills:
      return
    rarities, items = self.world["rarities"], self.world["items"]
    low = rarities.index(cfg["from"]) if cfg["from"] in rarities else len(rarities)
    able = sorted(
      (
        x
        for x in carried(self.state)  # 持っている装備だけ（蔵の装備には付けない）
        if items[x["id"]]["rarity"] in prices and rarities.index(items[x["id"]]["rarity"]) >= low
      ),
      key=lambda x: (gear_key(self.world, items[x["id"]], self.state), x["id"]),
      reverse=True,
    )
    foes = self.quest_foes(self.state["quest"])

    def needed(x: dict) -> bool:  # 今の依頼に効く追加スキルは、付け直さない
      _, extra = equipment_skills(self.world, x)
      return any(counters_monster(skills.get(extra), monster) for monster in foes)

    target = next((x for x in able if prefer_index(self.world, self.state, x) == 99 and not needed(x)), None)
    if target is None:
      return
    cost = self.price(prices[items[target["id"]]["rarity"]])
    potion = self.cfg["potion"]
    spare = self.hero["gold"] - self.price(self.cfg["inn"]["price"]) - self.price(potion["price"]) * potion["keep"]
    if spare < cost * cfg["margin"]:
      return
    extra = self.random_item_skill(target["id"])
    if extra is None:
      return
    self.hero["gold"] -= cost
    self.smithed = True
    target["skill"] = extra
    star = "（優先スキル）" if prefer_index(self.world, self.state, target) < 99 else ""
    self.log(
      f"{self.world['places'][town]['name']}の鍛冶屋で{items[target['id']]['name']}にスキルを付けた"
      f"（-{cost}文）: 〔{skills[target['skill']]['name']}〕{star}"
    )

  def settle(self, quest: dict, status: str) -> None:
    """高札の依頼を、達成（done）か失敗（failed）にする（翌日の朝に入れ替わる・受付中に戻る）。
    何人かの依頼は、物の怪と戦った結果で決まる（party_hunt）。戦う前に 1 人が失敗したら、その人だけ外れる（枠が空く）。"""
    entry = guild_entry(self.guild, quest)
    me = self.state["id"]
    if entry and entry.get("party", 1) > 1:
      if status == "failed" and entry["status"] in ("open", "held") and me in (entry.get("members") or []):
        entry["members"].remove(me)
        entry["arrived"] = [h for h in entry.get("arrived") or [] if h != me]
        entry["status"] = "open"
        if not entry["members"]:
          entry["gather_until"] = None
      return
    if entry and entry["status"] == "held" and entry["holder"] == me:
      entry["status"] = status

  def report(self, quest: dict) -> None:
    self.reported = True
    self.settle(quest, "done")
    reward = dict(quest["reward"])
    bonus = (
      self.skill("hunter") if quest["type"] == "hunt" else self.skill("escort") if quest["type"] == "deliver" else None
    )
    if bonus:  # 手柄・荷守り: 報酬のお金が増える
      reward["gold"] = W.half_up(reward["gold"] * bonus)
    self.hero["gold"] += reward["gold"]
    self.state["season_stats"]["quests_done"] += 1
    self.state["quest"] = None
    gain = ([f"+{reward['exp']}EXP"] if reward["exp"] else []) + [f"+{reward['gold']}文"]
    self.log(f"依頼達成！{quest_text(self.world, quest)}（{' '.join(gain)}）")
    iid = self.draw(reward.get("item"), quest["town"]) if not quest.get("party") else None
    if iid:
      self.obtain(iid, "報酬で")
    self.gain_exp(reward["exp"])

  def shop(self, town: str) -> None:
    hero, items, bought = self.hero, self.world["items"], []
    potion, me = self.cfg["potion"], personality(self.world, self.state)
    if me:
      # 性格の順: 旅籠（泊まるなら、その分のお金を残す）→ 丸薬を potions_first 個まで → 装備 → 丸薬を keep 個まで
      inn = self.price(self.cfg["inn"]["price"]) if self.wants_inn() else 0
      while (
        hero["potions"] < min(me["potions_first"], potion["keep"]) and hero["gold"] >= self.price(potion["price"]) + inn
      ):
        hero["gold"] -= self.price(potion["price"])
        hero["potions"] += 1
      reserve = keep_after = inn
    else:
      reserve = potion["price"] * max(0, potion["keep"] - hero["potions"])  # 丸薬の分は残して装備を買う
      keep_after = 0
    key = lambda i: gear_key(self.world, items[i], self.state)  # noqa: E731  装備を選ぶとき（worn）と同じ比べ方（★ → 点数）
    best_owned: dict[str, str | None] = {slot: None for slot in W.SLOTS}  # 蔵も含めて、部位ごとにいちばん強い品
    for x in owned(self.state):
      slot = items[x["id"]]["slot"]
      if best_owned[slot] is None or key(x["id"]) > key(best_owned[slot]):
        best_owned[slot] = x["id"]
    # 強い品から順に見る（買える中でいちばん強い品を買い、それより弱い品は買わない）
    for iid in sorted(self.world["places"][town].get("shop", []), key=key, reverse=True):
      item, cur = items[iid], best_owned[items[iid]["slot"]]
      # 安さは、同じ強さの品をどれから見るかだけに使う。買い替えるのは、安さを除いて強いときだけ（同じ強さは買い直さない）
      better = cur is None or key(iid)[:-1] > key(cur)[:-1]
      # まだ持っていない属性の武器も買う（属性の弱点を突くために、戦いのたびに持ち替える）
      elements = {items[x["id"]].get("element") for x in owned(self.state) if items[x["id"]]["slot"] == "weapon"}
      better = better or (item["slot"] == "weapon" and item.get("element") and item["element"] not in elements)
      if better and not owns(self.state, iid) and hero["gold"] >= self.price(item["price"]) + reserve:
        hero["gold"] -= self.price(item["price"])
        carried(self.state).append({"id": iid, "skill": None})
        best_owned[item["slot"]] = iid
        bought.append(item["name"])
    while hero["potions"] < potion["keep"] and hero["gold"] >= self.price(potion["price"]) + keep_after:  # 旅籠代は残す
      hero["gold"] -= self.price(potion["price"])
      hero["potions"] += 1
    name = self.world["places"][town]["name"]
    if bought:
      self.log(f"{name}で{'と'.join(bought)}を買った")

  def mark_stay(self, town: str) -> None:
    """町にいる日を数え始める（別の町に着いたときだけ。同じ町に戻っても数え直さない）。"""
    stay = self.state.get("stay")
    try:
      ok = isinstance(stay, dict) and stay.get("town") == town and bool(date.fromisoformat(stay["since"]))
    except (KeyError, TypeError, ValueError):  # 着いた日が壊れていたら、今日から数え直す
      ok = False
    if not ok:
      self.state["stay"] = {"town": town, "since": self.today.isoformat()}

  def stayed_too_long(self, town: str) -> bool:
    """町に着いた日から stay_days_max 日たったか（着いた日を 0 日目として数える。その日は依頼を受けず、となりの町へ旅に出る。#231）。"""
    self.mark_stay(town)  # 数え始めていなければ、今日から
    limit = self.cfg.get("stay_days_max")
    since = date.fromisoformat(self.state["stay"]["since"])
    return bool(limit) and (self.today - since).days >= limit

  def take_quest(self, town: str) -> None:
    if self.stayed_too_long(town):  # 居ついたので、依頼を受けずに旅に出る（依頼の途中なら、帰ってきた次の日の朝）
      if self.reported:  # 今日、依頼から帰ってきたばかり: 今日は残りの行動をして、旅は次の日の朝
        self.state["taken"].append(f"{self.today.isoformat()}-back")
        return
      quest = patrol(self.world, self.state, self.rng)
      self.state["taken"].append(f"{self.today.isoformat()}-patrol")
      self.log(
        f"{self.world['places'][town]['name']}に長くいたので、{self.world['places'][quest['place']]['name']}へ旅に出る"
      )
      self.state["quest"] = quest
      return
    level, me = self.hero["level"], personality(self.world, self.state)
    refresh_guild(self.world, self.guild, self.today, season_of(self.world, self.today), [town])
    levels = me.get("quest", {}).get("levels")
    open_ = [  # 受付中の依頼だけ（受注中のものは受けられない）
      e
      for e in self.guild["towns"].get(town, [])
      if e["status"] == "open"
      and e["id"] not in self.state["taken"]
      and self.state["id"] not in (e.get("members") or [])  # 何人かの依頼に、同じもののふが 2 度入らない
    ]
    entries = [e for e in open_ if suitable(e, level, levels)]
    if not entries and levels:
      # 性格の範囲に合う依頼がなければ、範囲より弱い依頼のうち、範囲にいちばん近い Lv の依頼を受ける
      # （範囲より強い依頼は受けない。大物は Lv が足りなければ受けない）
      others = [e for e in open_ if suitable(e, level, [None, levels[1]])]  # 下限だけを外して、ほかの決まりは同じ
      if others:
        near = min(level_gap(e, level, levels) for e in others)
        entries = [e for e in others if level_gap(e, level, levels) == near]
    if entries:
      if me:  # 性格の選び方で並べ、同じなら高札の上のもの（ランダムはなし）
        order = [QUEST_ORDER[k] for k in me["quest"]["order"]]
        entry = min(entries, key=lambda e: tuple(f(e, level) for f in order))
      else:
        entry = self.rng.choice(entries)
      if entry.get("party", 1) > 1:  # 何人かの依頼: 枠に入る（そろったら受注中）
        entry.setdefault("members", []).append(self.state["id"])
        if not entry.get("gather_until"):
          days = self.cfg["party"]["gather_days"]
          entry["gather_until"] = (self.today + timedelta(days=days - 1)).isoformat()
        if len(entry["members"]) >= entry["party"]:
          entry["status"] = "held"
      else:
        entry.update(status="held", holder=self.state["id"])
      # 深く写す（高札の依頼と段取りの一覧を共有しない）。段取り・進み・行動力・報酬は最初の状態から作り直す
      # （前に受けたもののふが力尽きて戻ってきた依頼でも、今の world/ の道の長さで、最初の状態で受ける）
      quest = finish_quest(self.world, copy.deepcopy({k: v for k, v in entry.items() if k not in GUILD_FIELDS}), town)
      self.state["taken"].append(quest["id"])
      party = (
        f"、{quest['party']} 人で受ける依頼。{len(entry['members'])}/{quest['party']} 人"
        if quest.get("party", 1) > 1
        else ""
      )
      self.log(
        f"{self.world['places'][town]['name']}の口入れ屋で依頼を受けた: {quest_text(self.world, quest)}"
        f"（行動力 {quest['cost']}{party}）"
      )
      self.arrange(town, quest)  # 依頼に合わせて、蔵の装備と入れ替える（#234）
    else:  # 受けられる依頼がない（どれも性格の範囲より強い・大物の Lv が足りない・ほかのもののふに取られた）ので、旅に出る
      quest = patrol(self.world, self.state, self.rng)
      self.state["taken"].append(f"{self.today.isoformat()}-patrol")  # 旅もその日の 1 回に数える
      self.log(
        f"{self.world['places'][town]['name']}の口入れ屋には受けられる依頼がない。"
        f"{self.world['places'][quest['place']]['name']}へ旅に出る"
      )
    self.state["quest"] = quest

  def fail_quest(self, why: str) -> None:
    quest = self.state["quest"]
    self.state["quest"] = None
    if quest and quest["type"] != "patrol":
      self.settle(quest, "failed")
      self.state["season_stats"]["quests_failed"] += 1
      self.log(f"依頼失敗（{why}）: {quest_text(self.world, quest)}")

  # 道 --------------------------------------------------------------------------------
  def event_weights(self, stroll: bool) -> dict[str, float]:
    """道・散策の出来事の重み（スキルの忍び足・宝探し・物見で変わる。減った分は「何もない」へ）。"""
    weights = dict(self.cfg["stroll_events" if stroll else "road_events"])
    before = sum(weights.values())
    if self.skill("stealth"):
      for k in ("battle", "strong_battle"):
        if k in weights:
          weights[k] *= self.skill("stealth")
    if stroll and self.skill("alert") and "strong_battle" in weights:
      weights["strong_battle"] = min(weights["strong_battle"], self.skill("alert"))
    if self.skill("treasure_hunt") and "treasure" in weights:
      weights["treasure"] *= self.skill("treasure_hunt")
    weights["nothing"] = max(0, weights.get("nothing", 0) + before - sum(weights.values()))
    return weights

  def danger_shift(self) -> int:
    """配達を運んでいる途中なら、その配達の危なさで、道で出会う物の怪の Lv を上げる量（quests.json の deliveries）。"""
    quest = self.state["quest"]
    if not quest or quest.get("type") != "deliver" or quest.get("progress", 0) >= quest.get("count", 1):
      return 0
    dangers = (self.world.get("deliveries") or {}).get("dangers", [])
    danger = quest.get("danger", 0)
    shift = dangers[danger]["shift"] if 0 <= danger < len(dangers) else 0
    return max(0, shift - (self.skill("caution") or 0)) if shift else 0  # 用心: 上がる Lv を小さくする

  def road_event(self, a: str, b: str, stroll: bool = False) -> bool:
    """道を 1 行動進んだときの出来事。力尽きたら False。
    stroll（散策）なら散策の表（stroll_events・stroll_treasure）。強い物の怪（strong_battle）は、もののふの Lv + stroll_strong_levels の物の怪。"""
    info = self.world["road_info"].get((a, b)) or self.trail_info(a, b)
    kinds, weights = zip(*self.event_weights(stroll).items())
    kind = self.rng.choices(kinds, weights)[0]
    if kind in ("battle", "strong_battle"):  # 道の戦いは 1 体だけ（何体かと戦うのは討伐の依頼だけ）
      level = self.hero["level"] + self.danger_shift()
      window = self.cfg["stroll_strong_levels"] if kind == "strong_battle" else None
      mids, mw = zip(*encounters(self.world, info["monsters"], level, window))
      monster = self.world["monsters"][self.rng.choices(mids, mw)[0]]
      if kind == "strong_battle" and self.skill("escape") and self.rng.random() < self.skill("escape"):  # 逃げ足
        self.log(f"{self.monster_name(monster)}から逃げた")
        return True
      gap = self.skill("clear_path")
      if gap and monster["level"] <= self.hero["level"] - gap:  # 露払い: 弱い物の怪は戦わずに追い払う
        self.log(f"{self.monster_name(monster)}を追い払った")
        return True
      if not self.battle(monster):
        return False
    elif kind == "treasure":
      chances = self.cfg["stroll_treasure" if stroll else "road_treasure"]
      found = self.rng.choices(list(chances), list(chances.values()))[0]
      iid = self.draw_once([["rare", 1]], a) if found == "item" else None
      if iid:
        self.obtain(iid, "宝箱から")
      elif found == "stone":
        self.obtain_stones(a, 1, "宝箱から")
      elif found == "potion":
        self.hero["potions"] += 1
        self.log("宝箱から丸薬を見つけた")
      else:  # 武具の配布が止まっている場合もお金
        gold = self.rng.randint(*self.cfg["treasure_gold"])  # 宝箱のお金（Lv に関係なく決まった範囲。#150）
        self.hero["gold"] += gold
        self.log(f"宝箱から{gold}文を見つけた")
    else:
      texts = [t for k in info["terrain"] for t in self.cfg.get("terrain_scenery", {}).get(k, [])]
      self.log(self.rng.choice(texts or self.cfg["scenery"]))
    return True

  def trail_info(self, a: str, b: str) -> dict:
    """霊場への山道の、道の情報（出る物の怪・地形。主の依頼で通る）。"""
    road = next(r for r in self.world["trails"] if {r["from"], r["to"]} == {a, b})
    monsters = W.road_monsters(road, self.world["places"], list(self.world["monsters"].values()))
    return {"kind": "trail", "terrain": [t for t, _ in road["terrain"]], "monsters": monsters}

  def fall(self) -> None:
    state, hero = self.state, self.hero
    state["season_stats"]["deaths"] += 1
    state.update(place=state["home_town"], road=None)
    town = self.world["places"][state["home_town"]]["name"]
    self.log(f"力尽きた… {town}で目を覚ました")
    self.fail_quest("力尽きた")
    hero["hp"] = self.stats["max_hp"]
    self.fallen = True

  # 行動 --------------------------------------------------------------------------------
  def step(self, road: dict) -> bool:
    """今の道を 1 行動ぶん進む（出来事が起き、残りが 0 なら町に着く）。力尽きたら False。"""
    state = self.state
    origin = state["place"]
    road["left"] -= 1
    state["road"] = road
    if not self.road_event(origin, road["to"]):
      return False
    if road["left"] > 0:  # まだ道の途中
      return True
    state.update(place=road["to"], road=None)
    self.log(f"{self.world['places'][road['to']]['name']}に着いた")
    self.visit_town(road["to"])
    return True

  def go(self, to: str) -> bool:
    """となりの町 to への道に出て、1 行動ぶん進む。"""
    return self.step({"to": to, "left": self.world["roads"][(self.state["place"], to)]})

  def move_toward(self, target: str) -> bool:
    """target へ最短経路で 1 行動ぶん進む。"""
    if self.state["road"]:
      return self.step(self.state["road"])
    return self.go(self.world["next"][self.state["place"]][target])

  def hunt_step(self, quest: dict) -> bool:
    """討伐の依頼を 1 行動ぶん進める（行き・帰りは道の出来事。討伐は 1 行動で、残りの目的の物の怪と 1 体ずつ連戦する。
    HP は減ったまま次の物の怪へ。倒すたびに経験値・お金が入り、途中で力尽きても倒した分は残る）。"""
    if not quest.get("steps"):  # 段取りが壊れている（読み込みで捨てるので、ふつうは起きない）。止まらずに失敗にする
      self.fail_quest("段取りが壊れていた")
      return True
    phase = quest["steps"][0]
    if quest.get("party", 1) > 1 and phase[0] != "back":
      entry = guild_entry(self.guild, quest)
      if not entry or self.state["id"] not in (entry.get("members") or []) or entry["status"] not in ("open", "held"):
        self.log(f"仲間がそろわず、依頼が流れた: {quest_text(self.world, quest)}")  # 失敗にはしない
        quest["steps"] = [["back", quest["away"]]] if quest.get("away") else []
        quest["flowed"] = True  # 町に戻ったら、報告せずに依頼を終える
        if not quest["steps"]:
          self.state["quest"] = None
          return True
        phase = quest["steps"][0]
      elif phase[0] == "hunt":
        return self.party_hunt(quest, entry)
    if phase[0] == "hunt":
      left = quest["count"] - quest["progress"]
      if left > 1:
        self.log(f"{self.world['monsters'][quest['monster']]['name']}が{left}体現れた")
      self.hunting = True  # 連戦の途中（息継ぎ）
      try:
        while quest["progress"] < quest["count"]:
          if not self.battle(self.world["monsters"][quest["monster"]]):
            return False
          quest["progress"] += 1
      finally:
        self.hunting = False
      self.log(f"討伐の目標を果たした（{quest['count']}体）")
      phase[1] = 1  # 何体いても 1 行動（前の形式の段取りも、ここで終わる）
    else:
      if not self.road_event(quest["report"], quest["place"]):
        return False
      quest["away"] += 1 if phase[0] == "go" else -1
    phase[1] -= 1
    if phase[1] <= 0:
      quest["steps"].pop(0)
    if not quest["steps"]:  # 町に帰ってきた
      quest["away"] = 0
      self.log(f"{self.world['places'][quest['report']]['name']}に戻った")
      if quest.get("flowed"):  # 流れた依頼は、報告せずに終える（失敗にはしない）
        self.state["quest"] = None
      self.visit_town(quest["report"])
    return True

  def party_hunt(self, quest: dict, entry: dict) -> bool:
    """何人かで受ける依頼の討伐（#72）。狩り場（霊場）に着いたら仲間を待ち、全員がそろったら一緒に戦う
    （最後に着いたもののふの番で、ほかの仲間の状態も進める）。全員が倒れたら False（このもののふは力尽きる）。"""
    me = self.state["id"]
    arrived = entry.setdefault("arrived", [])
    if me not in arrived:
      arrived.append(me)
    members = entry.get("members") or []
    ready = len(members) >= quest["party"] and all(h in arrived for h in members)
    others = {h: self.others.get(h) for h in members if h != me}
    if not ready or any(st is None or (st.get("quest") or {}).get("id") != quest["id"] for st in others.values()):
      if not self.waiting:
        self.waiting = True
        places = self.world["places"]
        where = places[quest["place"]]["name"]  # 霊場か、町と町の間の道（大物の狩り場）
        if places[quest["place"]]["type"] != "dungeon":
          where = f"{places[quest['report']]['name']}と{where}の間の道"
        self.log(f"{where}で仲間を待っている（{len(arrived)}/{quest['party']} 人）")
      return True
    days = {me: self}
    for h, st in others.items():
      days[h] = Day(self.world, st, self.today, random.Random(f"{self.today.isoformat()}-party-{h}"), self.guild)
    order = [days[h] for h in members]
    target = self.world["monsters"][quest["monster"]]
    names = "・".join(d.state["name"] for d in order)
    for d in order:
      d.log(f"{names}で{self.monster_name(target)}に挑んだ")
    won = party_battle(order, dict(target))
    entry["status"] = "done" if won else "failed"
    if won:
      self.party_loot(order, quest["town"], "boss" if target.get("boss") else "elite")
      if target.get("boss"):  # 主を倒したら、大物を数え直す
        self.guild["awake"] = [m for m in self.guild.get("awake") or [] if m != quest["monster"]]
        self.guild["elite_kills"] = 0
      elif target.get("elite"):  # 大物を数える。決まった数を討つと、主が目覚める
        self.guild["elite_kills"] = self.guild.get("elite_kills", 0) + 1
        need = self.cfg["party"]["boss_after_elites"]
        if self.guild["elite_kills"] >= need:
          self.guild["elite_kills"] = 0
          for mid, m in self.world["monsters"].items():
            if m.get("boss") and boss_town(self.world, m) and mid not in self.guild.setdefault("awake", []):
              self.guild["awake"].append(mid)
              for d in order:
                d.log(f"大物が討たれ続け、{self.world['places'][m['boss']]['name']}の{m['name']}が目覚めた")
      for d in order:
        q = d.state["quest"]
        q["progress"] = q["count"]
        q["steps"].pop(0)
        d.log(f"討伐の目標を果たした（{len(order)} 人で）")
    for h, d in days.items():
      if h == me:
        continue
      if not won:
        d.fall()
        if d.state.get("last_date") != self.today.isoformat():  # まだ今日の分を進めていなければ、今日は休む
          d.state["fell_on"] = self.today.isoformat()
      deliver_events(d.state, d.events, self.today)
    return won

  def party_loot(self, members: list[Day], town: str, kind: str) -> None:
    """石を受け取る 1 人を決め、残りのもののふにはそれぞれ武具を渡す。"""
    stone_recipient = self.rng.choice(members)
    stone_recipient.obtain_stones(town, self.cfg["party_stones"][kind], "討伐の褒美で")
    if not self.cfg.get("rare_drops", True):
      return
    pool = W.party_gear(self.world["items"], town)
    if not pool:
      return
    for member in members:
      if member is stone_recipient:
        continue
      iid = member.rng.choice(pool)
      skill = member.random_item_skill(iid) if kind == "boss" else None
      member.obtain(iid, "討伐の褒美で", skill)

  def stroll(self, town: str) -> bool:
    """その日はもう依頼を受けたので、町の周りを散策する（道の出来事だけが起きる。町からは動かない）。"""
    if not self.strolled:
      self.strolled = True
      self.log(f"{self.world['places'][town]['name']}の周りを散策した")
    return self.road_event(town, self.rng.choice(self.world["neighbors"][town]), stroll=True)

  def wants_inn(self) -> bool:
    """性格の inn_below（HP の割合）を下回っていて、旅籠代があるか。"""
    below = personality(self.world, self.state).get("inn_below")
    hero = self.hero
    return (
      below is not None
      and hero["hp"] < self.stats["max_hp"] * below
      and hero["gold"] >= self.price(self.cfg["inn"]["price"])
    )

  def rest_at_inn(self, town: str) -> bool:
    """旅籠で休憩する（1 行動と旅籠代で HP が全回復する）。"""
    cost = self.price(self.cfg["inn"]["price"])
    self.hero["gold"] -= cost
    self.hero["hp"] = self.stats["max_hp"]
    self.log(f"{self.world['places'][town]['name']}の旅籠で休んだ（-{cost}文、HP 全回復）")
    return True

  def spare(self, town: str) -> bool:
    """その日の依頼が終わったあとの 1 行動（性格の spare）。休む（行動力を溜める）・散策する・となりの町へ移動する。
    溜まりきっていたら、休む代わりに when_full。策士（stroll_hp）は HP がその割合以上なら散策する。"""
    me, hero = personality(self.world, self.state), self.hero
    choice = me.get("spare", "stroll")
    if "stroll_hp" in me and hero["hp"] >= self.stats["max_hp"] * me["stroll_hp"]:
      choice = "stroll"
    if choice == "rest":
      if hero["stored"] < stored_max(self.world):
        hero["stored"] += 1
        if self.skill("camper"):  # 野宿上手: 休むと少し回復する
          self.heal(self.skill("camper"))
        if not self.rested:
          self.rested = True
          self.log(f"{self.world['places'][town]['name']}で休んで、行動力を溜めた")
        return True
      choice = me.get("when_full", "stroll")
    if choice == "move":
      return self.go(self.rng.choice(self.world["neighbors"][town]))
    return self.stroll(town)

  def act(self) -> bool:
    """1 行動。力尽きたら False。依頼を受けるのは 1 日 1 回で、受けるのに行動回数は使わない。"""
    state = self.state
    for _ in range(5):  # 町での受注・報告は行動回数を使わない
      if state["road"]:  # 道の途中は、HP が減っても引き返さずに進む
        return self.step(state["road"])
      place, quest = state["place"], state["quest"]
      if quest is None:
        if self.wants_inn():  # 依頼を持っていないときだけ、旅籠で休憩する
          return self.rest_at_inn(place)
        if state["taken"]:  # 今日はもう依頼を受けた（旅に出た）
          return self.spare(place)
        self.take_quest(place)
        continue
      if quest["type"] == "hunt":
        return self.hunt_step(quest)
      if quest["progress"] >= quest["count"]:
        if quest["report"] is None:  # 旅は報告しない
          state["quest"] = None
          continue
        if place == quest["report"]:
          self.visit_town(place)
          continue
        return self.move_toward(quest["report"])
      if place == quest["place"]:  # もう目的の町にいる（地図が変わって町に戻されたときなど）
        self.visit_town(place)
        continue
      return self.move_toward(quest["place"])
    return True


def party_battle(days: list[Day], monster: dict) -> bool:
  """もののふ（days。1 人でも何人でも）が力を合わせて物の怪と戦う。誰かが物の怪を倒せば True（全員の勝ち）、
  全員が倒れたら False。1 人なら、Day.battle と同じ戦い（乱数の使い方も同じ）。
  - 物の怪の HP は 1 つ。もののふはそれぞれ自分の番に攻撃する（物の怪より速い・先手の心得なら物の怪の前、遅ければ後）
  - 物の怪は毎回、倒れていないもののふから 1 人を選んで攻撃する（攻撃の回数・強さは、選んだもののふとの素早さ・スキルで決まる）
  - 丸薬は、それぞれの性格のとおりに使う。倒れたもののふは、その戦いではもう動かない
  - 勝てば、倒れたもののふも含めて全員がもらう。経験値は満額、お金は人数で割る（倒れていたもののふは HP 1 で起き上がる）"""
  effect = W.trait_effect(monster)  # 凶暴・硬い・群れで変わる値
  try:
    sides = []
    for d in days:
      sides.append(d.ready(monster, effect) | {"party": len(days)})
    won = _fight(sides, monster, effect["hp"])
    if won:  # 勝ったあとも、その戦いの装備のスキルで（self.gear を入れたまま）
      for c in sides:
        if c["down"]:
          c["day"].hero["hp"] = 1
          c["day"].log("仲間が物の怪を討ち取り、起き上がった")
        c["day"].win(monster, c)
    return won
  finally:
    for d in days:
      d.gear = None


def _fight(sides: list[dict], monster: dict, hp: int) -> bool:
  """party_battle の戦いの部分（勝てば True）。sides は Day.ready の支度。"""
  full = hp
  alive = list(sides)
  solo = len(sides) == 1

  def strike(c: dict) -> int:
    d = c["day"]
    d.crit = False
    extra = {k: v for k, v in (("divisor", c["divisor"]), ("multiplier", c["mult"])) if v}  # スキルがなければ渡さない
    dmg = round(d.damage(c["s"]["atk"], c["guard"], c["s"]["dex"], **extra) * c["rate"])
    if d.skill("finisher") and hp <= full * 0.2:
      dmg = W.half_up(dmg * d.skill("finisher"))
    if c["pierce"]:
      dmg = W.half_up(dmg * c["pierce"])
    c["killing_crit"] = d.crit  # 最後の攻撃が会心か（とどめを刺したもののふのものだけ、悟りに使う）
    return max(1, dmg)

  def hero_turn(c: dict) -> bool:  # 物の怪を倒したら True
    nonlocal hp
    for _ in range(c["hero_hits"]):
      hp -= strike(c)
      if hp <= 0:
        c["killer"] = True
        return True
    return False

  def monster_turn() -> bool | None:  # 物の怪を倒したら True、全員倒れたら False、続くなら None
    nonlocal hp
    c = alive[0] if len(alive) == 1 else sides[0]["day"].rng.choice(alive)
    d, hero = c["day"], c["day"].hero
    for _ in range(c["hits"]):
      extra = {"multiplier": c["taken_mult"]} if c["taken_mult"] else {}
      dmg = d.damage(c["atk"], c["s"]["def"], monster.get("dex", 6), **extra)
      dmg = d.guard_skills(dmg, monster, c["spd"], c["mspd"])
      hero["hp"] -= dmg
      c["taken"] += dmg
      if hero["hp"] <= 0 and d.skill("endure") and not d.endured:  # 踏ん張り: 1 日 1 回、HP 1 で耐える
        d.endured = True
        d.remember("endured")
        hero["hp"] = 1
        d.log("踏ん張って倒れずに耐えた")
      below = personality(d.world, d.state).get("potion_below")
      if below is None and hero["hp"] <= 0 and hero["potions"]:  # 性格がなければ、倒れる前に丸薬を使う
        d.spend_potion()
        # 回復の量は、ふだんの装備の最大 HP で決める（持ち替えた武器・御守の HP は、その戦いだけ）
        hero["hp"] = max(1, int(d.stats["max_hp"] * (d.skill("revive_art") or d.cfg["potion"]["heal_rate"])))
        continue
      if hero["hp"] <= 0:  # 性格があれば、HP が 0 になったら丸薬があっても倒れる
        hero["hp"] = 0
        c["down"] = True
        alive.remove(c)
        d.log(f"{d.monster_name(monster)}に敗れた" if solo else f"{d.monster_name(monster)}に倒された")
        return False if not alive else None
      d.use_potion()
      if dmg > 0 and d.skill("counter") and d.rng.random() < d.skill("counter"):  # 返し技
        hp -= strike(c)
        if hp <= 0:
          c["killer"] = True
          return True
    return None

  first_round = True
  while True:
    before = [c for c in alive if c["first"] or (first_round and c["first_move"])]
    after = [c for c in alive if c not in before]
    first_round = False
    for c in before:
      if hero_turn(c):
        return True
    result = monster_turn()
    if result is not None:
      return result
    for c in after:
      if c in alive and hero_turn(c):
        return True


def deliver_events(state: dict, events: list[str], today: date) -> None:
  """ほかのもののふの番で起きた出来事（一緒に戦った、など）を、そのもののふの日誌に届ける。
  今日の分をもう進めていれば今日の日誌に足し、まだなら次に進めるときの最初に足す（inbox）。"""
  if not events:
    return
  last = state.get("last") or {}
  if state.get("last_date") == today.isoformat() and last.get("date") == today.isoformat():
    last["events"] = list(last.get("events") or []) + list(events)
    if state.get("journal") and state["journal"][0].get("date") == today.isoformat():
      state["journal"][0]["text"] = " / ".join(last["events"])
  else:
    state.setdefault("inbox", []).extend(events)


def start_season(world: dict, state: dict, season: int) -> None:
  """シーズンを切り替える: 記録を残し、持ち物以外を最初の状態に戻す。"""
  if state["season"] is not None and state["season_day"] > 0:
    stats_ = state["season_stats"]
    state["seasons"].append(
      {
        "season": state["season"],
        "days": state["season_day"],
        "level": state["hero"]["level"],
        **stats_,
        "ended_on": state["last_date"],
      }
    )
  # 最初のシーズンは、登録したときに決めた町から。シーズンが変わったら最初の町を決め直す
  town = state["place"] if state["season"] is None else start_town(world, state["id"], season)
  state.update(
    season=season,
    season_day=0,
    hero=new_hero(world),
    place=town,
    home_town=town,
    road=None,
    quest=None,
    season_stats=new_season_stats(),
    stay=None,
  )


def advance(
  world: dict,
  state: dict,
  today: date,
  actions: int | None = None,
  guild: dict | None = None,
  others: dict | None = None,
) -> bool:
  """today の分を進める（actions を省くと season.json の actions_per_day。毎日同じ回数で、出来事が乱数で決まる）。処理済み（last_date 以前の日付）なら何もせず False。"""
  if state["last_date"] and today.isoformat() <= state["last_date"]:
    return False
  season = season_of(world, today)
  new_season = state["season"] is not None and season > state["season"]
  if state["season"] is None or new_season:
    start_season(world, state, season)
  rng = random.Random(f"{today.isoformat()}-{season}-{state['id']}")
  state["day"] += 1
  state["season_day"] += 1
  state["last_date"] = today.isoformat()
  state["taken"] = []
  if guild is not None:  # 朝の高札にする（前の日の達成・失敗は、ここで入れ替わる・受付中に戻る）
    refresh_guild(world, guild, today, season)
  day = Day(world, state, today, rng, guild, others)
  day.events.extend(state.pop("inbox", None) or [])  # ほかのもののふの番で起きた出来事（一緒に戦った、など）
  fell = state.pop("fell_on", None) == today.isoformat()  # ほかのもののふの番で、一緒に戦って力尽きた日は動かない
  if new_season:
    day.log(f"シーズン{season}が始まった（レベルは 1 に戻り、持ち物はそのまま）")
  if actions is None:
    actions = world["season"]["actions_per_day"]
  if fell:
    day.log("仲間と戦って力尽きたので、今日は休んだ")
    actions = 0
  town = state["place"]
  if state["season_day"] == 1 and world["places"][town]["type"] == "town" and not state["road"]:
    day.visit_town(town)  # 旅立ちの日は最初の町で支度する
  if actions == 0 and not fell:
    hero = state["hero"]
    hero["hp"] = min(day.stats["max_hp"], hero["hp"] + int(day.stats["max_hp"] * world["season"]["rest_heal_rate"]))
    day.log(rng.choice(world["season"]["rest"]))
  for _ in range(actions):
    if not day.act():
      day.fall()
      break
  else:
    # 溜めた行動力: 依頼の途中で今日の行動回数を使い切ったら使う（旅籠の休憩にも使う）。1 日に使う量に上限はない。
    # 旅（受けられる依頼がないとき）や、残りの行動でとなりの町へ向かう道には使わない。休みの日（行動回数 0）も使わない
    hero, rest_day = state["hero"], actions == 0

    def on_quest() -> bool:  # 仲間を待っている日は、溜めた行動力を使わない
      quest = state["quest"]
      return bool(quest) and quest["type"] != "patrol" and not day.waiting

    while (
      not rest_day
      and not day.waiting  # 仲間を待っている日は、溜めた行動力を使わない（旅籠にも行かない）
      and hero.get("stored", 0) > 0
      and (on_quest() or (personality(world, state) and not state["road"] and day.wants_inn()))
    ):
      hero["stored"] -= 1
      actions += 1
      if not day.act():
        day.fall()
        break
  state["last"] = {"date": today.isoformat(), "actions": actions, "events": day.events}
  state["journal"] = (
    [{"date": today.isoformat(), "actions": actions, "text": " / ".join(day.events)}] + state["journal"]
  )[:JOURNAL_DAYS]
  return True
