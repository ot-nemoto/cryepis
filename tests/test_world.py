import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import world as W  # noqa: E402

RAW = W.load_raw()
WORLD = W.build(copy.deepcopy(RAW))


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
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open", None)  # 開放していない航路も含めて
    world = W.build(raw)
    sea = [k for k, i in world["road_info"].items() if i["kind"] == "sea"]
    self.assertTrue(sea)
    for key in sea:
      self.assertTrue(all(world["monsters"][m]["terrain"] == "sea" for m, _ in world["road_info"][key]["monsters"]))


class MonsterRewardTest(unittest.TestCase):
  """魔物の経験値とお金は、能力から計算する（強さ点² ÷ exp_divisor、お金は経験値 × gold_rate）。"""

  def test_rewards_follow_the_stats(self):
    cfg, traits, monsters = WORLD["season"]["monster_reward"], WORLD["traits"], WORLD["monsters"]
    dog = monsters["wild_dog"]  # HP20・攻7・防2・速8・器6 → 20 + 4×9 + 2×14 = 84 → 84² ÷ 700 = 10.08
    self.assertEqual(W.monster_score(dog, traits, cfg), 84)
    self.assertEqual((dog["exp"], dog["gold"]), (10, 8))
    eagle = monsters["great_eagle"]  # 先手は速 +10 として数える
    plain = W.monster_score(eagle | {"trait": None}, traits, cfg)
    self.assertEqual(W.monster_score(eagle, traits, cfg), plain + cfg["weights"]["spd"] * 10)
    rat = monsters["giant_rat"]  # 群れは戦いで効く値（HP ×1.5・攻撃 ×0.6 を 2 回）
    self.assertGreater(rat["exp"], W.monster_reward(rat | {"trait": None}, traits, cfg)[0])
    boss = monsters["den_lord"]
    self.assertGreater(boss["exp"], 400)
    self.assertEqual(boss["gold"], round(boss["exp"] * cfg["gold_rate"]))

  def test_reward_rounds_half_up_and_never_grows_from_a_negative_score(self):
    self.assertEqual((W.half_up(4.5), W.half_up(10.5), W.half_up(10.49)), (5, 11, 10))
    cfg = {"weights": {"hp": 1}, "exp_divisor": 1, "gold_rate": 0.75}
    m = {"hp": 6, "atk": 0, "def": 0, "spd": 1, "dex": 0}
    self.assertEqual(W.monster_reward(m, {}, cfg), (36, 27))  # 36 × 0.75 = 27
    self.assertEqual(W.monster_reward(m | {"hp": 3}, {}, cfg | {"gold_rate": 0.5}), (9, 5))  # 4.5 → 5
    traits = {"thief": {"score": {"hp": -200}}}
    self.assertEqual(W.monster_reward(m | {"trait": "thief"}, traits, cfg), (0, 0))  # マイナスは 0 にする

  def test_trait_effect_is_shared_with_the_battle(self):
    m = {"hp": 20, "atk": 10, "def": 4}
    self.assertEqual(W.trait_effect(m), {"hp": 20, "atk": 10, "guard": 4, "hits": 1})
    self.assertEqual(W.trait_effect(m | {"trait": "fierce"}), {"hp": 20, "atk": 13, "guard": 2, "hits": 1})
    self.assertEqual(W.trait_effect(m | {"trait": "armored"}), {"hp": 20, "atk": 9, "guard": 9, "hits": 1})
    self.assertEqual(W.trait_effect(m | {"trait": "swarm"}), {"hp": 30, "atk": 6, "guard": 4, "hits": 2})


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
      r["map"]["places"].append(
        {"id": "island", "type": "town", "name": "離れ小島", "pos": [1, 1], "quests_per_day": 3, "area": "crownlands"}
      )
      r["map"]["open"].append("island")

    self.assertError(change, ["island", "行けません"])

  def test_bad_road(self):
    self.assertError(lambda r: r["map"]["roads"].append({"from": "aurea", "to": "x", "distance": 0}), ["distance"])

  def test_road_kinds_connect_the_right_places(self):
    def land_to_dungeon(r):
      r["map"]["roads"].append(
        {"from": "aurea", "to": "jade_tomb", "distance": 1, "kind": "land", "terrain": [["plain", 100]]}
      )

    self.assertError(land_to_dungeon, ["町どうし"])

    def trail_between_towns(r):
      r["map"]["roads"].append({"from": "aurea", "to": "zafra", "distance": 1, "kind": "trail"})

    self.assertError(trail_between_towns, ["trail", "ダンジョン"])

  def test_start_must_be_a_town(self):
    def change(r):
      r["map"]["starts"] = ["jade_tomb"]

    self.assertError(change, ["starts", "jade_tomb"])
    self.assertError(lambda r: r["map"].update(starts=["aurea"]), ["starts", "aurea", "開放"])

  def test_bad_areas_are_reported_not_crashed(self):
    self.assertError(lambda r: r["map"].update(areas=None), ["areas"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": ["x"], "name": "y"}), ["areas", "id"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "x", "name": 1}), ["areas", "name"])
    self.assertError(lambda r: r["map"]["places"][0].update(area=["crownlands"]), ["area", "地方"])

  def test_area_ids_do_not_clash_with_places(self):
    self.assertError(lambda r: r["map"]["areas"].append({"id": "eques", "name": "x"}), ["areas", "町・ダンジョンの ID"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "_other", "name": "x"}), ["areas", "_"])

  def test_elements_speed_and_personas_are_checked(self):
    self.assertError(lambda r: r["monsters"][0].update(element="light"), ["wild_dog", "element"])
    self.assertError(lambda r: r["monsters"][0].pop("spd"), ["wild_dog", "spd"])
    self.assertError(lambda r: r["items"][0].update(element="fire", slot="helmet"), ["element", "武器"])
    self.assertError(lambda r: r["items"][0].update(spd="速い"), ["spd"])
    self.assertError(lambda r: r["elements"]["fire"].update(strong="light"), ["elements", "fire"])
    self.assertError(lambda r: r["elements"]["fire"].update(strong="fire"), ["elements", "fire"])  # 自分には強くない
    self.assertError(lambda r: r["elements"]["fire"].update(strong=["ice"]), ["elements", "fire"])  # 落ちずに返す
    self.assertError(lambda r: r["monsters"][0].update(element=["fire"]), ["wild_dog", "element"])
    self.assertError(lambda r: r["season"]["element_rates"].pop("weak"), ["element_rates"])
    self.assertError(lambda r: r["season"]["hero_traits"]["strong"].update(growth={"atk": "2"}), ["strong", "growth"])
    self.assertError(lambda r: r["season"]["hero_traits"]["strong"].update(growth={"luck": 1}), ["strong", "growth"])
    self.assertError(lambda r: r["season"]["growth"]["atk"].pop(), ["growth", "atk"])  # 最大レベルの数だけ要る
    self.assertError(lambda r: r["season"]["growth"]["def"].__setitem__(5, 1), ["growth", "def"])  # 下がらない
    self.assertError(lambda r: r["monsters"][0].update(dex=-1), ["wild_dog", "dex"])
    self.assertError(lambda r: r["season"].pop("max_level"), ["max_level"])
    self.assertError(lambda r: r["season"].update(hero_traits={}), ["hero_traits"])
    self.assertError(lambda r: r["season"].pop("growth"), ["growth"])

  def test_monster_reward_is_checked(self):
    self.assertError(lambda r: r["monsters"][0].update(exp=9), ["wild_dog", "exp", "計算"])  # 経験値は書かない
    self.assertError(lambda r: r["season"]["monster_reward"].update(exp_divisor=0), ["monster_reward"])
    self.assertError(lambda r: r["season"]["monster_reward"]["weights"].update(luck=1), ["monster_reward"])
    self.assertError(lambda r: r["traits"]["poison"].update(score={"atk": "2"}), ["poison", "score"])

  def test_critical_is_checked(self):
    self.assertError(lambda r: r["season"]["critical"].update(dex_divisor=0), ["critical"])
    self.assertError(lambda r: r["season"].pop("critical"), ["critical"])

  def test_boss_needs_a_dungeon(self):
    self.assertError(lambda r: r["monsters"][-1].update(boss="salina"), ["den_lord", "boss"])
    self.assertError(lambda r: r["monsters"][-1].update(boss=["wall_den"]), ["den_lord", "boss"])  # 落ちない

  def test_road_needs_a_monster_other_than_the_boss(self):
    def boss_only(r):  # 火山にはボスだけ、という道
      r["monsters"] = [m for m in r["monsters"] if m["terrain"] != "volcano" or m.get("boss")]
      r["monsters"][-1]["terrain"] = "volcano"
      r["map"]["roads"][0]["terrain"] = [["volcano", 1]]

    self.assertError(boss_only, ["roads[0]", "ボス以外"])

  def test_hunt_quest_cannot_target_the_boss(self):
    def boss_quest(r):
      q = r["town_quests"]["salina"][0]
      boss = r["monsters"][-1]
      boss["terrain"] = next(m["terrain"] for m in r["monsters"] if m["id"] == q["monster"])  # 道の地形に合わせる
      q["monster"] = boss["id"]

    self.assertError(boss_quest, ["salina_1", "ボス以外"])

  def test_item_dex_is_checked(self):
    self.assertError(lambda r: r["items"][-1].update(dex="2"), ["power_ring", "dex"])

  def test_every_terrain_has_a_japanese_name(self):
    self.assertEqual(set(W.TERRAIN_NAMES), set(W.TERRAINS))

  def test_areas_and_english_names(self):
    self.assertError(lambda r: r["map"]["places"][0].update(area="moon"), ["area", "地方"])
    self.assertError(lambda r: r["map"]["places"][0].pop("area"), ["area", "地方"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "crownlands", "name": "x"}), ["areas", "重複"])
    self.assertError(lambda r: r["map"]["places"][0].update(en=""), ["en"])
    world = W.load_world()
    self.assertEqual(len(world["areas"]), 8)
    for p in world["places"].values():  # どの町・ダンジョンにも地方と英語名がある
      self.assertIn(p["area"], world["areas"])
      self.assertTrue(p["en"])
    self.assertEqual({world["places"][t]["area"] for t in world["towns"]}, {"austral_land"})

  def test_every_town_has_a_guild_size(self):
    self.assertError(lambda r: r["map"]["places"][0].pop("quests_per_day"), ["quests_per_day", "ギルドの規模"])

  def test_open_must_be_towns(self):
    self.assertError(lambda r: r["map"].update(open=["jade_tomb"]), ["open", "jade_tomb"])
    self.assertError(lambda r: r["map"].update(open=[]), ["open"])

  def test_closed_roads_are_not_used(self):
    world = W.load_world()
    opened = set(RAW["map"]["open"])
    self.assertEqual(set(world["towns"]), opened)
    for a, b in world["roads"]:
      self.assertTrue(a in opened and b in opened, (a, b))
    for r in world["trails"]:
      self.assertTrue({r["from"], r["to"]} & opened, r)
    self.assertFalse(any(i["kind"] == "sea" for i in world["road_info"].values()))  # サリナからの航路は出さない
    self.assertEqual(len(world["places"]), len(RAW["map"]["places"]))  # 町・ダンジョンは地図に出す

  def test_without_open_every_town_is_open(self):
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open")
    world = W.build(raw)
    self.assertEqual(set(world["towns"]), {p["id"] for p in RAW["map"]["places"] if p["type"] == "town"})

  def test_shop_item_needs_price(self):
    def change(r):
      r["map"]["places"][0]["shop"].append("power_ring")

    self.assertError(change, ["power_ring", "price"])

  def test_bad_drop_table(self):
    def change(r):
      r["quest_ranks"][0]["item"] = [["mythic", 1]]

    self.assertError(change, ["mythic"])

  def test_drop_level_is_no_longer_used(self):
    self.assertError(lambda r: r["items"][5].update(drop_level=5), ["drop_level"])

  def test_rare_items_need_quest_towns(self):
    self.assertError(lambda r: r["items"][-1].pop("towns"), ["power_ring", "towns"])
    self.assertError(lambda r: r["items"][-1].update(towns=["jade_tomb"]), ["towns", "jade_tomb"])
    self.assertError(lambda r: r["items"][0].update(towns=["salina"]), ["wooden_sword", "common"])
    self.assertError(
      lambda r: r["items"][-1].update(towns=["aurea"]), ["power_ring", "aurea", "開放"]
    )  # 開放していない町
    for bad in (5, "salina", [["salina"]]):  # 型が違っても落ちずにエラーを返す
      self.assertError(lambda r, bad=bad: r["items"][-1].update(towns=bad), ["power_ring", "towns"])

  def test_every_town_needs_a_rare(self):
    def change(r):
      for it in r["items"]:
        if it["rarity"] == "rare" and "karun" in it["towns"]:
          it["towns"].remove("karun")

    self.assertError(change, ["karun", "rare"])

  def test_treasure_cannot_hold_rares(self):
    self.assertError(lambda r: r["season"]["treasure"].append(["rare", 1]), ["treasure", "rare"])
    self.assertError(lambda r: r["season"]["treasure"].append(["power_ring", 1]), ["treasure", "power_ring"])

  def test_every_town_has_its_own_specialty(self):
    shops = {p["id"]: set(p["shop"]) for p in RAW["map"]["places"] if p["type"] == "town"}
    for town, items in shops.items():
      only_here = {i for i in items if all(i not in other for t, other in shops.items() if t != town)}
      self.assertTrue(only_here, town)

  def test_every_town_quest_has_some_rare(self):
    towns = {tid for i in RAW["items"] if i["rarity"] == "rare" for tid in i["towns"]}
    self.assertEqual(towns, set(RAW["map"]["open"]))

  def test_every_slot_has_items_in_a_shop(self):
    sold = {sid for p in RAW["map"]["places"] for sid in p.get("shop", [])}
    items = {i["id"]: i for i in RAW["items"]}
    self.assertEqual({items[i]["slot"] for i in sold}, set(W.SLOTS))

  def test_town_quests_are_checked(self):
    def town_quest(**change):
      def apply(r):
        r["town_quests"]["salina"][0].update(change)

      return apply

    self.assertError(town_quest(to="karun"), ["salina_1", "直接つながる"])  # 道がない
    self.assertError(town_quest(to="flumen"), ["salina_1", "直接つながる"])  # 開放していない航路
    self.assertError(town_quest(monster="ice_wolf"), ["salina_1", "その道に出る"])  # その道にいない魔物
    self.assertError(town_quest(rank=9), ["salina_1", "rank"])
    self.assertError(town_quest(count=0), ["salina_1", "count"])
    self.assertError(town_quest(type="deliver", to="salina"), ["salina_1", "別の町"])
    self.assertError(town_quest(type="deliver", to="aurea"), ["salina_1", "開放した別の町"])
    self.assertError(town_quest(id="hunt"), ["重複"])  # 共通の依頼と同じ ID
    self.assertError(lambda r: r["town_quests"].update(jade_tomb=[]), ["jade_tomb", "町の ID"])
    self.assertError(lambda r: r["town_quests"].update(aurea=[]), ["aurea", "開放した町"])

  def test_quest_reward_is_checked(self):
    self.assertError(lambda r: r["quest_reward"].pop("gold"), ["reward", "gold"])

  def test_quest_ranks_are_checked(self):
    self.assertError(lambda r: r["quest_ranks"][0].update(weight=0), ["ranks[1]", "weight"])
    self.assertError(lambda r: r["quest_ranks"].clear(), ["ranks"])

    def no_rank1(r):
      for t in r["quests"]:
        t["rank"] = 2

    self.assertError(no_rank1, ["rank 1"])

  def test_every_town_has_its_own_quests(self):
    self.assertEqual(set(RAW["town_quests"]), set(RAW["map"]["open"]))
    for tid, qs in RAW["town_quests"].items():
      self.assertTrue(any(q["rank"] >= 2 for q in qs), tid)  # どの町にもレアな依頼がある

  def test_too_strong_item(self):
    def change(r):
      r["items"].append(
        {"id": "god_sword", "name": "神の剣", "slot": "weapon", "rarity": "legendary", "atk": 99, "towns": ["salina"]}
      )

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
      del r["season"]["hero"]["max_hp"]

    self.assertError(bad_hero, ["hero", "max_hp"])

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
