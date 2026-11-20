import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import world as W  # noqa: E402

RAW = W.load_raw()


def errors_after(change) -> list[str]:
    raw = copy.deepcopy(RAW)
    change(raw)
    return W.validate(raw)


class DataTest(unittest.TestCase):
    """world/ の下のデータに誤りがないこと（手で編集したら、まずこのテストが通るか確かめる）。"""

    def test_world_data_is_valid(self):
        self.assertEqual(W.validate(RAW), [])

    def test_load_world_builds_routes(self):
        world = W.load_world()
        start = world["starts"][0]
        for pid in world["places"]:
            self.assertIn(pid, world["dist"][start])
        self.assertEqual(world["dist"][start][start], 0)


class ValidateTest(unittest.TestCase):
    """誤ったデータを検出できること。"""

    def assertError(self, change, words):
        errors = errors_after(change)
        self.assertTrue(any(all(w in e for w in words) for e in errors), errors)

    def test_unknown_monster(self):
        self.assertError(lambda r: r["map"]["places"][5]["monsters"].append("nobody"), ["nobody"])

    def test_duplicate_id(self):
        self.assertError(lambda r: r["monsters"].append(dict(r["monsters"][0])), ["重複", "slime"])

    def test_disconnected_place(self):
        def change(r):
            r["map"]["places"].append({"id": "island", "type": "town", "name": "離れ小島"})
        self.assertError(change, ["island", "行けません"])

    def test_bad_road(self):
        self.assertError(lambda r: r["map"]["roads"].append({"from": "town_hajimari", "to": "x", "distance": 0}),
                         ["distance"])

    def test_bad_level_range(self):
        def change(r):
            r["map"]["places"][5]["level"] = [5, 2]
        self.assertError(change, ["level"])

    def test_start_must_be_a_town(self):
        def change(r):
            r["map"]["starts"] = ["north_plain"]
        self.assertError(change, ["starts", "north_plain"])

    def test_shop_item_needs_price(self):
        def change(r):
            r["map"]["places"][0]["shop"].append("blue_flame_sword")
        self.assertError(change, ["blue_flame_sword", "price"])

    def test_bad_drop_table(self):
        def change(r):
            r["quests"][0]["reward"]["item"] = [["mythic", 1]]
        self.assertError(change, ["mythic"])

    def test_too_strong_item(self):
        def change(r):
            r["items"].append({"id": "god_sword", "name": "神の剣", "slot": "weapon", "rarity": "legendary", "atk": 99})
        self.assertError(change, ["強すぎ"])

    def test_season_start_day_must_exist_in_every_month(self):
        def change(r):
            r["season"]["start"] = "2027-01-31"
        self.assertError(change, ["start", "1〜28"])

    def test_season_keys_are_checked(self):
        def typo(r):
            r["season"]["hunt_rang"] = r["season"].pop("hunt_range")
        self.assertError(typo, ["hunt_range"])

        def bad_hero(r):
            del r["season"]["hero"]["atk"]
        self.assertError(bad_hero, ["hero", "atk"])

        def bad_rate(r):
            r["season"]["potion"]["heal_rate"] = 2
        self.assertError(bad_rate, ["potion", "heal_rate"])

        def no_texts(r):
            r["season"]["rest"] = []
        self.assertError(no_texts, ["rest"])

    def test_bad_region(self):
        def change(r):
            r["map"]["places"][0]["region"] = ""
        self.assertError(change, ["region"])

    def test_bad_season(self):
        def change(r):
            r["season"]["months"] = 0
        self.assertError(change, ["months"])


if __name__ == "__main__":
    unittest.main()
