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


def play(days, hero_id="h1", actions=None, first=START):
    state = G.new_state(WORLD, hero_id)
    for i in range(days):
        G.advance(WORLD, state, first + timedelta(days=i), actions)
    return state


def day_of(state, d=START, seed="t"):
    return G.Day(WORLD, state, d, random.Random(seed))


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
        self.assertEqual((state["hero"]["level"], state["place"], state["quest"]), (1, WORLD["start"], None))
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
        state["quest"] = G.patrol(WORLD, state) | {"type": "hunt", "monster": "slime", "report": WORLD["start"],
                                                  "town": WORLD["start"], "deadline": START.isoformat(),
                                                  "reward": {"exp": 1, "gold": 1, "item": None}}
        G.advance(WORLD, state, START + timedelta(days=1), 0)
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["quests_failed"], 1)


class QuestTest(unittest.TestCase):
    def test_board_is_shared_and_deterministic(self):
        town = WORLD["start"]
        self.assertEqual(G.board(WORLD, town, START, 1), G.board(WORLD, town, START, 1))
        self.assertTrue(G.board(WORLD, town, START, 1))

    def test_board_quests_are_reachable_and_well_formed(self):
        for town in WORLD["towns"]:
            for i in range(20):
                for q in G.board(WORLD, town, START + timedelta(days=i), 1):
                    self.assertIn(q["place"], WORLD["places"])
                    self.assertEqual(WORLD["places"][q["report"]]["type"], "town")
                    self.assertGreater(q["deadline"], START.isoformat())
                    if q["type"] == "hunt":
                        self.assertIn(q["monster"], WORLD["places"][q["place"]]["monsters"])

    def test_hunt_report_gives_reward(self):
        state = play(1, actions=0)
        quest = next(q for i in range(30) for q in G.board(WORLD, WORLD["start"], START + timedelta(days=i), 1)
                     if q["type"] == "hunt")
        quest["progress"] = quest["count"]
        state["quest"] = quest
        gold = state["hero"]["gold"]
        day_of(state).visit_town(WORLD["start"])
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["quests_done"], 1)
        self.assertGreaterEqual(state["hero"]["gold"], gold + quest["reward"]["gold"] - 200)  # 買い物をしても報酬は入る

    def test_deliver_completes_on_arrival(self):
        state = play(1, actions=0)
        to = WORLD["towns"][1]
        state["quest"] = {"type": "deliver", "name": "配達", "template": "deliver", "town": WORLD["start"],
                          "place": to, "report": to, "monster": None, "count": 1, "progress": 0, "level": [1, 5],
                          "deadline": "2099-01-01", "reward": {"exp": 5, "gold": 5, "item": None}}
        day_of(state).visit_town(to)
        self.assertIsNone(state["quest"])
        self.assertEqual(state["home_town"], to)

    def test_patrol_matches_level(self):
        state = G.new_state(WORLD)
        low = G.patrol(WORLD, state)
        state["hero"]["level"] = 30
        high = G.patrol(WORLD, state)
        self.assertLess(WORLD["places"][low["place"]]["level"][0], WORLD["places"][high["place"]]["level"][0])

    def test_suitable(self):
        q = {"level": [10, 15]}
        self.assertFalse(G.suitable(q, 1))
        self.assertTrue(G.suitable(q, 10))
        self.assertFalse(G.suitable(q, 30))


class BattleAndItemTest(unittest.TestCase):
    def test_fall_fails_quest_and_returns_to_town(self):
        state = play(1, actions=0)
        state["quest"] = G.patrol(WORLD, state) | {"type": "hunt", "monster": "slime", "report": WORLD["start"]}
        state["place"] = "north_plain"
        day = day_of(state)
        day.fall()
        self.assertEqual(state["place"], state["home_town"])
        self.assertIsNone(state["quest"])
        self.assertEqual(state["season_stats"]["deaths"], 1)
        self.assertEqual(state["hero"]["hp"], G.stats(WORLD, state)["max_hp"])

    def test_strong_monster_beats_a_new_hero(self):
        state = G.new_state(WORLD)
        state["hero"]["potions"] = 0
        self.assertFalse(day_of(state).battle(WORLD["monsters"]["frost_giant"]))

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
                         (WORLD["start"], WORLD["start"], None, None))
        self.assertNotIn("gone_item", loaded["inventory"])

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
        self.assertTrue(15 <= self.median(levels) <= 30, sorted(levels))

    def test_quest_success_rate(self):
        done = sum(s["season_stats"]["quests_done"] for s in self.results)
        failed = sum(s["season_stats"]["quests_failed"] for s in self.results)
        self.assertTrue(0.6 <= done / (done + failed) <= 0.97, (done, failed))

    def test_rare_items_are_rare_but_not_absent(self):
        rares = [len(s["season_stats"]["rares"]) for s in self.results]
        self.assertTrue(1 <= self.median(rares) <= 6, sorted(rares))

    def test_heroes_drift_apart(self):
        self.assertGreater(len({s["place"] for s in self.results}), 2)


if __name__ == "__main__":
    unittest.main()
