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
        for pid in world["towns"]:  # 町にはどこからでも陸路・航路で行ける
            self.assertIn(pid, world["dist"][start])
        self.assertEqual(world["dist"][start][start], 0)
        for pid in world["dungeons"]:  # ダンジョンへは（まだ）行けない
            self.assertNotIn(pid, world["dist"][start])

    def test_every_road_has_monsters(self):
        world = W.load_world()
        for key, info in world["road_info"].items():
            self.assertTrue(info["monsters"], key)
            for mid, weight in info["monsters"]:
                self.assertIn(world["monsters"][mid]["terrain"], info["terrain"])
                self.assertGreater(weight, 0)

    def test_sea_routes_have_sea_monsters(self):
        world = W.load_world()
        sea = [k for k, i in world["road_info"].items() if i["kind"] == "sea"]
        self.assertTrue(sea)
        for key in sea:
            self.assertTrue(all(world["monsters"][m]["terrain"] == "sea" for m, _ in world["road_info"][key]["monsters"]))


class ValidateTest(unittest.TestCase):
    """誤ったデータを検出できること。"""

    def assertError(self, change, words):
        errors = errors_after(change)
        self.assertTrue(any(all(w in e for w in words) for e in errors), errors)

    def test_unknown_terrain_and_trait(self):
        self.assertError(lambda r: r["map"]["roads"][0]["terrain"].append(["lava_sea", 5]), ["terrain"])
        self.assertError(lambda r: r["monsters"][0].update(terrain="moon"), ["wild_dog", "terrain"])
        self.assertError(lambda r: r["monsters"][0].update(trait="laser"), ["wild_dog", "trait"])

    def test_road_terrain_needs_a_monster(self):
        def change(r):
            r["monsters"] = [m for m in r["monsters"] if m["terrain"] != "sea"]
        self.assertError(change, ["terrain", "魔物がいません"])

    def test_duplicate_id(self):
        self.assertError(lambda r: r["monsters"].append(dict(r["monsters"][0])), ["重複", "wild_dog"])

    def test_disconnected_place(self):
        def change(r):
            r["map"]["places"].append({"id": "island", "type": "town", "name": "離れ小島"})
        self.assertError(change, ["island", "行けません"])

    def test_bad_road(self):
        self.assertError(lambda r: r["map"]["roads"].append({"from": "aurea", "to": "x", "distance": 0}),
                         ["distance"])

    def test_road_kinds_connect_the_right_places(self):
        def land_to_dungeon(r):
            r["map"]["roads"].append({"from": "aurea", "to": "jade_tomb", "distance": 1, "kind": "land",
                                      "terrain": [["plain", 100]]})
        self.assertError(land_to_dungeon, ["町どうし"])

        def trail_between_towns(r):
            r["map"]["roads"].append({"from": "aurea", "to": "zafra", "distance": 1, "kind": "trail"})
        self.assertError(trail_between_towns, ["trail", "ダンジョン"])

    def test_start_must_be_a_town(self):
        def change(r):
            r["map"]["starts"] = ["jade_tomb"]
        self.assertError(change, ["starts", "jade_tomb"])

    def test_shop_item_needs_price(self):
        def change(r):
            r["map"]["places"][0]["shop"].append("blue_flame_sword")
        self.assertError(change, ["blue_flame_sword", "price"])

    def test_bad_drop_table(self):
        def change(r):
            r["quest_ranks"][0]["item"] = [["mythic", 1]]
        self.assertError(change, ["mythic"])

    def test_drop_level_is_no_longer_used(self):
        self.assertError(lambda r: r["items"][5].update(drop_level=5), ["drop_level"])

    def test_rare_items_need_quest_towns(self):
        self.assertError(lambda r: r["items"][-1].pop("towns"), ["dragon_heart_stone", "towns"])
        self.assertError(lambda r: r["items"][-1].update(towns=["jade_tomb"]), ["towns", "jade_tomb"])
        self.assertError(lambda r: r["items"][0].update(towns=["aurea"]), ["wooden_sword", "common"])
        for bad in (5, "aurea", [["aurea"]]):  # 型が違っても落ちずにエラーを返す
            self.assertError(lambda r, bad=bad: r["items"][-1].update(towns=bad), ["dragon_heart_stone", "towns"])

    def test_every_town_needs_a_rare(self):
        def change(r):
            for it in r["items"]:
                if it["rarity"] == "rare" and "vike" in it["towns"]:
                    it["towns"].remove("vike")
        self.assertError(change, ["vike", "rare"])

    def test_treasure_cannot_hold_rares(self):
        self.assertError(lambda r: r["season"]["treasure"].append(["rare", 1]), ["treasure", "rare"])
        self.assertError(lambda r: r["season"]["treasure"].append(["star_sword", 1]), ["treasure", "star_sword"])

    def test_every_town_has_its_own_specialty(self):
        shops = {p["id"]: set(p["shop"]) for p in RAW["map"]["places"] if p["type"] == "town"}
        for town, items in shops.items():
            only_here = {i for i in items if all(i not in other for t, other in shops.items() if t != town)}
            self.assertTrue(only_here, town)

    def test_every_town_quest_has_some_rare(self):
        towns = {tid for i in RAW["items"] if i["rarity"] == "rare" for tid in i["towns"]}
        self.assertEqual(towns, {p["id"] for p in RAW["map"]["places"] if p["type"] == "town"})

    def test_every_slot_has_items_in_a_shop(self):
        sold = {sid for p in RAW["map"]["places"] for sid in p.get("shop", [])}
        items = {i["id"]: i for i in RAW["items"]}
        self.assertEqual({items[i]["slot"] for i in sold}, set(W.SLOTS))

    def test_town_quests_are_checked(self):
        def town_quest(**change):
            def apply(r):
                r["town_quests"]["aurea"][0].update(change)
            return apply
        self.assertError(town_quest(to="zafra"), ["aurea_1", "直接つながる"])         # 道がない
        self.assertError(town_quest(monster="ice_wolf"), ["aurea_1", "その道に出る"])  # その道にいない魔物
        self.assertError(town_quest(rank=9), ["aurea_1", "rank"])
        self.assertError(town_quest(count=0), ["aurea_1", "count"])
        self.assertError(town_quest(type="deliver", to="aurea"), ["aurea_1", "別の町"])
        self.assertError(town_quest(id="hunt"), ["重複"])  # 共通の依頼と同じ ID
        self.assertError(lambda r: r["town_quests"].update(jade_tomb=[]), ["jade_tomb", "町の ID"])

    def test_quest_ranks_are_checked(self):
        self.assertError(lambda r: r["quest_ranks"][0].update(weight=0), ["ranks[1]", "weight"])
        self.assertError(lambda r: r["quest_ranks"].clear(), ["ranks"])
        def no_rank1(r):
            for t in r["quests"]:
                t["rank"] = 2
        self.assertError(no_rank1, ["rank 1"])

    def test_every_town_has_its_own_quests(self):
        towns = {p["id"] for p in RAW["map"]["places"] if p["type"] == "town"}
        self.assertEqual(set(RAW["town_quests"]), towns)
        for tid, qs in RAW["town_quests"].items():
            self.assertTrue(any(q["rank"] >= 2 for q in qs), tid)  # どの町にもレアな依頼がある

    def test_too_strong_item(self):
        def change(r):
            r["items"].append({"id": "god_sword", "name": "神の剣", "slot": "weapon", "rarity": "legendary", "atk": 99,
                               "towns": ["aurea"]})
        self.assertError(change, ["強すぎ"])

    def test_season_start_day_must_exist_in_every_month(self):
        def change(r):
            r["season"]["start"] = "2027-01-31"
        self.assertError(change, ["start", "1〜28"])

    def test_season_keys_are_checked(self):
        def typo(r):
            r["season"]["deliver_rang"] = r["season"].pop("deliver_range")
        self.assertError(typo, ["deliver_range"])

        def bad_events(r):
            r["season"]["road_events"] = {"battle": -1}
        self.assertError(bad_events, ["road_events"])

        def no_battle(r):
            r["season"]["road_events"] = {"battle": 0, "nothing": 10}
        self.assertError(no_battle, ["road_events", "battle"])

        def bad_scenery(r):
            r["season"]["terrain_scenery"] = ["草原"]
        self.assertError(bad_scenery, ["terrain_scenery"])

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

    def test_bad_pos_and_layout(self):
        def bad_pos(r):
            r["map"]["places"][0]["pos"] = [1]
        self.assertError(bad_pos, ["pos"])

        def bad_layout(r):
            del r["map"]["layout"]["size"]
        self.assertError(bad_layout, ["layout", "size"])

        def no_pos(r):
            del r["map"]["places"][0]["pos"]
        self.assertError(no_pos, ["aurea", "pos"])

        def bad_quests_per_day(r):
            r["map"]["places"][0]["quests_per_day"] = 0
        self.assertError(bad_quests_per_day, ["aurea", "quests_per_day"])

        def bad_path(r):
            r["map"]["roads"][0]["path"] = [[1, 2]]
        self.assertError(bad_path, ["path"])

    def test_bad_season(self):
        def change(r):
            r["season"]["months"] = 0
        self.assertError(change, ["months"])


if __name__ == "__main__":
    unittest.main()
