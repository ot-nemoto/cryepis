import json
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import game as G  # noqa: E402
import world as W  # noqa: E402

WORLD = W.load_world()
START = date.fromisoformat(WORLD["season"]["start"])
TOWN = WORLD["starts"][0]


def play(days, hero_id="h1", actions=None, first=START):
    state = G.new_state(WORLD, hero_id)
    for i in range(days):
        G.advance(WORLD, state, first + timedelta(days=i), actions)
    return state


def day_of(state, d=START, seed="t"):
    return G.Day(WORLD, state, d, random.Random(seed))


def a_hunt(town=TOWN):
    """town の掲示板から討伐の依頼を 1 つ。"""
    return next(q for i in range(60) for q in G.board(WORLD, town, START + timedelta(days=i), 1) if q["type"] == "hunt")


STRONG = {"id": "x", "name": "魔王", "level": 30, "hp": 999, "atk": 99, "def": 50, "exp": 1, "gold": 1, "terrain": "plain"}


class SeasonTest(unittest.TestCase):
    def test_season_of(self):
        months = WORLD["season"]["months"]
        self.assertEqual(G.season_of(WORLD, START), 1)
        self.assertEqual(G.season_of(WORLD, START - timedelta(days=30)), 1)  # 始まる前はシーズン 1 に含める
        y, m = divmod(START.month - 1 + months, 12)
        nxt = date(START.year + y, m + 1, START.day)
        self.assertEqual(G.season_of(WORLD, nxt - timedelta(days=1)), 1)
        self.assertEqual(G.season_of(WORLD, nxt), 2)

    def test_season_range(self):
        first, last = G.season_range(WORLD, 1)
        self.assertEqual(first, START)
        self.assertEqual(G.season_of(WORLD, last), 1)
        self.assertEqual(G.season_of(WORLD, last + timedelta(days=1)), 2)
        self.assertEqual(G.season_range(WORLD, 2)[0], last + timedelta(days=1))

    def test_new_season_keeps_only_items(self):
        state = play(3)
        state["inventory"]["blue_flame_sword"] = 1
        state["hero"].update(level=15, gold=999)
        last = START + timedelta(days=2)
        state["last_date"] = last.isoformat()
        nxt = START
        while G.season_of(WORLD, nxt) == 1:
            nxt += timedelta(days=1)
        G.advance(WORLD, state, nxt, 0)
        self.assertEqual(state["season"], 2)
        self.assertEqual(state["season_day"], 1)
        self.assertEqual((state["hero"]["level"], state["place"], state["quest"]), (1, G.start_town(WORLD, state["id"], 2), None))
        self.assertIn("blue_flame_sword", state["inventory"])
        (record,) = state["seasons"]
        self.assertEqual((record["season"], record["level"], record["days"]), (1, 15, 3))
        self.assertIn("シーズン2が始まった", state["last"]["events"][0])


class AdvanceTest(unittest.TestCase):
    def test_same_day_is_processed_once_and_deterministic(self):
        a, b = play(10), play(10)
        self.assertEqual(a, b)
        self.assertFalse(G.advance(WORLD, a, START + timedelta(days=9)))
        self.assertFalse(G.advance(WORLD, a, START))

    def test_hero_id_changes_the_dice(self):
        self.assertNotEqual(play(10, "h1")["journal"], play(10, "h2")["journal"])

    def test_actions_are_the_same_every_day(self):
        state = play(10)
        per_day = WORLD["season"]["actions_per_day"]
        self.assertEqual({e["actions"] for e in state["journal"]}, {per_day})

    def test_rest_day_heals_and_does_not_move(self):
        state = play(1, actions=0)
        state["hero"]["hp"] = 1
        place = state["place"]
        G.advance(WORLD, state, START + timedelta(days=1), 0)
        self.assertGreater(state["hero"]["hp"], 1)
        self.assertEqual(state["place"], place)

    def test_first_day_shops_and_takes_a_quest(self):
        state = play(1, actions=1)
        self.assertTrue(state["inventory"])  # 最初の町で装備を買う
        self.assertIsNotNone(state["quest"])

    def test_journal_is_capped(self):
        self.assertEqual(len(play(G.JOURNAL_DAYS + 5)["journal"]), G.JOURNAL_DAYS)

    def test_expired_quest_fails(self):
        state = play(1, actions=0)
        state["quest"] = a_hunt() | {"deadline": START.isoformat()}
        G.advance(WORLD, state, START + timedelta(days=1), 0)
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["quests_failed"], 1)


class QuestTest(unittest.TestCase):
    def test_board_is_shared_and_deterministic(self):
        town = TOWN
        self.assertEqual(G.board(WORLD, town, START, 1), G.board(WORLD, town, START, 1))
        self.assertTrue(G.board(WORLD, town, START, 1))

    def test_board_quests_are_reachable_and_well_formed(self):
        for town in WORLD["towns"]:
            for i in range(20):
                for q in G.board(WORLD, town, START + timedelta(days=i), 1):
                    self.assertIn(q["place"], WORLD["towns"])
                    self.assertIn(q["report"], WORLD["towns"])
                    self.assertGreater(q["deadline"], START.isoformat())
                    self.assertTrue(G.quest_ok(WORLD, q))
                    if q["type"] == "hunt":  # 討伐は町から出る道で、その道に出る魔物
                        self.assertEqual(q["report"], town)
                        pool = [m for m, _ in WORLD["road_info"][(town, q["place"])]["monsters"]]
                        self.assertIn(q["monster"], pool)

    def test_hunt_report_gives_reward(self):
        state = play(1, actions=0)
        quest = a_hunt()
        quest["progress"] = quest["count"]
        state["quest"] = quest
        gold = state["hero"]["gold"]
        day_of(state).visit_town(TOWN)
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["quests_done"], 1)
        self.assertGreaterEqual(state["hero"]["gold"], gold + quest["reward"]["gold"] - 200)  # 買い物をしても報酬は入る

    def test_deliver_completes_on_arrival(self):
        state = play(1, actions=0)
        to = WORLD["towns"][1]
        state["quest"] = {"type": "deliver", "name": "配達", "template": "deliver", "town": TOWN,
                          "place": to, "report": to, "monster": None, "count": 1, "progress": 0, "level": [1, 5],
                          "deadline": "2099-01-01", "reward": {"exp": 5, "gold": 5, "item": None}}
        day_of(state).visit_town(to)
        self.assertIsNone(state["quest"])
        self.assertEqual(state["home_town"], to)

    def test_same_quest_is_not_taken_twice_a_day(self):
        state = play(1, actions=0)
        state["hero"]["level"] = 99  # どの依頼も合わない…ことがないよう、掲示板の依頼をすべて受けられるようにする
        quests = G.board(WORLD, TOWN, START, 1)
        state["hero"]["level"] = max(q["level"][0] for q in quests)
        day = day_of(state)
        ids = []
        for _ in range(len(quests) + 1):
            state["quest"] = None
            day.take_quest(TOWN)
            if state["quest"]["type"] != "patrol":
                ids.append(state["quest"]["id"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(state["quest"]["type"], "patrol")  # 受けられる依頼が尽きたら見回り

    def test_taken_quests_reset_the_next_day(self):
        state = play(1, actions=1)
        self.assertTrue(state["taken"])
        G.advance(WORLD, state, START + timedelta(days=1), 0)
        self.assertEqual(state["taken"], [])

    def test_patrol_travels_to_a_neighbor(self):
        state = G.new_state(WORLD)
        for seed in range(10):
            trip = G.patrol(WORLD, state, random.Random(seed))
            self.assertIn(trip["place"], WORLD["neighbors"][state["place"]])
            self.assertIsNone(trip["report"])

    def test_patrol_ends_on_arrival(self):
        state = play(1, actions=0)
        to = WORLD["neighbors"][state["place"]][0]
        state["quest"] = G.patrol(WORLD, state, random.Random(0)) | {"place": to}
        day_of(state).visit_town(to)
        self.assertEqual(state["quest"]["progress"], 1)

    def test_suitable(self):
        q = {"level": [10, 10]}
        self.assertFalse(G.suitable(q, 1))
        self.assertTrue(G.suitable(q, 10))
        self.assertTrue(G.suitable(q, 30))  # 魔物の強さはどこでも同じなので、弱い依頼も受ける


class BattleAndItemTest(unittest.TestCase):
    def test_fall_fails_quest_and_returns_to_town(self):
        state = play(1, actions=0)
        state["quest"] = a_hunt()
        state["road"] = {"to": state["quest"]["place"], "left": 1}
        day = day_of(state)
        day.fall()
        self.assertEqual(state["place"], state["home_town"])
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["deaths"], 1)
        self.assertEqual(state["hero"]["hp"], G.stats(WORLD, state)["max_hp"])

    def low_hp_on_road(self, origin, to, left):
        state = G.new_state(WORLD)
        state.update(season=1, place=origin, road={"to": to, "left": left})
        state["hero"].update(hp=1, potions=0)
        return state

    def test_turns_back_on_the_road_when_hp_is_low(self):
        # シルウェン →（4 行動）→ ヤルカ の 1 歩目。引き返せば 1、進めば 3
        state = self.low_hp_on_road("silwen", "yarka", 3)
        day = day_of(state)
        day.turn_back()
        self.assertEqual((state["place"], state["road"]), ("yarka", {"to": "silwen", "left": 1}))
        self.assertIn("引き返した", day.events[0])

    def test_keeps_going_when_turning_back_is_not_closer(self):
        state = self.low_hp_on_road("silwen", "yarka", 1)  # あと 1 行動で着く
        day = day_of(state)
        day.turn_back()
        self.assertEqual((state["place"], state["road"]["to"]), ("silwen", "yarka"))
        self.assertEqual(day.events, [])

    def test_start_town_is_random_per_hero_and_season(self):
        towns = {G.start_town(WORLD, f"h{i}", None) for i in range(30)}
        self.assertGreater(len(towns), 3)
        self.assertTrue(towns <= set(WORLD["starts"]))
        self.assertEqual(G.start_town(WORLD, "h1", 1), G.start_town(WORLD, "h1", 1))
        state = G.new_state(WORLD, "h7")
        self.assertEqual(state["place"], G.start_town(WORLD, "h7", None))
        G.advance(WORLD, state, START, 0)
        self.assertEqual(state["place"], G.start_town(WORLD, "h7", None))  # 最初のシーズンは登録した町から

    def test_strong_monster_beats_a_new_hero(self):
        state = G.new_state(WORLD)
        state["hero"]["potions"] = 0
        self.assertFalse(day_of(state).battle(STRONG))

    def test_best_item_is_equipped(self):
        state = G.new_state(WORLD)
        state["inventory"] = {"wooden_sword": 1, "iron_sword": 1, "lucky_charm": 1}
        worn = G.equipment(WORLD, state)
        self.assertEqual((worn["weapon"], worn["armor"], worn["accessory"]), ("iron_sword", None, "lucky_charm"))
        s = G.stats(WORLD, state)
        self.assertEqual(s["atk"], state["hero"]["base_atk"] + 5)
        self.assertEqual(s["max_hp"], state["hero"]["max_hp"] + 5)

    def test_rare_item_is_recorded(self):
        state = G.new_state(WORLD)
        day = day_of(state)
        day.obtain("blue_flame_sword", "宝箱から")
        self.assertEqual(state["season_stats"]["rares"], ["blue_flame_sword"])
        self.assertIn("★「蒼炎の剣」", day.events[0])
        self.assertIn("装備した", day.events[0])

    def test_draw_respects_drop_level(self):
        day = day_of(G.new_state(WORLD))
        for _ in range(50):
            iid = day.draw([["rare", 1]], 1)
            self.assertTrue(iid is None or WORLD["items"][iid].get("drop_level", 1) <= 1)
        self.assertIsNone(day.draw([["none", 1]], 99))


class RoadTest(unittest.TestCase):
    def test_every_step_on_a_road_has_an_event_and_arrival_visits_the_town(self):
        state = play(1, actions=0)
        to = WORLD["neighbors"][state["place"]][0]
        state["quest"] = G.patrol(WORLD, state, random.Random(0)) | {"place": to}
        day = day_of(state)
        steps = WORLD["roads"][(state["place"], to)]
        for _ in range(steps):
            before = len(day.events)
            self.assertTrue(day.act())
            self.assertGreater(len(day.events), before)  # 1 行動ごとに何か起きる
        self.assertEqual((state["place"], state["road"]), (to, None))
        self.assertTrue(any("に着いた" in e for e in day.events))

    def test_deliver_to_the_town_you_are_in_completes(self):
        state = play(1, actions=0)
        town = state["place"]
        state["quest"] = {"type": "deliver", "name": "配達", "template": "deliver", "town": "x", "place": town,
                          "report": town, "monster": None, "count": 1, "progress": 0, "level": [1, 1],
                          "deadline": "2099-01-01", "reward": {"exp": 1, "gold": 1, "item": None}}
        day = day_of(state)
        self.assertTrue(day.act())
        self.assertEqual(state["season_stats"]["quests_done"], 1)

    def test_hunt_goes_back_and_forth_on_its_road(self):
        state = play(1, actions=0)
        quest = a_hunt(state["place"]) | {"count": 99, "deadline": "2099-01-01"}
        state["quest"] = quest
        day = day_of(state)
        visited = set()
        for _ in range(40):
            if not day.act():
                day.fall()
            visited.add(state["place"])
            if state["road"]:
                self.assertIn(state["road"]["to"], (quest["report"], quest["place"]))
        self.assertEqual(visited, {quest["report"], quest["place"]})
        self.assertGreater(state["quest"]["progress"], 0)

    def test_sea_route_has_sea_monsters_and_scenery(self):
        (a, b), _ = next((k, i) for k, i in WORLD["road_info"].items() if i["kind"] == "sea")
        state = G.new_state(WORLD)
        state.update(season=1, place=a)
        day = day_of(state, seed="sea")
        for _ in range(30):
            day.road_event(a, b)
        sea_names = {m["name"] for m in WORLD["monsters"].values() if m["terrain"] == "sea"}
        fought = [e for e in day.events if "を倒した" in e]
        self.assertTrue(fought)
        self.assertTrue(all(any(n in e for n in sea_names) for e in fought), fought)


class TraitTest(unittest.TestCase):
    def fight(self, trait, hp=None, potions=3, seed="t"):
        state = G.new_state(WORLD)
        state["hero"]["potions"] = potions
        if hp is not None:
            state["hero"]["hp"] = hp
        monster = dict(WORLD["monsters"]["wild_dog"]) | ({"trait": trait} if trait else {})
        day = day_of(state, seed=seed)
        return day.battle(monster), state, day

    def test_name_shows_the_trait(self):
        _, _, day = self.fight("poison")
        self.assertIn("野犬〔毒〕を倒した", day.events[0])

    def test_poison_hurts_after_the_battle(self):
        won, state, day = self.fight("poison")
        self.assertTrue(won)
        self.assertTrue(any("毒で HP が" in e for e in day.events))
        self.assertGreaterEqual(state["hero"]["hp"], 1)

    def test_first_strike_attacks_before_the_hero(self):
        won, _, day = self.fight("first_strike", hp=1, potions=0)
        self.assertFalse(won)  # HP 1 では、先に攻撃されて負ける
        won, _, _ = self.fight(None, hp=1, potions=0, seed="x")
        self.assertIsInstance(won, bool)

    def test_thief_can_steal_gold(self):
        stolen = 0
        for seed in range(20):
            _, state, day = self.fight("thief", seed=str(seed))
            stolen += any("盗まれた" in e for e in day.events)
        self.assertGreater(stolen, 0)

    def test_traits_change_the_fight(self):
        def hp_lost(trait):
            total = 0
            for seed in range(30):
                state = G.new_state(WORLD)
                state["hero"].update(max_hp=999, hp=999)
                monster = dict(WORLD["monsters"]["wild_dog"]) | ({"trait": trait} if trait else {})
                day_of(state, seed=str(seed)).battle(monster)
                total += 999 - state["hero"]["hp"]
            return total
        plain = hp_lost(None)
        self.assertGreater(hp_lost("fierce"), plain)   # 凶暴は攻撃が強い
        self.assertGreater(hp_lost("swarm"), plain)    # 群れは攻撃の回数が多い
        self.assertGreater(hp_lost("armored"), plain)  # 硬いので戦いが長引く


class StateFileTest(unittest.TestCase):
    def test_save_and_load(self):
        state = play(5)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h1.json"
            G.save_state(state, path)
            self.assertEqual(G.load_state(WORLD, path), state)

    def test_load_repairs_references_removed_from_the_world(self):
        state = play(5)
        state.update(place="gone", home_town="gone", road={"to": "gone", "left": 1})
        state["inventory"]["gone_item"] = 1
        state["quest"] = {"place": "gone", "report": None}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h1.json"
            G.save_state(state, path)
            loaded = G.load_state(WORLD, path)
        self.assertEqual((loaded["place"], loaded["home_town"], loaded["road"], loaded["quest"]),
                         (G.start_town(WORLD, "h1", 1),) * 2 + (None, None))
        self.assertNotIn("gone_item", loaded["inventory"])

    def test_load_drops_removed_items_from_rare_records(self):
        state = play(2)
        state["season_stats"]["rares"] = ["blue_flame_sword", "gone_item"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h1.json"
            G.save_state(state, path)
            loaded = G.load_state(WORLD, path)
        self.assertEqual(loaded["season_stats"]["rares"], ["blue_flame_sword"])

    def test_load_drops_quests_whose_place_changed_type(self):
        state = play(2)
        base = {"name": "x", "template": "t", "town": TOWN, "report": TOWN, "count": 1,
                "progress": 0, "level": [1, 5], "deadline": "2099-01-01", "reward": None}
        near = WORLD["neighbors"][TOWN][0]
        far = next(t for t in WORLD["towns"] if t != TOWN and (TOWN, t) not in WORLD["roads"])
        cases = [({"type": "hunt", "place": near, "monster": "wild_dog"}, True),
                 ({"type": "hunt", "place": far, "monster": "wild_dog"}, False),       # 道がない
                 ({"type": "hunt", "place": near, "monster": "slime"}, False),         # 魔物がいない
                 ({"type": "deliver", "place": "jade_tomb", "monster": None}, False),  # ダンジョンへは配達しない
                 ({"type": "deliver", "place": far, "monster": None}, True),
                 ({"type": "teleport", "place": near, "monster": None}, False)]
        no_report = {k: v for k, v in (base | {"type": "hunt", "place": near, "monster": "wild_dog"}).items()
                     if k != "report"}
        self.assertFalse(G.quest_ok(WORLD, no_report))  # 報告先のない討伐は捨てる（エラーにしない）
        for quest, ok in cases:
            self.assertEqual(G.quest_ok(WORLD, base | quest), ok, quest)

    def test_old_format_starts_over(self):
        old = {"id": "h2", "name": "アリス", "day": 37, "lap": 1, "pos": 26, "hero": {"level": 7}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h2.json"
            path.write_text(json.dumps(old), encoding="utf-8")
            loaded = G.load_state(WORLD, path)
        self.assertEqual(loaded, G.new_state(WORLD, "h2", "アリス"))


class BalanceTest(unittest.TestCase):
    """勇者を何人か 1 シーズン動かして、狙いの範囲に収まるか確かめる（数値は world/ で調整する）。"""

    @classmethod
    def setUpClass(cls):
        cls.results = []
        for i in range(30):
            state, d = G.new_state(WORLD, f"b{i}"), START
            while G.season_of(WORLD, d) == 1:
                G.advance(WORLD, state, d)
                d += timedelta(days=1)
            cls.results.append(state)

    def median(self, values):
        return sorted(values)[len(values) // 2]

    def test_season_end_level(self):
        levels = [s["hero"]["level"] for s in self.results]
        self.assertTrue(6 <= self.median(levels) <= 20, sorted(levels))

    def test_quest_success_rate(self):
        done = sum(s["season_stats"]["quests_done"] for s in self.results)
        failed = sum(s["season_stats"]["quests_failed"] for s in self.results)
        self.assertTrue(done >= 10 * len(self.results), done)  # 依頼をこなしている（1 シーズンに 1 人 10 件以上）
        self.assertTrue(0.6 <= done / (done + failed), (done, failed))

    def test_rare_items_are_rare_but_not_absent(self):
        rares = [len(s["season_stats"]["rares"]) for s in self.results]
        self.assertTrue(1 <= self.median(rares) <= 6, sorted(rares))

    def test_heroes_drift_apart(self):
        self.assertGreater(len({s["place"] for s in self.results}), 2)


if __name__ == "__main__":
    unittest.main()
