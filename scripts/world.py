"""シーズン制 RPG の世界（world/ の下のデータファイル）を読み込み、検証する。

world/
  season.json   シーズンの長さ・初期ステータス・レベルアップ・1 日の行動回数（固定）など
  map.json      地方（areas）、場所（町・霊場）と道（陸路・航路・霊場への道）、開放した町（open）
  map-base.svg  ベースの地図（地形の絵。map.json の layout.background）
  monsters.json 敵（出る地形と性質）と、性質の一覧
  items.json    武具（珍しさの一覧を含む）
  quests.json   口入れ屋の依頼（レアリティ・共通のひな形・町ごとの依頼）

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
# 兜の部位（helmet）は「頭」、盾の部位（shield）は「胴」と呼ぶ（#261・#263）
SLOTS = ("weapon", "helmet", "shield", "accessory")
# 属性を書ける部位（武器の属性で攻める。御守の属性は、武器に属性がないとき攻撃に付く）
ELEMENT_SLOTS = ("weapon", "accessory")
SLOT_NAMES = {"weapon": "武器", "helmet": "頭", "shield": "胴", "accessory": "御守"}
PLACE_TYPES = ("town", "dungeon")
# 道の種類: 陸路・航路は町どうしを結び、もののふが通る。霊場への道は地図に描くだけ（第 2 段階で使う）
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
REWARD_STATS = ("hp", "atk", "def", "spd", "dex")  # 物の怪の強さ点に使う能力（season.json の monster_reward）
QUEST_TYPES = ("hunt", "deliver")
ROAD_EVENTS = ("battle", "treasure", "nothing")
STROLL_EVENTS = ("battle", "strong_battle", "treasure", "nothing")
TREASURE_KINDS = ("item", "stone", "potion", "gold")
PERSONALITY_SPARE = (
  "rest",
  "stroll",
  "move",
)  # その日の残りの行動（休む＝行動力を溜める・散策する・となりの町へ移動する）
PERSONALITY_ORDER = ("level_high", "level_low", "rank_high", "rank_low")  # 依頼の選び方（game.QUEST_ORDER）
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
ITEM_TEXT_MAX = 100  # 武具の説明（items.json の text）の長さの上限
BIG = 10**9
# season.json の必須項目: 名前 → (型, 最小, 最大)
SEASON_KEYS = {
  "months": (int, 1, 120),
  "actions_per_day": (int, 1, 50),
  "rest_heal_rate": (float, 0, 1),
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
# 何人かで受ける依頼（#72）: 大物・主の人数、集まるのを待つ日数、主が目覚めるまでに討つ大物の数
PARTY_KEYS = {
  "elite": (int, 1, 10),
  "boss": (int, 1, 10),
  "gather_days": (int, 1, 30),
  "boss_after_elites": (int, 1, BIG),
}


def read_json(path: Path) -> dict:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    raise SystemExit(f"{path} がありません") from None
  except json.JSONDecodeError as e:
    raise SystemExit(f"{path} が JSON として読めません: {e}") from e


def _need(data, key: str, file: str):
  """data（ファイルの中身）の key を取る。なければ SystemExit（KeyError のスタックトレースにしない）。"""
  if not isinstance(data, dict) or key not in data:
    raise SystemExit(f"world/{file} に {key} がありません")
  return data[key]


def load_raw(directory: Path | None = None) -> dict:
  d = directory or WORLD_DIR
  items = read_json(d / "items.json")
  mp = read_json(d / "map.json")
  monsters = read_json(d / "monsters.json")
  quests = read_json(d / "quests.json")
  for file, data in (("items.json", items), ("map.json", mp), ("monsters.json", monsters), ("quests.json", quests)):
    if not isinstance(data, dict):
      raise SystemExit(f"world/{file} は {{ ... }}（辞書）の形にしてください")
  background = None
  name = (mp.get("layout") or {}).get("background") if isinstance(mp.get("layout"), dict) else None
  if isinstance(name, str) and name and "/" not in name and "\\" not in name:
    path = d / name
    background = {"name": name, "bytes": path.read_bytes() if path.is_file() else None}
  return {
    "season": read_json(d / "season.json"),
    "map": mp,
    "background": background,
    "monsters": _need(monsters, "monsters", "monsters.json"),
    "traits": monsters.get("traits", {}),
    "elements": monsters.get("elements", {}),
    "rarities": _need(items, "rarities", "items.json"),
    "items": _need(items, "items", "items.json"),
    "skills": items.get("skills", []),
    "skill_kinds": items.get("skill_kinds", {}),
    "skill_prices": items.get("skill_prices", {}),
    "quests": _need(quests, "templates", "quests.json"),
    "quest_ranks": quests.get("ranks"),
    "quest_reward": quests.get("reward"),
    "hunt_ranks": quests.get("hunt_ranks", []),
    "deliveries": quests.get("deliveries"),
    "hunts": quests.get("hunts"),
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
  """開放した町（もののふが行ける町）。map.json に open がなければ、全部の町。"""
  towns = {
    p.get("id")
    for p in _rows(mp.get("places"))
    if isinstance(p, dict) and p.get("type") == "town" and _hashable(p.get("id"))
  }
  if not isinstance(mp.get("open"), list):
    return towns
  return {t for t in mp["open"] if _hashable(t)} & towns


def road_open(r: dict, opened: set, places: dict) -> bool:
  """道を使うか（両端の町が開放されている。霊場への道は、その町が開放されている）。"""
  return all(r.get(e) in opened or places.get(r.get(e), {}).get("type") == "dungeon" for e in ("from", "to"))


def boss_town(trails: list[dict], towns, dungeon: str | None) -> str | None:
  """その霊場への山道がつながる、最初の開放した町。"""
  for trail in trails:
    a, b = trail.get("from"), trail.get("to")
    if a == dungeon and b in towns:
      return b
    if b == dungeon and a in towns:
      return a
  return None


def party_gear(items: dict, town: str) -> list[str]:
  """大物・主の討伐で、その依頼元の町から出る★★★武具。"""
  return sorted(
    iid
    for iid, item in items.items()
    if item.get("rarity") == "epic" and isinstance(item.get("towns"), list) and town in item["towns"]
  )


def _rows(value) -> list:
  """一覧ならそのまま、そうでなければ空（形の誤りは _check_shape が知らせる）。"""
  return value if isinstance(value, list) else []


def _hashable(value) -> bool:
  try:
    hash(value)
  except TypeError:
    return False
  return True


def _index(rows) -> dict:
  """ID → 行。一覧でない・辞書でない行・辞書の鍵にできない ID は飛ばす（そのことは _check_shape が知らせる）。"""
  return {row.get("id"): row for row in _rows(rows) if isinstance(row, dict) and _hashable(row.get("id"))}


class _Check:
  """validate の途中経過（データと、見つけた誤り）。検査の関数（_check_*）がこれを読んで、誤りを err で足す。
  誤りの並びは、検査を呼ぶ順（validate の中）で決まる。"""

  def __init__(self, raw: dict):
    # 形が違うデータでも落ちない（形の誤りは _check_shape が知らせる）。一覧でなければ空、ID が辞書の鍵にできなければ飛ばす
    self.raw = raw
    self.errors: list[str] = []
    season, mp = raw.get("season"), raw.get("map")
    self.season = season if isinstance(season, dict) else {}
    self.mp = mp if isinstance(mp, dict) else {}
    self.places = _index(self.mp.get("places"))
    self.roads = [r for r in _rows(self.mp.get("roads")) if isinstance(r, dict)]
    self.monsters = _index(raw.get("monsters"))
    self.items = _index(raw.get("items"))
    rarities = raw.get("rarities")
    self.rarities = rarities if isinstance(rarities, list) else []
    self.opened: set = set()  # _check_open_and_starts が決める（開放した町）
    self.elements: dict = {}  # _check_elements が決める（属性）

  def err(self, msg: str) -> None:
    self.errors.append(msg)

  def table_ok(self, where: str, table) -> None:
    """[キー, 正の重み] の一覧（ドロップ表）が正しいか。"""
    if not _weights_ok(table):
      self.err(f"{where}: [キー, 正の重み] の一覧にしてください")
      return
    for key, _ in table:
      if not _hashable(key):
        self.err(f"{where}: キーは文字列にしてください: {key!r}")
      elif key != NONE and key not in self.rarities and key not in self.items:
        self.err(f"{where}: {key!r} は none・珍しさ・武具の ID のどれでもありません")


def _rank_ok(err, ranks: list, where: str, rank) -> None:
  if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= max(len(ranks), 1):
    err(f"{where}: rank は 1〜{len(ranks)} の整数にしてください")


def _check_shape(c: _Check):
  """各ファイルの形のうち、ほかの検査が前提にしているもの（辞書の一覧であること、辞書の鍵や集合に入れる ID・地形が
  文字列などの鍵にできる値であること）。前提が崩れていたら、そのことを知らせて、ほかの検査はせずに止める
  （形を直してから、続きを調べる）。それ以外の値の誤りは、ほかの検査が個別に知らせる。"""
  err, raw, mp = c.err, c.raw, c.mp

  def rows(where: str, value, required: bool = True) -> list:
    """辞書の一覧。required でなければ、書かなくてよい（なければ空）。"""
    if value is None and not required:
      return []
    if not isinstance(value, list):
      err(f"{where} は、辞書の一覧にしてください")
      return []
    bad = [i for i, r in enumerate(value) if not isinstance(r, dict)]
    if bad:  # どの行か分かるように、番号（0 から。先頭の数件）を添える
      err(f"{where} は、辞書の一覧にしてください（辞書でないのは {', '.join(f'[{i}]' for i in bad[:5])}）")
      return []
    return value

  def keys(where: str, values) -> None:
    """一覧の中身が、辞書の鍵や集合に入れられる値（文字列・数など。一覧や辞書ではない）か。"""
    if isinstance(values, list) and not all(_hashable(v) for v in values):
      err(f"{where} は、文字列の一覧にしてください")

  for name, data in (("season.json", raw.get("season")), ("map.json", raw.get("map"))):
    if not isinstance(data, dict):
      err(f"{name} は {{ ... }}（辞書）の形にしてください")
  for i, p in enumerate(rows("map.json の places", mp.get("places"), False)):
    keys(f"map.json の places[{i}] の shop", p.get("shop"))
    if "legendary_forge" in p and not isinstance(p["legendary_forge"], bool):
      err(f"map.json の places[{i}]: legendary_forge は true または false にしてください")
  for i, r in enumerate(rows("map.json の roads", mp.get("roads"), False)):
    where = f"map.json の roads[{i}]"
    for end in ("from", "to"):
      if not _hashable(r.get(end)):
        err(f"{where}: {end} は文字列にしてください")
    terrain = r.get("terrain")
    if isinstance(terrain, list) and not all(not isinstance(t, list) or not t or _hashable(t[0]) for t in terrain):
      err(f"{where}: terrain は [地形（文字列）, 重み] の一覧にしてください")
  keys("map.json の open", mp.get("open"))
  keys("map.json の starts", mp.get("starts"))
  for i, m in enumerate(rows("monsters.json の monsters", raw.get("monsters"))):
    if not _hashable(m.get("terrain")):
      err(f"monsters.json の monsters[{i}]: terrain は文字列にしてください")
    if not _hashable(m.get("area")):
      err(f"monsters.json の monsters[{i}]: area は文字列にしてください")
  rows("items.json の items", raw.get("items"))
  rarities = raw.get("rarities")
  if isinstance(rarities, list) and not all(isinstance(r, str) for r in rarities):  # 文章に並べるので、文字列だけ
    err("items.json の rarities は、文字列の一覧にしてください")
  rows("quests.json の templates", raw.get("quests"))
  towns = raw.get("town_quests")
  for tid, qs in (towns if isinstance(towns, dict) else {}).items():
    for i, q in enumerate(rows(f"quests.json の towns の {tid}", qs, False)):
      for key in ("monster", "to"):
        if not _hashable(q.get(key)):
          err(f"quests.json の towns の {tid}[{i}]: {key} は文字列にしてください")


def _check_ids(c: _Check):
  """ID の空・重複（places・monsters・items・quests）。"""
  err, mp, raw = c.err, c.mp, c.raw
  for label, rows in (
    ("map.json の places", _rows(mp.get("places"))),
    ("monsters.json", raw["monsters"]),
    ("items.json", raw["items"]),
    ("quests.json", raw["quests"]),
  ):
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    for i in sorted({i for i in ids if _hashable(i) and (ids.count(i) > 1 or not i)}, key=str):
      err(f"{label}: ID が空か重複しています: {i!r}")
    for i in ids:
      if not _hashable(i):
        err(f"{label}: ID は文字列にしてください: {i!r}")


def _check_season(c: _Check):
  """season.json の基本の項目（start・数値の範囲・文章・宝箱・道の出来事など）。"""
  err, season = c.err, c.season
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
    ("season.json の party", season.get("party"), PARTY_KEYS),
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
  for key in ("stroll_treasure", "road_treasure"):
    if not _weights_dict_ok(season.get(key), TREASURE_KINDS):
      err(f"season.json: {key} は {'/'.join(TREASURE_KINDS)} の重み（0 以上、合計が正）にしてください")
  stones = season.get("party_stones")
  if (
    not isinstance(stones, dict)
    or set(stones) != {"elite", "boss"}
    or not all(type(n) is int and n > 0 for n in stones.values())
  ):
    err("season.json: party_stones の elite・boss は石の個数（正の整数）にしてください")
  gold = season.get("treasure_gold")
  if (
    not isinstance(gold, list)
    or len(gold) != 2
    or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in gold)
    or gold[0] > gold[1]
  ):
    err("season.json: treasure_gold は宝箱のお金 [下, 上]（0 以上の整数。下 ≦ 上）にしてください")
  if not isinstance(season.get("rare_drops", True), bool):
    err("season.json: rare_drops は true か false にしてください（false ならレアの武具を出さない）")
  levels = season.get("encounter_levels")
  if (
    not isinstance(levels, list)
    or len(levels) != 2
    or not all(isinstance(v, int) and not isinstance(v, bool) for v in levels)
    or levels[0] > levels[1]
  ):
    err("season.json: encounter_levels は [下, 上]（もののふの Lv との差。整数で、下 ≦ 上）にしてください")
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


def _check_areas(c: _Check) -> set | None:
  """map.json の areas（地方）。地方の ID の集合を返す（areas がなければ None）。"""
  err, mp, places = c.err, c.mp, c.places
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
      if a["id"].startswith("_") or a["id"] in places:  # サイトで、町・霊場の ID と区別するため
        err(f"map.json の areas[{i}]: id は _ で始めず、町・霊場の ID と違うものにしてください")
      if "en" in a and (not isinstance(a["en"], str) or not a["en"]):
        err(f"map.json の areas[{i}]: en（英語名）は空でない文字列にしてください")
      if "stone" in a and (not isinstance(a["stone"], str) or not a["stone"]):
        err(f"map.json の areas[{i}]: stone（地方の原石名）は空でない文字列にしてください")
      if "stone_effect" in a and (not isinstance(a["stone_effect"], str) or not a["stone_effect"]):
        err(f"map.json の areas[{i}]: stone_effect（原石の性質）は空でない文字列にしてください")
      area_ids.add(a["id"])
  return area_ids if has_areas else None


def _check_open_and_starts(c: _Check) -> str | None:
  """map.json の open（開放した町）と starts（最初の町の候補）。開放した町を c.opened に決め、最初の町を返す。"""
  err, mp, places = c.err, c.mp, c.places
  c.opened = opened = open_towns(mp)
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
  return start


def _check_places(c: _Check, area_ids: set | None):
  """map.json の places（名前・地方・座標・口入れ屋の規模・店）。area_ids が None なら、地方は調べない。"""
  err, places, items = c.err, c.places, c.items
  for pid, p in places.items():
    where = f"map.json の {pid}"
    if p.get("type") not in PLACE_TYPES:
      err(f"{where}: type は {'/'.join(PLACE_TYPES)} のどれかにしてください")
    if not p.get("name"):
      err(f"{where}: name がありません")
    if area_ids is not None and (not isinstance(p.get("area"), str) or p["area"] not in area_ids):
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
      err(f"{where}: quests_per_day（口入れ屋の規模。高札の依頼の枠の数）は 1〜50 の整数にしてください")
    if "label" in p and p["label"] not in LABEL_SIDES:
      err(f"{where}: label（名前を置く向き）は {'/'.join(LABEL_SIDES)} のどれかにしてください")
    if p.get("type") == "town":
      if "quests_per_day" not in p:
        err(f"{where}: quests_per_day（口入れ屋の規模。高札の依頼の枠の数）を書いてください")
      if "shop" in p and not isinstance(p["shop"], list):
        err(f"{where}: shop は店で売る武具の ID の一覧にしてください")
      for iid in _rows(p.get("shop")):
        if iid not in items:
          err(f"{where}: 店の {iid!r} が items.json にありません")
        elif not isinstance(items[iid].get("price"), int) or items[iid]["price"] <= 0:
          err(f"{where}: 店の {iid!r} に price（正の整数）がありません")


def _check_layout(c: _Check):
  """map.json の layout（地図の大きさとベースの地図）。"""
  err, mp, places, raw = c.err, c.mp, c.places, c.raw
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


def _check_roads(c: _Check):
  """map.json の roads（種類・両端・距離・地形の割合・線）。"""
  err, places, monsters = c.err, c.places, c.monsters
  homes = {(m.get("area"), m.get("terrain")) for m in monsters.values() if on_road(m)}  # 主・大物は道に出ない
  for i, r in enumerate(c.roads):
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
    ends = sorted(str(places.get(r.get(e), {}).get("type") or "") for e in ("from", "to"))
    if kind == "trail" and ends != ["dungeon", "town"]:
      err(f"{where}: 霊場への道（trail）は町と霊場を結んでください")
    if kind != "trail" and ends != ["town", "town"]:
      err(f"{where}: 陸路・航路は町どうしを結んでください")
    if not isinstance(r.get("distance"), int) or r["distance"] < 1:
      err(f"{where}: distance（移動にかかる行動回数）は 1 以上の整数にしてください")
    terrain = r.get("terrain")
    if kind == "trail":
      pass
    elif not _weights_ok(terrain) or any(t not in TERRAINS for t, _ in terrain):
      err(f"{where}: terrain は [地形, 正の重み] の一覧にしてください（地形は {'/'.join(TERRAINS)}）")
    elif not {(places.get(r.get(e), {}).get("area"), t) for e in ("from", "to") for t, _ in terrain} & homes:
      err(f"{where}: 両端の町の地方に、terrain のどの地形にも住む道の物の怪がいません（主・大物以外。monsters.json）")
    path = r.get("path")
    if path is not None and (not isinstance(path, list) or len(path) < 2 or not all(_point_ok(p) for p in path)):
      err(f"{where}: path（地図に描く線）は [x, y] の一覧（2 点以上）にしてください")


def _check_connectivity(c: _Check, start: str | None):
  """最初の町から、開放した町すべてに陸路・航路で行けるか。"""
  err, places, opened = c.err, c.places, c.opened
  if start in places:
    adj: dict = {pid: set() for pid in places}
    for r in c.roads:
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


def _check_elements(c: _Check):
  """monsters.json の elements（属性と相性）。属性を c.elements に決める。"""
  err, raw = c.err, c.raw
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
  c.elements = elements


def _check_critical(c: _Check):
  """season.json の critical（会心の一撃）。"""
  err, season = c.err, c.season
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


def _check_growth(c: _Check):
  """season.json の growth（能力の伸び）。"""
  err, season = c.err, c.season
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


def _check_element_rates(c: _Check):
  """season.json の element_rates（属性の相性の倍率）。"""
  err, season = c.err, c.season
  rates = season.get("element_rates")
  if not isinstance(rates, dict) or any(
    not isinstance(rates.get(k), (int, float)) or isinstance(rates.get(k), bool) or rates[k] <= 0
    for k in ("strong", "weak")
  ):
    err("season.json: element_rates には strong と weak（正の数）を書いてください")


def _check_hero_traits(c: _Check):
  """season.json の hero_traits（もののふの特性）。"""
  err, season = c.err, c.season
  # もののふの特性（season.json の hero_traits）。growth は Lv1 からの伸びにかける倍率、luck は引き直し
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
    for key in ("strong_stat", "weak_stat"):  # 同じ点数の装備を選ぶときの得意・不得意（#244）
      if key in t and t[key] not in ("hp", "atk", "def", "spd", "dex"):
        err(f"{where}: {key} は hp/atk/def/spd/dex のどれかにしてください")


def _weights_dict_ok(table, keys) -> bool:
  """{名前: 重み}（名前は keys のどれか、重みは 0 以上、合計が正）か。"""
  return (
    isinstance(table, dict)
    and bool(table)
    and all(k in keys and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for k, v in table.items())
    and sum(table.values()) > 0
  )


def _rate_ok(value) -> bool:
  return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _check_personalities(c: _Check):
  """season.json の旅籠（inn）・溜める行動力（stored_days）・散策の表・性格（personalities）。"""
  err, season = c.err, c.season
  if not _weights_dict_ok(season.get("stroll_events"), STROLL_EVENTS):
    err(f"season.json: stroll_events は {'/'.join(STROLL_EVENTS)} の重み（0 以上、合計が正）にしてください")
  levels = season.get("stroll_strong_levels")
  if (
    not isinstance(levels, list)
    or len(levels) != 2
    or not all(isinstance(v, int) and not isinstance(v, bool) for v in levels)
    or levels[0] > levels[1]
  ):
    err("season.json: stroll_strong_levels は [下, 上]（もののふの Lv との差。整数で、下 ≦ 上）にしてください")
  inn = season.get("inn")
  price = inn.get("price") if isinstance(inn, dict) else None
  if not isinstance(price, int) or isinstance(price, bool) or price < 0:
    err('season.json: inn は {"price": 旅籠代（0 以上の整数）} にしてください')
  carry = season.get("carry")
  if (
    not isinstance(carry, dict)
    or any(k not in SLOTS or not isinstance(v, int) or isinstance(v, bool) or v < 1 for k, v in carry.items())
    or set(carry) != set(SLOTS)
  ):
    err(f"season.json: carry（部位ごとに持てる数）は {'/'.join(SLOTS)} それぞれに 1 以上の整数を書いてください")
  stay = season.get("stay_days_max")
  if not isinstance(stay, int) or isinstance(stay, bool) or stay < 0:
    err("season.json: stay_days_max（同じ町にいられる日数。0 なら旅に出さない）は 0 以上の整数にしてください")
  days = season.get("stored_days")
  if not isinstance(days, int) or isinstance(days, bool) or days < 0:
    err("season.json: stored_days（行動力を溜められる日数）は 0 以上の整数にしてください")
  table = season.get("personalities")
  if not isinstance(table, dict) or not table:
    err("season.json: personalities（もののふの性格）を 1 つ以上書いてください")
    return
  for pid, p in table.items():
    where = f"season.json の personalities の {pid}"
    if (
      not isinstance(p, dict)
      or not isinstance(p.get("name"), str)
      or not p["name"]
      or not isinstance(p.get("text"), str)
    ):
      err(f"{where}: name と text（文字列）を書いてください")
      continue
    for key in ("inn_below", "potion_below"):
      if not _rate_ok(p.get(key)):
        err(f"{where}: {key} は 0〜1 の数（HP の割合）にしてください")
    first = p.get("potions_first")
    if not isinstance(first, int) or isinstance(first, bool) or first < 0:
      err(f"{where}: potions_first（装備より先に買う丸薬の数）は 0 以上の整数にしてください")
    quest = p.get("quest")
    lv = quest.get("levels") if isinstance(quest, dict) else None
    if (
      not isinstance(lv, list)
      or len(lv) != 2
      or not all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in lv)
      or not isinstance(lv[1], int)
      or (lv[0] is not None and lv[0] > lv[1])
    ):
      err(f"{where}: quest.levels は [下, 上]（もののふの Lv との差。下は null で下限なし。下 ≦ 上）にしてください")
    order = quest.get("order") if isinstance(quest, dict) else None
    if not isinstance(order, list) or any(o not in PERSONALITY_ORDER for o in order):
      err(f"{where}: quest.order は {'/'.join(PERSONALITY_ORDER)} の一覧にしてください")
    if "gear" in p and p["gear"] not in ("grow", "cover", "balance"):  # 書かなければバランス
      err(f"{where}: gear（同じ点数の装備の選び方）は grow（伸ばす）・cover（補う）・balance のどれかにしてください")
    if p.get("spare") not in PERSONALITY_SPARE:
      err(f"{where}: spare（その日の残りの行動）は {'/'.join(PERSONALITY_SPARE)} のどれかにしてください")
    if "when_full" in p and p["when_full"] not in ("stroll", "move"):
      err(f"{where}: when_full（溜まりきっていたら）は stroll か move にしてください")
    if "stroll_hp" in p and not _rate_ok(p["stroll_hp"]):
      err(f"{where}: stroll_hp は 0〜1 の数（散策する HP の割合）にしてください")
    sk = p.get("skill")
    rarities = c.raw.get("rarities") if isinstance(c.raw.get("rarities"), list) else []
    if not isinstance(sk, dict):
      err(f"{where}: skill（スキルを付ける条件。from・margin・prefer）を書いてください")
    else:
      if sk.get("from") not in rarities or sk.get("from") == COMMON:
        err(f"{where}: skill.from は、スキルを付ける装備の珍しさの下限（common 以外）にしてください")
      margin = sk.get("margin")
      if not isinstance(margin, (int, float)) or isinstance(margin, bool) or margin < 1:
        err(f"{where}: skill.margin は 1 以上の数（余裕の倍率）にしてください")
      prefer = sk.get("prefer")
      slots_of = {
        k.get("id"): k.get("slot") for k in (c.raw.get("skills") or []) if isinstance(k, dict) and k.get("id")
      }
      if not isinstance(prefer, dict) or set(prefer) != set(SLOTS):
        err(f"{where}: skill.prefer は {'/'.join(SLOTS)} ごとの優先スキルの一覧にしてください")
      else:
        for slot, ids in prefer.items():
          if not isinstance(ids, list) or not ids or any(slots_of.get(i) != slot for i in ids):
            err(f"{where}: skill.prefer の {slot} は、その部位のスキルの ID の一覧（1 つ以上）にしてください")


SKILL_KINDS = ("counter", "attack", "guard", "heal", "travel", "money", "growth")


def _check_skills(c: _Check):
  """items.json の skills（装備のスキル。#233）・skill_kinds・skill_prices。"""
  err, raw = c.err, c.raw
  kinds = raw.get("skill_kinds")
  if (
    not isinstance(kinds, dict)
    or set(kinds) != set(SKILL_KINDS)
    or not all(isinstance(v, str) and v for v in kinds.values())
  ):
    err(f"items.json: skill_kinds は {'/'.join(SKILL_KINDS)} の名前（文字列）にしてください")
  prices = raw.get("skill_prices")
  rarities = raw.get("rarities") if isinstance(raw.get("rarities"), list) else []
  if not isinstance(prices, dict) or any(
    k not in rarities or k == COMMON or not isinstance(v, int) or isinstance(v, bool) or v < 0
    for k, v in prices.items()
  ):
    err("items.json: skill_prices は {珍しさ（common 以外）: 付ける代金（0 以上の整数）} にしてください")
  skills = raw.get("skills")
  if not isinstance(skills, list):
    err("items.json: skills はスキルの一覧にしてください")
    return
  seen = set()
  for k in skills:
    if not isinstance(k, dict) or not isinstance(k.get("id"), str):
      err("items.json の skills: id のないスキルがあります")
      continue
    where = f"items.json の skills の {k['id']}"
    if k["id"] in seen:
      err(f"{where}: id が重なっています")
    seen.add(k["id"])
    for key in ("name", "text", "logic"):
      if not isinstance(k.get(key), str) or not k[key]:
        err(f"{where}: {key}（文字列）を書いてください")
    if k.get("slot") not in SLOTS:
      err(f"{where}: slot は {'/'.join(SLOTS)} のどれかにしてください")
    if k.get("kind") not in SKILL_KINDS:
      err(f"{where}: kind は {'/'.join(SKILL_KINDS)} のどれかにしてください")
    if "value" in k and (not isinstance(k["value"], (int, float)) or isinstance(k["value"], bool)):
      err(f"{where}: value は数にしてください")
    if "value" not in k and "value" in str(k.get("logic", "")):  # しくみに value を使うスキルは、値がいる
      err(f"{where}: logic に value を使うので、value（数）を書いてください")
    counter = k.get("counter")
    if counter is not None and not (
      isinstance(counter, dict)
      and (counter.get("trait") in (raw.get("traits") or {}) or counter.get("element") is True)
    ):
      err(f'{where}: counter は {{"trait": 物の怪の性質}} か {{"element": true}} にしてください')


def _check_monster_reward(c: _Check):
  """season.json の monster_reward（物の怪の経験値とお金の計算）。"""
  err, season = c.err, c.season
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


def _check_monster_traits(c: _Check):
  """monsters.json の traits（物の怪の性質）。"""
  err, raw = c.err, c.raw
  traits = raw.get("traits") or {}
  if not isinstance(traits, dict):
    err("monsters.json の traits は {性質の ID: {name, ...}} にしてください")
    return
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


def _check_monsters(c: _Check, area_ids: set | None):
  """monsters.json の物の怪（能力・地形・地方・性質・属性・主・大物）。area_ids が None（map.json に areas がない）なら、
  地方は調べない（道の物の怪は、地方のない町どうしの道に、地方のない物の怪として出る）。"""
  err, places, monsters, opened, elements, raw = c.err, c.places, c.monsters, c.opened, c.elements, c.raw
  area_terrains: dict = {}  # 地方ごとの、町につながる陸路・航路の地形（開放していない道も入れる）
  for r in c.roads:
    if r.get("kind", "land") != "trail" and _weights_ok(r.get("terrain")):
      for e in ("from", "to"):
        area_terrains.setdefault(places.get(r.get(e), {}).get("area"), set()).update(t for t, _ in r["terrain"])
  for mid, m in monsters.items():
    if not m.get("name"):
      err(f"monsters.json の {mid}: name がありません")
    if m.get("terrain") not in TERRAINS:
      err(f"monsters.json の {mid}: terrain は {'/'.join(TERRAINS)} のどれかにしてください")
    if area_ids is not None and on_road(m) and m.get("area") not in area_ids:
      err(f"monsters.json の {mid}: area（道で出会う地方）は map.json の areas の ID にしてください")
    elif (
      on_road(m) and m.get("terrain") in TERRAINS and m.get("terrain") not in area_terrains.get(m.get("area"), set())
    ):
      err(
        f"monsters.json の {mid}: terrain の {m['terrain']!r} は、その地方（{m.get('area')}）の町につながる陸路・航路にない地形です"
        "（その地方にある地形の物の怪にしてください）"
      )
    elif not on_road(m) and "area" in m:
      err(f"monsters.json の {mid}: 主・大物には area を書かないでください（道に出ないため）")
    if "trait" in m and m["trait"] not in TRAITS:
      err(f"monsters.json の {mid}: trait は {'/'.join(TRAITS)} のどれかにしてください")
    for key, low in (("level", 1), ("hp", 1), ("atk", 0), ("def", 0), ("spd", 1), ("dex", 0)):
      if not isinstance(m.get(key), int) or isinstance(m.get(key), bool) or m[key] < low:
        err(f"monsters.json の {mid}: {key} は {low} 以上の整数にしてください")
    for key in ("exp", "gold"):
      if key in m:
        err(f"monsters.json の {mid}: {key} は書かないでください（能力から計算する。season.json の monster_reward）")
    if "boss" in m and (not isinstance(m["boss"], str) or places.get(m["boss"], {}).get("type") != "dungeon"):
      err(f"monsters.json の {mid}: boss は、その主がいる霊場の ID にしてください")
    if "elite" in m and (not isinstance(m["elite"], bool) or (m["elite"] and "boss" in m)):
      err(f"monsters.json の {mid}: elite（大物。道に出ず、討伐の依頼で狙う）は true か false。主には付けない")
    elif m.get("elite") and not any(
      q.get("type") == "hunt" and q.get("monster") == mid
      for town, qs in (raw["town_quests"] if isinstance(raw.get("town_quests"), dict) else {}).items()
      if town in opened and isinstance(qs, list)
      for q in qs
      if isinstance(q, dict)
    ):
      err(
        f"monsters.json の {mid}: 大物（elite）は、開放した町の討伐の依頼で狙えるようにしてください（道では出会わないため）"
      )
    if "text" in m and (not isinstance(m["text"], str) or not m["text"]):
      err(f"monsters.json の {mid}: text は空でない文字列にしてください")
    if "element" in m and (not isinstance(m["element"], str) or m["element"] not in elements):
      err(f"monsters.json の {mid}: element は {'/'.join(elements)} のどれかにしてください（属性なしなら書かない）")


def _check_items(c: _Check):
  """items.json の rarities と武具（スロット・能力・属性・町・値段）。"""
  err, places, items, rarities, opened, elements = c.err, c.places, c.items, c.rarities, c.opened, c.elements
  skills = _index(c.raw.get("skills"))
  # items.json
  if not rarities or len(set(rarities)) != len(rarities) or NONE in rarities or COMMON not in rarities:
    err(f"items.json: rarities は重複のない一覧にし、{COMMON} を入れてください（none は使えません）")
  for iid, it in items.items():
    where = f"items.json の {iid}"
    if iid in rarities:
      err(f"{where}: ID に珍しさの名前は使えません")
    if not it.get("name"):
      err(f"{where}: name がありません")
    if "text" in it and (not isinstance(it["text"], str) or not it["text"].strip() or len(it["text"]) > ITEM_TEXT_MAX):
      err(f"{where}: text（説明）は、空でない {ITEM_TEXT_MAX} 文字以内の文字列にしてください")
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
      err(f"{where}: element は武器と御守だけに、{'/'.join(elements)} のどれかで書いてください")
    if "fixed_skill" in it:
      fixed = it["fixed_skill"]
      if it.get("rarity") not in rarities[2:]:
        err(f"{where}: fixed_skill は ★★★ 以上の武具に書いてください")
      if not isinstance(fixed, str) or fixed not in skills or skills[fixed].get("slot") != it.get("slot"):
        err(f"{where}: fixed_skill は武具の部位に合うスキルの ID にしてください")
      elif not any(sid != fixed and sk.get("slot") == it.get("slot") for sid, sk in skills.items()):
        err(f"{where}: fixed_skill と異なる同じ部位の追加スキルを1つ以上用意してください")
    if "drop_level" in it:
      err(f"{where}: drop_level はもう使いません（出やすさはドロップ表の重みで決める）")
    towns = it.get("towns")
    forge_town = it.get("forge_town")
    if forge_town is not None:
      if it.get("rarity") != "legendary" or not isinstance(forge_town, str):
        err(f"{where}: forge_town は legendary の鍛造場所にしてください")
      elif places.get(forge_town, {}).get("type") != "town" or forge_town not in opened:
        err(f"{where}: forge_town は開放した町の ID にしてください")
    if it.get("rarity") == COMMON:
      if towns is not None:
        err(f"{where}: common に towns は書けません（common は店で売る）")
    elif it.get("rarity") in rarities:
      craft_only = it.get("rarity") == "legendary" and isinstance(forge_town, str)
      if towns is None:
        if not craft_only:
          err(f"{where}: レアには towns（宝箱や依頼で出る町の ID の一覧）を書いてください")
      elif not isinstance(towns, list):
        err(f"{where}: towns（武具を入手できる町の ID の一覧）は配列にしてください")
      elif not towns and not craft_only:
        err(f"{where}: レアには towns（宝箱や依頼で出る町の ID の一覧）を書いてください")
      elif isinstance(towns, list):
        for tid in towns:
          if not isinstance(tid, str) or places.get(tid, {}).get("type") != "town":
            err(f"{where}: towns の {tid!r} は町の ID にしてください")
          elif tid not in opened:
            err(f"{where}: towns の {tid!r} は開放した町（open）にしてください（ほかの町の依頼は出ないため）")
    if "price" in it and (not isinstance(it["price"], int) or it["price"] <= 0):
      err(f"{where}: price は正の整数にしてください")

  forge_areas: dict[str, list[str]] = {}
  for tid, town in places.items():
    if not town.get("legendary_forge"):
      continue
    area = town.get("area")
    if isinstance(area, str):
      forge_areas.setdefault(area, []).append(tid)
    crafted = [it for it in items.values() if it.get("forge_town") == tid]
    slots = [it.get("slot") for it in crafted]
    if len(crafted) != 4 or any(slot not in SLOTS for slot in slots) or set(slots) != set(SLOTS):
      err(f"map.json の {tid}: legendary_forge の町には、4 部位の legendary 武具を1つずつ用意してください")
  for area, towns in forge_areas.items():
    if len(towns) > 1:
      err(f"map.json の {area}: 地方に legendary_forge は1か所だけにしてください")
  for iid, it in items.items():
    forge_town = it.get("forge_town")
    if isinstance(forge_town, str) and forge_town and not places.get(forge_town, {}).get("legendary_forge"):
      err(f"items.json の {iid}: forge_town の町に legendary_forge がありません")


def _check_lowest_rare(c: _Check):
  """開放したどの町にも、いちばん下のレアの報酬がある（上のレアがない町で 1 つ下に落とすため）。"""
  err, items, rarities, opened = c.err, c.items, c.rarities, c.opened
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


def _check_quest_ranks(c: _Check) -> list:
  """quests.json の ranks（依頼のレアリティ）。ranks を返す。"""
  err, raw = c.err, c.raw
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
    c.table_ok(f"{where} の item", r.get("item"))
  return ranks


def _check_quest_reward(c: _Check):
  """quests.json の reward（1 行動あたりの報酬）。"""
  err, raw = c.err, c.raw
  base = raw.get("quest_reward")  # 1 行動あたりの報酬（× 必要な行動力 × レアリティの倍率）
  if not isinstance(base, dict) or any(
    not isinstance(base.get(k), (int, float)) or isinstance(base.get(k), bool) or base[k] < 0 for k in ("exp", "gold")
  ):
    err("quests.json: reward には、1 行動あたりの exp と gold（0 以上の数）を書いてください")
  elif "deliver_rate" in base and (not _num(base["deliver_rate"]) or base["deliver_rate"] <= 0):
    err("quests.json: reward の deliver_rate（配達のお金の倍率）は正の数にしてください")
  if isinstance(base, dict):
    party_gold = base.get("party_gold")
    if (
      not isinstance(party_gold, dict)
      or set(party_gold) != {"elite", "boss"}
      or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in party_gold.values())
    ):
      err(
        "quests.json: reward の party_gold には、大物（elite）・主（boss）の達成報酬のお金（0 以上の整数）を書いてください"
      )


def _check_hunt_ranks(c: _Check, ranks: list):
  """quests.json の hunt_ranks（共通の討伐の ★ の区切り）。"""
  err, raw = c.err, c.raw
  # 共通の討伐の ★: 点数 = 物の怪の Lv ×（数 + 1）が hunt_ranks[0] 以下なら ★、hunt_ranks[1] 以下なら ★★ …
  hunt_ranks = raw.get("hunt_ranks")
  if (
    not isinstance(hunt_ranks, list)
    or not all(_num(v) and v > 0 for v in hunt_ranks)
    or hunt_ranks != sorted(hunt_ranks)
    or len(hunt_ranks) >= max(len(ranks), 1)
  ):
    err(f"quests.json: hunt_ranks は小さい順の正の数の一覧（{max(len(ranks) - 1, 0)} 個まで）にしてください")


def _check_quest_templates(c: _Check):
  """quests.json の共通の依頼（templates）。"""
  err, raw = c.err, c.raw
  for q in raw["quests"]:
    where = f"quests.json の {q.get('id')}"
    if q.get("type") not in QUEST_TYPES:
      err(f"{where}: type は {'/'.join(QUEST_TYPES)} のどれかにしてください")
    if "rank" in q:  # 共通の依頼の ★ は計算で決める（討伐は物の怪の Lv と数、配達は距離と危なさ）
      err(f"{where}: rank は書かないでください（★ は、討伐は物の怪の Lv と数、配達は距離と危なさで決まる）")
    if not isinstance(q.get("weight"), (int, float)) or q["weight"] <= 0:
      err(f"{where}: weight は正の数にしてください")
    if q.get("type") == "hunt" and not _range_ok(q.get("count")):
      err(f"{where}: count は [最小, 最大]（1 以上）にしてください")
  if not raw["quests"]:
    err("quests.json: 共通の依頼（templates）を 1 つ以上書いてください（高札の空いた枠に入る）")


def _check_deliveries(c: _Check, ranks: list):
  """quests.json の deliveries（配達の ★・危なさ・荷物）。"""
  err, places, raw = c.err, c.places, c.raw
  # 配達（quests.json の deliveries）: ★ の区切り、危なさごとの物の怪の Lv のずらし・受けられる Lv・説明の文、町ごとの荷物
  if any(q.get("type") == "deliver" for q in raw["quests"]):
    deliveries_ok(raw.get("deliveries"), len(ranks), places, err)


def _check_hunt_texts(c: _Check):
  """quests.json の hunts（討伐の説明の文）。"""
  err, places, raw = c.err, c.places, c.raw
  # 討伐の説明（quests.json の hunts）: 町ごとの困りごとと、★ ごとの頼み方
  hs = raw.get("hunts")
  if hs is not None:
    hp, ht = (hs.get("places"), hs.get("texts")) if isinstance(hs, dict) else (None, None)
    if not isinstance(hp, dict) or not all(
      isinstance(v, list) and all(isinstance(x, str) and x for x in v) for v in hp.values()
    ):
      err("quests.json: hunts の places は {町の ID か _any: [困りごと, ...]} にしてください")
    else:
      for key in hp:
        if key != "_any" and key not in places:
          err(f"quests.json: hunts の places の {key!r} は map.json にない場所です")
    if not isinstance(ht, list) or not all(
      isinstance(x, list)
      and x
      and all(
        isinstance(t, str) and "{monster}" in t and "{place}" in t and format_ok(t, place="x", monster="x", count=1)
        for t in x
      )
      for x in ht
    ):
      err(
        "quests.json: hunts の texts は ★ ごとに、{place} と {monster} を入れた文の一覧にしてください"
        "（ほかに使えるのは {count} だけ）"
      )


def _check_town_quests(c: _Check, ranks: list):
  """quests.json の towns（町の依頼）。"""
  err, places, monsters, opened, raw = c.err, c.places, c.monsters, c.opened, c.raw
  # 町の依頼: 討伐は町から出る道（陸路・航路）と、その道に出る物の怪。配達は別の町へ
  road_foes = {}
  for r in c.roads:
    if r.get("kind", "land") != "trail" and road_open(r, opened, places):
      foes = {mid for mid, _ in road_monsters(r, places, list(monsters.values()))}
      road_foes[(r.get("from"), r.get("to"))] = road_foes[(r.get("to"), r.get("from"))] = foes
  town_quests = raw.get("town_quests") or {}
  if not isinstance(town_quests, dict):
    err("quests.json: towns は {町の ID: 依頼の一覧} にしてください")
    town_quests = {}
  seen_ids = [q.get("id") for q in raw["quests"]]
  party_towns = set()
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
      _rank_ok(err, ranks, where, q.get("rank"))
      to = q.get("to")
      if q.get("type") == "hunt":
        target = monsters.get(q.get("monster"), {})
        if target.get("elite"):
          party_towns.add(tid)
        if (tid, to) not in road_foes:
          err(f"{where}: to の {to!r} は、{tid} から陸路・航路で直接つながる開放した町にしてください")
        elif q.get("monster") not in road_foes[(tid, to)] and not target.get("elite"):
          err(f"{where}: monster の {q.get('monster')!r} は、その道に出る物の怪か大物にしてください（主は狙えない）")
        if not isinstance(q.get("count"), int) or q["count"] < 1:
          err(f"{where}: count は 1 以上の整数にしてください")
      elif q.get("type") == "deliver":
        if to == tid or to not in opened:
          err(f"{where}: to は開放した別の町の ID にしてください")
      else:
        err(f"{where}: type は {'/'.join(QUEST_TYPES)} のどれかにしてください")

  if c.season.get("rare_drops", True):
    trails = [r for r in c.roads if r.get("kind") == "trail" and road_open(r, opened, places)]
    boss_towns = {boss_town(trails, opened, m["boss"]) for m in monsters.values() if isinstance(m.get("boss"), str)}
    for town in sorted(party_towns | (boss_towns - {None})):
      if not party_gear(c.items, town):
        err(f"items.json: {town} の大物・主の討伐で出る ★★★ 武具（epic）がありません")


def _check_item_points(c: _Check):
  """season.json の item_points と、武具の点数が珍しさの範囲に入っているか。"""
  err, errors, season, items, rarities = c.err, c.errors, c.season, c.items, c.rarities
  # 強さ: 武具の点数（season.json の item_points）が、0 点から珍しさごとの上限（max）までに入る
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


def _run(c: _Check, check, *args):
  """検査を 1 つ動かす。形の検査で拾えなかった想定外のデータで落ちても、スタックトレースにせず、
  どの検査かを添えた誤りとして知らせる（そのあとの検査は続ける）。落ちたときは None を返す。"""
  try:
    return check(c, *args)
  except Exception as e:  # noqa: BLE001
    label = (check.__doc__ or check.__name__).split("。")[0]
    c.err(f"{label}: データの形が想定と違うため、調べきれませんでした（{type(e).__name__}: {e}）")
    return None


def validate(raw: dict) -> list[str]:
  """データの誤りの一覧（空なら問題なし）。データがどんな形でも、例外ではなく誤りの一覧を返す。"""
  c = _Check(raw)
  _check_shape(c)
  if c.errors:  # 形が違うと、ほかの検査は前提が崩れる。先に形を直してもらう
    return c.errors
  _run(c, _check_ids)
  _run(c, _check_season)
  area_ids = _run(c, _check_areas)
  start = _run(c, _check_open_and_starts)
  _run(c, _check_places, area_ids)
  _run(c, _check_layout)
  _run(c, _check_roads)
  _run(c, _check_connectivity, start)
  _run(c, _check_elements)
  _run(c, _check_critical)
  _run(c, _check_growth)
  _run(c, _check_element_rates)
  _run(c, _check_hero_traits)
  _run(c, _check_personalities)
  _run(c, _check_skills)
  _run(c, _check_monster_reward)
  _run(c, _check_monster_traits)
  _run(c, _check_monsters, area_ids)
  _run(c, _check_items)
  _run(c, _check_lowest_rare)
  ranks = _run(c, _check_quest_ranks) or []
  _run(c, _check_quest_reward)
  _run(c, _check_hunt_ranks, ranks)
  _run(c, _check_quest_templates)
  _run(c, _check_deliveries, ranks)
  _run(c, _check_hunt_texts)
  _run(c, _check_town_quests, ranks)
  _run(c, _check_item_points)
  return c.errors


def item_points(item: dict, cfg: dict) -> float:
  """武具の点数（能力の増減 × season.json の item_points.weights の合計。マイナスの能力も同じ重みで引く）。
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


def road_monsters(road: dict, places: dict, monsters: list[dict]) -> list[tuple[str, float]]:
  """道に出る物の怪と重み。両端の町の地方（#265）の物の怪のうち、道の地形に住む物の怪（#267）。
  地方をまたぐ道は、どちらの地方も半分ずつ（道の地形に住む物の怪のいない地方は入れない）。地方の中は、道の地形の割合を
  （その地方の物の怪が住む地形だけで分け直して）、その地形の物の怪の数で分ける。terrain の形が崩れていれば空。"""
  if not _weights_ok(road.get("terrain")):
    return []
  homes: dict = {}  # (地方, 地形) → 物の怪（主・大物は道には出ない）
  for m in monsters:
    if on_road(m):
      homes.setdefault((m.get("area"), m.get("terrain")), []).append(m["id"])
  areas = [
    a
    for a in dict.fromkeys(places.get(road.get(e), {}).get("area") for e in ("from", "to"))
    if any((a, t) in homes for t, _ in road["terrain"])
  ]
  pool = []
  for area in areas:
    here = [(t, w) for t, w in road["terrain"] if (area, t) in homes]
    total = sum(w for _, w in here)
    for t, w in here:
      pool += [(mid, w / total / len(areas) / len(homes[(area, t)])) for mid in homes[(area, t)]]
  return pool


def on_road(monster: dict) -> bool:
  """道で出会う物の怪か（主と大物は道に出ない。大物は町の討伐の依頼で狙って戦う）。"""
  return not monster.get("boss") and not monster.get("elite")


def format_ok(text: str, **values) -> bool:
  """説明の文に、values のほかの {…} がなく、{ } の書き方が正しいか（依頼を作るときに落ちないように）。"""
  try:
    text.format(**values)
  except (KeyError, IndexError, ValueError):
    return False
  return True


def deliveries_ok(dv, n_ranks: int, places: dict, err) -> None:
  """quests.json の deliveries を確かめる（誤りは err に渡す）。"""
  if not isinstance(dv, dict):
    err("quests.json: deliveries（配達の ★・危なさ・荷物）を書いてください")
    return
  dr = dv.get("ranks")
  if (
    not isinstance(dr, list) or not all(_num(v) and v > 0 for v in dr) or dr != sorted(dr) or len(dr) >= max(n_ranks, 1)
  ):
    err(f"quests.json: deliveries の ranks は小さい順の正の数の一覧（{max(n_ranks - 1, 0)} 個まで）にしてください")
  dangers = dv.get("dangers")
  if not isinstance(dangers, list) or not dangers:
    err("quests.json: deliveries の dangers（危なさの段階）を 1 つ以上書いてください")
    dangers = []
  for n, dg in enumerate(dangers):
    where = f"quests.json の deliveries の dangers[{n}]"
    ints = isinstance(dg, dict) and all(
      isinstance(dg.get(k), int) and not isinstance(dg.get(k), bool) for k in ("shift", "min_level")
    )
    if not ints or dg["min_level"] < 0:
      err(f"{where}: shift（物の怪の Lv のずらし。整数）と min_level（受けられる Lv。0 以上の整数）を書いてください")
    texts = dg.get("texts") if isinstance(dg, dict) else None
    if (
      not isinstance(texts, list)
      or not texts
      or not all(
        isinstance(t, str) and "{cargo}" in t and "{to}" in t and format_ok(t, cargo="x", to="x") for t in texts
      )
    ):
      err(f"{where}: texts は {{cargo}} と {{to}} を入れた文の一覧にしてください（ほかの {{…}} は使えない）")
  cargo = dv.get("cargo")
  if not isinstance(cargo, dict) or "_any" not in cargo:
    err("quests.json: deliveries の cargo に、どの町でも使う荷物（_any）を書いてください")
    return
  for key, lists in cargo.items():
    where = f"quests.json の deliveries の cargo の {key}"
    if key != "_any" and key not in places:
      err(f"{where}: {key!r} は map.json にない場所です")
    if (
      not isinstance(lists, list)
      or len(lists) != len(dangers)
      or not all(isinstance(x, list) and all(isinstance(c, str) and c for c in x) for x in lists)
    ):
      err(f"{where}: 危なさの段階ごと（{len(dangers)} 個）に、荷物の名前の一覧を書いてください")
    elif key == "_any" and not all(lists):
      err(f"{where}: どの危なさにも、荷物を 1 つ以上書いてください")


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
  """物の怪の強さ点: 戦いで効く値（性質の効果のあと。攻撃は 1 回の番の合計）に、性質の score を足して、重みをかけて足す。"""
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
  towns = [pid for pid, p in places.items() if p["type"] == "town" and pid in opened]  # もののふが行ける町だけ
  order = {pid: i for i, pid in enumerate(places)}
  roads, info, trails = {}, {}, []
  for r in mp["roads"]:
    if not road_open(r, opened, places):  # 開放していない町への道は使わず、地図にも描かない
      continue
    kind = r.get("kind", "land")
    if kind == "trail":
      trails.append(r)
      continue
    pool = road_monsters(r, places, raw["monsters"])
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
    "skills": {k["id"]: k for k in raw.get("skills") or []},
    "skill_kinds": dict(raw.get("skill_kinds") or {}),
    "skill_prices": dict(raw.get("skill_prices") or {}),
    # 開放した地域の common（開放した町の店で売っている品）
    "region_items": sorted({iid for t in towns for iid in places[t].get("shop", [])}),
    "quests": raw["quests"],
    "quest_ranks": raw["quest_ranks"],
    "quest_reward": raw["quest_reward"],
    "hunt_ranks": raw.get("hunt_ranks", []),
    "deliveries": raw.get("deliveries"),
    "hunts": raw.get("hunts"),
    "town_quests": raw.get("town_quests", {}),
  }
