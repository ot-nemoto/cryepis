"""シーズン制 RPG の世界（world/ の下のデータファイル）を読み込み、検証する。

world/
  season.json   シーズンの長さ・初期ステータス・レベルアップ・1 日の行動回数（固定）など
  map.json      場所（町・ダンジョン）と道（陸路・航路・ダンジョンへの道）
  map-base.svg  ベースの地図（地形の絵。map.json の layout.background）
  monsters.json 敵（出る地形と性質）と、性質の一覧
  items.json    アイテム（珍しさの一覧を含む）
  quests.json   ギルドの依頼のひな形

参照はすべて ID で行う。データに誤りがあれば validate() がその一覧を返し、load_world() は止まる。
"""
from __future__ import annotations

import base64
import heapq
import json
from datetime import date
from pathlib import Path

import common

WORLD_DIR = common.ROOT / "world"
SLOTS = ("weapon", "helmet", "armor", "shield", "accessory")
SLOT_NAMES = {"weapon": "武器", "helmet": "兜", "armor": "鎧", "shield": "盾", "accessory": "装飾品"}
PLACE_TYPES = ("town", "dungeon")
# 道の種類: 陸路・航路は町どうしを結び、勇者が通る。ダンジョンへの道は地図に描くだけ（第 2 段階で使う）
ROAD_KINDS = ("land", "sea", "trail")
LABEL_SIDES = ("up", "down", "left", "right")  # 地図で場所の名前を置く向き
TERRAINS = ("plain", "forest", "jungle", "desert", "wetland", "snow", "mountain", "volcano", "sea")
TRAITS = ("first_strike", "poison", "swarm", "fierce", "armored", "thief")
QUEST_TYPES = ("hunt", "deliver")
ROAD_EVENTS = ("battle", "treasure", "nothing")
STATS = ("atk", "def", "hp")
NONE = "none"  # ドロップ表の「何も出ない」
# ベースの地図（layout.background）に使える形式と、大きさの上限
BACKGROUND_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp"}
BACKGROUND_MAX_BYTES = 1024 * 1024
BIG = 10 ** 9
# season.json の必須項目: 名前 → (型, 最小, 最大)
SEASON_KEYS = {
    "months": (int, 1, 120), "actions_per_day": (int, 1, 50), "rest_heal_rate": (float, 0, 1), "retreat_hp_rate": (float, 0, 1),
    "board_size": (int, 1, 50), "deliver_range": (int, 1, BIG),
    "hunt_battle_boost": (float, 0.01, BIG), "balance_level": (int, 1, BIG),
    "item_power_rate": (float, 0.01, BIG),
}
HERO_KEYS = {"max_hp": (int, 1, BIG), "atk": (int, 0, BIG), "def": (int, 0, BIG), "gold": (int, 0, BIG),
             "potions": (int, 0, BIG)}
LEVEL_UP_KEYS = {"hp": (int, 0, BIG), "atk": (int, 0, BIG), "def": (int, 0, BIG), "exp_base": (float, 0.01, BIG),
                 "exp_power": (float, 0.01, 10)}
POTION_KEYS = {"price": (int, 1, BIG), "heal_rate": (float, 0.01, 1), "keep": (int, 0, BIG)}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{path} がありません")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} が JSON として読めません: {e}")


def load_raw(directory: Path | None = None) -> dict:
    d = directory or WORLD_DIR
    items = read_json(d / "items.json")
    mp = read_json(d / "map.json")
    monsters = read_json(d / "monsters.json")
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
        "rarities": items["rarities"],
        "items": items["items"],
        "quests": read_json(d / "quests.json")["templates"],
    }


def load_world(directory: Path | None = None) -> dict:
    raw = load_raw(directory)
    errors = validate(raw)
    if errors:
        raise SystemExit("world/ のデータに誤りがあります:\n" + "\n".join(f"  - {e}" for e in errors))
    return build(raw)


# ---- 検証 ---------------------------------------------------------------------------
def _range_ok(value) -> bool:
    return (isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value)
            and 1 <= value[0] <= value[1])


def _point_ok(value) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def _weights_ok(pairs) -> bool:
    return (isinstance(pairs, list) and pairs
            and all(isinstance(p, list) and len(p) == 2 and isinstance(p[1], (int, float)) and p[1] > 0 for p in pairs))


def stat_at(season: dict, stat: str, level: int) -> int:
    """レベル level の基本能力値（装備なし）。"""
    base = {"atk": "atk", "def": "def", "hp": "max_hp"}[stat]
    return season["hero"][base] + season["level_up"][stat] * (level - 1)


def validate(raw: dict) -> list[str]:
    """データの誤りの一覧（空なら問題なし）。"""
    errors: list[str] = []
    err = errors.append
    season, mp = raw["season"], raw["map"]
    places = {p.get("id"): p for p in mp.get("places", [])}
    monsters = {m.get("id"): m for m in raw["monsters"]}
    items = {i.get("id"): i for i in raw["items"]}
    rarities = raw["rarities"]

    for label, rows in (("map.json の places", mp.get("places", [])), ("monsters.json", raw["monsters"]),
                        ("items.json", raw["items"]), ("quests.json", raw["quests"])):
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
    for where, obj, spec in (("season.json", season, SEASON_KEYS),
                             ("season.json の hero", season.get("hero"), HERO_KEYS),
                             ("season.json の level_up", season.get("level_up"), LEVEL_UP_KEYS),
                             ("season.json の potion", season.get("potion"), POTION_KEYS)):
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
    events = season.get("road_events")
    if (not isinstance(events, dict) or not events
            or any(k not in ROAD_EVENTS or not isinstance(v, (int, float)) or v < 0 for k, v in events.items())
            or sum(events.values()) <= 0):
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
    starts = mp.get("starts")
    if not isinstance(starts, list) or not starts:
        err("map.json: starts（最初の町の候補）は町の ID の一覧（1 つ以上）にしてください")
        starts = []
    for sid in starts:
        if places.get(sid, {}).get("type") != "town":
            err(f"map.json: starts の {sid!r} は町の ID にしてください")
    start = starts[0] if starts else None
    for pid, p in places.items():
        where = f"map.json の {pid}"
        if p.get("type") not in PLACE_TYPES:
            err(f"{where}: type は {'/'.join(PLACE_TYPES)} のどれかにしてください")
        if not p.get("name"):
            err(f"{where}: name がありません")
        for key in ("alias", "region"):
            if key in p and (not isinstance(p[key], str) or not p[key]):
                err(f"{where}: {key} は空でない文字列にしてください")
        if "pos" in p and not _point_ok(p["pos"]):
            err(f"{where}: pos（地図の座標）は [x, y] の数にしてください")
        if "label" in p and p["label"] not in LABEL_SIDES:
            err(f"{where}: label（名前を置く向き）は {'/'.join(LABEL_SIDES)} のどれかにしてください")
        if p.get("type") == "town":
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
                    err(f"map.json: world/{name} が {len(bg['bytes']) // 1024}KB あります"
                        f"（{BACKGROUND_MAX_BYTES // 1024}KB 以下にしてください。目安は 300KB 以下）")
    monster_terrains = {m.get("terrain") for m in monsters.values()}
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
            err(f"{where}: terrain のどの地形にも、monsters.json の魔物がいません")
        path = r.get("path")
        if path is not None and (not isinstance(path, list) or len(path) < 2 or not all(_point_ok(p) for p in path)):
            err(f"{where}: path（地図に描く線）は [x, y] の一覧（2 点以上）にしてください")
    if start in places:
        adj: dict = {pid: set() for pid in places}
        for r in mp.get("roads", []):
            if r.get("kind", "land") != "trail" and r.get("from") in places and r.get("to") in places:
                adj[r["from"]].add(r["to"])
                adj[r["to"]].add(r["from"])
        seen, todo = {start}, [start]
        while todo:
            for nxt in adj[todo.pop()] - seen:
                seen.add(nxt)
                todo.append(nxt)
        for pid, p in places.items():
            if p.get("type") == "town" and pid not in seen:
                err(f"map.json: {pid} に最初の町から行けません（陸路・航路がつながっていません）")

    # monsters.json
    traits = raw.get("traits", {})
    for key in TRAITS:
        if not isinstance(traits.get(key), dict) or not traits[key].get("name"):
            err(f"monsters.json: traits に {key}（name のある項目）を書いてください")
    for mid, m in monsters.items():
        if not m.get("name"):
            err(f"monsters.json の {mid}: name がありません")
        if m.get("terrain") not in TERRAINS:
            err(f"monsters.json の {mid}: terrain は {'/'.join(TERRAINS)} のどれかにしてください")
        if "trait" in m and m["trait"] not in TRAITS:
            err(f"monsters.json の {mid}: trait は {'/'.join(TRAITS)} のどれかにしてください")
        for key, low in (("level", 1), ("hp", 1), ("atk", 0), ("def", 0), ("exp", 0), ("gold", 0)):
            if not isinstance(m.get(key), int) or m[key] < low:
                err(f"monsters.json の {mid}: {key} は {low} 以上の整数にしてください")

    # items.json
    if not rarities or len(set(rarities)) != len(rarities) or NONE in rarities:
        err("items.json: rarities は重複のない一覧にしてください（none は使えません）")
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
        if "drop_level" in it:
            err(f"{where}: drop_level はもう使いません（出やすさはドロップ表の重みで決める）")
        if "price" in it and (not isinstance(it["price"], int) or it["price"] <= 0):
            err(f"{where}: price は正の整数にしてください")

    # quests.json
    for q in raw["quests"]:
        where = f"quests.json の {q.get('id')}"
        if q.get("type") not in QUEST_TYPES:
            err(f"{where}: type は {'/'.join(QUEST_TYPES)} のどれかにしてください")
        if not isinstance(q.get("weight"), (int, float)) or q["weight"] <= 0:
            err(f"{where}: weight は正の数にしてください")
        if not isinstance(q.get("days"), int) or q["days"] < 1:
            err(f"{where}: days は 1 以上の整数にしてください")
        reward = q.get("reward", {})
        for key in ("exp", "gold"):
            if not isinstance(reward.get(key), (int, float)) or reward[key] < 0:
                err(f"{where}: reward.{key} は 0 以上の数にしてください")
        if "item" in reward:
            table_ok(f"{where} の reward.item", reward["item"])
        if q.get("type") == "hunt" and not _range_ok(q.get("count")):
            err(f"{where}: count は [最小, 最大]（1 以上）にしてください")

    # 釣り合い: 部位ごとに最も強い装備を足しても、典型的なレベルの基本能力値の一定割合を超えない
    if not errors:
        level, rate = season["balance_level"], season["item_power_rate"]
        for stat in STATS:
            best = sum(max((it.get(stat, 0) for it in items.values() if it["slot"] == slot), default=0)
                       for slot in SLOTS)
            limit = stat_at(season, stat, level) * rate
            if best > limit:
                err(f"items.json: 部位ごとの最強装備の {stat} の合計 {best} が、Lv.{level} の基本値の "
                    f"{rate:.0%}（{limit:.1f}）を超えています（アイテムが強すぎます）")
    return errors


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
        here = [m["id"] for m in monsters if m["terrain"] == t]
        pool += [(mid, weight / len(here)) for mid in here]
    return pool


def build(raw: dict) -> dict:
    mp = raw["map"]
    places = {p["id"]: p for p in mp["places"]}
    towns = [pid for pid, p in places.items() if p["type"] == "town"]
    order = {pid: i for i, pid in enumerate(places)}
    roads, info, trails = {}, {}, []
    for r in mp["roads"]:
        kind = r.get("kind", "land")
        if kind == "trail":
            trails.append(r)
            continue
        pool = road_monsters(r["terrain"], raw["monsters"])
        for a, b in ((r["from"], r["to"]), (r["to"], r["from"])):
            roads[(a, b)] = r["distance"]
            path = r.get("path")
            info[(a, b)] = {"kind": kind, "terrain": [t for t, _ in r["terrain"]], "monsters": pool,
                            "path": path if a == r["from"] or not path else path[::-1]}
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
        "places": places,
        "towns": towns,
        "dungeons": [pid for pid, p in places.items() if p["type"] == "dungeon"],
        "roads": roads,
        "road_info": info,
        "trails": trails,
        "neighbors": {t: sorted((b for (a, b) in roads if a == t), key=lambda b: order[b]) for t in towns},
        "dist": dist,
        "next": nxt,
        "monsters": {m["id"]: m for m in raw["monsters"]},
        "traits": raw.get("traits", {}),
        "rarities": raw["rarities"],
        "items": {i["id"]: i for i in raw["items"]},
        "quests": raw["quests"],
    }
