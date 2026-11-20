"""シーズン制 RPG の 1 日分の進行（勇者の状態・ギルドの依頼・戦闘・アイテム・シーズンの切り替え）。

勇者は毎日、乱数で決まる回数だけ行動する。町のギルドで依頼を受け、目的の場所へ向かって討伐や配達をこなし、
依頼を出した町（配達なら届け先）で報告して報酬を受け取る。合う依頼がなければ、レベルに合った野外を見回る。
力尽きたら依頼は失敗になり、最後に寄った町で目を覚ます。
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
    if state["place"] not in places:
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


QUEST_PLACE_TYPES = {"hunt": "field", "patrol": "field", "deliver": "town"}


def quest_ok(world: dict, quest: dict) -> bool:
    """依頼の参照先が今の world/ にあり、種類も合っているか（討伐・見回りは野外、配達は町）。"""
    places = world["places"]
    want = QUEST_PLACE_TYPES.get(quest.get("type"))
    if want is None or places.get(quest.get("place"), {}).get("type") != want:
        return False
    if quest.get("report") is not None and places.get(quest["report"], {}).get("type") != "town":
        return False
    return quest.get("monster") is None or quest["monster"] in world["monsters"]


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


# ---- ギルドの依頼 ---------------------------------------------------------------------
def board(world: dict, town: str, d: date, season: int) -> list[dict]:
    """町 town の、日付 d の依頼の掲示板（全員共通）。"""
    cfg = world["season"]
    rng = random.Random(f"board-{d.isoformat()}-{season}-{town}")
    templates = world["quests"]
    weights = [t["weight"] for t in templates]
    quests = []
    for i in range(cfg["board_size"]):
        quest = make_quest(world, rng.choices(templates, weights)[0], town, d, rng)
        if quest:
            quest["id"] = f"{d.isoformat()}-{town}-{i}"
            quests.append(quest)
    return quests


def make_quest(world: dict, t: dict, town: str, d: date, rng: random.Random) -> dict | None:
    cfg, dist, places = world["season"], world["dist"][town], world["places"]
    if t["type"] == "hunt":
        fields = [p for p in world["fields"] if dist[p] <= cfg["hunt_range"]]
        if not fields:
            return None
        place = rng.choice(fields)
        field = places[place]
        count = rng.randint(*t["count"])
        quest = {"type": "hunt", "place": place, "report": town, "monster": rng.choice(field["monsters"]),
                 "count": count, "level": list(field["level"]), "days": t["days"] + 2 * dist[place]}
        unit = count
    else:  # deliver
        towns = [p for p in world["towns"] if p != town and dist[p] <= cfg["deliver_range"]]
        if not towns:
            return None
        to = rng.choice(towns)
        levels = [places[p]["level"] for p in route(world, town, to) if places[p]["type"] == "field"]
        lo = max((lv[0] for lv in levels), default=1)
        quest = {"type": "deliver", "place": to, "report": to, "monster": None, "count": 1,
                 "level": [lo, max((lv[1] for lv in levels), default=lo)], "days": t["days"] + dist[to]}
        unit = dist[to]
    lvl, reward = quest["level"][0], t["reward"]
    quest.update(template=t["id"], name=t.get("name", t["type"]), town=town, progress=0,
                 deadline=(d + timedelta(days=quest.pop("days"))).isoformat(),
                 reward={"exp": round(reward["exp"] * lvl * unit), "gold": round(reward["gold"] * lvl * unit),
                         "item": reward.get("item")})
    return quest


def suitable(quest: dict, level: int) -> bool:
    lo, hi = quest["level"]
    return lo <= level + 3 and level <= hi + 3


def patrol(world: dict, state: dict) -> dict:
    """合う依頼がないときの見回り（報酬なし）。レベルに合ういちばん手ごわい野外の、近いところへ行く。"""
    level, places = state["hero"]["level"], world["places"]
    ok = [p for p in world["fields"] if places[p]["level"][0] <= level] or world["fields"][:1]
    top = max(places[p]["level"][0] for p in ok)
    place = min((p for p in ok if places[p]["level"][0] == top), key=lambda p: world["dist"][state["place"]][p])
    return {"type": "patrol", "name": "見回り", "template": None, "town": None, "place": place, "report": None,
            "monster": None, "count": world["season"]["patrol_battles"], "progress": 0,
            "level": list(places[place]["level"]), "deadline": None, "reward": None}


def quest_text(world: dict, quest: dict) -> str:
    place = world["places"][quest["place"]]["name"]
    if quest["type"] == "hunt":
        return f"{place}で{world['monsters'][quest['monster']]['name']}を{quest['count']}体討伐"
    if quest["type"] == "deliver":
        return f"{place}へ荷物を届ける"
    return f"{place}を見回る"


def route(world: dict, a: str, b: str) -> list[str]:
    """a から b への最短経路（a と b を含む）。"""
    path = [a]
    while path[-1] != b:
        path.append(world["next"][path[-1]][b])
    return path


def nearest_town(world: dict, place: str) -> str:
    return min(world["towns"], key=lambda t: world["dist"][place][t])


# ---- 1 日分を進める -------------------------------------------------------------------
class Day:
    """1 日分の出来事を記録しながら進める。"""

    def __init__(self, world: dict, state: dict, today: date, rng: random.Random):
        self.world, self.state, self.today, self.rng = world, state, today, rng
        self.cfg = world["season"]
        self.hero = state["hero"]
        self.events: list[str] = []
        self.fallen = False

    def log(self, text: str) -> None:
        self.events.append(text)

    @property
    def stats(self) -> dict:
        return stats(self.world, self.state)

    # 戦闘 ----------------------------------------------------------------------------
    def damage(self, attack: int, guard: int) -> int:
        return max(1, attack - guard // 2 + self.rng.randint(-2, 2))

    def battle(self, monster: dict) -> bool:
        """勝てば True。回復薬が尽きて HP が 0 になったら False。"""
        hero, hp, s = self.hero, monster["hp"], self.stats
        while True:
            hp -= self.damage(s["atk"], monster["def"])
            if hp <= 0:
                break
            hero["hp"] -= self.damage(monster["atk"], s["def"])
            if hero["hp"] <= 0:
                if hero["potions"]:  # 倒れる前に回復薬を使う
                    hero["potions"] -= 1
                    hero["hp"] = max(1, int(s["max_hp"] * self.cfg["potion"]["heal_rate"]))
                    continue
                hero["hp"] = 0
                self.log(f"{monster['name']}に敗れた")
                return False
        # 自分よりずっと弱い敵からは経験値が少ない（低レベルの場所に居座らないように）
        gap = hero["level"] - monster["level"]
        exp = round(monster["exp"] * (1 if gap <= 3 else max(0.2, 1 - 0.15 * (gap - 3))))
        hero["gold"] += monster["gold"]
        self.state["season_stats"]["kills"] += 1
        self.log(f"{monster['name']}を倒した (+{exp}EXP)")
        self.gain_exp(exp)
        return True

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
    def draw(self, table: list | None, level: int) -> str | None:
        """ドロップ表から抽選する。珍しさが当たったら、その珍しさで level 以下で出るアイテムから選ぶ。"""
        if not table:
            return None
        keys, weights = zip(*table)
        key = self.rng.choices(keys, weights)[0]
        if key == W.NONE:
            return None
        if key in self.world["items"]:
            return key
        pool = sorted(i for i, it in self.world["items"].items()
                      if it["rarity"] == key and it.get("drop_level", 1) <= level)
        return self.rng.choice(pool) if pool else None

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
        if quest and quest["type"] == "deliver" and quest["place"] == town:
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
        iid = self.draw(reward.get("item"), quest["level"][0])
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
        quests = [q for q in board(self.world, town, self.today, self.state["season"])
                  if suitable(q, level) and q["id"] not in self.state["taken"]]  # その日に受けた依頼はもう受けない
        if quests:
            quest = self.rng.choice(quests)
            self.state["taken"].append(quest["id"])
            self.log(f"{self.world['places'][town]['name']}のギルドで依頼を受けた: {quest_text(self.world, quest)}")
        else:
            quest = patrol(self.world, self.state)
        self.state["quest"] = quest

    def fail_quest(self, why: str) -> None:
        quest = self.state["quest"]
        self.state["quest"] = None
        if quest and quest["type"] != "patrol":
            self.state["season_stats"]["quests_failed"] += 1
            self.log(f"依頼失敗（{why}）: {quest_text(self.world, quest)}")

    # 野外 --------------------------------------------------------------------------------
    def field_event(self, place: str) -> bool:
        """野外の出来事。力尽きたら False。"""
        field, quest = self.world["places"][place], self.state["quest"]
        here = quest is not None and quest["place"] == place and quest["progress"] < quest["count"]
        events = dict(field["events"])
        if here:  # 目的の場所では敵を探して回る
            events["battle"] = events.get("battle", 0) * self.cfg["hunt_battle_boost"]
        kinds, weights = zip(*events.items())
        kind = self.rng.choices(kinds, weights)[0]
        if kind == "battle":
            target = quest["monster"] if here and quest["type"] == "hunt" else None
            mid = target if target and self.rng.random() < 0.5 else self.rng.choice(field["monsters"])
            if not self.battle(self.world["monsters"][mid]):
                return False
            if here and (quest["type"] == "patrol" or mid == quest["monster"]):
                quest["progress"] += 1
                if quest["progress"] >= quest["count"] and quest["type"] == "hunt":
                    self.log(f"討伐の目標を果たした（{quest['count']}体）")
        elif kind == "treasure":
            iid = self.draw(field.get("treasure", self.cfg["treasure"]), field["level"][1])
            if iid:
                self.obtain(iid, "宝箱から")
            elif self.rng.random() < 0.3:
                self.hero["potions"] += 1
                self.log("宝箱から回復薬を見つけた")
            else:
                gold = self.rng.randint(5, 15) * field["level"][0]
                self.hero["gold"] += gold
                self.log(f"宝箱から{gold}Gを見つけた")
        else:
            self.log(self.rng.choice(field.get("scenery") or self.cfg["scenery"]))
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
    def move_toward(self, target: str) -> bool:
        """target へ向かって 1 行動ぶん進む。力尽きたら False。"""
        state = self.state
        road = state["road"]
        if road is None:
            nxt = self.world["next"][state["place"]][target]
            road = {"to": nxt, "left": self.world["roads"][(state["place"], nxt)]}
        road["left"] -= 1
        if road["left"] > 0:  # まだ道の途中
            state["road"] = road
            side = road["to"] if self.world["places"][road["to"]]["type"] == "field" else state["place"]
            return self.field_event(side) if self.world["places"][side]["type"] == "field" else True
        state.update(place=road["to"], road=None)
        if self.world["places"][road["to"]]["type"] == "town":
            self.visit_town(road["to"])
            return True
        return self.field_event(road["to"])

    def turn_back(self) -> None:
        """道の途中で、引き返したほうが町に早く着くなら引き返す。"""
        state, world = self.state, self.world
        road, origin = state["road"], state["place"]
        if world["places"][road["to"]]["type"] == "town":
            return
        total = world["roads"][(origin, road["to"])]
        town_dist = {p: min(world["dist"][p][t] for t in world["towns"]) for p in (origin, road["to"])}
        back = total - road["left"] + town_dist[origin]
        forward = road["left"] + town_dist[road["to"]]
        if back < forward:
            state.update(place=road["to"], road={"to": origin, "left": total - road["left"]})
            self.log(f"HP が心もとないので{world['places'][origin]['name']}へ引き返した")

    def act(self) -> bool:
        """1 行動。力尽きたら False。"""
        state, world, hero = self.state, self.world, self.hero
        for _ in range(5):  # 町での受注・報告は行動回数を使わない
            low = hero["hp"] < self.stats["max_hp"] * self.cfg["retreat_hp_rate"] and not hero["potions"]
            if state["road"]:
                if low:
                    self.turn_back()
                return self.move_toward(state["road"]["to"])
            place = state["place"]
            in_town = world["places"][place]["type"] == "town"
            if low and not in_town:
                return self.move_toward(nearest_town(world, place))
            quest = state["quest"]
            if quest is None:
                if in_town:
                    self.take_quest(place)
                    continue
                return self.move_toward(nearest_town(world, place))
            if quest["progress"] >= quest["count"]:
                if quest["report"] is None:  # 見回りは報告しない
                    self.log(f"{world['places'][quest['place']]['name']}の見回りを終えた")
                    state["quest"] = None
                    continue
                if place == quest["report"]:
                    self.visit_town(place)
                    continue
                return self.move_toward(quest["report"])
            if place == quest["place"]:
                return self.field_event(place)
            return self.move_toward(quest["place"])
        return True


def roll_actions(world: dict, rng: random.Random) -> int:
    actions, weights = zip(*world["season"]["action_weights"])
    return rng.choices(actions, weights)[0]


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


def advance(world: dict, state: dict, today: date, actions: int | None = None) -> bool:
    """today の分を進める（actions を省くと乱数で決める）。処理済み（last_date 以前の日付）なら何もせず False。"""
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
    day = Day(world, state, today, rng)
    if new_season:
        day.log(f"シーズン{season}が始まった（レベルは 1 に戻り、持ち物はそのまま）")
    if actions is None:
        actions = roll_actions(world, rng)
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
