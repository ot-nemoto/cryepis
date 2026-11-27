import copy
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import game as G  # noqa: E402
import world as W  # noqa: E402

RAW = W.load_raw()
RAW_MONSTERS = {m["id"]: m for m in RAW["monsters"]}
WORLD = W.build(copy.deepcopy(RAW))


def errors_after(change) -> list[str]:
  raw = copy.deepcopy(RAW)
  change(raw)
  return W.validate(raw)


class DataTest(unittest.TestCase):
  """world/ の下のデータに誤りがないこと（手で編集したら、まずこのテストが通るか確かめる）。"""

  def test_world_data_is_valid(self):
    self.assertEqual(W.validate(RAW), [])

  def test_quest_texts_do_not_use_game_terms(self):
    """依頼の文（作中の文）に、ゲームの用語を使わない。"""
    texts = []

    def walk(value):
      if isinstance(value, str):
        texts.append(value)
      elif isinstance(value, list):
        for v in value:
          walk(v)
      elif isinstance(value, dict):
        for v in value.values():
          walk(v)

    for key in ("hunts", "deliveries", "town_quests"):
      walk(RAW[key])
    self.assertGreater(len(texts), 40)
    for text in texts:
      for word in ("シーズン", "レベル", "経験値"):
        self.assertNotIn(word, text, text)  # ゲームの用語は、作中の文に出さない

  def test_load_world_builds_routes(self):
    world = W.load_world()
    start = world["starts"][0]
    for pid in world["towns"]:  # 町にはどこからでも陸路・航路で行ける
      self.assertIn(pid, world["dist"][start])
    self.assertEqual(world["dist"][start][start], 0)
    for pid in world["dungeons"]:  # 霊場へは（まだ）行けない
      self.assertNotIn(pid, world["dist"][start])

  def test_every_road_has_monsters(self):
    world = W.load_world()
    for key, info in world["road_info"].items():
      self.assertTrue(info["monsters"], key)
      ends = {world["places"][p]["area"] for p in key}
      for mid, weight in info["monsters"]:
        self.assertIn(world["monsters"][mid]["area"], ends)  # 両端の町の地方の物の怪（#265）
        self.assertIn(world["monsters"][mid]["terrain"], info["terrain"])  # 道の地形に住む物の怪（#267）
        self.assertGreater(weight, 0)

  def test_each_area_has_two_monsters_per_level(self):
    world = W.load_world()
    road = [m for m in world["monsters"].values() if W.on_road(m)]
    for area in world["areas"]:
      levels = sorted(m["level"] for m in road if m["area"] == area)
      self.assertEqual(levels, sorted(list(range(1, 16)) * 2), area)
    names = [m["name"] for m in world["monsters"].values()]
    self.assertEqual(len(names), len(set(names)))  # 地方どうしで名前が重ならない

  def test_every_road_has_a_foe_for_every_level_even_before_opening(self):
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open", None)  # 開放していない道（航路も）でも、Lv1〜15 のどのもののふにも Lv −3〜+1 の相手がいる
    world = W.build(raw)
    low, high = world["season"]["encounter_levels"]
    for key, info in world["road_info"].items():
      for level in range(1, 16):
        levels = {world["monsters"][m]["level"] for m, _ in G.encounters(world, info["monsters"], level)}
        self.assertTrue(levels and all(level + low <= x <= level + high for x in levels), (key, level, levels))

  def test_every_terrain_has_a_foe_for_every_level(self):
    """道の地形ごとに、Lv1〜15 のどのもののふにも Lv −3〜+1 の物の怪が住む（#269）。陸路は両端の地方それぞれで、
    航路の海は両端の地方を合わせて。Lv1〜2 の物の怪は地方に 4 体なので、地形が 5 つの北陸の雪原の Lv1 だけは除く。"""
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open", None)  # 開放していない道も
    world = W.build(raw)
    low, high = world["season"]["encounter_levels"]
    road_foes = [m for m in world["monsters"].values() if W.on_road(m)]
    allowed = {("hokuriku", "snow", 1)}
    for r in raw["map"]["roads"]:
      if r.get("kind", "land") == "trail":
        continue
      areas = {world["places"][r["from"]]["area"], world["places"][r["to"]]["area"]}
      for t, _ in r["terrain"]:
        for level in range(1, 16):
          here = {m["area"] for m in road_foes if m["terrain"] == t and level + low <= m["level"] <= level + high}
          if r.get("kind") == "sea":
            self.assertTrue(here & areas, (r["from"], r["to"], t, level))
          else:
            for area in areas - here:
              self.assertIn((area, t, level), allowed, (r["from"], r["to"]))

  def test_road_weights_follow_the_terrain_shares(self):
    world = W.load_world()
    info = world["road_info"][("kofu", "nagano")]  # 坂東の中の道。地形の割合 = その地形の物の怪の重みの合計
    road = next(r for r in RAW["map"]["roads"] if {r["from"], r["to"]} == {"kofu", "nagano"})
    total = sum(w for _, w in road["terrain"])
    for t, w in road["terrain"]:
      got = sum(x for m, x in info["monsters"] if world["monsters"][m]["terrain"] == t)
      self.assertAlmostEqual(got, w / total, msg=t)

  def test_terrain_without_monsters_is_left_out(self):
    raw = copy.deepcopy(RAW)
    road = next(r for r in raw["map"]["roads"] if {r["from"], r["to"]} == {"edo", "odawara"})
    road["terrain"] = [["plain", 1], ["wetland", 9]]  # 坂東に湿地の物の怪はいない
    info = W.build(raw)["road_info"][("edo", "odawara")]
    self.assertTrue(info["monsters"])
    self.assertAlmostEqual(sum(w for _, w in info["monsters"]), 1.0)  # 湿地の分は、住む物の怪のいる地形で分け直す
    self.assertEqual({RAW_MONSTERS[m]["terrain"] for m, _ in info["monsters"]}, {"plain"})

  def test_area_without_monsters_on_the_road_is_left_out(self):
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open", None)
    world = W.build(raw)
    info = world["road_info"][("ujiyamada", "edo")]  # 航路。坂東に海の物の怪はいないので、東海の物の怪だけ
    self.assertEqual({world["monsters"][m]["area"] for m, _ in info["monsters"]}, {"tokai"})
    self.assertAlmostEqual(sum(w for _, w in info["monsters"]), 1.0)

  def test_broken_road_terrain_does_not_stop_the_quest_check(self):
    def change(r):
      road = next(x for x in r["map"]["roads"] if {x["from"], x["to"]} == {"kofu", "nagano"})
      road["terrain"] = [["plain", 0]]

    errors = errors_after(change)
    self.assertTrue(any("terrain" in e for e in errors), errors)
    self.assertFalse(any("調べきれません" in e for e in errors), errors)

  def test_cross_area_road_splits_between_areas(self):
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open", None)  # 開放していない道も含めて
    world = W.build(raw)
    info = world["road_info"][("edo", "kurokawa")]  # 坂東と奥羽をまたぐ道
    share: dict = {}
    for mid, w in info["monsters"]:
      share[world["monsters"][mid]["area"]] = share.get(world["monsters"][mid]["area"], 0) + w
    self.assertEqual(set(share), {"bando", "ou"})
    for w in share.values():
      self.assertAlmostEqual(w, 0.5)


class MonsterRewardTest(unittest.TestCase):
  """物の怪の経験値とお金は、能力から計算する（強さ点² ÷ exp_divisor、お金は経験値 × gold_rate）。"""

  def test_rewards_follow_the_stats(self):
    cfg, traits, monsters = WORLD["season"]["monster_reward"], WORLD["traits"], WORLD["monsters"]
    dog = monsters["wild_dog"]  # HP13・攻5・防1・速8・器6 → 13 + 4×6 + 2×14 = 65 → 65² ÷ 700 = 6.04
    self.assertEqual(W.monster_score(dog, traits, cfg), 65)
    self.assertEqual((dog["exp"], dog["gold"]), (6, 5))  # お金は 6 × 0.75 = 4.5 → 5
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

  def test_thief_pays_more_gold(self):
    cfg, traits, monsters = WORLD["season"]["monster_reward"], WORLD["traits"], WORLD["monsters"]
    bandit = monsters["mountain_bandit"]  # 盗み
    rate = traits["thief"]["gold_rate"]
    self.assertGreater(rate, 1)
    exp, gold = W.monster_reward(bandit, traits | {"thief": {**traits["thief"], "gold_rate": 1}}, cfg)
    self.assertEqual(bandit["exp"], exp)  # 経験値は変わらない
    self.assertEqual(bandit["gold"], W.half_up(exp * cfg["gold_rate"] * rate))  # 四捨五入は最後に 1 回
    self.assertGreater(bandit["gold"], gold)

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

  def test_road_needs_a_monster_of_its_areas(self):
    def change(r):
      r["monsters"] = [m for m in r["monsters"] if m.get("area") != "bando"]

    self.assertError(change, ["roads", "物の怪がいません"])

  def test_monster_lives_on_a_terrain_of_its_area(self):
    self.assertError(
      lambda r: r["monsters"][0].update(terrain="wetland"), ["wild_dog", "wetland", "bando"]
    )  # 坂東に湿地はない

  def test_road_monster_needs_an_area(self):
    self.assertError(lambda r: r["monsters"][0].pop("area"), ["wild_dog", "area"])
    self.assertError(lambda r: r["monsters"][0].update(area="moon"), ["wild_dog", "area"])
    self.assertError(lambda r: r["monsters"][0].update(area=["bando"]), ["monsters[0]", "area は文字列"])  # 落ちない

  def test_broken_area_does_not_hide_other_monster_errors(self):
    def change(r):
      r["map"]["areas"].append({"id": ["x"], "name": "y"})
      r["monsters"][0]["hp"] = -5

    errors = errors_after(change)
    self.assertTrue(any("areas[" in e for e in errors), errors)
    self.assertTrue(any("wild_dog" in e and "hp" in e for e in errors), errors)  # 物の怪の検査も最後まで動く
    self.assertFalse(any("調べきれません" in e for e in errors), errors)

  def test_world_without_areas_needs_no_monster_area(self):
    def change(r):
      r["map"].pop("areas")
      for p in r["map"]["places"]:
        p.pop("area", None)
      for m in r["monsters"]:
        m.pop("area", None)

    self.assertFalse([e for e in errors_after(change) if "area" in e])  # 地方のない世界では、地方を調べない

  def test_duplicate_id(self):
    self.assertError(lambda r: r["monsters"].append(dict(r["monsters"][0])), ["重複", "wild_dog"])

  def test_disconnected_place(self):
    def change(r):
      r["map"]["places"].append(
        {"id": "island", "type": "town", "name": "離れ小島", "pos": [1, 1], "quests_per_day": 3, "area": "kinai"}
      )
      r["map"]["open"].append("island")

    self.assertError(change, ["island", "行けません"])

  def test_bad_road(self):
    self.assertError(lambda r: r["map"]["roads"].append({"from": "kyoto", "to": "x", "distance": 0}), ["distance"])

  def test_road_kinds_connect_the_right_places(self):
    def land_to_dungeon(r):
      r["map"]["roads"].append(
        {"from": "kyoto", "to": "kumano", "distance": 1, "kind": "land", "terrain": [["plain", 100]]}
      )

    self.assertError(land_to_dungeon, ["町どうし"])

    def trail_between_towns(r):
      r["map"]["roads"].append({"from": "kyoto", "to": "osaka", "distance": 1, "kind": "trail"})

    self.assertError(trail_between_towns, ["trail", "霊場"])

  def test_start_must_be_a_town(self):
    def change(r):
      r["map"]["starts"] = ["kumano"]

    self.assertError(change, ["starts", "kumano"])
    self.assertError(lambda r: r["map"].update(starts=["kyoto"]), ["starts", "kyoto", "開放"])

  def test_bad_areas_are_reported_not_crashed(self):
    self.assertError(lambda r: r["map"].update(areas=None), ["areas"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": ["x"], "name": "y"}), ["areas", "id"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "x", "name": 1}), ["areas", "name"])
    self.assertError(lambda r: r["map"]["places"][0].update(area=["kinai"]), ["area", "地方"])

  def test_area_ids_do_not_clash_with_places(self):
    self.assertError(lambda r: r["map"]["areas"].append({"id": "odawara", "name": "x"}), ["areas", "町・霊場の ID"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "_other", "name": "x"}), ["areas", "_"])

  def test_elements_speed_and_personas_are_checked(self):
    self.assertError(lambda r: r["monsters"][0].update(element="light"), ["wild_dog", "element"])
    self.assertError(lambda r: r["monsters"][0].pop("spd"), ["wild_dog", "spd"])
    self.assertError(lambda r: r["items"][0].update(element="flame", slot="helmet"), ["element", "武器"])
    self.assertError(lambda r: r["items"][0].update(spd="速い"), ["spd"])
    self.assertError(lambda r: r["elements"]["flame"].update(strong="light"), ["elements", "flame"])
    self.assertError(lambda r: r["elements"]["flame"].update(ore=3), ["elements", "flame", "ore"])
    self.assertEqual({e["ore"] for e in RAW["elements"].values()}, {"熾焔鋼", "霧氷石", "紫電鉄"})  # 属性の鉱石
    self.assertError(lambda r: r["elements"]["flame"].update(strong="flame"), ["elements", "flame"])  # 自分には強くない
    self.assertError(lambda r: r["elements"]["flame"].update(strong=["ice"]), ["elements", "flame"])  # 落ちずに返す
    self.assertError(lambda r: r["monsters"][0].update(element=["flame"]), ["wild_dog", "element"])
    self.assertError(lambda r: r["season"]["element_rates"].pop("weak"), ["element_rates"])
    self.assertError(lambda r: r["season"]["hero_traits"]["strong"].update(growth={"atk": "2"}), ["strong", "growth"])
    self.assertError(lambda r: r["season"]["hero_traits"]["strong"].update(growth={"luck": 1}), ["strong", "growth"])
    self.assertError(lambda r: r["season"]["growth"]["atk"].pop(), ["growth", "atk"])  # 最大レベルの数だけ要る
    self.assertError(lambda r: r["season"]["growth"]["def"].__setitem__(5, 1), ["growth", "def"])  # 下がらない
    self.assertError(lambda r: r["monsters"][0].update(dex=-1), ["wild_dog", "dex"])
    self.assertError(lambda r: r["season"].pop("max_level"), ["max_level"])
    self.assertError(lambda r: r["season"].update(hero_traits={}), ["hero_traits"])
    self.assertError(lambda r: r["season"].pop("growth"), ["growth"])

  def test_elite_needs_a_town_hunt_quest(self):
    self.assertError(lambda r: r["monsters"][1].update(elite=True), ["giant_rat", "大物"])  # 狙える依頼がない
    self.assertError(lambda r: r["monsters"][-1].update(elite=True), ["den_lord", "elite"])  # 主には付けない

  def test_monster_reward_is_checked(self):
    self.assertError(lambda r: r["monsters"][0].update(exp=9), ["wild_dog", "exp", "計算"])  # 経験値は書かない
    self.assertError(lambda r: r["season"]["monster_reward"].update(exp_divisor=0), ["monster_reward"])
    self.assertError(lambda r: r["season"]["monster_reward"]["weights"].update(luck=1), ["monster_reward"])
    self.assertError(lambda r: r["traits"]["poison"].update(score={"atk": "2"}), ["poison", "score"])
    self.assertError(lambda r: r["traits"]["thief"].update(gold_rate=-1), ["thief", "gold_rate"])

    def both(r):  # score と gold_rate の両方が誤りなら、両方を 1 度に知らせる
      r["traits"]["thief"].update(score={"atk": "2"}, gold_rate=-1)

    self.assertError(both, ["thief", "score"])
    self.assertError(both, ["thief", "gold_rate"])

  def test_critical_is_checked(self):
    self.assertError(lambda r: r["season"]["critical"].update(dex_divisor=0), ["critical"])
    self.assertError(lambda r: r["season"].pop("critical"), ["critical"])

  def test_boss_needs_a_dungeon(self):
    self.assertError(lambda r: r["monsters"][-1].update(boss="edo"), ["den_lord", "boss"])
    self.assertError(lambda r: r["monsters"][-1].update(boss=["fuji"]), ["den_lord", "boss"])  # 落ちない

  def test_boss_has_no_area(self):
    self.assertError(lambda r: r["monsters"][-1].update(area="bando"), ["den_lord", "area"])  # 主は道に出ない

  def test_hunt_quest_cannot_target_the_boss(self):
    def boss_quest(r):
      q = r["town_quests"]["nagano"][0]
      q["monster"] = r["monsters"][-1]["id"]

    self.assertError(boss_quest, ["nagano_3", "主は狙えない"])

  def test_item_dex_is_checked(self):
    self.assertError(
      lambda r: next(i for i in r["items"] if i["id"] == "crimson_grass_charm").update(dex="2"),
      ["crimson_grass_charm", "dex"],
    )

  def test_every_terrain_has_a_japanese_name(self):
    self.assertEqual(set(W.TERRAIN_NAMES), set(W.TERRAINS))

  def test_areas_and_english_names(self):
    self.assertError(lambda r: r["map"]["places"][0].update(area="moon"), ["area", "地方"])
    self.assertError(lambda r: r["map"]["places"][0].pop("area"), ["area", "地方"])
    self.assertError(lambda r: r["map"]["areas"].append({"id": "kinai", "name": "x"}), ["areas", "重複"])
    self.assertError(lambda r: r["map"]["places"][0].update(en=""), ["en"])
    world = W.load_world()
    self.assertEqual(len(world["areas"]), 7)
    for p in world["places"].values():  # どの町・霊場にも地方と英語名がある
      self.assertIn(p["area"], world["areas"])
      self.assertTrue(p["en"])
    self.assertEqual({world["places"][t]["area"] for t in world["towns"]}, {"bando"})

  def test_every_town_has_a_guild_size(self):
    self.assertError(lambda r: r["map"]["places"][0].pop("quests_per_day"), ["quests_per_day", "口入れ屋の規模"])

  def test_open_must_be_towns(self):
    self.assertError(lambda r: r["map"].update(open=["kumano"]), ["open", "kumano"])
    self.assertError(lambda r: r["map"].update(open=[]), ["open"])

  def test_closed_roads_are_not_used(self):
    world = W.load_world()
    opened = set(RAW["map"]["open"])
    self.assertEqual(set(world["towns"]), opened)
    for a, b in world["roads"]:
      self.assertTrue(a in opened and b in opened, (a, b))
    for r in world["trails"]:
      self.assertTrue({r["from"], r["to"]} & opened, r)
    self.assertFalse(any(i["kind"] == "sea" for i in world["road_info"].values()))  # 江戸からの航路は出さない
    self.assertEqual(len(world["places"]), len(RAW["map"]["places"]))  # 町・霊場は地図に出す

  def test_without_open_every_town_is_open(self):
    raw = copy.deepcopy(RAW)
    raw["map"].pop("open")
    world = W.build(raw)
    self.assertEqual(set(world["towns"]), {p["id"] for p in RAW["map"]["places"] if p["type"] == "town"})

  def test_shop_item_needs_price(self):
    def change(r):
      r["map"]["places"][0]["shop"].append("crimson_grass_charm")

    self.assertError(change, ["crimson_grass_charm", "price"])

  def test_bad_drop_table(self):
    def change(r):
      r["quest_ranks"][0]["item"] = [["mythic", 1]]

    self.assertError(change, ["mythic"])

  def test_drop_level_is_no_longer_used(self):
    self.assertError(lambda r: r["items"][5].update(drop_level=5), ["drop_level"])

  def test_fixed_skills_belong_to_the_same_slot_and_require_epic_rarity(self):
    def assign(raw, iid, skill):
      next(item for item in raw["items"] if item["id"] == iid)["fixed_skill"] = skill

    self.assertError(lambda r: assign(r, "cross_spear", "focus"), ["cross_spear", "fixed_skill", "部位"])
    self.assertError(lambda r: assign(r, "steel_sword", "pierce"), ["steel_sword", "fixed_skill", "★★★"])

  def test_rare_items_need_quest_towns(self):
    def charm(raw):
      return next(i for i in raw["items"] if i["id"] == "crimson_grass_charm")

    self.assertError(lambda r: charm(r).pop("towns"), ["crimson_grass_charm", "towns"])
    self.assertError(lambda r: charm(r).update(towns=["kumano"]), ["towns", "kumano"])
    self.assertError(lambda r: r["items"][0].update(towns=["edo"]), ["iron_sword", "common"])
    self.assertError(lambda r: charm(r).update(towns=["kyoto"]), ["crimson_grass_charm", "kyoto", "開放"])
    for bad in (5, "edo", [["edo"]]):  # 型が違っても落ちずにエラーを返す
      self.assertError(lambda r, bad=bad: charm(r).update(towns=bad), ["crimson_grass_charm", "towns"])

  def test_every_town_needs_a_rare(self):
    def change(r):
      for it in r["items"]:
        if it["rarity"] == "rare" and "nagano" in it["towns"]:
          it["towns"].remove("nagano")

    self.assertError(change, ["nagano", "rare"])

  def test_party_quests_need_epic_rewards_when_rare_drops_are_enabled(self):
    for town in ("nagano", "kofu"):
      with self.subTest(town=town):
        raw = copy.deepcopy(RAW)
        for item in raw["items"]:
          if item["rarity"] == "epic":
            item["towns"].remove(town)
        self.assertTrue(any(town in e and "epic" in e for e in W.validate(raw)))
        raw["season"]["rare_drops"] = False
        self.assertEqual(W.validate(raw), [])

  def test_treasure_table_rejects_unknown_outcomes(self):
    self.assertError(lambda r: r["season"]["road_treasure"].update(unknown=1), ["road_treasure"])

  def test_every_town_quest_has_some_rare(self):
    towns = {tid for i in RAW["items"] if i["rarity"] == "rare" for tid in i["towns"]}
    self.assertEqual(towns, set(RAW["map"]["open"]))

  def test_every_slot_has_items_in_a_shop(self):
    sold = {sid for p in RAW["map"]["places"] for sid in p.get("shop", [])}
    items = {i["id"]: i for i in RAW["items"]}
    self.assertEqual({items[i]["slot"] for i in sold}, set(W.SLOTS))

  def test_town_quests_are_checked(self):
    base = {
      "id": "nagano_x",
      "rank": 1,
      "type": "hunt",
      "name": "試し",
      "to": "kofu",
      "monster": "ice_wolf",
      "count": 3,
    }

    def town_quest(**change):
      def apply(r):
        r["town_quests"]["nagano"].append(dict(base, **change))

      return apply

    self.assertEqual(errors_after(town_quest()), [])  # 町の討伐も書ける（今は大物だけ）
    self.assertError(town_quest(to="edo"), ["nagano_x", "直接つながる"])  # 道がない
    self.assertError(town_quest(monster="ou_01a"), ["nagano_x", "その道に出る"])  # その道にいない物の怪（奥羽の物の怪）
    self.assertError(town_quest(rank=9), ["nagano_x", "rank"])
    self.assertError(town_quest(count=0), ["nagano_x", "count"])
    self.assertError(town_quest(type="deliver", to="nagano"), ["nagano_x", "別の町"])
    self.assertError(town_quest(type="deliver", to="kyoto"), ["nagano_x", "開放した別の町"])
    self.assertError(town_quest(id="hunt"), ["重複"])  # 共通の依頼と同じ ID
    self.assertError(lambda r: r["town_quests"].update(kumano=[]), ["kumano", "町の ID"])
    self.assertError(lambda r: r["town_quests"].update(kyoto=[]), ["kyoto", "開放した町"])

  def test_bad_hunt_texts(self):
    self.assertError(lambda r: r["hunts"]["places"].update(nowhere=["x"]), ["nowhere"])
    self.assertError(lambda r: r["hunts"].update(texts=[["{monster}を倒して"]]), ["hunts の texts"])
    self.assertError(lambda r: r["hunts"].update(texts=[["{place}{monster}を{num}体"]]), ["hunts の texts"])
    self.assertError(lambda r: r["deliveries"]["dangers"][0].update(texts=["{to}へ{cargo}を{"]), ["texts"])

  def test_quest_reward_is_checked(self):
    self.assertError(lambda r: r["quest_reward"].pop("gold"), ["reward", "gold"])

  def test_quest_ranks_are_checked(self):
    self.assertError(lambda r: r["quest_ranks"][0].update(weight=0), ["ranks[1]", "weight"])
    self.assertError(lambda r: r["quest_ranks"].clear(), ["ranks"])

    # 共通の依頼の ★ は計算で決まる（rank は書けない）。共通の依頼が 1 つもなくてもいけない
    self.assertError(lambda r: r["quests"][0].update(rank=2), ["hunt", "rank は書かないでください"])
    self.assertError(lambda r: r["quests"].clear(), ["共通の依頼"])

  def test_every_town_has_a_quest_list(self):
    self.assertEqual(set(RAW["town_quests"]), set(RAW["map"]["open"]))  # 町の依頼は大物だけ（ない町もある）

  def test_item_text_is_checked(self):
    for bad in ("", "  ", 5, None, ["説明"], "あ" * (W.ITEM_TEXT_MAX + 1)):
      with self.subTest(bad=bad):
        self.assertError(lambda r, bad=bad: r["items"][0].update(text=bad), ["iron_sword", "text", "説明"])
    self.assertEqual(
      errors_after(lambda r: r["items"][0].update(text="あ" * W.ITEM_TEXT_MAX)), []
    )  # 上限ちょうどは通る
    self.assertEqual(errors_after(lambda r: r["items"][0].pop("text")), [])  # 説明は、書かなくてよい

  def test_every_item_has_a_text(self):
    for item in RAW["items"]:
      self.assertTrue(item.get("text"), item["id"])  # すべてに、説明がある

  def test_too_strong_item(self):
    def change(r):
      r["items"].append(
        {"id": "god_sword", "name": "神の剣", "slot": "weapon", "rarity": "legendary", "atk": 99, "towns": ["edo"]}
      )

    self.assertError(change, ["god_sword", "点数 99", "legendary"])

  def test_item_points_follow_the_rarity(self):
    cfg = RAW["season"]["item_points"]
    self.assertEqual(cfg["max"], {"common": 1, "rare": 2, "epic": 2, "legendary": 3})  # 珍しさごとの上限
    for iid, it in WORLD["items"].items():
      self.assertTrue(0 <= W.item_points(it, cfg) <= cfg["max"][it["rarity"]] + 1e-9, iid)
    self.assertAlmostEqual(W.item_points({"atk": 2, "spd": -2}, cfg), 1)  # マイナスも同じ点数で引く（速 0.5）
    self.assertAlmostEqual(W.item_points({"hp": 5, "def": 2, "spd": 2, "dex": 5}, cfg), 4)  # どれも 1 点ぶん
    self.assertEqual(cfg["element"], 1)
    self.assertAlmostEqual(W.item_points({"atk": 1, "element": "flame"}, cfg), 2)  # 属性は 1 点
    self.assertAlmostEqual(W.item_points(WORLD["items"]["crimson_grass_charm"], cfg), 1)

    def strong_common(r):
      next(i for i in r["items"] if i["id"] == "iron_sword")["atk"] = 2  # 2 点

    self.assertError(strong_common, ["iron_sword", "点数 2", "common", "0〜1"])

    def minus_common(r):
      next(i for i in r["items"] if i["id"] == "iron_axe")["atk"] = 0  # 速 -2 だけで -1 点

    self.assertError(minus_common, ["iron_axe", "点数 -1"])

    def common_element(r):
      next(i for i in r["items"] if i["id"] == "iron_sword")["element"] = "flame"

    self.assertError(common_element, ["iron_sword", "属性", "common"])

    def bad_cfg(r):
      del r["season"]["item_points"]["max"]["epic"]

    self.assertError(bad_cfg, ["item_points"])
    self.assertError(
      lambda r: r["season"]["item_points"]["weights"].update({"def": 0}), ["item_points"]
    )  # 0 は使えない

  def test_bando_legendary_forge_has_one_item_per_slot(self):
    self.assertEqual(
      {aid: area["stone"] for aid, area in WORLD["areas"].items()},
      {
        "ou": "霧氷石",
        "bando": "玄岳鋼",
        "tokai": "潮風銀",
        "hokuriku": "星鏡鉱",
        "kinai": "紫電鉄",
        "saigoku": "常盤玉",
        "kyushu": "熾焔鋼",
      },
    )
    self.assertTrue(all(area.get("stone_effect") for area in WORLD["areas"].values()))
    self.assertError(lambda raw: raw["map"]["areas"][0].update(stone_effect=[]), ["stone_effect"])
    forge = WORLD["places"]["odawara"]
    self.assertTrue(forge["legendary_forge"])
    items = [item for item in WORLD["items"].values() if item.get("forge_town") == "odawara"]
    self.assertEqual(len(items), 4)
    self.assertEqual({item["slot"] for item in items}, set(W.SLOTS))
    self.assertTrue(all(item["rarity"] == "legendary" for item in items))
    self.assertError(
      lambda raw: next(item for item in raw["items"] if item["id"] == "gengaku_odachi").update(slot=["weapon"]),
      ["gengaku_odachi", "slot"],
    )

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

    for bad in ([1, -3], [-3], "x", [-3, 1.5]):  # 下 > 上・2 つでない・整数でない
      self.assertError(lambda r, bad=bad: r["season"].update(encounter_levels=bad), ["encounter_levels"])

    def no_battle(r):
      r["season"]["road_events"] = {"battle": 0, "nothing": 10}

    self.assertError(no_battle, ["road_events", "battle"])

    def bad_scenery(r):
      r["season"]["terrain_scenery"] = ["草原"]

    self.assertError(bad_scenery, ["terrain_scenery"])

  def test_personality_settings(self):
    self.assertError(lambda r: r["season"].update(stroll_events={"fight": 1}), ["stroll_events"])
    self.assertError(lambda r: r["season"].update(stroll_treasure={"item": -1}), ["stroll_treasure"])
    self.assertError(lambda r: r["season"].update(stroll_strong_levels=[3, 2]), ["stroll_strong_levels"])
    self.assertError(lambda r: r["season"].update(inn={"price": -1}), ["inn"])
    self.assertError(lambda r: r["season"].update(stored_days=-1), ["stored_days"])
    self.assertError(lambda r: r["season"].update(stay_days_max="7"), ["stay_days_max"])
    self.assertError(lambda r: r["season"].update(carry={"weapon": 3}), ["carry"])
    self.assertError(lambda r: r["season"].update(treasure_gold=[30, 10]), ["treasure_gold"])
    self.assertError(lambda r: r["skills"][0].update(slot="boots"), ["skills", "slot"])
    self.assertError(lambda r: r["skills"][0].update(kind="magic"), ["skills", "kind"])
    self.assertError(lambda r: r["skills"][0].update(counter={"trait": "laser"}), ["skills", "counter"])
    self.assertError(lambda r: r["skill_prices"].update(common=1), ["skill_prices"])
    self.assertError(lambda r: r["skills"][1].pop("value"), ["skills", "value"])  # しくみに value を使うのに値がない
    self.assertError(
      lambda r: r["season"]["personalities"]["brave"]["skill"]["prefer"].update(weapon=["haggle"]),
      ["brave", "skill.prefer"],
    )
    self.assertError(lambda r: r["season"]["personalities"]["brave"]["skill"].pop("prefer"), ["brave", "skill.prefer"])
    self.assertError(lambda r: r["season"].update(personalities={}), ["personalities"])
    self.assertError(lambda r: r["season"]["personalities"]["careful"].update(inn_below=2), ["careful", "inn_below"])
    self.assertError(
      lambda r: r["season"]["personalities"]["careful"]["quest"].update(levels=[1, 0]), ["careful", "quest.levels"]
    )
    self.assertError(
      lambda r: r["season"]["personalities"]["careful"]["quest"].update(order=["fast"]), ["careful", "quest.order"]
    )
    self.assertError(lambda r: r["season"]["personalities"]["brave"].update(spare="sleep"), ["brave", "spare"])
    self.assertError(lambda r: r["season"]["personalities"]["brave"].update(gear="x"), ["brave", "gear"])
    self.assertError(
      lambda r: r["season"]["hero_traits"]["strong"].update(strong_stat="luck"), ["strong", "strong_stat"]
    )
    self.assertError(
      lambda r: r["season"]["personalities"]["careful"].update(when_full="rest"), ["careful", "when_full"]
    )

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

    self.assertError(no_pos, ["tosaminato", "pos"])

    def bad_quests_per_day(r):
      r["map"]["places"][0]["quests_per_day"] = 0

    self.assertError(bad_quests_per_day, ["tosaminato", "quests_per_day"])

    def bad_path(r):
      r["map"]["roads"][0]["path"] = [[1, 2]]

    self.assertError(bad_path, ["path"])

  def test_bad_deliveries(self):
    def no_any(r):
      del r["deliveries"]["cargo"]["_any"]

    self.assertError(no_any, ["_any"])
    self.assertError(lambda r: r["deliveries"].update(ranks=[8, 4]), ["deliveries の ranks"])
    self.assertError(lambda r: r["deliveries"]["dangers"][0].update(texts=["{cargo}を運ぶ"]), ["texts"])
    self.assertError(lambda r: r["deliveries"]["cargo"].update(nowhere=[[], [], []]), ["nowhere"])
    self.assertError(lambda r: r["deliveries"]["cargo"].update(edo=[["塩"]]), ["edo"])

  def test_bad_season(self):
    def change(r):
      r["season"]["months"] = 0

    self.assertError(change, ["months"])


class BrokenDataTest(unittest.TestCase):
  """手で編集したデータの形が違っても、validate はスタックトレースではなく、読める誤りの一覧を返す。"""

  def test_validate_never_raises(self):
    """world/ の項目を 1 つずつ、削除・null・数・文字列・空・真偽値などに変えても、validate は例外を出さない。"""
    paths = []

    def walk(o, p=()):
      if p and p[0] != "background":  # ベースの地図のバイト列は、ファイルから読んだ中身（JSON ではない）
        paths.append(p)
      for k, v in o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else ():
        walk(v, p + (k,))

    walk(RAW)
    values = [None, 0, -1, "x", "", [], {}, True, [1, 2], 10**6]
    rng = random.Random(0)
    shallow = [p for p in paths if len(p) <= 2]  # 浅いところは全部の項目
    deep = rng.sample([p for p in paths if len(p) > 2], 150)  # 深いところは一部（毎回同じ。全部を試すには時間がかかる）
    flagged = 0
    for p in shallow + deep:
      for value in rng.sample(values, 3):
        for delete in (False, True):
          raw = copy.deepcopy(RAW)
          parent = raw
          for key in p[:-1]:
            parent = parent[key]
          if delete:
            parent.pop(p[-1])
          else:
            parent[p[-1]] = copy.deepcopy(value)
          try:
            errors = W.validate(raw)
          except Exception as e:  # noqa: BLE001
            self.fail(f"{p} を {'削除' if delete else repr(value)} にしたら {type(e).__name__}: {e}")
          self.assertTrue(all(isinstance(e, str) and e for e in errors), (p, errors))
          flagged += bool(errors)
    self.assertGreater(flagged, 100)  # 壊したデータは、たいてい誤りとして見つかる（検査が空振りしていない）

  def test_wrong_shapes_are_reported_in_readable_words(self):
    self.assertEqual(
      errors_after(lambda r: r["monsters"].__setitem__(33, None)),
      ["monsters.json の monsters は、辞書の一覧にしてください（辞書でないのは [33]）"],  # どの行か分かる
    )
    for change, words in (
      (lambda r: r["map"]["places"][0]["shop"].append([]), ["places[0]", "shop", "文字列の一覧"]),
      (lambda r: r["map"]["roads"][0].update({"from": []}), ["roads[0]", "from", "文字列"]),
      (lambda r: r["map"]["roads"][0].update(terrain=[[["plain"], 5]]), ["roads[0]", "terrain"]),
      (lambda r: r["monsters"][0].update(terrain=[]), ["monsters[0]", "terrain", "文字列"]),
      (lambda r: r.update(rarities=[1, 2]), ["rarities", "文字列の一覧"]),
      (lambda r: r.update(map=[]), ["map.json", "辞書"]),
      (lambda r: r["town_quests"]["kofu"][0].update(to=[]), ["kofu", "to", "文字列"]),
      (lambda r: r["map"].update(open=[[]]), ["open", "文字列の一覧"]),
    ):
      with self.subTest(words):
        errors = errors_after(change)
        self.assertTrue(any(all(w in e for w in words) for e in errors), errors)
        self.assertFalse(any("調べきれませんでした" in e for e in errors), errors)  # 想定した形の誤りは、個別に知らせる

  def test_unhashable_ids_and_tables_are_reported(self):
    self.assertTrue(
      any("ID は文字列" in e for e in errors_after(lambda r: r["map"]["places"][0].update(id=[]))),
    )
    for change in (
      lambda r: r["season"].update(road_treasure=[[[], 1]]),
      lambda r: r["season"].update(road_treasure=-1),
      lambda r: r["map"].update(roads=None),
      lambda r: r["map"]["places"][0].update(shop=None),
      lambda r: r.update(traits=None),
    ):
      errors = errors_after(change)
      self.assertFalse(any("調べきれませんでした" in e for e in errors), errors)

  def test_unexpected_failures_become_errors(self):
    """形の検査で拾えなかった想定外のデータで検査が落ちても、どの検査かを添えた誤りにする（残りの検査は続ける）。"""

    def boom(c):
      """テスト用の検査。"""
      raise AttributeError("'NoneType' object has no attribute 'get'")

    c = W._Check(copy.deepcopy(RAW))
    self.assertIsNone(W._run(c, boom))
    self.assertEqual(len(c.errors), 1)
    self.assertIn("テスト用の検査", c.errors[0])
    self.assertIn("AttributeError", c.errors[0])

  def test_missing_keys_in_the_files_are_reported(self):
    with tempfile.TemporaryDirectory() as tmp:
      d = Path(tmp)
      shutil.copytree(W.WORLD_DIR, d, dirs_exist_ok=True)
      for file, key in (("monsters.json", "monsters"), ("items.json", "rarities"), ("quests.json", "templates")):
        data = json.loads((W.WORLD_DIR / file).read_text(encoding="utf-8"))
        data.pop(key)
        (d / file).write_text(json.dumps(data), encoding="utf-8")
        with self.subTest(file), self.assertRaises(SystemExit) as cm:
          W.load_raw(d)
        self.assertIn(f"{file} に {key}", str(cm.exception))
        shutil.copy(W.WORLD_DIR / file, d / file)
      (d / "map.json").write_text("[]", encoding="utf-8")
      with self.assertRaises(SystemExit) as cm:
        W.load_raw(d)
      self.assertIn("map.json", str(cm.exception))


if __name__ == "__main__":
  unittest.main()
