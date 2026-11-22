"""lore/ の設定（町・ダンジョン・道・航路）から world/map.json を作り、ベースの地図を置く。

使い方: python build_world.py（先に make.py の手前の手順で .cache/ を作っておく）
"""

import json
import math
import shutil

import paths
from lore import AREAS, DUNGEON_EN, TEXT
from names import NAMES
from places import DUNGEONS, LABEL, TOWNS

STEP = 2
kind = paths.kind()

# 地形のマス → 魔物の地形（道の地形の割合に使う）
TERRAIN = {
  "plain": "plain",
  "steppe": "plain",
  "savanna": "plain",
  "beach": "plain",
  "river": "plain",
  "forest": "forest",
  "taiga": "forest",
  "jungle": "jungle",
  "desert": "desert",
  "badland": "desert",
  "wetland": "wetland",
  "marshwater": "wetland",
  "tundra": "snow",
  "snowcap": "snow",
  "ice": "snow",
  "mount": "mountain",
  "hill": "mountain",
  "volcano": "volcano",
  "lava": "volcano",
}

# 呼び名 → (ID, 絵文字, 地域)
# 店の品ぞろえは、どの町も同じ（町ごとの特産はない。町の違いは、その町でしか受けられない依頼）
SHOP = [
  "iron_sword",
  "iron_axe",
  "wooden_helm",
  "iron_helm",
  "wooden_shield",
  "iron_shield",
  "soaring_leaf_charm",
  "silkvine_charm",
  "lifeblood_charm",
]
TOWN_INFO = {
  "王都": ("aurea", "🏰", "黄金平原"),
  "河港": ("flumen", "⚓", "大河の三角州"),
  "天門の町": ("caelum", "🗻", "竜骨山脈・天の頂のふもと"),
  "古木の町": ("silwen", "🌳", "古の大森林"),
  "北の漁村": ("vike", "🐟", "中央大陸の北の海岸"),
  "霧沼の村": ("morna", "🛖", "霧の大湿原"),
  "鋼山の町": ("durgan", "⚒️", "竜骨山脈の西のふもと"),
  "椰子の港": ("cocora", "🌴", "熱帯の半島"),
  "風巻きの宿": ("kazal", "🏕️", "風の草原"),
  "湖畔の村": ("linde", "🛶", "湖沼地帯"),
  "氷牙の砦": ("isval", "🧊", "氷牙山地"),
  "凍土の集落": ("yarka", "❄️", "中央大陸の北の凍土"),
  "オアシスの町": ("zafra", "🏺", "灼熱の大砂漠"),
  "暁の港": ("fajil", "🌅", "東の草原の海岸"),
  "雨林の村": ("yashum", "🌿", "東の雨林"),
  "霧氷の修道院": ("astol", "🔭", "霧氷の半島"),
  "蔦の集落": ("urka", "🍃", "西の密林"),
  "赤岩の鉱山町": ("rubela", "💎", "赤い荒野"),
  "西の港": ("novaris", "⛵", "西大陸の東の海岸"),
  "南の森の村": ("rowen", "🏹", "南の森"),
  "干潟の港": ("salina", "🐚", "南の干潟"),
  "大草原の町": ("eques", "🐎", "南の大草原"),
  "南壁の砦": ("muralis", "🛡️", "南壁山脈"),
  "焔の湯治場": ("homura", "♨️", "炎の列島"),
  "凍原の村": ("karun", "🦌", "南の凍原"),
}
# ギルドの規模（掲示板の依頼の枠の数）。どの町にも書く（王都 6、港や大きな町 5、ふつうの町 4、小さな村や砦 3）
QUESTS_PER_DAY = {
  "王都": 6,
  "河港": 5,
  "暁の港": 5,
  "干潟の港": 5,
  "西の港": 5,
  "椰子の港": 5,
  "オアシスの町": 5,
  "天門の町": 5,
  "古木の町": 4,
  "北の漁村": 4,
  "鋼山の町": 4,
  "風巻きの宿": 4,
  "湖畔の村": 4,
  "赤岩の鉱山町": 4,
  "南の森の村": 4,
  "大草原の町": 4,
  "焔の湯治場": 4,
  "霧沼の村": 3,
  "氷牙の砦": 3,
  "凍土の集落": 3,
  "雨林の村": 3,
  "霧氷の修道院": 3,
  "蔦の集落": 3,
  "南壁の砦": 3,
  "凍原の村": 3,
}
DUNGEON_INFO = {
  "始祖竜の頭骨": ("dragon_skull", "天の頂"),
  "翠国の王墓": ("jade_tomb", "灼熱の大砂漠"),
  "竜の心臓の火口": ("heart_crater", "炎の列島"),
  "沈んだ神殿": ("sunken_temple", "霧の大湿原"),
  "氷牙の氷窟": ("ice_cave", "氷牙山地"),
  "古木の根の洞": ("root_hollow", "古の大森林"),
  "南壁の巣穴": ("wall_den", "南壁山脈"),
}
# 開放した町（勇者が行ける範囲）。ほかの町は地図に出すが、道（陸路・航路・ダンジョンへの道）は出さず、行けない。
# 少しずつ開放していく（今は南大陸だけ）
OPEN = ["干潟の港", "大草原の町", "南壁の砦", "凍原の村"]
STARTS = ["干潟の港", "大草原の町", "凍原の村"]  # 始まりの町（南壁の砦からは始めない）


def pid(name):
  return TOWN_INFO[name][0] if name in TOWN_INFO else DUNGEON_INFO[name][0]


BAND = 40  # 道の両側この幅（px）の地形を、その道の「土地」とみなす


def terrain_mix(path):
  """道とその両側 BAND px の地形を数え、割合（%）の多い順に返す（10% 未満は捨てる）。"""
  cells = set()
  for (ax, ay), (bx, by) in zip(path, path[1:]):
    n = max(1, int(math.hypot(bx - ax, by - ay) // 4))
    for t in range(n + 1):
      x, y = ax + (bx - ax) * t / n, ay + (by - ay) * t / n
      for dx in range(-BAND, BAND + 1, 4):
        for dy in range(-BAND, BAND + 1, 4):
          if dx * dx + dy * dy <= BAND * BAND:
            cells.add(((int(x) + dx) // 4, (int(y) + dy) // 4))
  count = {}
  for cx, cy in cells:
    x, y = cx * 4 + 2, cy * 4 + 2
    if 0 <= x < 1600 and 0 <= y < 1000:
      k = TERRAIN.get(kind[y // STEP][x // STEP])
      if k:
        count[k] = count.get(k, 0) + 1
  total = sum(count.values()) or 1
  mix = sorted(((k, round(100 * v / total)) for k, v in count.items()), key=lambda kv: -kv[1])
  return [[k, v] for k, v in mix if v >= 10] or [["plain", 100]]


def main():
  area_of = {member: aid for aid, _, _, members in AREAS for member in members}
  places = []
  for name, x, y in TOWNS:
    tid, emoji, region = TOWN_INFO[name]
    places.append(
      {
        "id": tid,
        "type": "town",
        "name": NAMES[name],
        "en": tid.capitalize(),
        "alias": name,
        "area": area_of[name],
        "region": region,
        "emoji": emoji,
        "text": TEXT[name],
        "shop": list(SHOP),
        "pos": [x, y],
      }
      | {"quests_per_day": QUESTS_PER_DAY[name]}
      | ({"label": LABEL[name]} if name in LABEL else {})
    )
  for name, x, y in DUNGEONS:
    did, region = DUNGEON_INFO[name]
    places.append(
      {
        "id": did,
        "type": "dungeon",
        "name": name,
        "en": DUNGEON_EN[name],
        "area": area_of[name],
        "region": region,
        "pos": [x, y],
      }
      | ({"label": LABEL[name]} if name in LABEL else {})
    )

  roads = []
  for r in json.load(open(paths.ROADS, encoding="utf-8")):
    kind_ = "land" if r["kind"] == "road" else "trail"
    roads.append(
      {
        "from": pid(r["from"]),
        "to": pid(r["to"]),
        "distance": r["actions"],
        "kind": kind_,
        "terrain": terrain_mix(r["path"]),
        "path": [[round(x), round(y)] for x, y in r["path"]],
      }
    )
  for r in json.load(open(paths.SEA, encoding="utf-8")):
    roads.append(
      {
        "from": pid(r["from"]),
        "to": pid(r["to"]),
        "distance": r["actions"],
        "kind": "sea",
        "terrain": [["sea", 100]],
        "path": [[round(x), round(y)] for x, y in r["path"]],
      }
    )

  areas = [{"id": aid, "name": n, "en": en} for aid, n, en, _ in AREAS]
  mp = {
    "areas": areas,
    "open": [pid(n) for n in OPEN],
    "starts": [pid(n) for n in STARTS],
    "layout": {"size": [1600, 1000], "background": "map-base.svg"},
    "places": places,
    "roads": roads,
  }
  # 道の線（path）や小さな一覧は 1 行にまとめて読みやすくする
  inline = {}
  for i, area in enumerate(areas):  # 地方は 1 つ 1 行
    token = f"@@{len(inline)}@@"
    inline[token] = json.dumps(area, ensure_ascii=False, separators=(", ", ": "))
    areas[i] = token
  for obj in places + roads:
    for key in ("pos", "shop", "terrain", "path"):
      if key in obj:
        token = f"@@{len(inline)}@@"
        inline[token] = json.dumps(obj[key], ensure_ascii=False, separators=(",", ":"))
        obj[key] = token
  text = json.dumps(mp, ensure_ascii=False, indent=1)
  for token, value in inline.items():
    text = text.replace(f'"{token}"', value)
  open(paths.REPO / "world" / "map.json", "w", encoding="utf-8").write(text + "\n")
  shutil.copy(paths.BASE_SVG, paths.REPO / "world" / "map-base.svg")
  print(f"  町・ダンジョン {len(places)}、道 {len(roads)}")


if __name__ == "__main__":
  main()
