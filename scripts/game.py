"""シーズン制 RPG の 1 日分の進行（勇者の状態・ギルドの依頼・戦闘・アイテム・シーズンの切り替え）。

勇者は毎日、決まった回数だけ行動する。場所は町だけで、町と町は陸路・航路でつながっている。
町のギルドで依頼を受け、討伐なら依頼の道を行き来して魔物を倒し、配達なら届け先の町へ向かう。
道を 1 行動進むごとに、道の地形に合った魔物に出会ったり、宝箱を見つけたりする（乱数）。
魔物の強さはどこでも同じで、地形ごとに性質（先手・毒・群れなど）が違う。
依頼がなければ、となりの町へ旅をする。力尽きたら依頼は失敗になり、最後に寄った町で目を覚ます。
シーズンが変わると、アイテム（持ち物）以外は最初の状態に戻る。
乱数は「日付＋シーズン＋勇者の ID」を種にするので、同じ日に何度実行しても結果は同じ。
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import world as W

JOURNAL_DAYS = 30
RARITY_MARKS = {"common": "", "rare": "★", "epic": "★★", "legendary": "★★★"}


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
def new_hero(world: dict) -> dict:
    h = world["season"]["hero"]
    return {"level": 1, "exp": 0, "max_hp": h["max_hp"], "hp": h["max_hp"], "base_atk": h["atk"],
            "base_def": h["def"], "gold": h["gold"], "potions": h["potions"]}


def new_season_stats() -> dict:
    return {"quests_done": 0, "quests_failed": 0, "kills": 0, "deaths": 0, "rares": []}


def start_town(world: dict, hero_id: str, season: int | None) -> str:
    """最初の町（勇者とシーズンごとに、map.json の starts からランダムに決める）。"""
    return random.Random(f"start-{season or 0}-{hero_id}").choice(world["starts"])


def new_state(world: dict, hero_id: str = "h1", name: str = "勇者") -> dict:
    town = start_town(world, hero_id, None)
    return {"id": hero_id, "name": name, "season": None, "season_day": 0, "day": 0, "last_date": None,
            "hero": new_hero(world), "inventory": {}, "place": town, "home_town": town,
            "road": None, "quest": None, "taken": [], "season_stats": new_season_stats(), "last": None,
            "journal": [], "seasons": []}


def load_state(world: dict, path: Path, hero_id: str = "h1", name: str = "勇者") -> dict:
    """状態を読む。足りない項目は補い、world/ から消えた場所・アイテム・依頼の参照は安全な値に戻す。
    前の形式（一本道の RPG）の状態なら、新しい形式で最初から作り直す。"""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return new_state(world, hero_id, name)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}")
    if "season" not in loaded:  # 前の形式
        return new_state(world, loaded.get("id", hero_id), loaded.get("name", name))
    state = {**new_state(world, hero_id, name), **loaded}
    state["hero"] = {**new_hero(world), **state["hero"]}
    state["season_stats"] = {**new_season_stats(), **state["season_stats"]}
    places = world["places"]
    fallback = start_town(world, state["id"], state["season"])
    state["inventory"] = {k: v for k, v in state["inventory"].items() if k in world["items"]}
    state["season_stats"]["rares"] = [i for i in state["season_stats"]["rares"] if i in world["items"]]
    if state["place"] not in world["towns"]:
        state.update(place=fallback, road=None)
    if places.get(state["home_town"], {}).get("type") != "town":
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


def quest_ok(world: dict, quest: dict) -> bool:
    """依頼の参照先が今の world/ にあるか（行き先・報告先は町、討伐は報告先からの道と魔物）。"""
    places = world["places"]
    if quest.get("type") not in QUEST_TYPES or places.get(quest.get("place"), {}).get("type") != "town":
        return False
    if quest.get("report") is not None and places.get(quest["report"], {}).get("type") != "town":
        return False
    if quest["type"] == "hunt":
        return (quest.get("report"), quest["place"]) in world["roads"] and quest.get("monster") in world["monsters"]
    return True


def save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---- 装備と能力値 ----------------------------------------------------------------------
def item_score(item: dict) -> float:
    return item.get("atk", 0) + item.get("def", 0) + item.get("hp", 0) / 4


def equipment(world: dict, state: dict) -> dict[str, str | None]:
    """部位ごとに、持ち物の中でいちばん強いアイテムの ID（なければ None）。"""
    rank = {r: i for i, r in enumerate(world["rarities"])}
    best: dict[str, str | None] = {slot: None for slot in W.SLOTS}
    for iid in sorted(state["inventory"]):
        item, cur = world["items"][iid], best[world["items"][iid]["slot"]]
        key = (item_score(item), rank[item["rarity"]])
        if cur is None or key > (item_score(world["items"][cur]), rank[world["items"][cur]["rarity"]]):
            best[item["slot"]] = iid
    return best


def stats(world: dict, state: dict) -> dict[str, int]:
    hero = state["hero"]
    worn = [world["items"][i] for i in equipment(world, state).values() if i]
    return {"atk": hero["base_atk"] + sum(i.get("atk", 0) for i in worn),
            "def": hero["base_def"] + sum(i.get("def", 0) for i in worn),
            "max_hp": hero["max_hp"] + sum(i.get("hp", 0) for i in worn)}


def exp_need(world: dict, level: int) -> int:
    up = world["season"]["level_up"]
    return round(up["exp_base"] * level ** up["exp_power"])


def monster_level(world: dict) -> int:
    """道の魔物のふつうのレベル（どこでも同じ強さなので、いちばん多いレベル）。依頼の報酬の目安に使う。"""
    levels = [m["level"] for m in world["monsters"].values()]
    return max(set(levels), key=levels.count)


# ---- ギルドの依頼 ---------------------------------------------------------------------
def board(world: dict, town: str, d: date, season: int) -> list[dict]:
    """町 town の、日付 d の依頼の掲示板（全員共通）。
    枠ごとにレアリティ（★〜）を重みで選び、その町の依頼から選ぶ。その町に当たったレアリティの依頼がなければ
    1 つ下に落とす。町の依頼は同じ日に 1 度だけ出て、足りない枠には共通の依頼（templates）が入る。"""
    cfg, ranks = world["season"], world["quest_ranks"]
    rng = random.Random(f"board-{d.isoformat()}-{season}-{town}")
    own = list(world["town_quests"].get(town, []))
    quests = []
    for i in range(quests_per_day(world, town)):
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
    """町のギルドに 1 日に出る依頼の数（map.json の quests_per_day。なければ season.json の board_size）。"""
    return world["places"][town].get("quests_per_day", world["season"]["board_size"])


def finish_quest(world: dict, quest: dict, town: str, d: date, days: int, unit: int) -> dict:
    """報酬（種類ごとの基本 × 魔物のレベル × 数か距離 × レアリティの倍率）と期限をつける。"""
    lvl, rank = monster_level(world), quest["rank"]
    base, cfg = world["quest_reward"][quest["type"]], world["quest_ranks"][rank - 1]
    quest.update(town=town, progress=0, level=[lvl, lvl], deadline=(d + timedelta(days=days)).isoformat(),
                 reward={"exp": round(base["exp"] * lvl * unit * cfg["reward"]),
                         "gold": round(base["gold"] * lvl * unit * cfg["reward"]), "item": cfg["item"]})
    return quest


def make_town_quest(world: dict, q: dict, town: str, d: date) -> dict:
    """町の依頼（quests.json の towns）から、掲示板に出す依頼を作る。"""
    if q["type"] == "hunt":
        road = world["roads"][(town, q["to"])]
        quest = {"type": "hunt", "place": q["to"], "report": town, "monster": q["monster"], "count": q["count"]}
        days, unit = 2 + q["count"] // 2 + 2 * road, q["count"]
    else:
        dist = world["dist"][town][q["to"]]
        quest = {"type": "deliver", "place": q["to"], "report": q["to"], "monster": None, "count": 1}
        days, unit = 2 + dist, dist
    quest.update(template=q["id"], name=q["name"], rank=q["rank"], text=q.get("text", ""))
    return finish_quest(world, quest, town, d, days, unit)


def make_quest(world: dict, t: dict, town: str, d: date, rng: random.Random) -> dict | None:
    """共通の依頼。討伐: 町から出る道の 1 本で、その道に出る魔物を倒す。配達: 近くの町へ荷物を届ける。"""
    cfg, dist = world["season"], world["dist"][town]
    if t["type"] == "hunt":
        other = rng.choice(world["neighbors"][town])
        mids, weights = zip(*world["road_info"][(town, other)]["monsters"])
        count = rng.randint(*t["count"])
        quest = {"type": "hunt", "place": other, "report": town, "monster": rng.choices(mids, weights)[0],
                 "count": count}
        days, unit = t["days"] + 2 * world["roads"][(town, other)], count
    else:  # deliver
        towns = [p for p in world["towns"] if p != town and dist.get(p, cfg["deliver_range"] + 1) <= cfg["deliver_range"]]
        if not towns:
            return None
        to = rng.choice(towns)
        quest = {"type": "deliver", "place": to, "report": to, "monster": None, "count": 1}
        days, unit = t["days"] + dist[to], dist[to]
    quest.update(template=t["id"], name=t.get("name", t["type"]), rank=t["rank"], text="")
    return finish_quest(world, quest, town, d, days, unit)


def suitable(quest: dict, level: int) -> bool:
    """受けられる依頼か（魔物の強さはどこでも同じなので、強すぎる依頼だけを避ける。弱すぎる依頼も受ける）。"""
    return quest["level"][0] <= level + 3


def patrol(world: dict, state: dict, rng: random.Random) -> dict:
    """合う依頼がないときの旅（報酬なし）。となりの町のどれかへ向かう。"""
    lvl = monster_level(world)
    return {"type": "patrol", "name": "旅", "template": None, "town": None,
            "place": rng.choice(world["neighbors"][state["place"]]), "report": None, "monster": None,
            "count": 1, "progress": 0, "level": [lvl, lvl], "deadline": None, "reward": None}


def quest_detail(world: dict, quest: dict) -> str:
    places = world["places"]
    place = places[quest["place"]]["name"]
    if quest["type"] == "hunt":
        return (f"{places[quest['report']]['name']}と{place}の間の道で"
                f"{world['monsters'][quest['monster']]['name']}を{quest['count']}体討伐")
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
        self.guild = guild  # 全員で共有する「受けられた依頼」（依頼の ID → 受けた勇者の ID）。None なら共有しない

    def log(self, text: str) -> None:
        self.events.append(text)

    @property
    def stats(self) -> dict:
        return stats(self.world, self.state)

    # 戦闘 ----------------------------------------------------------------------------
    def damage(self, attack: int, guard: int) -> int:
        return max(1, attack - guard // 2 + self.rng.randint(-2, 2))

    def battle(self, monster: dict) -> bool:
        """勝てば True。回復薬が尽きて HP が 0 になったら False。魔物の性質（trait）で戦い方が変わる。"""
        hero, s, trait = self.hero, self.stats, monster.get("trait")
        hp, atk, guard, hits = monster["hp"], monster["atk"], monster["def"], 1
        if trait == "fierce":  # 凶暴: 攻撃が強いが、守りが弱い
            atk, guard = round(atk * 1.3), guard // 2
        elif trait == "armored":  # 硬い: 守りが固く、攻撃は少し弱い
            atk, guard = max(1, round(atk * 0.9)), guard * 2 + 1
        elif trait == "swarm":  # 群れ: 数が多い（倒すのに手間がかかり、攻撃の回数が多い）
            hp, atk, hits = round(hp * 1.5), max(1, round(atk * 0.6)), 2
        monster_first = trait == "first_strike"  # 先手: 先に攻撃してくる
        while True:
            if not monster_first:
                hp -= self.damage(s["atk"], guard)
                if hp <= 0:
                    break
            monster_first = False
            for _ in range(hits):
                hero["hp"] -= self.damage(atk, s["def"])
                if hero["hp"] <= 0:
                    if hero["potions"]:  # 倒れる前に回復薬を使う
                        hero["potions"] -= 1
                        hero["hp"] = max(1, int(s["max_hp"] * self.cfg["potion"]["heal_rate"]))
                        continue
                    hero["hp"] = 0
                    self.log(f"{self.monster_name(monster)}に敗れた")
                    return False
        # 自分よりずっと弱い敵からは経験値が少ない
        gap = hero["level"] - monster["level"]
        exp = round(monster["exp"] * (1 if gap <= 3 else max(0.2, 1 - 0.15 * (gap - 3))))
        hero["gold"] += monster["gold"]
        self.state["season_stats"]["kills"] += 1
        self.log(f"{self.monster_name(monster)}を倒した (+{exp}EXP)")
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
        hero, up = self.hero, self.cfg["level_up"]
        hero["exp"] += exp
        while hero["exp"] >= exp_need(self.world, hero["level"]):
            hero["exp"] -= exp_need(self.world, hero["level"])
            hero["level"] += 1
            hero["max_hp"] += up["hp"]
            hero["base_atk"] += up["atk"]
            hero["base_def"] += up["def"]
            hero["hp"] = self.stats["max_hp"]
            self.log(f"レベルが上がった！ Lv.{hero['level']}")

    # アイテム -------------------------------------------------------------------------
    def draw(self, table: list | None, town: str | None = None) -> str | None:
        """ドロップ表から抽選する。珍しさが当たったら、その珍しさのアイテムから均等に選ぶ。
        レア（common 以外）は、その町（town。依頼を出した町）の依頼で出るもの（items.json の towns）だけ。
        その町にない珍しさが当たったら、1 つ下の珍しさに落として選び直す（どの町にも rare は必ずある）。"""
        if not table:
            return None
        keys, weights = zip(*table)
        key = self.rng.choices(keys, weights)[0]
        if key == W.NONE:
            return None
        items = self.world["items"]
        if key in items:  # アイテムの ID を直接書いたとき（レアなら、その町のものだけ）
            item = items[key]
            return key if item["rarity"] == W.COMMON or town in item.get("towns", []) else None
        rarities = self.world["rarities"]
        for rarity in reversed(rarities[rarities.index(W.COMMON):rarities.index(key) + 1]):
            pool = sorted(i for i, it in items.items()
                          if it["rarity"] == rarity and (rarity == W.COMMON or town in it.get("towns", [])))
            if pool:
                return self.rng.choice(pool)
            if rarity == rarities[rarities.index(W.COMMON) + 1]:
                return None  # rare もない（宝箱など、町のない抽選）なら何も出ない
        return None

    def obtain(self, iid: str, source: str) -> None:
        item, inv = self.world["items"][iid], self.state["inventory"]
        before = equipment(self.world, self.state)[item["slot"]]
        inv[iid] = inv.get(iid, 0) + 1
        mark = RARITY_MARKS.get(item["rarity"], "")
        if item["rarity"] != "common":
            self.state["season_stats"]["rares"].append(iid)
        text = f"{source}{mark}「{item['name']}」を手に入れた"
        if equipment(self.world, self.state)[item["slot"]] == iid and before != iid:
            text += "（装備した）"
        self.log(text + ("！" if mark else ""))

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

    def report(self, quest: dict) -> None:
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
            better = cur is None or item_score(item) > item_score(items[cur])
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
        taken = set(self.state["taken"]) | set(self.guild or {})  # 自分か、ほかの勇者が受けた依頼は受けられない
        quests = [q for q in board(self.world, town, self.today, self.state["season"])
                  if suitable(q, level) and q["id"] not in taken]
        if quests:
            quest = self.rng.choice(quests)
            self.state["taken"].append(quest["id"])
            if self.guild is not None:
                self.guild[quest["id"]] = self.state["id"]
            self.log(f"{self.world['places'][town]['name']}のギルドで依頼を受けた: {quest_text(self.world, quest)}")
        else:  # 受けられる依頼がない（ほかの勇者に取られた）ので、となりの町へ旅に出る
            quest = patrol(self.world, self.state, self.rng)
            self.log(f"{self.world['places'][town]['name']}のギルドには受けられる依頼がない。"
                     f"{self.world['places'][quest['place']]['name']}へ旅に出る")
        self.state["quest"] = quest

    def fail_quest(self, why: str) -> None:
        quest = self.state["quest"]
        self.state["quest"] = None
        if quest and quest["type"] != "patrol":
            self.state["season_stats"]["quests_failed"] += 1
            self.log(f"依頼失敗（{why}）: {quest_text(self.world, quest)}")

    # 道 --------------------------------------------------------------------------------
    def hunting(self, a: str, b: str) -> bool:
        """a→b の道が、まだ終わっていない討伐の依頼の道か。"""
        quest = self.state["quest"]
        return (quest is not None and quest["type"] == "hunt" and quest["progress"] < quest["count"]
                and {a, b} == {quest["report"], quest["place"]})

    def road_event(self, a: str, b: str) -> bool:
        """道を 1 行動進んだときの出来事。力尽きたら False。"""
        info, quest = self.world["road_info"][(a, b)], self.state["quest"]
        here = self.hunting(a, b)
        events = dict(self.cfg["road_events"])
        if here:  # 依頼の道では魔物を探して歩く
            events["battle"] = events.get("battle", 0) * self.cfg["hunt_battle_boost"]
        kinds, weights = zip(*events.items())
        kind = self.rng.choices(kinds, weights)[0]
        if kind == "battle":
            mids, mw = zip(*info["monsters"])
            mid = quest["monster"] if here and self.rng.random() < 0.5 else self.rng.choices(mids, mw)[0]
            if not self.battle(self.world["monsters"][mid]):
                return False
            if here and mid == quest["monster"]:
                quest["progress"] += 1
                if quest["progress"] >= quest["count"]:
                    self.log(f"討伐の目標を果たした（{quest['count']}体）")
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

    def act(self) -> bool:
        """1 行動。力尽きたら False。"""
        state, hero = self.state, self.hero
        for _ in range(5):  # 町での受注・報告は行動回数を使わない
            if state["road"]:
                if hero["hp"] < self.stats["max_hp"] * self.cfg["retreat_hp_rate"] and not hero["potions"]:
                    self.turn_back()
                return self.step(state["road"])
            place, quest = state["place"], state["quest"]
            if quest is None:
                self.take_quest(place)
                continue
            if quest["progress"] >= quest["count"]:
                if quest["report"] is None:  # 旅は報告しない
                    state["quest"] = None
                    continue
                if place == quest["report"]:
                    self.visit_town(place)
                    continue
                return self.move_toward(quest["report"])
            if quest["type"] == "hunt":  # 依頼の道を行き来する
                if place == quest["report"]:
                    return self.go(quest["place"])
                if place == quest["place"]:
                    return self.go(quest["report"])
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
        state["seasons"].append({"season": state["season"], "days": state["season_day"],
                                 "level": state["hero"]["level"], **stats_, "ended_on": state["last_date"]})
    # 最初のシーズンは、登録したときに決めた町から。シーズンが変わったら最初の町を決め直す
    town = state["place"] if state["season"] is None else start_town(world, state["id"], season)
    state.update(season=season, season_day=0, hero=new_hero(world), place=town,
                 home_town=town, road=None, quest=None, season_stats=new_season_stats())


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
    day = Day(world, state, today, rng, guild)
    if new_season:
        day.log(f"シーズン{season}が始まった（レベルは 1 に戻り、持ち物はそのまま）")
    if actions is None:
        actions = world["season"]["actions_per_day"]
    if state["quest"] and state["quest"]["deadline"] and today.isoformat() > state["quest"]["deadline"]:
        day.fail_quest("期限切れ")
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
    state["journal"] = ([{"date": today.isoformat(), "actions": actions, "text": " / ".join(day.events)}]
                        + state["journal"])[:JOURNAL_DAYS]
    return True
