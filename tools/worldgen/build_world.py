"""lore/ の設定（町・ダンジョン・道・航路）から world/map.json を作り、ベースの地図を置く。

使い方: python build_world.py（先に make.py の手前の手順で .cache/ を作っておく）
"""
import json
import math
import paths
import shutil

from names import NAMES
from places import DUNGEONS, LABEL, TOWNS

STEP = 2
kind = paths.kind()

# 地形のマス → 魔物の地形（道の地形の割合に使う）
TERRAIN = {
    "plain": "plain", "steppe": "plain", "savanna": "plain", "beach": "plain", "river": "plain",
    "forest": "forest", "taiga": "forest", "jungle": "jungle",
    "desert": "desert", "badland": "desert", "wetland": "wetland", "marshwater": "wetland",
    "tundra": "snow", "snowcap": "snow", "ice": "snow",
    "mount": "mountain", "hill": "mountain", "volcano": "volcano", "lava": "volcano",
}

# 呼び名 → (ID, 絵文字, 地域, 店)
BASIC = ["wooden_sword", "copper_sword", "leather_cap", "cloth_armor", "leather_armor", "wooden_shield"]
MID = ["iron_sword", "iron_helm", "chain_mail", "iron_shield", "lucky_charm", "travel_bangle"]
SMITH = ["steel_sword", "steel_helm", "steel_armor", "steel_shield"]
TOWN_INFO = {
    "王都": ("aurea", "🏰", "黄金平原", BASIC + MID + SMITH),
    "河港": ("flumen", "⚓", "大河の三角州", BASIC + MID),
    "天門の町": ("caelum", "🗻", "竜骨山脈・天の頂のふもと", MID + SMITH),
    "古木の町": ("silwen", "🌳", "古の大森林", BASIC),
    "北の漁村": ("vike", "🐟", "中央大陸の北の海岸", BASIC),
    "霧沼の村": ("morna", "🛖", "霧の大湿原", BASIC + MID),
    "鋼山の町": ("durgan", "⚒️", "竜骨山脈の西のふもと", MID + SMITH),
    "椰子の港": ("cocora", "🌴", "熱帯の半島", BASIC + MID),
    "風巻きの宿": ("kazal", "🏕️", "風の草原", BASIC + MID),
    "湖畔の村": ("linde", "🛶", "湖沼地帯", BASIC),
    "氷牙の砦": ("isval", "🧊", "氷牙山地", MID + SMITH),
    "凍土の集落": ("yarka", "❄️", "中央大陸の北の凍土", BASIC + MID),
    "オアシスの町": ("zafra", "🏺", "灼熱の大砂漠", MID),
    "暁の港": ("fajil", "🌅", "東の草原の海岸", BASIC),
    "雨林の村": ("yashum", "🌿", "東の雨林", BASIC + MID),
    "霧氷の修道院": ("astol", "🔭", "霧氷の半島", MID),
    "蔦の集落": ("urka", "🍃", "西の密林", BASIC + MID),
    "赤岩の鉱山町": ("rubela", "💎", "赤い荒野", MID + SMITH),
    "西の港": ("novaris", "⛵", "西大陸の東の海岸", BASIC + MID),
    "南の森の村": ("rowen", "🏹", "南の森", BASIC),
    "干潟の港": ("salina", "🐚", "南の干潟", BASIC + MID),
    "大草原の町": ("eques", "🐎", "南の大草原", BASIC),
    "南壁の砦": ("muralis", "🛡️", "南壁山脈", MID + SMITH),
    "焔の湯治場": ("homura", "♨️", "炎の列島", MID + SMITH),
    "凍原の村": ("karun", "🦌", "南の凍原", BASIC),
}
# 町の特産（その町でしか買えない common）
SPECIALTY = {
    "古木の町": "bark_shield", "北の漁村": "fur_hat", "湖畔の村": "waterweed_bangle", "暁の港": "trader_dagger",
    "南の森の村": "hunter_bow", "大草原の町": "cavalry_lance", "王都": "royal_shield", "河港": "sailor_hat",
    "天門の町": "mountain_helm", "霧沼の村": "reed_shield", "鋼山の町": "battle_axe", "椰子の港": "coral_necklace",
    "風巻きの宿": "nomad_bow", "氷牙の砦": "wolf_fur_armor", "凍土の集落": "polar_fur", "オアシスの町": "sand_hood",
    "雨林の村": "jade_ring", "霧氷の修道院": "stargazer_amulet", "蔦の集落": "blowgun", "赤岩の鉱山町": "ruby_ring",
    "西の港": "settler_armor", "干潟の港": "shell_shield", "南壁の砦": "wall_shield", "焔の湯治場": "volcanic_blade",
    "凍原の村": "reindeer_coat",
}
# 1 日にギルドに出る依頼の数（町の大きさ。書いていない町は season.json の board_size）
QUESTS_PER_DAY = {
    "王都": 6,
    "河港": 5, "暁の港": 5, "干潟の港": 5, "西の港": 5, "椰子の港": 5, "オアシスの町": 5, "天門の町": 5,
    "霧沼の村": 3, "氷牙の砦": 3, "凍土の集落": 3, "雨林の村": 3, "霧氷の修道院": 3, "蔦の集落": 3, "南壁の砦": 3,
    "凍原の村": 3,
}
DUNGEON_INFO = {
    "始祖竜の頭骨": ("dragon_skull", "天の頂"), "翠国の王墓": ("jade_tomb", "灼熱の大砂漠"),
    "竜の心臓の火口": ("heart_crater", "炎の列島"), "沈んだ神殿": ("sunken_temple", "霧の大湿原"),
    "氷牙の氷窟": ("ice_cave", "氷牙山地"), "古木の根の洞": ("root_hollow", "古の大森林"),
    "南壁の巣穴": ("wall_den", "南壁山脈"),
}
# 開放した町（勇者が行ける範囲）。ほかの町は地図に出すが、道（陸路・航路・ダンジョンへの道）は出さず、行けない。
# 少しずつ開放していく（今は南大陸だけ）
OPEN = ["干潟の港", "大草原の町", "南壁の砦", "凍原の村"]
STARTS = ["干潟の港", "大草原の町", "凍原の村"]  # 安い装備を売る町から始める（南壁の砦は高い装備だけ）


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
    places = []
    for name, x, y in TOWNS:
        tid, emoji, region, shop = TOWN_INFO[name]
        places.append({"id": tid, "type": "town", "name": NAMES[name], "alias": name, "region": region,
                       "emoji": emoji, "shop": shop + [SPECIALTY[name]], "pos": [x, y]}
                      | ({"quests_per_day": QUESTS_PER_DAY[name]} if name in QUESTS_PER_DAY else {}) | ({"label": LABEL[name]} if name in LABEL else {}))
    for name, x, y in DUNGEONS:
        did, region = DUNGEON_INFO[name]
        places.append({"id": did, "type": "dungeon", "name": name, "region": region, "pos": [x, y]}
                      | ({"label": LABEL[name]} if name in LABEL else {}))

    roads = []
    for r in json.load(open(paths.ROADS, encoding="utf-8")):
        kind_ = "land" if r["kind"] == "road" else "trail"
        roads.append({"from": pid(r["from"]), "to": pid(r["to"]), "distance": r["actions"], "kind": kind_,
                      "terrain": terrain_mix(r["path"]), "path": [[round(x), round(y)] for x, y in r["path"]]})
    for r in json.load(open(paths.SEA, encoding="utf-8")):
        roads.append({"from": pid(r["from"]), "to": pid(r["to"]), "distance": r["actions"], "kind": "sea",
                      "terrain": [["sea", 100]], "path": [[round(x), round(y)] for x, y in r["path"]]})

    mp = {"open": [pid(n) for n in OPEN], "starts": [pid(n) for n in STARTS],
          "layout": {"size": [1600, 1000], "background": "map-base.svg"},
          "places": places, "roads": roads}
    # 道の線（path）や小さな一覧は 1 行にまとめて読みやすくする
    inline = {}
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
