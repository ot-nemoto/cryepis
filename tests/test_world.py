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

  def test_quest_texts_follow_the_story_rules(self):
    """依頼の文は、民話の語り口の決まり（docs/MYTH.md の 6・7）を守る: 設定の言葉を使わず、竜の思惑を断定しない。"""
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
      for word in ("創造主", "シーズン", "レベル", "経験値", "加護を与え", "利用"):
        self.assertNotIn(word, text, text)  # 設定の言葉・竜の思惑の断定は、作中の文に出さない

  def test_quest_texts_hint_at_the_teachings(self):
    """依頼の文に、教えどうしの争いと骨の禁令が、少しだけ混ざっている（docs/MYTH.md §5）。"""
    deliveries = " ".join(t for d in RAW["deliveries"]["dangers"] for t in d["texts"])
    hunts = " ".join(t for tier in RAW["hunts"]["texts"] for t in tier)
    towns = " ".join(q["text"] for qs in RAW["town_quests"].values() for q in qs)
    self.assertIn("教え", deliveries)
    self.assertIn("竜の骨", deliveries)
    self.assertIn("教え", hunts)
    self.assertTrue("竜の民" in towns and "星読み" in towns)

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
    self.assertError(lambda r: r["monsters"][1].update(elite=True), ["giant_rat", "強敵"])  # 狙える依頼がない
    self.assertError(lambda r: r["monsters"][-1].update(elite=True), ["den_lord", "elite"])  # ボスには付けない

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
      q = r["town_quests"]["karun"][0]
      boss = r["monsters"][-1]
      boss["terrain"] = next(m["terrain"] for m in r["monsters"] if m["id"] == q["monster"])  # 道の地形に合わせる
      q["monster"] = boss["id"]

    self.assertError(boss_quest, ["karun_3", "ボス以外"])

  def test_item_dex_is_checked(self):
    self.assertError(lambda r: r["items"][-1].update(dex="2"), ["crimson_grass_charm", "dex"])

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
      r["map"]["places"][0]["shop"].append("crimson_grass_charm")

    self.assertError(change, ["crimson_grass_charm", "price"])

  def test_bad_drop_table(self):
    def change(r):
      r["quest_ranks"][0]["item"] = [["mythic", 1]]

    self.assertError(change, ["mythic"])

  def test_drop_level_is_no_longer_used(self):
    self.assertError(lambda r: r["items"][5].update(drop_level=5), ["drop_level"])

  def test_rare_items_need_quest_towns(self):
    self.assertError(lambda r: r["items"][-1].pop("towns"), ["crimson_grass_charm", "towns"])
    self.assertError(lambda r: r["items"][-1].update(towns=["jade_tomb"]), ["towns", "jade_tomb"])
    self.assertError(lambda r: r["items"][0].update(towns=["salina"]), ["iron_sword", "common"])
    self.assertError(
      lambda r: r["items"][-1].update(towns=["aurea"]), ["crimson_grass_charm", "aurea", "開放"]
    )  # 開放していない町
    for bad in (5, "salina", [["salina"]]):  # 型が違っても落ちずにエラーを返す
      self.assertError(lambda r, bad=bad: r["items"][-1].update(towns=bad), ["crimson_grass_charm", "towns"])

  def test_every_town_needs_a_rare(self):
    def change(r):
      for it in r["items"]:
        if it["rarity"] == "rare" and "karun" in it["towns"]:
          it["towns"].remove("karun")

    self.assertError(change, ["karun", "rare"])

  def test_treasure_cannot_hold_rares(self):
    self.assertError(lambda r: r["season"]["treasure"].append(["rare", 1]), ["treasure", "rare"])
    self.assertError(
      lambda r: r["season"]["treasure"].append(["crimson_grass_charm", 1]), ["treasure", "crimson_grass_charm"]
    )

  def test_every_town_quest_has_some_rare(self):
    towns = {tid for i in RAW["items"] if i["rarity"] == "rare" for tid in i["towns"]}
    self.assertEqual(towns, set(RAW["map"]["open"]))

  def test_every_slot_has_items_in_a_shop(self):
    sold = {sid for p in RAW["map"]["places"] for sid in p.get("shop", [])}
    items = {i["id"]: i for i in RAW["items"]}
    self.assertEqual({items[i]["slot"] for i in sold}, set(W.SLOTS))

  def test_town_quests_are_checked(self):
    base = {
      "id": "karun_x",
      "rank": 1,
      "type": "hunt",
      "name": "試し",
      "to": "eques",
      "monster": "ice_wolf",
      "count": 3,
    }

    def town_quest(**change):
      def apply(r):
        r["town_quests"]["karun"].append(dict(base, **change))

      return apply

    self.assertEqual(errors_after(town_quest()), [])  # 町の討伐も書ける（今は強敵だけ）
    self.assertError(town_quest(to="salina"), ["karun_x", "直接つながる"])  # 道がない
    self.assertError(town_quest(monster="mountain_bandit"), ["karun_x", "その道に出る"])  # その道にいない魔物
    self.assertError(town_quest(rank=9), ["karun_x", "rank"])
    self.assertError(town_quest(count=0), ["karun_x", "count"])
    self.assertError(town_quest(type="deliver", to="karun"), ["karun_x", "別の町"])
    self.assertError(town_quest(type="deliver", to="aurea"), ["karun_x", "開放した別の町"])
    self.assertError(town_quest(id="hunt"), ["重複"])  # 共通の依頼と同じ ID
    self.assertError(lambda r: r["town_quests"].update(jade_tomb=[]), ["jade_tomb", "町の ID"])
    self.assertError(lambda r: r["town_quests"].update(aurea=[]), ["aurea", "開放した町"])

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
    self.assertEqual(set(RAW["town_quests"]), set(RAW["map"]["open"]))  # 町の依頼は強敵だけ（ない町もある）

  def test_item_text_is_checked(self):
    for bad in ("", "  ", 5, None, ["説明"], "あ" * (W.ITEM_TEXT_MAX + 1)):
      with self.subTest(bad=bad):
        self.assertError(lambda r, bad=bad: r["items"][0].update(text=bad), ["iron_sword", "text", "説明"])
    self.assertEqual(
      errors_after(lambda r: r["items"][0].update(text="あ" * W.ITEM_TEXT_MAX)), []
    )  # 上限ちょうどは通る
    self.assertEqual(errors_after(lambda r: r["items"][0].pop("text")), [])  # 説明は、書かなくてよい

  def test_ore_descriptions_match_between_story_and_world_doc(self):
    """鉱石の説明は、world/story.md（サイト）と docs/WORLD.md に同じ文を書いている（直したら、両方を直す）。"""

    names = ("熾焔鋼", "霧氷石", "紫電鉄", "宵翳鋼", "星屑鉄", "陽炎石")

    def ores(path):
      lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
      return [line for line in lines if any(line.startswith(f"- **{name}") for name in names)]

    story, doc = ores("world/story.md"), ores("docs/WORLD.md")
    self.assertEqual(len(story), 6)
    self.assertEqual(story, doc)

  def test_conflict_history_matches_between_story_and_world_doc(self):
    """教えの争いの出来事（骨の禁令・大災厄・霧氷の裁き）は、story.md と WORLD.md の歴史の表で、時代と出来事の文が同じ。"""

    def rows(path):
      lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
      found = {}
      for line in lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if line.startswith("|") and cells[0].startswith(("骨の禁令", "大災厄", "霧氷の裁き")):
          found[cells[0]] = cells[1]
      return found

    story, doc = rows("world/story.md"), rows("docs/WORLD.md")
    self.assertEqual(len(story), 3)
    for key in story:
      if key.startswith("大災厄"):  # WORLD.md の文は、生き残りの話が続く。誰のせいかが分かれる一文だけをそろえる
        for rows_ in (story, doc):
          self.assertIn("誰のせいかは、教えによって言うことが違う", rows_[key])
      else:
        self.assertEqual(story[key], doc[key])

  def test_calendar_is_called_tomoshi_goyomi(self):
    """年の数え方は「灯暦」。古い呼び名「星暦」は、設定とサイトの物語に残さない。"""
    for path in ("world/story.md", "docs/WORLD.md", "docs/MYTH.md"):
      text = (ROOT / path).read_text(encoding="utf-8")
      self.assertIn("灯暦", text, path)
    for path in [
      *ROOT.glob("world/*"),
      *ROOT.glob("docs/*.md"),
      *ROOT.glob("scripts/*.py"),
      *ROOT.glob("tools/worldgen/*.py"),
      ROOT / "README.md",
    ]:
      self.assertNotIn("星暦", path.read_text(encoding="utf-8"), str(path))

  def test_story_keeps_the_secret_and_the_rules(self):
    """サイトに出す物語に、作者だけの秘密（竜は前の人類がつくった AI で、三つの教えを埋め込んだ。docs/MYTH.md §7）と、設定の言葉を書かない。教えの語りは「と教える」「という」の形。"""
    text = (ROOT / "world/story.md").read_text(encoding="utf-8")
    self.assertIn("## 三つの教え", text)
    section = text.split("## 三つの教え")[1].split("\n## ")[0]
    for word in ("創造主", "利用", "シーズン", "経験値", "加護を与え", "思惑", "道具"):
      self.assertNotIn(word, section)
    for phrase in (
      "人を利用",
      "思惑",
      "AI",
      "機械",
      "人工",
      "埋め込",
    ):  # 秘密の言い換えも、出さない（文字どおりの語だけを見る簡単な守り）
      self.assertNotIn(phrase, text)
    for word in ("創造主", "利用"):  # 物語の全体にも、出さない
      self.assertNotIn(word, text)
    for teaching in ("星読み", "竜の民", "王国の教え"):
      self.assertIn(teaching, section)
    self.assertIn("どれが本当かは、誰も知らない", section)

  def test_item_texts_hint_at_the_teachings(self):
    """装備の説明に、教えのちがいが少し混ざっている（王の兵・星読みの兵・竜の民。docs/MYTH.md §5）。断定せず、秘密は出さない。"""
    texts = {i["id"]: i["text"] for i in RAW["items"]}
    self.assertIn("王の兵", texts["iron_helm"])
    self.assertIn("王", texts["iron_shield"])
    self.assertIn("教えの兵", texts["steel_sword"])
    self.assertIn("星読み", texts["steel_shield"])
    self.assertIn("竜の民", texts["lifeblood_charm"])
    joined = " ".join(texts.values())
    for word in ("創造主", "利用", "シーズン", "加護を与え"):
      self.assertNotIn(word, joined)

  def test_every_item_has_a_text(self):
    for item in RAW["items"]:
      self.assertTrue(item.get("text"), item["id"])  # 13 個すべてに、説明がある（民話の語り口）

  def test_too_strong_item(self):
    def change(r):
      r["items"].append(
        {"id": "god_sword", "name": "神の剣", "slot": "weapon", "rarity": "legendary", "atk": 99, "towns": ["salina"]}
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
    self.assertError(lambda r: r["season"].update(personalities={}), ["personalities"])
    self.assertError(lambda r: r["season"]["personalities"]["careful"].update(inn_below=2), ["careful", "inn_below"])
    self.assertError(
      lambda r: r["season"]["personalities"]["careful"]["quest"].update(levels=[1, 0]), ["careful", "quest.levels"]
    )
    self.assertError(
      lambda r: r["season"]["personalities"]["careful"]["quest"].update(order=["fast"]), ["careful", "quest.order"]
    )
    self.assertError(lambda r: r["season"]["personalities"]["brave"].update(spare="sleep"), ["brave", "spare"])
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

    self.assertError(no_pos, ["aurea", "pos"])

    def bad_quests_per_day(r):
      r["map"]["places"][0]["quests_per_day"] = 0

    self.assertError(bad_quests_per_day, ["aurea", "quests_per_day"])

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
    self.assertError(lambda r: r["deliveries"]["cargo"].update(salina=[["塩"]]), ["salina"])

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
      (lambda r: r["town_quests"]["muralis"][0].update(to=[]), ["muralis", "to", "文字列"]),
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
      lambda r: r["season"]["treasure"].append([[], 1]),  # ドロップ表のキーが一覧
      lambda r: r["season"].update(treasure=-1),
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
