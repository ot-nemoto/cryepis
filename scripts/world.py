"""シーズン制 RPG の世界（world/ の下のデータファイル）を読み込み、検証する。

world/
  season.json   シーズンの長さ・初期ステータス・レベルアップ・1 日の行動回数（固定）など
  map.json      地方（areas）、場所（町・ダンジョン）と道（陸路・航路・ダンジョンへの道）、開放した町（open）
  map-base.svg  ベースの地図（地形の絵。map.json の layout.background）
  monsters.json 敵（出る地形と性質）と、性質の一覧
  items.json    アイテム（珍しさの一覧を含む）
  quests.json   ギルドの依頼（レアリティ・共通のひな形・町ごとの依頼）

参照はすべて ID で行う。データに誤りがあれば validate() がその一覧を返し、load_world() は止まる。
"""

from __future__ import annotations

import base64
import heapq
import json
import math
from datetime import date
from pathlib import Path

import common

WORLD_DIR = common.ROOT / "world"
SLOTS = ("weapon", "helmet", "shield", "accessory")  # 鎧はない
# 属性を書ける部位（武器の属性で攻める。装飾品の属性は、武器に属性がないとき攻撃に付く）
ELEMENT_SLOTS = ("weapon", "accessory")
SLOT_NAMES = {"weapon": "武器", "helmet": "兜", "shield": "盾", "accessory": "装飾品"}
PLACE_TYPES = ("town", "dungeon")
# 道の種類: 陸路・航路は町どうしを結び、勇者が通る。ダンジョンへの道は地図に描くだけ（第 2 段階で使う）
ROAD_KINDS = ("land", "sea", "trail")
LABEL_SIDES = ("up", "down", "left", "right")  # 地図で場所の名前を置く向き
TERRAINS = ("plain", "forest", "jungle", "desert", "wetland", "snow", "mountain", "volcano", "sea")
TERRAIN_NAMES = {
  "plain": "草原",
  "forest": "森",
  "jungle": "密林",
  "desert": "砂漠",
  "wetland": "湿地",
  "snow": "雪原",
  "mountain": "山",
  "volcano": "火山",
  "sea": "海",
}
TRAITS = ("first_strike", "poison", "swarm", "fierce", "armored", "thief")
REWARD_STATS = ("hp", "atk", "def", "spd", "dex")  # 魔物の強さ点に使う能力（season.json の monster_reward）
QUEST_TYPES = ("hunt", "deliver")
ROAD_EVENTS = ("battle", "treasure", "nothing")
STATS = ("atk", "def", "hp")
NONE = "none"  # ドロップ表の「何も出ない」
COMMON = "common"  # 店で売り、どの町でも出る珍しさ（それ以外はレアで、決めた町の依頼でしか出ない）
# ベースの地図（layout.background）に使える形式と、大きさの上限
BACKGROUND_TYPES = {
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
}
BACKGROUND_MAX_BYTES = 1024 * 1024
BIG = 10**9
# season.json の必須項目: 名前 → (型, 最小, 最大)
SEASON_KEYS = {
  "months": (int, 1, 120),
  "actions_per_day": (int, 1, 50),
  "rest_heal_rate": (float, 0, 1),
  "retreat_hp_rate": (float, 0, 1),
  "quest_open_days": (int, 1, 30),
  "deliver_range": (int, 1, BIG),
  "max_level": (int, 1, BIG),
}
HERO_KEYS = {
  "max_hp": (int, 1, BIG),
  "gold": (int, 0, BIG),
  "potions": (int, 0, BIG),
}
LEVEL_UP_KEYS = {
  "hp": (int, 0, BIG),
  "exp_base": (float, 0.01, BIG),
  "exp_power": (float, 0.01, 10),
}
POTION_KEYS = {"price": (int, 1, BIG), "heal_rate": (float, 0.01, 1), "keep": (int, 0, BIG)}


def read_json(path: Path) -> dict:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    raise SystemExit(f"{path} がありません") from None
  except json.JSONDecodeError as e:
    raise SystemExit(f"{path} が JSON として読めません: {e}") from e


def load_raw(directory: Path | None = None) -> dict:
  d = directory or WORLD_DIR
  items = read_json(d / "items.json")
  mp = read_json(d / "map.json")
  monsters = read_json(d / "monsters.json")
  quests = read_json(d / "quests.json")
  background = None
  name = (mp.get("layout") or {}).get("background") if isinstance(mp.get("layout"), dict) else None
  if isinstance(name, str) and name and "/" not in name and "\\" not in name:
    path = d / name
    background = {"name": name, "bytes": path.read_bytes() if path.is_file() else None}
  return {
    "season": read_json(d / "season.json"),
    "map": mp,
    "background": background,
    "monsters": monsters["monsters"],
    "traits": monsters.get("traits", {}),
    "elements": monsters.get("elements", {}),
    "rarities": items["rarities"],
    "items": items["items"],
    "quests": quests["templates"],
    "quest_ranks": quests.get("ranks"),
    "quest_reward": quests.get("reward"),
    "town_quests": quests.get("towns", {}),
  }


def load_world(directory: Path | None = None) -> dict:
  raw = load_raw(directory)
  errors = validate(raw)
  if errors:
    raise SystemExit("world/ のデータに誤りがあります:\n" + "\n".join(f"  - {e}" for e in errors))
  return build(raw)


# ---- 検証 ---------------------------------------------------------------------------
def _range_ok(value) -> bool:
  return (
    isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value) and 1 <= value[0] <= value[1]
  )


def _point_ok(value) -> bool:
  return (
    isinstance(value, list)
    and len(value) == 2
    and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
  )


def _weights_ok(pairs) -> bool:
  return (
    isinstance(pairs, list)
    and pairs
    and all(isinstance(p, list) and len(p) == 2 and isinstance(p[1], (int, float)) and p[1] > 0 for p in pairs)
  )


def stat_at(season: dict, stat: str, level: int) -> int:
  """レベル level の基本能力値（装備なし・特性なし）。HP は level_up、ほかは growth の一覧。"""
  if stat == "hp":
    return season["hero"]["max_hp"] + season["level_up"]["hp"] * (level - 1)
  table = season["growth"][stat]
  return table[max(0, min(level, len(table)) - 1)]


def open_towns(mp: dict) -> set:
  """開放した町（勇者が行ける町）。map.json に open がなければ、全部の町。"""
  towns = {p.get("id") for p in mp.get("places", []) if p.get("type") == "town"}
  return set(mp["open"]) & towns if isinstance(mp.get("open"), list) else towns


def road_open(r: dict, opened: set, places: dict) -> bool:
  """道を使うか（両端の町が開放されている。ダンジョンへの道は、その町が開放されている）。"""
  return all(r.get(e) in opened or places.get(r.get(e), {}).get("type") == "dungeon" for e in ("from", "to"))


def validate(raw: dict) -> list[str]:
  """データの誤りの一覧（空なら問題なし）。"""
  errors: list[str] = []
  err = errors.append
  season, mp = raw["season"], raw["map"]
  places = {p.get("id"): p for p in mp.get("places", [])}
  monsters = {m.get("id"): m for m in raw["monsters"]}
  items = {i.get("id"): i for i in raw["items"]}
  rarities = raw["rarities"]

  for label, rows in (
    ("map.json の places", mp.get("places", [])),
    ("monsters.json", raw["monsters"]),
    ("items.json", raw["items"]),
    ("quests.json", raw["quests"]),
  ):
    ids = [r.get("id") for r in rows]
    for i in sorted({i for i in ids if ids.count(i) > 1 or not i}, key=str):
      err(f"{label}: ID が空か重複しています: {i!r}")

  def table_ok(where: str, table) -> None:
    if not _weights_ok(table):
      err(f"{where}: [キー, 正の重み] の一覧にしてください")
      return
    for key, _ in table:
      if key != NONE and key not in rarities and key not in items:
        err(f"{where}: {key!r} は none・珍しさ・アイテムの ID のどれでもありません")

  # season.json
  try:
    if date.fromisoformat(season["start"]).day > 28:  # どの月にもある日でないと、シーズンの区切りが作れない
      err("season.json: start の日は 1〜28 にしてください")
  except (KeyError, TypeError, ValueError):
    err("season.json: start は YYYY-MM-DD で指定してください")
  for where, obj, spec in (
    ("season.json", season, SEASON_KEYS),
    ("season.json の hero", season.get("hero"), HERO_KEYS),
    ("season.json の level_up", season.get("level_up"), LEVEL_UP_KEYS),
    ("season.json の potion", season.get("potion"), POTION_KEYS),
  ):
    if not isinstance(obj, dict):
      err(f"{where} がありません")
      continue
    for key, (kind, low, high) in spec.items():
      value = obj.get(key)
      ok = isinstance(value, int) if kind is int else isinstance(value, (int, float))
      if not ok or isinstance(value, bool) or not low <= value <= high:
        name = "整数" if kind is int else "数"
        err(f"{where}: {key} は {low}〜{high} の{name}にしてください")
  for key in ("rest", "scenery"):
    texts = season.get(key)
    if not isinstance(texts, list) or not texts or not all(isinstance(t, str) and t for t in texts):
      err(f"season.json: {key} は文字列の一覧（1 つ以上）にしてください")
  table_ok("season.json の treasure", season.get("treasure"))
  for key, _ in season.get("treasure") or []:  # レアは依頼の報酬だけで出る
    if (key in rarities and key != COMMON) or items.get(key, {}).get("rarity", COMMON) != COMMON:
      err(f"season.json の treasure: {key!r} は宝箱からは出せません（レアは町の依頼の報酬だけで出る）")
  levels = season.get("encounter_levels")
  if (
    not isinstance(levels, list)
    or len(levels) != 2
    or not all(isinstance(v, int) and not isinstance(v, bool) for v in levels)
    or levels[0] > levels[1]
  ):
    err("season.json: encounter_levels は [下, 上]（勇者の Lv との差。整数で、下 ≦ 上）にしてください")
  events = season.get("road_events")
  if (
    not isinstance(events, dict)
    or not events
    or any(k not in ROAD_EVENTS or not isinstance(v, (int, float)) or v < 0 for k, v in events.items())
    or sum(events.values()) <= 0
  ):
    err(f"season.json: road_events は {'/'.join(ROAD_EVENTS)} の重み（0 以上、合計が正）にしてください")
  elif not events.get("battle", 0) > 0:  # 戦闘が起きないと、討伐の依頼がこなせない
    err("season.json: road_events の battle は正の数にしてください（0 だと討伐の依頼が終わりません）")
  scenery = season.get("terrain_scenery", {})
  if not isinstance(scenery, dict):
    err("season.json: terrain_scenery は {地形: 文字列の一覧} にしてください")
    scenery = {}
  for key, texts in scenery.items():
    if key not in TERRAINS or not isinstance(texts, list) or not all(isinstance(t, str) and t for t in texts):
      err(f"season.json: terrain_scenery の {key!r} は地形（{'/'.join(TERRAINS)}）と文字列の一覧にしてください")

  # map.json
  has_areas = "areas" in mp
  areas = mp.get("areas")
  area_ids = set()
  if has_areas:
    if not isinstance(areas, list) or not areas:
      err("map.json: areas（地方）は {id, name, en} の一覧（1 つ以上）にしてください")
      areas = []
    for i, a in enumerate(areas):
      if (
        not isinstance(a, dict)
        or not isinstance(a.get("id"), str)
        or not a["id"]
        or not isinstance(a.get("name"), str)
        or not a["name"]
        or a["id"] in area_ids
      ):
        err(f"map.json の areas[{i}]: id（重複しない文字列）と name（文字列）を書いてください")
        continue
      if a["id"].startswith("_") or a["id"] in places:  # サイトで、町・ダンジョンの ID と区別するため
        err(f"map.json の areas[{i}]: id は _ で始めず、町・ダンジョンの ID と違うものにしてください")
      if "en" in a and (not isinstance(a["en"], str) or not a["en"]):
        err(f"map.json の areas[{i}]: en（英語名）は空でない文字列にしてください")
      area_ids.add(a["id"])
  opened = open_towns(mp)
  if "open" in mp:
    if not isinstance(mp["open"], list) or not mp["open"]:
      err("map.json: open（開放した町）は町の ID の一覧（1 つ以上）にしてください")
    else:
      for tid in mp["open"]:
        if places.get(tid, {}).get("type") != "town":
          err(f"map.json: open の {tid!r} は町の ID にしてください")
  starts = mp.get("starts")
  if not isinstance(starts, list) or not starts:
    err("map.json: starts（最初の町の候補）は町の ID の一覧（1 つ以上）にしてください")
    starts = []
  for sid in starts:
    if places.get(sid, {}).get("type") != "town":
      err(f"map.json: starts の {sid!r} は町の ID にしてください")
    elif sid not in opened:
      err(f"map.json: starts の {sid!r} は開放した町（open）にしてください")
  start = starts[0] if starts else None
  for pid, p in places.items():
    where = f"map.json の {pid}"
    if p.get("type") not in PLACE_TYPES:
      err(f"{where}: type は {'/'.join(PLACE_TYPES)} のどれかにしてください")
    if not p.get("name"):
      err(f"{where}: name がありません")
    if has_areas and (not isinstance(p.get("area"), str) or p["area"] not in area_ids):
      err(f"{where}: area（地方）は areas の ID にしてください")
    for key in ("alias", "region", "text", "en"):
      if key in p and (not isinstance(p[key], str) or not p[key]):
        err(f"{where}: {key} は空でない文字列にしてください")
    if "pos" in p and not _point_ok(p["pos"]):
      err(f"{where}: pos（地図の座標）は [x, y] の数にしてください")
    if "quests_per_day" in p and (
      not isinstance(p["quests_per_day"], int)
      or isinstance(p["quests_per_day"], bool)
      or not 1 <= p["quests_per_day"] <= 50
    ):
      err(f"{where}: quests_per_day（ギルドの規模。掲示板の依頼の枠の数）は 1〜50 の整数にしてください")
    if "label" in p and p["label"] not in LABEL_SIDES:
      err(f"{where}: label（名前を置く向き）は {'/'.join(LABEL_SIDES)} のどれかにしてください")
    if p.get("type") == "town":
      if "quests_per_day" not in p:
        err(f"{where}: quests_per_day（ギルドの規模。掲示板の依頼の枠の数）を書いてください")
      for iid in p.get("shop", []):
        if iid not in items:
          err(f"{where}: 店の {iid!r} が items.json にありません")
        elif not isinstance(items[iid].get("price"), int) or items[iid]["price"] <= 0:
          err(f"{where}: 店の {iid!r} に price（正の整数）がありません")
  layout = mp.get("layout")
  if layout is not None:
    for pid, p in places.items():  # 地図を描くなら、どの場所にも座標がいる（道の線がない道は、両端の座標を結ぶ）
      if "pos" not in p:
        err(f"map.json の {pid}: layout（地図）があるときは pos（地図の座標）を書いてください")
    if not isinstance(layout, dict) or not _point_ok(layout.get("size")):
      err("map.json: layout には size（[幅, 高さ]）を書いてください")
    else:
      name = layout.get("background")
      if name is not None:
        bg = raw.get("background")
        ext = Path(name).suffix.lower() if isinstance(name, str) else ""
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
          err("map.json: layout.background は world/ の中のファイル名だけを書いてください（例: map-base.svg）")
        elif ext not in BACKGROUND_TYPES:
          err(f"map.json: layout.background の形式は {'/'.join(BACKGROUND_TYPES)} のどれかにしてください")
        elif not bg or bg["bytes"] is None:
          err(f"map.json: layout.background の world/{name} がありません")
        elif len(bg["bytes"]) > BACKGROUND_MAX_BYTES:
          err(
            f"map.json: world/{name} が {len(bg['bytes']) // 1024}KB あります"
            f"（{BACKGROUND_MAX_BYTES // 1024}KB 以下にしてください。目安は 300KB 以下）"
          )
  monster_terrains = {m.get("terrain") for m in monsters.values() if on_road(m)}  # ボス・強敵は道に出ない
  for i, r in enumerate(mp.get("roads", [])):
    where = f"map.json の roads[{i}]"
    kind = r.get("kind", "land")
    if kind not in ROAD_KINDS:
      err(f"{where}: kind は {'/'.join(ROAD_KINDS)} のどれかにしてください")
      continue
    for end in ("from", "to"):
      if r.get(end) not in places:
        err(f"{where}: {end} の {r.get(end)!r} が places にありません")
    if r.get("from") == r.get("to"):
      err(f"{where}: 同じ場所どうしをつないでいます")
    ends = sorted(places.get(r.get(e), {}).get("type", "") for e in ("from", "to"))
    if kind == "trail" and ends != ["dungeon", "town"]:
      err(f"{where}: ダンジョンへの道（trail）は町とダンジョンを結んでください")
    if kind != "trail" and ends != ["town", "town"]:
      err(f"{where}: 陸路・航路は町どうしを結んでください")
    if not isinstance(r.get("distance"), int) or r["distance"] < 1:
      err(f"{where}: distance（移動にかかる行動回数）は 1 以上の整数にしてください")
    terrain = r.get("terrain")
    if kind == "trail":
      pass
    elif not _weights_ok(terrain) or any(t not in TERRAINS for t, _ in terrain):
      err(f"{where}: terrain は [地形, 正の重み] の一覧にしてください（地形は {'/'.join(TERRAINS)}）")
    elif not any(t in monster_terrains for t, _ in terrain):
      err(f"{where}: terrain のどの地形にも、monsters.json の魔物がいません（ボス以外）")
    path = r.get("path")
    if path is not None and (not isinstance(path, list) or len(path) < 2 or not all(_point_ok(p) for p in path)):
      err(f"{where}: path（地図に描く線）は [x, y] の一覧（2 点以上）にしてください")
  if start in places:
    adj: dict = {pid: set() for pid in places}
    for r in mp.get("roads", []):
      if (
        r.get("kind", "land") != "trail"
        and r.get("from") in places
        and r.get("to") in places
        and road_open(r, opened, places)
      ):
        adj[r["from"]].add(r["to"])
        adj[r["to"]].add(r["from"])
    seen, todo = {start}, [start]
    while todo:
      for nxt in adj[todo.pop()] - seen:
        seen.add(nxt)
        todo.append(nxt)
    for pid in sorted(opened - seen):
      err(f"map.json: {pid} に最初の町から行けません（開放した町どうしの陸路・航路がつながっていません）")

  # 属性（monsters.json の elements）と相性の倍率（season.json の element_rates）
  elements = raw.get("elements") or {}
  if not isinstance(elements, dict):
    err("monsters.json: elements は {属性の ID: {name, strong}} にしてください")
    elements = {}
  for eid, e in elements.items():
    strong = e.get("strong") if isinstance(e, dict) else None
    if (
      not isinstance(e, dict)
      or not isinstance(e.get("name"), str)
      or not e["name"]
      or not isinstance(strong, str)
      or strong not in elements
      or strong == eid
    ):
      err(
        f"monsters.json の elements の {eid}: name（文字列）と strong（自分以外の、強い相手の属性の ID）を書いてください"
      )
    elif "ore" in e and (not isinstance(e["ore"], str) or not e["ore"]):
      err(f"monsters.json の elements の {eid}: ore（その属性の力を持つ鉱石の名前）は文字列にしてください")
  crit = season.get("critical")
  if (
    not isinstance(crit, dict)
    or not isinstance(crit.get("dex_divisor"), (int, float))
    or isinstance(crit.get("dex_divisor"), bool)
    or crit["dex_divisor"] <= 0
    or not isinstance(crit.get("multiplier"), int)
    or isinstance(crit.get("multiplier"), bool)
    or crit["multiplier"] < 1
  ):
    err(
      "season.json: critical には dex_divisor（器用さをこれで割った値が会心率。正の数）と multiplier（倍率。1 以上の整数）を書いてください"
    )

  # 能力の伸び（season.json の growth）: 攻撃・防御・素早さ・器用さの Lv1〜最大レベルの一覧（下がらない整数）
  growth = season.get("growth")
  levels = season.get("max_level") if isinstance(season.get("max_level"), int) else 0
  for stat in ("atk", "def", "spd", "dex"):
    table = growth.get(stat) if isinstance(growth, dict) else None
    if (
      not isinstance(table, list)
      or len(table) != levels
      or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in table)
      or any(b < a for a, b in zip(table, table[1:]))
    ):
      err(f"season.json: growth の {stat} は、Lv1〜{levels} の値（0 以上の整数・下がらない）の一覧にしてください")
  rates = season.get("element_rates")
  if not isinstance(rates, dict) or any(
    not isinstance(rates.get(k), (int, float)) or isinstance(rates.get(k), bool) or rates[k] <= 0
    for k in ("strong", "weak")
  ):
    err("season.json: element_rates には strong と weak（正の数）を書いてください")

  # 勇者の特性（season.json の hero_traits）。growth は Lv1 からの伸びにかける倍率、luck は引き直し
  table = season.get("hero_traits")
  if not isinstance(table, dict) or not table:
    err("season.json: hero_traits を 1 つ以上書いてください（name・text、growth か luck）")
    table = {}
  for tid, t in table.items():
    where = f"season.json の hero_traits の {tid}"
    if (
      not isinstance(t, dict)
      or not isinstance(t.get("name"), str)
      or not t["name"]
      or not isinstance(t.get("text"), str)
    ):
      err(f"{where}: name と text（文字列）を書いてください")
      continue
    mult = t.get("growth", {})
    if not isinstance(mult, dict) or any(
      k not in ("hp", "atk", "def", "spd", "dex") or not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0
      for k, v in mult.items()
    ):
      err(f"{where}: growth は {{hp/atk/def/spd/dex: 伸びの倍率（0 以上の数）}} にしてください")
    if "luck" in t and not isinstance(t["luck"], bool):
      err(f"{where}: luck は true か false にしてください")

  # monsters.json
  traits = raw.get("traits", {})
  reward_cfg = season.get("monster_reward")
  weights = reward_cfg.get("weights") if isinstance(reward_cfg, dict) else None
  if (
    not isinstance(weights, dict)
    or not weights
    or any(k not in REWARD_STATS or not _num(v) or v < 0 for k, v in weights.items())
    or not _num(reward_cfg.get("exp_divisor"))
    or reward_cfg["exp_divisor"] <= 0
    or not _num(reward_cfg.get("gold_rate"))
    or reward_cfg["gold_rate"] < 0
  ):
    err(
      "season.json: monster_reward は {weights: {hp/atk/def/spd/dex: 重み（0 以上）}, exp_divisor: 正の数, "
      "gold_rate: 0 以上の数} にしてください"
    )
  for key in TRAITS:
    if not isinstance(traits.get(key), dict) or not traits[key].get("name"):
      err(f"monsters.json: traits に {key}（name のある項目）を書いてください")
    elif "score" in traits[key] and (
      not isinstance(traits[key]["score"], dict)
      or any(k not in REWARD_STATS or not _num(v) for k, v in traits[key]["score"].items())
    ):
      err(f"monsters.json の traits の {key}: score は {{hp/atk/def/spd/dex: 強さ点で足す値}} にしてください")
    if isinstance(traits.get(key), dict) and (
      "gold_rate" in traits[key] and (not _num(traits[key]["gold_rate"]) or traits[key]["gold_rate"] < 0)
    ):
      err(f"monsters.json の traits の {key}: gold_rate（倒したときのお金の倍率）は 0 以上の数にしてください")
  for mid, m in monsters.items():
    if not m.get("name"):
      err(f"monsters.json の {mid}: name がありません")
    if m.get("terrain") not in TERRAINS:
      err(f"monsters.json の {mid}: terrain は {'/'.join(TERRAINS)} のどれかにしてください")
    if "trait" in m and m["trait"] not in TRAITS:
      err(f"monsters.json の {mid}: trait は {'/'.join(TRAITS)} のどれかにしてください")
    for key, low in (("level", 1), ("hp", 1), ("atk", 0), ("def", 0), ("spd", 1), ("dex", 0)):
      if not isinstance(m.get(key), int) or isinstance(m.get(key), bool) or m[key] < low:
        err(f"monsters.json の {mid}: {key} は {low} 以上の整数にしてください")
    for key in ("exp", "gold"):
      if key in m:
        err(f"monsters.json の {mid}: {key} は書かないでください（能力から計算する。season.json の monster_reward）")
    if "boss" in m and (not isinstance(m["boss"], str) or places.get(m["boss"], {}).get("type") != "dungeon"):
      err(f"monsters.json の {mid}: boss は、そのボスがいるダンジョンの ID にしてください")
    if "elite" in m and (not isinstance(m["elite"], bool) or (m["elite"] and "boss" in m)):
      err(f"monsters.json の {mid}: elite（強敵。道に出ず、討伐の依頼で狙う）は true か false。ボスには付けない")
    elif m.get("elite") and not any(
      q.get("type") == "hunt" and q.get("monster") == mid
      for town, qs in (raw.get("town_quests") or {}).items()
      if town in opened and isinstance(qs, list)
      for q in qs
      if isinstance(q, dict)
    ):
      err(
        f"monsters.json の {mid}: 強敵（elite）は、開放した町の討伐の依頼で狙えるようにしてください（道では出会わないため）"
      )
    if "text" in m and (not isinstance(m["text"], str) or not m["text"]):
      err(f"monsters.json の {mid}: text は空でない文字列にしてください")
    if "element" in m and (not isinstance(m["element"], str) or m["element"] not in elements):
      err(f"monsters.json の {mid}: element は {'/'.join(elements)} のどれかにしてください（属性なしなら書かない）")

  # items.json
  if not rarities or len(set(rarities)) != len(rarities) or NONE in rarities or COMMON not in rarities:
    err(f"items.json: rarities は重複のない一覧にし、{COMMON} を入れてください（none は使えません）")
  for iid, it in items.items():
    where = f"items.json の {iid}"
    if iid in rarities:
      err(f"{where}: ID に珍しさの名前は使えません")
    if not it.get("name"):
      err(f"{where}: name がありません")
    if it.get("slot") not in SLOTS:
      err(f"{where}: slot は {'/'.join(SLOTS)} のどれかにしてください")
    if it.get("rarity") not in rarities:
      err(f"{where}: rarity は {'/'.join(rarities)} のどれかにしてください")
    for key in STATS:
      if key in it and (not isinstance(it[key], int) or it[key] < 0):
        err(f"{where}: {key} は 0 以上の整数にしてください")
    if "spd" in it and (not isinstance(it["spd"], int) or isinstance(it["spd"], bool)):
      err(f"{where}: spd（素早さの増減。重い装備はマイナス）は整数にしてください")
    if "dex" in it and (not isinstance(it["dex"], int) or isinstance(it["dex"], bool) or it["dex"] < 0):
      err(f"{where}: dex（器用さ）は 0 以上の整数にしてください")
    if "element" in it and (
      it.get("slot") not in ELEMENT_SLOTS or not isinstance(it["element"], str) or it["element"] not in elements
    ):
      err(f"{where}: element は武器と装飾品だけに、{'/'.join(elements)} のどれかで書いてください")
    if "drop_level" in it:
      err(f"{where}: drop_level はもう使いません（出やすさはドロップ表の重みで決める）")
    towns = it.get("towns")
    if it.get("rarity") == COMMON:
      if towns is not None:
        err(f"{where}: common に towns は書けません（common は店で売る。宝箱と依頼の報酬ではどの町でも出る）")
    elif it.get("rarity") in rarities:
      if not isinstance(towns, list) or not towns:
        err(f"{where}: レアには towns（依頼の報酬で出る町の ID の一覧）を書いてください")
      else:
        for tid in towns:
          if not isinstance(tid, str) or places.get(tid, {}).get("type") != "town":
            err(f"{where}: towns の {tid!r} は町の ID にしてください")
          elif tid not in opened:
            err(f"{where}: towns の {tid!r} は開放した町（open）にしてください（ほかの町の依頼は出ないため）")
    if "price" in it and (not isinstance(it["price"], int) or it["price"] <= 0):
      err(f"{where}: price は正の整数にしてください")

  # どの町の依頼にも、いちばん下のレア（rare）がある（上のレアがない町では、当たったら 1 つ下に落とすため）
  if COMMON in rarities and rarities.index(COMMON) + 1 < len(rarities):
    lowest = rarities[rarities.index(COMMON) + 1]
    have = {
      t
      for it in items.values()
      if it.get("rarity") == lowest and isinstance(it.get("towns"), list)
      for t in it["towns"]
      if isinstance(t, str)
    }
    for pid in sorted(opened - have):
      err(f"items.json: {pid} の依頼で出る {lowest} がありません（開放したどの町にも 1 つ以上置いてください）")

  # quests.json
  ranks = raw.get("quest_ranks") or []
  if not isinstance(ranks, list) or not ranks:
    err("quests.json: ranks（依頼のレアリティの一覧）を 1 つ以上書いてください")
    ranks = []
  for n, r in enumerate(ranks, 1):
    where = f"quests.json の ranks[{n}]"
    if not isinstance(r, dict) or not r.get("mark"):
      err(f"{where}: mark（★ の表示）を書いてください")
      continue
    for key in ("weight", "reward"):
      if not isinstance(r.get(key), (int, float)) or isinstance(r.get(key), bool) or r[key] <= 0:
        err(f"{where}: {key} は正の数にしてください")
    table_ok(f"{where} の item", r.get("item"))
  base = raw.get("quest_reward")  # 1 行動あたりの報酬（× 必要な行動力 × レアリティの倍率）
  if not isinstance(base, dict) or any(
    not isinstance(base.get(k), (int, float)) or isinstance(base.get(k), bool) or base[k] < 0 for k in ("exp", "gold")
  ):
    err("quests.json: reward には、1 行動あたりの exp と gold（0 以上の数）を書いてください")

  def rank_ok(where: str, rank) -> None:
    if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= max(len(ranks), 1):
      err(f"{where}: rank は 1〜{len(ranks)} の整数にしてください")

  for q in raw["quests"]:
    where = f"quests.json の {q.get('id')}"
    if q.get("type") not in QUEST_TYPES:
      err(f"{where}: type は {'/'.join(QUEST_TYPES)} のどれかにしてください")
    rank_ok(where, q.get("rank"))
    if not isinstance(q.get("weight"), (int, float)) or q["weight"] <= 0:
      err(f"{where}: weight は正の数にしてください")
    if q.get("type") == "hunt" and not _range_ok(q.get("count")):
      err(f"{where}: count は [最小, 最大]（1 以上）にしてください")
  if not any(q.get("rank") == 1 for q in raw["quests"]):
    err("quests.json: rank 1 の共通の依頼（templates）を 1 つ以上書いてください（掲示板の空いた枠に入る）")
  # 町の依頼: 討伐は町から出る道（陸路・航路）と、その道に出る魔物。配達は別の町へ
  road_terrain = {}
  for r in mp.get("roads", []):
    if r.get("kind", "land") != "trail" and _weights_ok(r.get("terrain")) and road_open(r, opened, places):
      terr = {t for t, _ in r["terrain"]}
      road_terrain[(r.get("from"), r.get("to"))] = road_terrain[(r.get("to"), r.get("from"))] = terr
  town_quests = raw.get("town_quests") or {}
  if not isinstance(town_quests, dict):
    err("quests.json: towns は {町の ID: 依頼の一覧} にしてください")
    town_quests = {}
  seen_ids = [q.get("id") for q in raw["quests"]]
  for tid, qs in town_quests.items():
    if places.get(tid, {}).get("type") != "town":
      err(f"quests.json の towns: {tid!r} は町の ID にしてください")
      continue
    if tid not in opened:
      err(f"quests.json の towns: {tid!r} は開放した町（open）ではありません（依頼は開放した町だけに書いてください）")
      continue
    for q in qs if isinstance(qs, list) else []:
      where = f"quests.json の {tid} の {q.get('id')}"
      if not q.get("id") or q["id"] in seen_ids:
        err(f"{where}: id が空か重複しています")
      seen_ids.append(q.get("id"))
      if not q.get("name"):
        err(f"{where}: name がありません")
      rank_ok(where, q.get("rank"))
      to = q.get("to")
      if q.get("type") == "hunt":
        if (tid, to) not in road_terrain:
          err(f"{where}: to の {to!r} は、{tid} から陸路・航路で直接つながる開放した町にしてください")
        elif monsters.get(q.get("monster"), {}).get("terrain") not in road_terrain[(tid, to)] or monsters[
          q["monster"]
        ].get("boss"):
          err(f"{where}: monster の {q.get('monster')!r} は、その道に出る魔物（ボス以外）にしてください")
        if not isinstance(q.get("count"), int) or q["count"] < 1:
          err(f"{where}: count は 1 以上の整数にしてください")
      elif q.get("type") == "deliver":
        if to == tid or to not in opened:
          err(f"{where}: to は開放した別の町の ID にしてください")
      else:
        err(f"{where}: type は {'/'.join(QUEST_TYPES)} のどれかにしてください")

  # 強さ: アイテムの点数（season.json の item_points）が、0 点から珍しさごとの上限（max）までに入る
  points = season.get("item_points")
  weights = points.get("weights") if isinstance(points, dict) else None
  caps = points.get("max") if isinstance(points, dict) else None
  if (
    not isinstance(weights, dict)
    or set(weights) != set(REWARD_STATS)
    or any(not _num(v) or v <= 0 for v in weights.values())  # 0 だと、その能力をいくら足しても点数が増えない
    or not _num(points.get("element"))
    or points["element"] < 0
    or not isinstance(caps, dict)
    or set(caps) != set(rarities)
    or any(not _num(v) or v < 0 for v in caps.values())
  ):
    err(
      "season.json: item_points は {weights: {hp/atk/def/spd/dex: 能力 +1 の点数（0 より大きい数）}, "
      f"element: 属性の点数（0 以上）, max: {{{'/'.join(rarities)}: 点数の上限（0 以上）}}}} にしてください"
    )
  elif not errors:
    for iid, it in items.items():
      pts, cap = item_points(it, points), caps[it["rarity"]]
      if not -1e-9 <= pts <= cap + 1e-9:
        err(f"items.json の {iid}: 点数 {pts:g} が、{it['rarity']} の範囲（0〜{cap:g} 点）に入っていません")
      if "element" in it and it["rarity"] == COMMON:
        err(f"items.json の {iid}: 属性は common には付けられません（rare から）")
  return errors


def item_points(item: dict, cfg: dict) -> float:
  """アイテムの点数（能力の増減 × season.json の item_points.weights の合計。マイナスの能力も同じ重みで引く）。
  属性があれば item_points.element を足す。"""
  stats = sum(cfg["weights"].get(k, 0) * item.get(k, 0) for k in REWARD_STATS)
  return stats + (cfg.get("element", 0) if item.get("element") else 0)


# ---- 組み立て -------------------------------------------------------------------------
def background_uri(bg: dict | None) -> str | None:
  """ベースの地図を、SVG に埋め込める data URI にする（GitHub は SVG の中から別のファイルを読めないため）。"""
  if not bg or bg["bytes"] is None:
    return None
  mime = BACKGROUND_TYPES[Path(bg["name"]).suffix.lower()]
  return f"data:{mime};base64,{base64.b64encode(bg['bytes']).decode()}"


def road_monsters(terrain: list, monsters: list[dict]) -> list[tuple[str, float]]:
  """道に出る魔物と重み（地形の割合を、その地形の魔物の数で分ける）。"""
  pool = []
  for t, weight in terrain:
    here = [m["id"] for m in monsters if m["terrain"] == t and on_road(m)]  # ボス・強敵は道には出ない
    pool += [(mid, weight / len(here)) for mid in here]
  return pool


def on_road(monster: dict) -> bool:
  """道で出会う魔物か（ボスと強敵は道に出ない。強敵は町の討伐の依頼で狙って戦う）。"""
  return not monster.get("boss") and not monster.get("elite")


def _num(v) -> bool:
  return isinstance(v, (int, float)) and not isinstance(v, bool)


def trait_effect(monster: dict) -> dict:
  """性質で変わる、戦いで効く値 {hp, atk, guard, hits}（凶暴・硬い・群れ）。戦い（game.Day.battle）と報酬の計算で使う。"""
  hp, atk, guard, hits = monster["hp"], monster["atk"], monster["def"], 1
  trait = monster.get("trait")
  if trait == "fierce":  # 凶暴: 攻撃が強いが、守りが弱い
    atk, guard = round(atk * 1.3), guard // 2
  elif trait == "armored":  # 硬い: 守りが固く、攻撃は少し弱い
    atk, guard = max(1, round(atk * 0.9)), guard * 2 + 1
  elif trait == "swarm":  # 群れ: 数が多い（倒すのに手間がかかり、攻撃の回数が多い）
    hp, atk, hits = round(hp * 1.5), max(1, round(atk * 0.6)), 2
  return {"hp": hp, "atk": atk, "guard": guard, "hits": hits}


def monster_score(monster: dict, traits: dict, cfg: dict) -> float:
  """魔物の強さ点: 戦いで効く値（性質の効果のあと。攻撃は 1 回の番の合計）に、性質の score を足して、重みをかけて足す。"""
  e = trait_effect(monster)
  stats = {"hp": e["hp"], "atk": e["atk"] * e["hits"], "def": e["guard"], "spd": monster["spd"], "dex": monster["dex"]}
  bonus = traits.get(monster.get("trait"), {}).get("score", {})
  return sum(w * (stats[k] + bonus.get(k, 0)) for k, w in cfg["weights"].items())


def half_up(x: float) -> int:
  """四捨五入（0.5 は切り上げ。Python の round は 0.5 を偶数に寄せるので使わない）。"""
  return math.floor(x + 0.5)


def monster_reward(monster: dict, traits: dict, cfg: dict) -> tuple[int, int]:
  """倒したときの経験値とお金: 経験値 = 強さ点² ÷ exp_divisor、お金 = 経験値 × gold_rate × 性質の gold_rate
  （盗みは多い。どちらも四捨五入）。強さ点は 0 より小さくしない（性質の score をマイナスにしても、2 乗で報酬が増えないように）。"""
  score = max(0.0, monster_score(monster, traits, cfg))
  exp = half_up(score * score / cfg["exp_divisor"])
  return exp, half_up(exp * cfg["gold_rate"] * traits.get(monster.get("trait"), {}).get("gold_rate", 1))


def build(raw: dict) -> dict:
  mp = raw["map"]
  places = {p["id"]: p for p in mp["places"]}
  opened = open_towns(mp)
  towns = [pid for pid, p in places.items() if p["type"] == "town" and pid in opened]  # 勇者が行ける町だけ
  order = {pid: i for i, pid in enumerate(places)}
  roads, info, trails = {}, {}, []
  for r in mp["roads"]:
    if not road_open(r, opened, places):  # 開放していない町への道は使わず、地図にも描かない
      continue
    kind = r.get("kind", "land")
    if kind == "trail":
      trails.append(r)
      continue
    pool = road_monsters(r["terrain"], raw["monsters"])
    for a, b in ((r["from"], r["to"]), (r["to"], r["from"])):
      roads[(a, b)] = r["distance"]
      path = r.get("path")
      info[(a, b)] = {
        "kind": kind,
        "terrain": [t for t, _ in r["terrain"]],
        "monsters": pool,
        "path": path if a == r["from"] or not path else path[::-1],
      }
  adj: dict[str, list[tuple[str, int]]] = {pid: [] for pid in towns}
  for (a, b), d in roads.items():
    adj[a].append((b, d))
  dist: dict[str, dict[str, int]] = {}
  nxt: dict[str, dict[str, str]] = {}
  for src in towns:  # 町ごとに最短経路（同じ距離なら places の並び順が先の道を選ぶ）
    d, first = {src: 0}, {src: src}
    heap = [(0, order[src], src)]
    while heap:
      du, _, u = heapq.heappop(heap)
      if du > d[u]:
        continue
      for v, w in sorted(adj[u], key=lambda e: order[e[0]]):
        if v not in d or du + w < d[v]:
          d[v] = du + w
          first[v] = v if u == src else first[u]
          heapq.heappush(heap, (d[v], order[v], v))
    dist[src], nxt[src] = d, first
  return {
    "season": raw["season"],
    "starts": list(mp["starts"]),
    "layout": mp.get("layout"),
    "background": background_uri(raw.get("background")),
    "areas": {a["id"]: a for a in mp.get("areas") or []},
    "places": places,
    "towns": towns,
    "dungeons": [pid for pid, p in places.items() if p["type"] == "dungeon"],
    "roads": roads,
    "road_info": info,
    "trails": trails,
    "neighbors": {t: sorted((b for (a, b) in roads if a == t), key=lambda b: order[b]) for t in towns},
    "dist": dist,
    "next": nxt,
    "monsters": {
      m["id"]: m | dict(zip(("exp", "gold"), monster_reward(m, raw.get("traits", {}), raw["season"]["monster_reward"])))
      for m in raw["monsters"]
    },
    "traits": raw.get("traits", {}),
    "elements": raw.get("elements", {}),
    "rarities": raw["rarities"],
    "items": {i["id"]: i for i in raw["items"]},
    # 開放した地域の common（開放した町の店で売っている品）。宝箱と依頼の報酬の common はここから出る
    "region_items": sorted({iid for t in towns for iid in places[t].get("shop", [])}),
    "quests": raw["quests"],
    "quest_ranks": raw["quest_ranks"],
    "quest_reward": raw["quest_reward"],
    "town_quests": raw.get("town_quests", {}),
  }
