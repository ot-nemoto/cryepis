"""表（places・names・lore と、このファイルの表）と .cache/ の道から world/map.json を作り、ベースの地図を置く。

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

# 地形のマス → 物の怪の地形（道の地形の割合に使う）
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
# 町の名前 → (ID, 絵文字, 地域＝国)
TOWN_INFO = {
  "十三湊": ("tosaminato", "⚓", "陸奥"),
  "酒田": ("sakata", "⚓", "出羽"),
  "米沢": ("yonezawa", "🏯", "出羽"),
  "黒川": ("kurokawa", "🏯", "陸奥"),
  "江戸": ("edo", "🏯", "武蔵"),
  "小田原": ("odawara", "🏯", "相模"),
  "甲府": ("kofu", "🏯", "甲斐"),
  "長野": ("nagano", "⛩️", "信濃"),
  "駿府": ("sumpu", "🏯", "駿河"),
  "清洲": ("kiyosu", "🏯", "尾張"),
  "宇治山田": ("ujiyamada", "⛩️", "伊勢"),
  "春日山": ("kasugayama", "🏯", "越後"),
  "金沢": ("kanazawa", "🛕", "加賀"),
  "一乗谷": ("ichijodani", "🏯", "越前"),
  "敦賀": ("tsuruga", "⚓", "越前"),
  "京都": ("kyoto", "🏛️", "山城"),
  "安土": ("azuchi", "🏯", "近江"),
  "大坂": ("osaka", "🛕", "摂津"),
  "今井町": ("imaicho", "🛕", "大和"),
  "広島": ("hiroshima", "🏯", "安芸"),
  "山口": ("yamaguchi", "🏯", "周防"),
  "勝瑞": ("shozui", "🏯", "阿波"),
  "博多": ("hakata", "⚓", "筑前"),
  "豊後府内": ("funai", "🏯", "豊後"),
  "鹿児島": ("kagoshima", "🏯", "薩摩"),
}
# 口入れ屋の規模（高札の依頼の枠の数）。どの町にも書く（都 6、港や大きな町 5、ふつうの町 4、小さな町 3）
QUESTS_PER_DAY = {
  "十三湊": 3,
  "酒田": 4,
  "米沢": 3,
  "黒川": 4,
  "江戸": 5,
  "小田原": 4,
  "甲府": 3,
  "長野": 3,
  "駿府": 4,
  "清洲": 4,
  "宇治山田": 4,
  "春日山": 4,
  "金沢": 4,
  "一乗谷": 3,
  "敦賀": 5,
  "京都": 6,
  "安土": 4,
  "大坂": 5,
  "今井町": 3,
  "広島": 4,
  "山口": 4,
  "勝瑞": 3,
  "博多": 5,
  "豊後府内": 4,
  "鹿児島": 4,
}
AREA_STONES = {
  "ou": ("霧氷石", "冷たく澄み、氷の力を宿す"),
  "bando": ("玄岳鋼", "重く頑丈で、攻めと守りに優れる"),
  "tokai": ("潮風銀", "軽くしなやかで、素早さを活かす"),
  "hokuriku": ("星鏡鉱", "星を映すように澄み、器用さを助ける"),
  "kinai": ("紫電鉄", "雷の力を宿す"),
  "saigoku": ("常盤玉", "命の力を蓄え、体を支える"),
  "kyushu": ("熾焔鋼", "熱く燃え、炎の力を宿す"),
}
LEGENDARY_FORGES = {"小田原"}
# 霊場の名前 → (ID, 地域)
DUNGEON_INFO = {
  "八甲田山": ("hakkoda", "陸奥"),
  "平泉": ("hiraizumi", "陸奥"),
  "富士山": ("fuji", "駿河・甲斐"),
  "熊野": ("kumano", "紀伊"),
  "黄泉比良坂": ("yomotsu_hirasaka", "出雲"),
  "剣山": ("tsurugi", "阿波"),
  "阿蘇山": ("aso", "肥後"),
}
# 開放した町（もののふが行ける範囲）。ほかの町は地図に出すが、道（陸路・航路・霊場への道）は出さず、行けない。
# 少しずつ開放していく（今は坂東だけ）
OPEN = ["江戸", "小田原", "甲府", "長野"]
STARTS = ["江戸", "小田原", "長野"]  # 始まりの町（甲府からは始めない）


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


def build_places():
  """町と霊場（world/map.json の places）。表（places.py・names.py・lore.py と、このファイルの表）から作る。"""
  area_of = {member: aid for aid, _, _, members in AREAS for member in members}
  places = []
  for name, x, y in TOWNS:
    tid, emoji, region = TOWN_INFO[name]
    places.append(
      {
        "id": tid,
        "type": "town",
        "name": name,
        "en": tid.capitalize(),
        "alias": NAMES[name],
        "area": area_of[name],
        "region": region,
        "emoji": emoji,
        "text": TEXT[name],
        "shop": list(SHOP),
        "pos": [x, y],
      }
      | {"quests_per_day": QUESTS_PER_DAY[name]}
      | ({"legendary_forge": True} if name in LEGENDARY_FORGES else {})
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
  return places


def main():
  places = build_places()
  roads = []
  with open(paths.ROADS, encoding="utf-8") as f:
    land_roads = json.load(f)
  for r in land_roads:
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
  with open(paths.SEA, encoding="utf-8") as f:
    sea_routes = json.load(f)
  for r in sea_routes:
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

  areas = [
    {"id": aid, "name": n, "en": en}
    | ({"stone": AREA_STONES[aid][0], "stone_effect": AREA_STONES[aid][1]} if aid in AREA_STONES else {})
    for aid, n, en, _ in AREAS
  ]
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
  with open(paths.REPO / "world" / "map.json", "w", encoding="utf-8") as f:
    f.write(text + "\n")
  shutil.copy(paths.BASE_SVG, paths.REPO / "world" / "map-base.svg")
  print(f"  町・霊場 {len(places)}、道 {len(roads)}")


if __name__ == "__main__":
  main()
