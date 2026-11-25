"""シーズン制 RPG の 1 日分の進行（勇者の状態・ギルドの依頼・戦闘・アイテム・シーズンの切り替え）。

勇者は毎日、決まった回数だけ行動する。場所は町だけで、町と町は陸路・航路でつながっている。
町のギルドで依頼を受け、討伐なら依頼の道を行き来して魔物を倒し、配達なら届け先の町へ向かう。
道を 1 行動進むごとに、道の地形に合った魔物に出会ったり、宝箱を見つけたりする（乱数）。
魔物は種類ごとにレベル（強さ）があり、地形ごとに出る魔物と性質（先手・毒・群れなど）が違う。
依頼がなければ、となりの町へ旅をする。力尽きたら依頼は失敗になり、最後に寄った町で目を覚ます。
シーズンが変わると、アイテム（持ち物）以外は最初の状態に戻る。
乱数は「日付＋シーズン＋勇者の ID」を種にするので、同じ日に何度実行しても結果は同じ。
"""

from __future__ import annotations

import copy
import json
import random
from datetime import date, timedelta
from pathlib import Path

import world as W

JOURNAL_DAYS = 30


def rarity_mark(world: dict, rarity: str) -> str:
  """アイテムのレアリティの印。items.json の rarities の順に ★・★★・★★★…（依頼のレアリティと同じく ★ から始まる）。"""
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
  """勇者の基本の能力を、今のレベルと特性に合わせる（レベルアップ・読み込みのとき）。"""
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


def new_season_stats() -> dict:
  return {"quests_done": 0, "quests_failed": 0, "kills": 0, "deaths": 0, "rares": []}


def start_town(world: dict, hero_id: str, season: int | None) -> str:
  """最初の町（勇者とシーズンごとに、map.json の starts からランダムに決める）。"""
  return random.Random(f"start-{season or 0}-{hero_id}").choice(world["starts"])


def pick_trait(world: dict, hero_id: str) -> str:
  """勇者を作るときにランダムで付ける特性。勇者の ID で決まる（シーズンが変わっても同じ）。"""
  return random.Random(f"trait-{hero_id}").choice(sorted(world["season"]["hero_traits"]))


def pick_personality(world: dict, hero_id: str) -> str | None:
  """勇者を作るときにランダムで付ける性格（season.json の personalities）。勇者の ID で決まる（シーズンが変わっても同じ）。"""
  table = world["season"].get("personalities") or {}
  return random.Random(f"personality-{hero_id}").choice(sorted(table)) if table else None


def personality(world: dict, state: dict) -> dict:
  """勇者の性格（season.json の personalities）。性格のない勇者（シミュレーターなど）は空の dict（今までの動き。
  ただし配達は、性格のない勇者も Lv を見ない。配達の Lv はいつも仮の値なので、前から Lv で外れることはなかった）。"""
  return (world["season"].get("personalities") or {}).get(state.get("personality"), {})


def stored_max(world: dict) -> int:
  """溜められる行動力の上限（stored_days 日分）。"""
  cfg = world["season"]
  return cfg.get("stored_days", 0) * cfg["actions_per_day"]


def new_state(world: dict, hero_id: str = "h1", name: str = "勇者") -> dict:
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
    "inventory": {},
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


def load_state(world: dict, path: Path, hero_id: str = "h1", name: str = "勇者") -> dict:
  """状態を読む。足りない項目は補い、world/ から消えた場所・アイテム・依頼の参照は安全な値に戻す。
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
  state["inventory"] = {k: v for k, v in state["inventory"].items() if k in world["items"]}
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


QUEST_TYPES = ("hunt", "deliver", "patrol")


def hunt_steps_ok(quest: dict) -> bool:
  """討伐の段取りが、進み（progress）と合っているか。段取りが空なのに倒し終えていない、討伐の段取りが残っているのに
  倒し終えている・残っていないのに倒し終えていない、などは壊れている（前に掲示板と段取りを共有していたころに壊れた依頼）。
  討伐の段取りの行動力は問わない（前の形式の「1 体 1 行動」の段取りも、そのまま 1 行動で残りを連戦する）。"""
  steps, progress, count = quest.get("steps"), quest.get("progress", 0), quest.get("count")
  if not isinstance(steps, list) or not steps or not isinstance(count, int) or not isinstance(progress, int):
    return False
  if not all(isinstance(p, list) and len(p) == 2 and isinstance(p[1], int) and p[1] > 0 for p in steps):
    return False
  hunting = any(phase == "hunt" for phase, _ in steps)
  return progress < count if hunting else progress >= count


def elite_ok(world: dict, quest: dict) -> bool:
  """強敵の討伐が、今の決まりで作られた依頼か（受けられるレベルが魔物のレベルで、倒す数が町の依頼と同じ）。
  強敵にする前に貼られた・受けられた依頼（Lv の決まりがなく、数も多い）は捨てる。強敵でなければ True。"""
  target = world["monsters"].get(quest.get("monster"), {})
  if not target.get("elite"):
    return True
  template = next(
    (q for q in world["town_quests"].get(quest.get("town"), []) if q["id"] == quest.get("template")), None
  )
  return (
    template is not None
    and quest.get("min_level") == target["level"]
    and quest.get("count") == template["count"]
    and quest.get("level") == [target["level"], target["level"]]
  )


def quest_ok(world: dict, quest: dict, steps: bool = True) -> bool:
  """依頼の参照先が今の world/ にあるか（行き先・報告先・依頼を出した町は開放した町、討伐は報告先からの道と魔物）。
  steps が True なら、討伐の段取りが壊れていないかも確かめる。"""
  towns = set(world["towns"])
  if quest.get("type") not in QUEST_TYPES or quest.get("place") not in towns:
    return False
  for key in ("report", "town"):
    if quest.get(key) is not None and quest[key] not in towns:
      return False
  if quest["type"] == "hunt":  # 前の形式（段取りのない討伐）は捨てる
    return (
      (quest.get("report"), quest["place"]) in world["roads"]
      and quest.get("monster") in world["monsters"]
      and not world["monsters"][quest["monster"]].get("boss")  # ボスは道に出ない
      and elite_ok(world, quest)
      and (hunt_steps_ok(quest) if steps else isinstance(quest.get("steps"), list))
    )
  return True


def save_state(state: dict, path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---- 装備と能力値 ----------------------------------------------------------------------
def item_score(world: dict, item: dict) -> float:
  """アイテムの強さ（点数。season.json の item_points）。装備を選ぶ・買うときに比べる。"""
  return W.item_points(item, world["season"]["item_points"])


def minus_points(world: dict, item: dict) -> float:
  """アイテムのマイナスの能力の点数（0 以上。損のないアイテムは 0）。"""
  weights = world["season"]["item_points"]["weights"]
  return -sum(weights.get(k, 0) * v for k, v in item.items() if k in W.REWARD_STATS and isinstance(v, int) and v < 0)


def equip_key(world: dict, item: dict, rank: dict[str, int]) -> tuple:
  """装備を比べる順: 点数 → 珍しさ → マイナスの少なさ（同じ点数なら、損のないものを選ぶ） → 値段の高さ。"""
  return (item_score(world, item), rank[item["rarity"]], -minus_points(world, item), item.get("price", 0))


def equipment(world: dict, state: dict) -> dict[str, str | None]:
  """部位ごとに、持ち物の中でいちばん強いアイテムの ID（なければ None）。比べ方は equip_key（すべて同じなら ID の順）。"""
  rank = {r: i for i, r in enumerate(world["rarities"])}
  best: dict[str, str | None] = {slot: None for slot in W.SLOTS}
  for iid in sorted(state["inventory"]):
    item, cur = world["items"][iid], best[world["items"][iid]["slot"]]
    if cur is None or equip_key(world, item, rank) > equip_key(world, world["items"][cur], rank):
      best[item["slot"]] = iid
  return best


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
  """武器 weapon（None は素手）と装飾品 accessory で攻めるときの属性。
  武器に属性がなければ、装飾品の属性（なければ None）。"""
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


def monster_level(world: dict) -> int:
  """魔物のいちばん多いレベル。魔物を決めない依頼（配達・旅）のレベルと、宝箱のお金の目安に使う。"""
  levels = [m["level"] for m in world["monsters"].values()]
  return max(set(levels), key=levels.count)


def quest_level(world: dict, quest: dict) -> int:
  """依頼のレベル。討伐は狙う魔物のレベル、それ以外は monster_level。"""
  target = world["monsters"].get(quest.get("monster")) if quest.get("type") == "hunt" else None
  return target["level"] if target else monster_level(world)


# ---- ギルドの依頼 ---------------------------------------------------------------------
def board(world: dict, town: str, d: date, season: int) -> list[dict]:
  """町 town に、日付 d に新しく出る依頼（枠をすべて埋めるとき）。"""
  return draw_quests(world, town, d, season, quests_per_day(world, town))


def draw_quests(world: dict, town: str, d: date, season: int, n: int, exclude: set | None = None) -> list[dict]:
  """町 town に、日付 d に新しく出す依頼を n 件まで作る（全員共通。日付と町で決まる）。
  枠ごとにレアリティ（★〜）を重みで選び、その町の依頼から選ぶ。その町に当たったレアリティの依頼がなければ
  1 つ下に落とす。町の依頼は掲示板に 1 度に 1 つだけ出て（exclude は掲示板に残っている町の依頼の ID）、
  足りない枠には共通の依頼（templates）が入る。"""
  ranks = world["quest_ranks"]
  rng = random.Random(f"board-{d.isoformat()}-{season}-{town}")
  own = [q for q in world["town_quests"].get(town, []) if q["id"] not in (exclude or set())]
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
  """町のギルドの規模（掲示板の依頼の枠の数。map.json の quests_per_day。どの町にも書く）。"""
  return world["places"][town]["quests_per_day"]


# ---- ギルドの掲示板（全員で共有。.state/guild.json に保存して、日をまたいで持ち越す）----------------------
# 掲示板の依頼は、町ごとの枠（quests_per_day）に貼られ、次のどれかの状態になる:
#   open（受付中）: 誰でも受けられる。誰も受けないまま quest_open_days 日たつと、新しい依頼に入れ替わる
#   held（受注中）: 勇者が受けて、こなしている（ほかの勇者は受けられない。期限はない）
#   done（達成）/ failed（失敗）: 翌日の朝、達成なら新しい依頼に入れ替わり、失敗なら同じ依頼が受付中に戻る
GUILD_FIELDS = ("posted", "status", "holder", "returned")


def new_guild() -> dict:
  return {"season": None, "towns": {}, "refreshed": {}}


def last_open_day(world: dict, entry: dict) -> date:
  """受付中の依頼を受けられる最後の日（貼られた日から quest_open_days 日）。"""
  return date.fromisoformat(entry["posted"]) + timedelta(days=world["season"]["quest_open_days"] - 1)


def refresh_guild(world: dict, guild: dict, d: date, season: int, towns: list[str] | None = None) -> None:
  """日付 d の朝の掲示板にする（towns を省くと全部の町。町ごとに、その日の最初の 1 回だけ）。
  達成された依頼と、受付が終わった依頼をはがし、失敗した依頼は受付中に戻して、空いた枠に新しい依頼を貼る。
  シーズンが変わったら全部貼り直す。掲示板より前の日（遅れている勇者がさかのぼって進めるとき）は貼り替えない
  （同じ依頼の ID が二重にできたり、今の掲示板が消えたりしないように）。"""
  if guild.get("season") is not None and season < guild["season"]:
    return
  if guild.get("season") != season:
    guild.update(season=season, towns={}, refreshed={})
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
        if quest_ok(world, e, steps=False):  # 受付に戻すときは、段取り・行動力・報酬を最初の状態に作り直す
          finish_quest(world, e, e["town"])
      # 掲示板の段取りは見ない（受けるときに作り直す。受注中の進みは勇者の状態のほう）
      if e.get("status") not in ("open", "held") or not quest_ok(world, e, steps=False):
        continue  # 達成された依頼（と、world/ から消えた依頼）ははがす
      if e["status"] == "open" and last_open_day(world, e) < d:
        continue  # 誰も受けないまま受付が終わった
      if e["status"] == "open":  # 魔物のレベルが変わったら、依頼のレベルも合わせる
        lv = quest_level(world, e)
        e["level"] = [lv, lv]
      kept.append(e)
    exclude = {e["template"] for e in kept}
    new = draw_quests(world, town, d, season, max(0, quests_per_day(world, town) - len(kept)), exclude)
    for i, q in enumerate(new):
      q.update(id=f"{d.isoformat()}-{town}-{i}", posted=d.isoformat(), status="open", holder=None, returned=False)
    boards[town] = kept + new
    refreshed[town] = d.isoformat()


def guild_entry(guild: dict, quest: dict) -> dict | None:
  """勇者が持っている依頼の、掲示板の上の依頼。"""
  return next((e for e in guild.get("towns", {}).get(quest.get("town"), []) if e["id"] == quest.get("id")), None)


def release_orphans(guild: dict, states: dict[str, dict], keep: set | frozenset = frozenset()) -> int:
  """受注中なのに、受けた勇者がもうその依頼を持っていない（引退した・状態が戻ったなど）依頼を失敗にする
  （翌日、受付中に戻る）。states は勇者の ID → 状態。keep の勇者（状態を読めなかったなど）の依頼はそのまま。
  戻した件数を返す。"""
  n = 0
  for entries in guild.get("towns", {}).values():
    for e in entries:
      state = states.get(e.get("holder"))
      if (
        e["status"] == "held"
        and e.get("holder") not in keep
        and (state is None or (state.get("quest") or {}).get("id") != e["id"])
      ):
        e["status"] = "failed"
        n += 1
  return n


HUNT_TRIP_MAX = 2  # 討伐の狩り場までの行動力の上限（狩り場は町の近く。道の長さの半分、最大 2）


def hunt_steps(world: dict, town: str, to: str) -> list[list]:
  """討伐の段取り: 行き（狩り場まで）→ 討伐（何体いても 1 行動で連戦）→ 帰り。[[段取り, 残りの行動力], ...]。"""
  trip = min(HUNT_TRIP_MAX, -(-world["roads"][(town, to)] // 2))
  return [["go", trip], ["hunt", 1], ["back", trip]]


def finish_quest(world: dict, quest: dict, town: str) -> dict:
  """必要な行動力と、報酬（1 行動あたりの報酬 × 必要な行動力 × レアリティの倍率）をつける（期限はない）。
  討伐も配達も、行動力が同じなら報酬も同じ（討伐は別に、1 体ごとの戦闘の経験値とお金が入る）。"""
  lvl, rank = quest_level(world, quest), quest["rank"]
  base, cfg = world["quest_reward"], world["quest_ranks"][rank - 1]
  if quest["type"] == "hunt":
    quest["steps"] = hunt_steps(world, town, quest["place"])
    target = world["monsters"].get(quest["monster"], {})
    if target.get("elite"):  # 強敵は、魔物のレベル以上の勇者だけが受ける
      quest["min_level"] = lvl
    quest["away"] = 0  # 町から何行動ぶん離れているか（地図に描く位置）
    cost = sum(n for _, n in quest["steps"])
  else:
    cost = world["dist"][town][quest["place"]]
  quest.update(
    town=town,
    progress=0,
    level=[lvl, lvl],
    deadline=None,
    cost=cost,
    reward={
      "exp": round(base["exp"] * cost * cfg["reward"]),
      "gold": round(
        base["gold"] * cost * cfg["reward"] * (base.get("deliver_rate", 1) if quest["type"] == "deliver" else 1)
      ),
      "item": cfg["item"],
    },
  )
  return quest


def make_town_quest(world: dict, q: dict, town: str, d: date) -> dict:
  """町の依頼（quests.json の towns）から、掲示板に出す依頼を作る。"""
  if q["type"] == "hunt":
    quest = {"type": "hunt", "place": q["to"], "report": town, "monster": q["monster"], "count": q["count"]}
  else:
    quest = {"type": "deliver", "place": q["to"], "report": q["to"], "monster": None, "count": 1}
  quest.update(template=q["id"], name=q["name"], rank=q["rank"], text=q.get("text", ""))
  return finish_quest(world, quest, town)


def hunt_rank(world: dict, level: int, count: int) -> int:
  """共通の討伐の ★ の数。点数 = 魔物の Lv ×（数 + 1）を、quests.json の hunt_ranks の区切りと比べる
  （[15, 40] なら 15 以下は ★、40 以下は ★★、それより上は ★★★）。"""
  score = level * (count + 1)
  return 1 + sum(1 for limit in world["hunt_ranks"] if score > limit)


def rank_hunt(world: dict, t: dict, town: str, rank: int, rng: random.Random) -> dict | None:
  """★ が rank になる共通の討伐（町から出る道の 1 本で、その道に出る魔物を数体）。道を均等に選び、
  その道で rank になる魔物の Lv を均等に選び、同じ Lv の中は地形の重みで魔物を選び、数は rank になるものから均等に選ぶ。
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
  """共通の依頼。★ は計算で決める（rank はほしい ★）。討伐は魔物の Lv と数、配達は距離と危なさで決まり、
  討伐は町から出る道の 1 本で、その道に出る魔物を倒す。配達は近くの町へ、町の荷物を届ける。"""
  if t["type"] == "hunt":
    quest = rank_hunt(world, t, town, rank or 1, rng)
  else:
    quest = rank_deliver(world, town, rank or 1, rng)
  if not quest:
    return None
  quest["template"] = t["id"]
  return finish_quest(world, quest, town)


def quest_gap(quest: dict, level: int) -> int:
  """依頼の Lv と勇者の Lv の差。配達は Lv を見ない（道中の魔物は勇者の Lv で決まり、危なさは ★ に入っている。#228）ので 0。"""
  return 0 if quest.get("type") == "deliver" else quest["level"][0] - level


def suitable(quest: dict, level: int, levels: list | None = None) -> bool:
  """受けられる依頼か。levels（性格の [下, 上]。勇者の Lv との差。None は下限なし）があれば、依頼の Lv がその範囲のものだけ。
  なければ、依頼のレベルが自分のレベル＋3 を超える、強すぎる依頼だけを避ける（弱すぎる依頼も受ける）。
  配達は Lv を見ない（いつも範囲に入る）。強敵の討伐は、勇者のレベルが魔物のレベル以上のときだけ受ける（min_level）。"""
  low, high = levels if levels else (None, 3)
  gap = quest_gap(quest, level)
  return (low is None or gap >= low) and gap <= high and level >= quest.get("min_level", 0)


def level_gap(quest: dict, level: int, levels: list) -> int:
  """依頼の Lv が、性格の範囲（levels。勇者の Lv との差 [下, 上]、下は None で下限なし）からどれだけ外れているか（範囲内は 0）。"""
  low, high = levels
  gap = quest_gap(quest, level)
  return max(0 if low is None else low - gap, gap - high, 0)


QUEST_ORDER = {  # 性格の依頼の選び方（quest.order）。小さいほうを先に選ぶ。配達の Lv は勇者の Lv と同じに数える
  "level_high": lambda q, lv: -quest_gap(q, lv),
  "level_low": lambda q, lv: quest_gap(q, lv),
  "rank_high": lambda q, lv: -q.get("rank", 1),
  "rank_low": lambda q, lv: q.get("rank", 1),
}


def encounters(world: dict, monsters: list, level: int, window: list | None = None) -> list:
  """道で出会う魔物（[ID, 重み], ...）のうち、勇者の Lv に合うもの。Lv が「勇者の Lv + encounter_levels」の範囲
  （season.json。[-3, 1] なら Lv −3〜+1）の魔物だけにする。範囲に 1 体もいなければ、範囲にいちばん近い Lv の魔物。
  地形ごとの割合は道のまま（残った魔物で、その地形の重みを分け直す。残った魔物のいない地形は出ない）。
  window（[下, 上]）を渡すと、encounter_levels の代わりにその範囲を使う（散策の強い魔物）。"""
  low, high = (level + d for d in (window or world["season"]["encounter_levels"]))
  lv = world["monsters"]

  def gap(mid: str) -> int:
    return max(low - lv[mid]["level"], lv[mid]["level"] - high, 0)

  near = min(gap(mid) for mid, _ in monsters)
  kept = [(mid, w) for mid, w in monsters if gap(mid) == near]
  total: dict[str, float] = {}  # 地形ごとの重み（道のまま）と、残った魔物の重み
  left: dict[str, float] = {}
  for mid, w in monsters:
    total[lv[mid]["terrain"]] = total.get(lv[mid]["terrain"], 0) + w
  for mid, w in kept:
    left[lv[mid]["terrain"]] = left.get(lv[mid]["terrain"], 0) + w
  return [(mid, w * total[lv[mid]["terrain"]] / left[lv[mid]["terrain"]]) for mid, w in kept]


def patrol(world: dict, state: dict, rng: random.Random) -> dict:
  """合う依頼がないときの旅（報酬なし）。となりの町のどれかへ向かう。"""
  lvl, to = monster_level(world), rng.choice(world["neighbors"][state["place"]])
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
    "level": [lvl, lvl],
    "deadline": None,
    "reward": None,
    "cost": world["roads"][(state["place"], to)],
  }


def quest_detail(world: dict, quest: dict) -> str:
  places = world["places"]
  place = places[quest["place"]]["name"]
  if quest["type"] == "hunt":
    return (
      f"{places[quest['report']]['name']}と{place}の間の道で"
      f"{world['monsters'][quest['monster']]['name']}を{quest['count']}体討伐"
    )
  if quest["type"] == "deliver":
    return f"{place}へ{quest.get('cargo', '荷物')}を届ける"
  return f"{place}へ旅をする"


def quest_text(world: dict, quest: dict) -> str:
  """依頼の表示。レアリティと名前があれば「★★ 砂漠の盗賊団（〜を 6 体討伐）」の形にする。"""
  detail = quest_detail(world, quest)
  if quest["type"] == "patrol":
    return detail
  rank = quest.get("rank", 1)
  marks = world["quest_ranks"]
  mark = marks[rank - 1]["mark"] if 1 <= rank <= len(marks) else ""
  return f"{mark} {quest.get('name', '依頼')}（{detail}）"


# ---- 1 日分を進める -------------------------------------------------------------------
class Day:
  """1 日分の出来事を記録しながら進める。"""

  def __init__(self, world: dict, state: dict, today: date, rng: random.Random, guild: dict | None = None):
    self.world, self.state, self.today, self.rng = world, state, today, rng
    self.cfg = world["season"]
    self.hero = state["hero"]
    self.events: list[str] = []
    self.fallen = False
    self.strolled = False
    self.rested = False
    self.guild = guild if guild is not None else new_guild()  # 全員で共有する掲示板（None なら、この勇者だけの掲示板）

  def log(self, text: str) -> None:
    self.events.append(text)

  @property
  def stats(self) -> dict:
    return stats(self.world, self.state)

  # 戦闘 ----------------------------------------------------------------------------
  def damage(self, attack: int, guard: int, dex: int = 0) -> int:
    """1 回の攻撃のダメージ。器用さ（dex）÷ critical.dex_divisor の確率で会心の一撃（multiplier 倍）になる。
    勇者も魔物も同じ。会心があるので、能力の差が小さいと勝ち負けに幅が出る。"""
    hit = max(1, attack - guard // 2 + self.rng.randint(-2, 2))
    crit = self.cfg["critical"]
    rate = dex / crit["dex_divisor"]
    if rate > 0 and self.rng.random() < rate:  # 会心がなければ乱数を使わない
      return hit * crit["multiplier"]
    return hit

  def pick_weapon(self, monster: dict, guard: int) -> tuple[str | None, float, dict[str, int], str | None]:
    """この魔物に、1 回の攻撃の番でいちばんダメージの大きい武器（持ち物から持ち替える）と、属性の倍率と、
    その武器を持ったときの能力（stats。攻撃・素早さだけでなく、武器の HP・防御・器用さも持ち替えたものになる）と、
    その戦いで着ける装飾品。ダメージは、魔物の守り（性質で変わったあと）・属性・2 回攻撃・会心の平均まで考える。
    武器を持たない（None）ほうが効くとき（苦手な属性の武器しかないとき）は、武器を持たずに戦う。
    攻める属性は武器の属性。武器に属性がなければ（素手も）、装飾品の属性（attack_element）。
    装飾品は、ふだんの装飾品（equipment）のまま戦う。ただし、魔物の弱点を突ける属性の装飾品を持っていれば、
    その戦いだけ着け替える。ふだんの装飾品の属性が魔物に苦手なときは、属性のない装飾品に着け替えるか、外す。
    候補の中からどれにするかは、与えるダメージで決める（同じなら、ふだんの装飾品・武器）。"""
    items, crit = self.world["items"], self.cfg["critical"]
    worn = equipment(self.world, self.state)
    usual, enemy = worn["accessory"], monster.get("element")
    accessories = [usual]
    accessories += [  # 弱点を突ける属性の装飾品
      i
      for i in sorted(self.state["inventory"])
      if items[i]["slot"] == "accessory" and i != usual and element_rate(self.world, items[i].get("element"), enemy) > 1
    ]
    if usual and element_rate(self.world, items[usual].get("element"), enemy) < 1:  # 苦手な相手: 着け替えるか外す
      accessories += sorted(
        i for i in self.state["inventory"] if items[i]["slot"] == "accessory" and not items[i].get("element")
      )
      accessories.append(None)
    best = None
    for acc in accessories:
      for iid in [*sorted(i for i in self.state["inventory"] if items[i]["slot"] == "weapon"), None]:
        s = stats(self.world, self.state, {"weapon": iid, "accessory": acc})
        rate = element_rate(self.world, attack_element(self.world, iid, acc), enemy)
        hits = 2 if s["spd"] >= 2 * monster.get("spd", s["spd"]) else 1
        crits = 1 + (crit["multiplier"] - 1) * s["dex"] / crit["dex_divisor"]
        damage = max(1, s["atk"] - guard // 2) * rate * hits * crits
        key = (damage, acc == usual, iid == worn["weapon"])  # 同じなら、いつもの装飾品・武器
        if best is None or key > best[0]:
          best = (key, iid, rate, s, acc)
    return best[1], best[2], best[3], best[4]

  def battle(self, monster: dict) -> bool:
    """勝てば True。回復薬が尽きて HP が 0 になったら False。
    魔物の性質（trait）・属性の相性（武器の属性と魔物の属性）・素早さで戦い方が変わる。
    素早いほうが先に攻撃し、相手の 2 倍以上素早ければ 2 回攻撃する（性質「先手」は、素早さに関係なく魔物が先）。
    勇者は戦いのたびに、持ち物の中からいちばん効く武器に持ち替える（能力は、持ち替えた武器のもの）。"""
    hero, trait = self.hero, monster.get("trait")
    e = W.trait_effect(monster)  # 凶暴・硬い・群れで変わる値
    hp, atk, guard, hits = e["hp"], e["atk"], e["guard"], e["hits"]
    weapon, rate, s, accessory = self.pick_weapon(monster, guard)
    items, worn = self.world["items"], equipment(self.world, self.state)["weapon"]
    hero_atk, spd = s["atk"], s["spd"]
    mspd = monster.get("spd", spd)
    hero_hits = 2 if spd >= 2 * mspd else 1
    hits *= 2 if mspd >= 2 * spd else 1
    hero_first = trait != "first_strike" and spd >= mspd
    while True:
      for side in ("hero", "monster") if hero_first else ("monster", "hero"):
        if side == "hero":
          for _ in range(hero_hits):
            hp -= max(1, round(self.damage(hero_atk, guard, s["dex"]) * rate))
            if hp <= 0:
              break
          if hp <= 0:
            break
          continue
        for _ in range(hits):
          hero["hp"] -= self.damage(atk, s["def"], monster.get("dex", 6))
          below = personality(self.world, self.state).get("potion_below")
          if below is None and hero["hp"] <= 0 and hero["potions"]:  # 性格がなければ、倒れる前に回復薬を使う
            hero["potions"] -= 1
            # 回復の量は、ふだんの装備の最大 HP で決める（持ち替えた武器・装飾品の HP は、その戦いだけ）
            hero["hp"] = max(1, int(self.stats["max_hp"] * self.cfg["potion"]["heal_rate"]))
            continue
          if hero["hp"] <= 0:  # 性格があれば、HP が 0 になったら回復薬があっても力尽きる
            hero["hp"] = 0
            self.log(f"{self.monster_name(monster)}に敗れた")
            return False
          self.use_potion()
      if hp <= 0:
        break
    exp = monster["exp"]
    hero["gold"] += monster["gold"]
    self.state["season_stats"]["kills"] += 1
    note = ""
    if rate > 1:
      if weapon and items[weapon].get("element"):  # 弱点を突いたのは武器の属性
        note = f"（{items[weapon]['name']}で弱点を突いた）" if weapon != worn else "（弱点を突いた）"
      elif accessory != equipment(self.world, self.state)["accessory"]:  # 着け替えた装飾品の属性
        note = f"（{items[accessory]['name']}で弱点を突いた）"
      else:
        note = "（弱点を突いた）"
    self.log(f"{self.monster_name(monster)}を倒した{note} (+{exp}EXP)")
    if trait == "poison":  # 毒: 倒したあとも HP が減る（0 にはならない）
      lost = min(hero["hp"] - 1, max(1, self.stats["max_hp"] // 10))
      if lost > 0:
        hero["hp"] -= lost
        self.log(f"毒で HP が {lost} 減った")
        self.use_potion()
    elif trait == "thief" and hero["gold"] > 0 and self.rng.random() < 0.4:  # 盗み
      stolen = min(hero["gold"], self.rng.randint(5, 20))
      hero["gold"] -= stolen
      self.log(f"逃げ際に {stolen}G 盗まれた")
    self.gain_exp(exp)
    return True

  def use_potion(self) -> None:
    """性格の potion_below（HP の割合）を下回ったら、回復薬を 1 個使う（最大 HP の heal_rate ぶん回復。最大 HP は超えない）。"""
    below, hero = personality(self.world, self.state).get("potion_below"), self.hero
    max_hp = self.stats["max_hp"]
    if below is not None and hero["potions"] and 0 < hero["hp"] < max_hp * below:
      hero["potions"] -= 1
      hero["hp"] = min(max_hp, hero["hp"] + int(max_hp * self.cfg["potion"]["heal_rate"]))
      self.log(f"回復薬を使った（HP {hero['hp']}/{max_hp}）")

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

  # アイテム -------------------------------------------------------------------------
  def draw(self, table: list | None, town: str | None = None) -> str | None:
    """ドロップ表から抽選する（特性「強運」なら、何も出なかったときに 1 度だけ引き直す）。"""
    got = self.draw_once(table, town)
    if got is None and table and hero_trait(self.world, self.state).get("luck"):
      got = self.draw_once(table, town)
    return got

  def draw_once(self, table: list | None, town: str | None = None) -> str | None:
    """ドロップ表から抽選する。珍しさが当たったら、その珍しさのアイテムから均等に選ぶ。
    common は、開放した地域のもの（開放した町の店で売っている品）だけ。
    レア（common 以外）は、その町（town。依頼を出した町）の依頼で出るもの（items.json の towns）だけ。
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

    def here(iid: str) -> bool:  # 出せるアイテムか（common は開放した地域のもの、レアはその町のもの）
      item = items[iid]
      return iid in region if item["rarity"] == W.COMMON else town in item.get("towns", [])

    if key in items:  # アイテムの ID を直接書いたとき
      return key if here(key) else None
    rarities = self.world["rarities"]
    for rarity in reversed(rarities[rarities.index(W.COMMON) : rarities.index(key) + 1]):
      pool = sorted(i for i, it in items.items() if it["rarity"] == rarity and here(i))
      if pool:
        return self.rng.choice(pool)
      if rarity == rarities[rarities.index(W.COMMON) + 1]:
        return None  # rare もない（宝箱など、町のない抽選）なら何も出ない
    return None

  def obtain(self, iid: str, source: str) -> None:
    item, inv = self.world["items"][iid], self.state["inventory"]
    before = equipment(self.world, self.state)[item["slot"]]
    inv[iid] = inv.get(iid, 0) + 1
    mark = rarity_mark(self.world, item["rarity"])
    if item["rarity"] != "common":
      self.state["season_stats"]["rares"].append(iid)
    text = f"{source}{mark}「{item['name']}」を手に入れた"
    if equipment(self.world, self.state)[item["slot"]] == iid and before != iid:
      text += "（装備した）"
    self.log(text + ("！" if item["rarity"] != W.COMMON else ""))

  # 町 --------------------------------------------------------------------------------
  def visit_town(self, town: str) -> None:
    """町に着いた: 依頼の報告と買い物（HP は宿で休憩しないと回復しない）。"""
    state, hero = self.state, self.hero
    state["home_town"] = town
    self.mark_stay(town)
    quest = state["quest"]
    if quest and quest["type"] in ("deliver", "patrol") and quest["place"] == town:
      quest["progress"] = quest["count"]
    if quest and quest["report"] == town and quest["progress"] >= quest["count"]:
      self.report(quest)
    if not personality(self.world, state):  # 性格のない勇者だけ、町に着くと全回復する（性格があれば宿で休憩する）
      hero["hp"] = self.stats["max_hp"]
    self.shop(town)

  def settle(self, quest: dict, status: str) -> None:
    """掲示板の依頼を、達成（done）か失敗（failed）にする（翌日の朝に入れ替わる・受付中に戻る）。"""
    entry = guild_entry(self.guild, quest)
    if entry and entry["status"] == "held" and entry["holder"] == self.state["id"]:
      entry["status"] = status

  def report(self, quest: dict) -> None:
    self.settle(quest, "done")
    reward = quest["reward"]
    self.hero["gold"] += reward["gold"]
    self.state["season_stats"]["quests_done"] += 1
    self.state["quest"] = None
    gain = ([f"+{reward['exp']}EXP"] if reward["exp"] else []) + [f"+{reward['gold']}G"]
    self.log(f"依頼達成！{quest_text(self.world, quest)}（{' '.join(gain)}）")
    iid = self.draw(reward.get("item"), quest["town"])
    if iid:
      self.obtain(iid, "報酬で")
    self.gain_exp(reward["exp"])

  def shop(self, town: str) -> None:
    hero, items, bought = self.hero, self.world["items"], []
    potion, me = self.cfg["potion"], personality(self.world, self.state)
    if me:
      # 性格の順: 宿（泊まるなら、その分のお金を残す）→ 回復薬を potions_first 個まで → 装備 → 回復薬を keep 個まで
      inn = self.cfg["inn"]["price"] if self.wants_inn() else 0
      while hero["potions"] < min(me["potions_first"], potion["keep"]) and hero["gold"] >= potion["price"] + inn:
        hero["gold"] -= potion["price"]
        hero["potions"] += 1
      reserve = keep_after = inn
    else:
      reserve = potion["price"] * max(0, potion["keep"] - hero["potions"])  # 回復薬の分は残して装備を買う
      keep_after = 0
    worn = equipment(self.world, self.state)
    rank = {r: n for n, r in enumerate(self.world["rarities"])}
    key = lambda i: equip_key(self.world, items[i], rank)  # noqa: E731  装備を選ぶときと同じ比べ方
    # 強い品から順に見る（買える中でいちばん強い品を買い、それより弱い品は買わない）
    for iid in sorted(self.world["places"][town].get("shop", []), key=key, reverse=True):
      item, cur = items[iid], worn[items[iid]["slot"]]
      better = cur is None or key(iid) > key(cur)
      # まだ持っていない属性の武器も買う（属性の弱点を突くために、戦いのたびに持ち替える）
      owned = {items[i].get("element") for i in self.state["inventory"] if items[i]["slot"] == "weapon"}
      better = better or (item["slot"] == "weapon" and item.get("element") and item["element"] not in owned)
      if better and iid not in self.state["inventory"] and hero["gold"] >= item["price"] + reserve:
        hero["gold"] -= item["price"]
        self.state["inventory"][iid] = 1
        worn = equipment(self.world, self.state)
        bought.append(item["name"])
    while hero["potions"] < potion["keep"] and hero["gold"] >= potion["price"] + keep_after:  # 宿代は残す
      hero["gold"] -= potion["price"]
      hero["potions"] += 1
    name = self.world["places"][town]["name"]
    if bought:
      self.log(f"{name}で{'と'.join(bought)}を買った")

  def mark_stay(self, town: str) -> None:
    """町にいる日を数え始める（別の町に着いたときだけ。同じ町に戻っても数え直さない）。"""
    stay = self.state.get("stay")
    if not isinstance(stay, dict) or stay.get("town") != town:
      self.state["stay"] = {"town": town, "since": self.today.isoformat()}

  def stayed_too_long(self, town: str) -> bool:
    """同じ町に stay_days_max 日以上いるか（その日は依頼を受けず、となりの町へ旅に出る。#231）。"""
    self.mark_stay(town)  # 数え始めていなければ、今日から
    limit = self.cfg.get("stay_days_max")
    since = date.fromisoformat(self.state["stay"]["since"])
    return bool(limit) and (self.today - since).days >= limit

  def take_quest(self, town: str) -> None:
    if self.stayed_too_long(town):  # 居ついたので、依頼を受けずに旅に出る（依頼の途中なら、帰ってきた次の日の朝）
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
      e for e in self.guild["towns"].get(town, []) if e["status"] == "open" and e["id"] not in self.state["taken"]
    ]
    entries = [e for e in open_ if suitable(e, level, levels)]
    if not entries and levels:
      # 性格の範囲に合う依頼がなければ、範囲より弱い依頼のうち、範囲にいちばん近い Lv の依頼を受ける
      # （範囲より強い依頼は受けない。強敵は Lv が足りなければ受けない）
      others = [e for e in open_ if suitable(e, level, [None, levels[1]])]  # 下限だけを外して、ほかの決まりは同じ
      if others:
        near = min(level_gap(e, level, levels) for e in others)
        entries = [e for e in others if level_gap(e, level, levels) == near]
    if entries:
      if me:  # 性格の選び方で並べ、同じなら掲示板の上のもの（ランダムはなし）
        order = [QUEST_ORDER[k] for k in me["quest"]["order"]]
        entry = min(entries, key=lambda e: tuple(f(e, level) for f in order))
      else:
        entry = self.rng.choice(entries)
      entry.update(status="held", holder=self.state["id"])
      # 深く写す（掲示板の依頼と段取りの一覧を共有しない）。段取り・進み・行動力・報酬は最初の状態から作り直す
      # （前に受けた勇者が力尽きて戻ってきた依頼でも、今の world/ の道の長さで、最初の状態で受ける）
      quest = finish_quest(self.world, copy.deepcopy({k: v for k, v in entry.items() if k not in GUILD_FIELDS}), town)
      self.state["taken"].append(quest["id"])
      self.log(
        f"{self.world['places'][town]['name']}のギルドで依頼を受けた: {quest_text(self.world, quest)}"
        f"（行動力 {quest['cost']}）"
      )
    else:  # 受けられる依頼がない（どれも性格の範囲より強い・強敵の Lv が足りない・ほかの勇者に取られた）ので、旅に出る
      quest = patrol(self.world, self.state, self.rng)
      self.state["taken"].append(f"{self.today.isoformat()}-patrol")  # 旅もその日の 1 回に数える
      self.log(
        f"{self.world['places'][town]['name']}のギルドには受けられる依頼がない。"
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
  def danger_shift(self) -> int:
    """配達を運んでいる途中なら、その配達の危なさで、道で出会う魔物の Lv を上げる量（quests.json の deliveries）。"""
    quest = self.state["quest"]
    if not quest or quest.get("type") != "deliver" or quest.get("progress", 0) >= quest.get("count", 1):
      return 0
    dangers = (self.world.get("deliveries") or {}).get("dangers", [])
    danger = quest.get("danger", 0)
    return dangers[danger]["shift"] if 0 <= danger < len(dangers) else 0

  def road_event(self, a: str, b: str, stroll: bool = False) -> bool:
    """道を 1 行動進んだときの出来事。力尽きたら False。
    stroll（散策）なら散策の表（stroll_events・stroll_treasure）。強い魔物（strong_battle）は、勇者の Lv + stroll_strong_levels の魔物。"""
    info = self.world["road_info"][(a, b)]
    kinds, weights = zip(*self.cfg["stroll_events" if stroll else "road_events"].items())
    kind = self.rng.choices(kinds, weights)[0]
    if kind in ("battle", "strong_battle"):  # 道の戦いは 1 体だけ（何体かと戦うのは討伐の依頼だけ）
      level = self.hero["level"] + self.danger_shift()
      window = self.cfg["stroll_strong_levels"] if kind == "strong_battle" else None
      mids, mw = zip(*encounters(self.world, info["monsters"], level, window))
      if not self.battle(self.world["monsters"][self.rng.choices(mids, mw)[0]]):
        return False
    elif kind == "treasure":
      if stroll:  # 散策の宝箱: 装備・回復薬・お金の重み（stroll_treasure）
        found, fw = zip(*self.cfg["stroll_treasure"].items())
        found = self.rng.choices(found, fw)[0]
        iid = self.draw([[W.COMMON, 1]]) if found == "item" else None
      else:
        iid = self.draw(self.cfg["treasure"])
        found = "item" if iid else ("potion" if self.rng.random() < 0.3 else "gold")
      if iid:
        self.obtain(iid, "宝箱から")
      elif found == "potion":
        self.hero["potions"] += 1
        self.log("宝箱から回復薬を見つけた")
      else:  # 出せる装備がないときも、お金
        gold = self.rng.randint(5, 15) * monster_level(self.world)
        self.hero["gold"] += gold
        self.log(f"宝箱から{gold}Gを見つけた")
    else:
      texts = [t for k in info["terrain"] for t in self.cfg.get("terrain_scenery", {}).get(k, [])]
      self.log(self.rng.choice(texts or self.cfg["scenery"]))
    return True

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
    """討伐の依頼を 1 行動ぶん進める（行き・帰りは道の出来事。討伐は 1 行動で、残りの目的の魔物と 1 体ずつ連戦する。
    HP は減ったまま次の魔物へ。倒すたびに経験値・お金が入り、途中で力尽きても倒した分は残る）。"""
    if not quest.get("steps"):  # 段取りが壊れている（読み込みで捨てるので、ふつうは起きない）。止まらずに失敗にする
      self.fail_quest("段取りが壊れていた")
      return True
    phase = quest["steps"][0]
    if phase[0] == "hunt":
      left = quest["count"] - quest["progress"]
      if left > 1:
        self.log(f"{self.world['monsters'][quest['monster']]['name']}が{left}体現れた")
      while quest["progress"] < quest["count"]:
        if not self.battle(self.world["monsters"][quest["monster"]]):
          return False
        quest["progress"] += 1
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
      self.visit_town(quest["report"])
    return True

  def stroll(self, town: str) -> bool:
    """その日はもう依頼を受けたので、町の周りを散策する（道の出来事だけが起きる。町からは動かない）。"""
    if not self.strolled:
      self.strolled = True
      self.log(f"{self.world['places'][town]['name']}の周りを散策した")
    return self.road_event(town, self.rng.choice(self.world["neighbors"][town]), stroll=True)

  def wants_inn(self) -> bool:
    """性格の inn_below（HP の割合）を下回っていて、宿代があるか。"""
    below = personality(self.world, self.state).get("inn_below")
    hero = self.hero
    return below is not None and hero["hp"] < self.stats["max_hp"] * below and hero["gold"] >= self.cfg["inn"]["price"]

  def rest_at_inn(self, town: str) -> bool:
    """宿で休憩する（1 行動と宿代で HP が全回復する）。"""
    self.hero["gold"] -= self.cfg["inn"]["price"]
    self.hero["hp"] = self.stats["max_hp"]
    self.log(f"{self.world['places'][town]['name']}の宿で休んだ（-{self.cfg['inn']['price']}G、HP 全回復）")
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
        if self.wants_inn():  # 依頼を持っていないときだけ、宿で休憩する
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


def advance(world: dict, state: dict, today: date, actions: int | None = None, guild: dict | None = None) -> bool:
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
  if guild is not None:  # 朝の掲示板にする（前の日の達成・失敗は、ここで入れ替わる・受付中に戻る）
    refresh_guild(world, guild, today, season)
  day = Day(world, state, today, rng, guild)
  if new_season:
    day.log(f"シーズン{season}が始まった（レベルは 1 に戻り、持ち物はそのまま）")
  if actions is None:
    actions = world["season"]["actions_per_day"]
  town = state["place"]
  if state["season_day"] == 1 and world["places"][town]["type"] == "town" and not state["road"]:
    day.visit_town(town)  # 旅立ちの日は最初の町で支度する
  if actions == 0:
    hero = state["hero"]
    hero["hp"] = min(day.stats["max_hp"], hero["hp"] + int(day.stats["max_hp"] * world["season"]["rest_heal_rate"]))
    day.log(rng.choice(world["season"]["rest"]))
  for _ in range(actions):
    if not day.act():
      day.fall()
      break
  else:
    # 溜めた行動力: 依頼の途中で今日の行動回数を使い切ったら使う（宿の休憩にも使う）。1 日に使う量に上限はない。
    # 旅（受けられる依頼がないとき）や、残りの行動でとなりの町へ向かう道には使わない。休みの日（行動回数 0）も使わない
    hero, rest_day = state["hero"], actions == 0

    def on_quest() -> bool:
      quest = state["quest"]
      return bool(quest) and quest["type"] != "patrol"

    while (
      not rest_day
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
