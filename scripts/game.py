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
  }


def new_season_stats() -> dict:
  return {"quests_done": 0, "quests_failed": 0, "kills": 0, "deaths": 0, "rares": []}


def start_town(world: dict, hero_id: str, season: int | None) -> str:
  """最初の町（勇者とシーズンごとに、map.json の starts からランダムに決める）。"""
  return random.Random(f"start-{season or 0}-{hero_id}").choice(world["starts"])


def pick_trait(world: dict, hero_id: str) -> str:
  """勇者を作るときにランダムで付ける特性。勇者の ID で決まる（シーズンが変わっても同じ）。"""
  return random.Random(f"trait-{hero_id}").choice(sorted(world["season"]["hero_traits"]))


def new_state(world: dict, hero_id: str = "h1", name: str = "勇者") -> dict:
  town = start_town(world, hero_id, None)
  return {
    "id": hero_id,
    "name": name,
    "trait": pick_trait(world, hero_id),
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
  hp_max = stats(world, state)["max_hp"]
  state["hero"]["hp"] = min(state["hero"]["hp"], hp_max)
  return state


QUEST_TYPES = ("hunt", "deliver", "patrol")


def hunt_steps_ok(quest: dict) -> bool:
  """討伐の段取りが、進み（progress）と合っているか。段取りが空なのに倒し終えていない、残りの討伐の数が
  count - progress と違う、などは壊れている（前に掲示板と段取りを共有していたころに壊れた依頼）。"""
  steps, progress, count = quest.get("steps"), quest.get("progress", 0), quest.get("count")
  if not isinstance(steps, list) or not steps or not isinstance(count, int) or not isinstance(progress, int):
    return False
  if not all(isinstance(p, list) and len(p) == 2 and isinstance(p[1], int) and p[1] > 0 for p in steps):
    return False
  left = sum(n for phase, n in steps if phase == "hunt")
  return left == count - progress if left else progress >= count


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
      common = [t for t in world["quests"] if t["rank"] == rank]
      if mine or common or rank == 1:
        break
      rank -= 1
    if mine and (not common or rng.random() < 0.8):  # 町の依頼を優先する
      q = rng.choice(mine)
      own.remove(q)
      quest = make_town_quest(world, q, town, d)
    else:
      quest = make_quest(world, rng.choices(common, [t["weight"] for t in common])[0], town, d, rng)
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


def hunt_steps(world: dict, town: str, to: str, count: int) -> list[list]:
  """討伐の段取り: 行き（狩り場まで）→ 討伐（1 行動で 1 体）→ 帰り。[[段取り, 残りの行動力], ...]。"""
  trip = min(HUNT_TRIP_MAX, -(-world["roads"][(town, to)] // 2))
  return [["go", trip], ["hunt", count], ["back", trip]]


def finish_quest(world: dict, quest: dict, town: str) -> dict:
  """必要な行動力と、報酬（1 行動あたりの報酬 × 必要な行動力 × レアリティの倍率）をつける（期限はない）。
  討伐も配達も、行動力が同じなら報酬も同じ（討伐は別に、1 体ごとの戦闘の経験値とお金が入る）。"""
  lvl, rank = quest_level(world, quest), quest["rank"]
  base, cfg = world["quest_reward"], world["quest_ranks"][rank - 1]
  if quest["type"] == "hunt":
    quest["steps"] = hunt_steps(world, town, quest["place"], quest["count"])
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
      "gold": round(base["gold"] * cost * cfg["reward"]),
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


def make_quest(world: dict, t: dict, town: str, d: date, rng: random.Random) -> dict | None:
  """共通の依頼。討伐: 町から出る道の 1 本で、その道に出る魔物を倒す。配達: 近くの町へ荷物を届ける。"""
  cfg, dist = world["season"], world["dist"][town]
  if t["type"] == "hunt":
    other = rng.choice(world["neighbors"][town])
    mids, weights = zip(*world["road_info"][(town, other)]["monsters"])
    count = rng.randint(*t["count"])
    quest = {"type": "hunt", "place": other, "report": town, "monster": rng.choices(mids, weights)[0], "count": count}
  else:  # deliver
    towns = [p for p in world["towns"] if p != town and dist.get(p, cfg["deliver_range"] + 1) <= cfg["deliver_range"]]
    if not towns:
      return None
    to = rng.choice(towns)
    quest = {"type": "deliver", "place": to, "report": to, "monster": None, "count": 1}
  quest.update(template=t["id"], name=t.get("name", t["type"]), rank=t["rank"], text="")
  return finish_quest(world, quest, town)


def suitable(quest: dict, level: int) -> bool:
  """受けられる依頼か（依頼のレベルが自分のレベル＋3 を超える、強すぎる依頼だけを避ける。弱すぎる依頼も受ける）。
  強敵の討伐は、勇者のレベルが魔物のレベル以上のときだけ受ける（min_level）。"""
  return quest["level"][0] <= level + 3 and level >= quest.get("min_level", 0)


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
    return f"{place}へ荷物を届ける"
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


def route(world: dict, a: str, b: str) -> list[str]:
  """a から b への最短経路（a と b を含む）。"""
  path = [a]
  while path[-1] != b:
    path.append(world["next"][path[-1]][b])
  return path


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

  def pick_weapon(self, monster: dict, guard: int) -> tuple[str | None, float, dict[str, int]]:
    """この魔物に、1 回の攻撃の番でいちばんダメージの大きい武器（持ち物から持ち替える）と、属性の倍率と、
    その武器を持ったときの能力（stats。攻撃・素早さだけでなく、武器の HP・防御・器用さも持ち替えたものになる）。
    ダメージは、魔物の守り（性質で変わったあと）・属性・2 回攻撃・会心の平均まで考える。
    武器を持たない（None）ほうが効くとき（苦手な属性の武器しかないとき）は、武器を持たずに戦う。"""
    items, crit = self.world["items"], self.cfg["critical"]
    worn = equipment(self.world, self.state)["weapon"]
    best = None
    for iid in [*sorted(i for i in self.state["inventory"] if items[i]["slot"] == "weapon"), None]:
      s = stats(self.world, self.state, {"weapon": iid})
      rate = element_rate(self.world, (items[iid] if iid else {}).get("element"), monster.get("element"))
      hits = 2 if s["spd"] >= 2 * monster.get("spd", s["spd"]) else 1
      crits = 1 + (crit["multiplier"] - 1) * s["dex"] / crit["dex_divisor"]
      key = (max(1, s["atk"] - guard // 2) * rate * hits * crits, iid == worn)  # 同じなら、いつもの武器
      if best is None or key > best[0]:
        best = (key, iid, rate, s)
    return best[1], best[2], best[3]

  def battle(self, monster: dict) -> bool:
    """勝てば True。回復薬が尽きて HP が 0 になったら False。
    魔物の性質（trait）・属性の相性（武器の属性と魔物の属性）・素早さで戦い方が変わる。
    素早いほうが先に攻撃し、相手の 2 倍以上素早ければ 2 回攻撃する（性質「先手」は、素早さに関係なく魔物が先）。
    勇者は戦いのたびに、持ち物の中からいちばん効く武器に持ち替える（能力は、持ち替えた武器のもの）。"""
    hero, trait = self.hero, monster.get("trait")
    e = W.trait_effect(monster)  # 凶暴・硬い・群れで変わる値
    hp, atk, guard, hits = e["hp"], e["atk"], e["guard"], e["hits"]
    weapon, rate, s = self.pick_weapon(monster, guard)
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
          if hero["hp"] <= 0:
            if hero["potions"]:  # 倒れる前に回復薬を使う
              hero["potions"] -= 1
              hero["hp"] = max(1, int(s["max_hp"] * self.cfg["potion"]["heal_rate"]))
              continue
            hero["hp"] = 0
            self.log(f"{self.monster_name(monster)}に敗れた")
            return False
      if hp <= 0:
        break
    exp = monster["exp"]
    hero["gold"] += monster["gold"]
    self.state["season_stats"]["kills"] += 1
    note = ""
    if rate > 1:
      note = f"（{items[weapon]['name']}で弱点を突いた）" if weapon != worn else "（弱点を突いた）"
    self.log(f"{self.monster_name(monster)}を倒した{note} (+{exp}EXP)")
    if trait == "poison":  # 毒: 倒したあとも HP が減る（0 にはならない）
      lost = min(hero["hp"] - 1, max(1, s["max_hp"] // 10))
      if lost > 0:
        hero["hp"] -= lost
        self.log(f"毒で HP が {lost} 減った")
    elif trait == "thief" and hero["gold"] > 0 and self.rng.random() < 0.4:  # 盗み
      stolen = min(hero["gold"], self.rng.randint(5, 20))
      hero["gold"] -= stolen
      self.log(f"逃げ際に {stolen}G 盗まれた")
    self.gain_exp(exp)
    return True

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
    その町にない珍しさが当たったら、1 つ下の珍しさに落として選び直す（どの町にも rare は必ずある）。"""
    if not table:
      return None
    keys, weights = zip(*table)
    key = self.rng.choices(keys, weights)[0]
    if key == W.NONE:
      return None
    items, region = self.world["items"], self.world["region_items"]

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
    """町に着いた: 依頼の報告、全回復、買い物。"""
    state, hero = self.state, self.hero
    state["home_town"] = town
    quest = state["quest"]
    if quest and quest["type"] in ("deliver", "patrol") and quest["place"] == town:
      quest["progress"] = quest["count"]
    if quest and quest["report"] == town and quest["progress"] >= quest["count"]:
      self.report(quest)
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
    self.log(f"依頼達成！{quest_text(self.world, quest)}（+{reward['exp']}EXP +{reward['gold']}G）")
    iid = self.draw(reward.get("item"), quest["town"])
    if iid:
      self.obtain(iid, "報酬で")
    self.gain_exp(reward["exp"])

  def shop(self, town: str) -> None:
    hero, items, bought = self.hero, self.world["items"], []
    potion = self.cfg["potion"]
    reserve = potion["price"] * max(0, potion["keep"] - hero["potions"])  # 回復薬の分は残して装備を買う
    worn = equipment(self.world, self.state)
    for iid in self.world["places"][town].get("shop", []):
      item, cur = items[iid], worn[items[iid]["slot"]]
      better = cur is None or item_score(self.world, item) > item_score(self.world, items[cur])
      # まだ持っていない属性の武器も買う（属性の弱点を突くために、戦いのたびに持ち替える）
      owned = {items[i].get("element") for i in self.state["inventory"] if items[i]["slot"] == "weapon"}
      better = better or (item["slot"] == "weapon" and item.get("element") and item["element"] not in owned)
      if better and iid not in self.state["inventory"] and hero["gold"] >= item["price"] + reserve:
        hero["gold"] -= item["price"]
        self.state["inventory"][iid] = 1
        worn = equipment(self.world, self.state)
        bought.append(item["name"])
    while hero["potions"] < potion["keep"] and hero["gold"] >= potion["price"]:
      hero["gold"] -= potion["price"]
      hero["potions"] += 1
    name = self.world["places"][town]["name"]
    if bought:
      self.log(f"{name}で{'と'.join(bought)}を買った")

  def take_quest(self, town: str) -> None:
    level = self.hero["level"]
    refresh_guild(self.world, self.guild, self.today, season_of(self.world, self.today), [town])
    entries = [
      e
      for e in self.guild["towns"].get(town, [])  # 受付中の依頼だけ（受注中のものは受けられない）
      if e["status"] == "open" and suitable(e, level) and e["id"] not in self.state["taken"]
    ]
    if entries:
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
    else:  # 受けられる依頼がない（ほかの勇者に取られた）ので、となりの町へ旅に出る
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
  def road_event(self, a: str, b: str) -> bool:
    """道を 1 行動進んだときの出来事。力尽きたら False。"""
    info = self.world["road_info"][(a, b)]
    kinds, weights = zip(*self.cfg["road_events"].items())
    kind = self.rng.choices(kinds, weights)[0]
    if kind == "battle":
      mids, mw = zip(*info["monsters"])
      if not self.battle(self.world["monsters"][self.rng.choices(mids, mw)[0]]):
        return False
    elif kind == "treasure":
      iid = self.draw(self.cfg["treasure"])
      if iid:
        self.obtain(iid, "宝箱から")
      elif self.rng.random() < 0.3:
        self.hero["potions"] += 1
        self.log("宝箱から回復薬を見つけた")
      else:
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

  def turn_back(self) -> None:
    """道の途中で、引き返したほうが早く町に着くなら引き返す。"""
    state, world = self.state, self.world
    road, origin = state["road"], state["place"]
    total = world["roads"][(origin, road["to"])]
    if total - road["left"] < road["left"]:
      state.update(place=road["to"], road={"to": origin, "left": total - road["left"]})
      self.log(f"HP が心もとないので{world['places'][origin]['name']}へ引き返した")

  def hunt_step(self, quest: dict) -> bool:
    """討伐の依頼を 1 行動ぶん進める（行き・帰りは道の出来事、討伐は目的の魔物と 1 回戦う）。"""
    if not quest.get("steps"):  # 段取りが壊れている（読み込みで捨てるので、ふつうは起きない）。止まらずに失敗にする
      self.fail_quest("段取りが壊れていた")
      return True
    phase = quest["steps"][0]
    if phase[0] == "hunt":
      if not self.battle(self.world["monsters"][quest["monster"]]):
        return False
      quest["progress"] += 1
      if quest["progress"] >= quest["count"]:
        self.log(f"討伐の目標を果たした（{quest['count']}体）")
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
    return self.road_event(town, self.rng.choice(self.world["neighbors"][town]))

  def act(self) -> bool:
    """1 行動。力尽きたら False。依頼を受けるのは 1 日 1 回で、受けるのに行動回数は使わない。"""
    state, hero = self.state, self.hero
    for _ in range(5):  # 町での受注・報告は行動回数を使わない
      if state["road"]:
        if hero["hp"] < self.stats["max_hp"] * self.cfg["retreat_hp_rate"] and not hero["potions"]:
          self.turn_back()
        return self.step(state["road"])
      place, quest = state["place"], state["quest"]
      if quest is None:
        if state["taken"]:  # 今日はもう依頼を受けた（旅に出た）
          return self.stroll(place)
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
  state["last"] = {"date": today.isoformat(), "actions": actions, "events": day.events}
  state["journal"] = (
    [{"date": today.isoformat(), "actions": actions, "text": " / ".join(day.events)}] + state["journal"]
  )[:JOURNAL_DAYS]
  return True
