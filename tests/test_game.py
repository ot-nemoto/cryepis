import copy
import json
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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


def ids(state):
  """持っている装備の ID（並べたもの）。"""
  return sorted(x["id"] for x in G.carried(state))


def day_of(state, d=START, seed="t"):
  return G.Day(WORLD, state, d, random.Random(seed))


def a_hunt(town=TOWN):
  """town の高札から、1 人で受ける討伐の依頼を 1 つ（大物は何人かで受けるので除く）。"""
  return next(
    q
    for i in range(60)
    for q in G.board(WORLD, town, START + timedelta(days=i), 1)
    if q["type"] == "hunt" and not q.get("party")
  )


def common_hunt(town=TOWN, rank=1, seed=0):
  """town の高札に出る共通の討伐を 1 つ（★ は rank）。"""
  t = next(t for t in WORLD["quests"] if t["type"] == "hunt")
  return G.make_quest(WORLD, t, town, START, random.Random(seed), rank)


def with_rares():
  """江戸に抽選確認用の epic・legendary を置いた世界。"""
  world = copy.deepcopy(WORLD)
  world["season"]["rare_drops"] = True  # レアの配布を止めていても、しくみは確かめる
  for item in world[
    "items"
  ].values():  # 江戸・小田原の大物の ★★★ 武具は外す（江戸は試しの武具だけ、小田原は epic なし）
    if item.get("rarity") == "epic" and isinstance(item.get("towns"), list):
      item["towns"] = [t for t in item["towns"] if t not in ("edo", "odawara")]
  world["items"]["x_epic"] = {
    "id": "x_epic",
    "name": "試しの鎧",
    "slot": "shield",
    "rarity": "epic",
    "def": 1,
    "towns": ["edo"],
  }
  world["items"]["x_legend"] = {
    "id": "x_legend",
    "name": "試しの剣",
    "slot": "weapon",
    "rarity": "legendary",
    "atk": 1,
    "towns": ["edo"],
  }
  return world


def everything_open():
  """開放していない町も含めた世界（航路などを確かめるため）。"""
  raw = W.load_raw()
  raw["map"].pop("open")
  return W.build(raw)


STRONG = {
  "id": "x",
  "name": "魔王",
  "level": 30,
  "hp": 999,
  "atk": 99,
  "def": 50,
  "exp": 1,
  "gold": 1,
  "terrain": "plain",
}


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
    G.carried(state).append({"id": "steel_sword", "skill": None})
    state["hero"].update(level=15, gold=999)
    last = START + timedelta(days=2)
    state["last_date"] = last.isoformat()
    nxt = START
    while G.season_of(WORLD, nxt) == 1:
      nxt += timedelta(days=1)
    G.advance(WORLD, state, nxt, 0)
    self.assertEqual(state["season"], 2)
    self.assertEqual(state["season_day"], 1)
    self.assertEqual(
      (state["hero"]["level"], state["place"], state["quest"]), (1, G.start_town(WORLD, state["id"], 2), None)
    )
    self.assertIn("steel_sword", ids(state))
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
    self.assertEqual(state["inventory"], [])  # 最初の手持ち（60文）では、★ の装備（240文〜）はまだ買えない（#245）
    self.assertIsNotNone(state["quest"])

  def test_journal_is_capped(self):
    self.assertEqual(len(play(G.JOURNAL_DAYS + 5)["journal"]), G.JOURNAL_DAYS)

  def test_quests_have_no_deadline(self):
    state = play(1, actions=0)
    state["quest"] = a_hunt()
    self.assertIsNone(state["quest"]["deadline"])
    for i in range(1, 30):
      G.advance(WORLD, state, START + timedelta(days=i), 0)  # 何日たっても失敗しない
    self.assertEqual(state["season_stats"]["quests_failed"], 0)


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
          self.assertIsNone(q["deadline"])
          self.assertGreater(q["cost"], 0)
          self.assertTrue(G.quest_ok(WORLD, q))
          if q["type"] == "hunt":  # 討伐は町から出る道で、その道に出る物の怪（大物は町ごとに決まった大物）
            self.assertEqual(q["report"], town)
            target = WORLD["monsters"][q["monster"]]
            if target.get("elite"):  # 大物の地形は道の地形と合わなくてよい（海坊主は海。行動回数は道によらない。#297）
              self.assertEqual(q["min_level"], target["level"])  # 物の怪のレベル以上で受けられる
            else:
              pool = [m for m, _ in WORLD["road_info"][(town, q["place"])]["monsters"]]
              self.assertIn(q["monster"], pool)
              self.assertNotIn("min_level", q)

  def test_elites_are_not_on_roads_and_need_their_level(self):
    elites = [mid for mid, m in WORLD["monsters"].items() if m.get("elite")]
    self.assertEqual(set(elites), {"sea_monk", "yeti", "rock_golem", "great_eagle"})
    on_roads = {m for i in WORLD["road_info"].values() for m, _ in i["monsters"]}
    self.assertFalse(on_roads & set(elites))  # 道では出会わない
    for town, quests in WORLD["town_quests"].items():  # どの大物の依頼も、物の怪のレベルから受けられる
      for quest in quests:
        if quest.get("monster") not in elites:
          continue
        lv = WORLD["monsters"][quest["monster"]]["level"]
        q = G.make_town_quest(WORLD, quest, town, START)
        self.assertEqual((q["min_level"], q["level"], q["count"]), (lv, [lv, lv], 1), quest["id"])
        self.assertFalse(G.suitable(q, lv - 1))
        self.assertTrue(G.suitable(q, lv))
        self.assertTrue(G.quest_ok(WORLD, q, steps=False))
    targets = {q.get("monster") for qs in WORLD["town_quests"].values() for q in qs}
    self.assertLessEqual(set(elites), targets)  # どの大物も、町の依頼で狙える
    quest = next(q for q in WORLD["town_quests"]["nagano"] if q.get("monster") == "yeti")
    q = G.make_town_quest(WORLD, quest, "nagano", START)
    # 大物にする前に貼られた依頼（Lv の決まりがなく、数も多い）は捨てる（高札からも、もののふの状態からも）
    old = {k: v for k, v in q.items() if k != "min_level"} | {"count": 5, "level": [2, 2]}
    self.assertFalse(G.quest_ok(WORLD, old, steps=False))
    self.assertFalse(G.quest_ok(WORLD, q | {"count": 5}, steps=False))

  def test_board_has_town_quests_once_a_day(self):
    names = {q["name"] for t in WORLD["town_quests"].values() for q in t}
    seen = 0
    for town in WORLD["towns"]:
      for i in range(10):
        board = G.board(WORLD, town, START + timedelta(days=i), 1)
        own = [q["template"] for q in board if q["template"] in {x["id"] for x in WORLD["town_quests"][town]}]
        self.assertEqual(len(own), len(set(own)))  # 同じ町の依頼は 1 日に 1 度だけ
        seen += len(own)
        for q in board:
          self.assertTrue(q["name"] in names or q["template"] in {t["id"] for t in WORLD["quests"]})
    self.assertGreater(seen, 0)

  def test_quest_rank_sets_reward_and_items(self):
    a, b = common_hunt("edo", 1), common_hunt("edo", 3)
    per = lambda q: q["reward"]["gold"] / q["cost"]  # noqa: E731  1 行動あたりの報酬（お金）
    self.assertEqual(per(b) / per(a), WORLD["quest_ranks"][2]["reward"] / WORLD["quest_ranks"][0]["reward"])
    self.assertEqual(b["reward"]["item"], WORLD["quest_ranks"][2]["item"])
    name = WORLD["monsters"][b["monster"]]["name"]
    self.assertEqual(b["name"], f"{name}の討伐")  # 名前は「{物の怪}の討伐」
    self.assertTrue(G.quest_text(WORLD, b).startswith(f"★★★ {name}の討伐（"))
    self.assertIn(name, b["text"])  # 説明は町の困りごとと ★ ごとの頼み方から作る
    hs = WORLD["hunts"]
    self.assertTrue(any(p in b["text"] for p in hs["places"]["edo"] + hs["places"]["_any"]), b["text"])

  def test_reward_is_gold_by_cost_and_delivery_pays_more(self):
    base, mult = WORLD["quest_reward"], [r["reward"] for r in WORLD["quest_ranks"]]
    self.assertEqual(base["exp"], 0)  # 依頼の報酬はお金だけ（経験値は物の怪を倒したときだけ）
    for town, qs in WORLD["town_quests"].items():
      for q in qs:
        x = G.make_town_quest(WORLD, q, town, START)
        rate = base["deliver_rate"] if q["type"] == "deliver" else 1  # 配達は 1 行動あたりのお金が多い
        self.assertEqual(x["reward"]["exp"], 0, q["id"])
        if x.get("party"):  # 大物の達成報酬は、★と行動力によらない決まった額（#297）
          self.assertEqual(x["reward"]["gold"], base["party_gold"]["elite"], q["id"])
          continue
        self.assertEqual(x["reward"]["gold"], round(base["gold"] * x["cost"] * mult[q["rank"] - 1] * rate), q["id"])

  def test_hunt_rank_by_level_and_count(self):
    cases = {(7, 1): 1, (5, 2): 1, (2, 5): 1, (6, 2): 2, (3, 5): 2, (12, 1): 2, (10, 3): 2, (11, 3): 3, (15, 5): 3}
    for (lv, n), rank in cases.items():  # 点数 = Lv ×（数 + 1）。15 以下 ★、40 以下 ★★、それより上 ★★★
      self.assertEqual(G.hunt_rank(WORLD, lv, n), rank, (lv, n))
    t = next(t for t in WORLD["quests"] if t["type"] == "hunt")
    for rank in (1, 2, 3):
      for i in range(40):
        q = G.make_quest(WORLD, t, "edo", START, random.Random(i), rank)
        self.assertEqual(q["rank"], rank)  # ほしい ★ の討伐を作る
        self.assertEqual(G.hunt_rank(WORLD, WORLD["monsters"][q["monster"]]["level"], q["count"]), rank)
        self.assertTrue(1 <= q["count"] <= 5)
    seen = {G.make_quest(WORLD, t, "edo", START, random.Random(i), 4)["rank"] for i in range(20)}
    self.assertEqual(seen, {3})  # ★★★★ は作れないので ★★★ に下げる（★★★★ は WORLD・RAID 用）
    world = copy.deepcopy(WORLD)  # Lv8 以上の物の怪しかいない道では ★ は作れないので、★★ に上げて作る
    for info in world["road_info"].values():
      info["monsters"] = [(m, w) for m, w in info["monsters"] if world["monsters"][m]["level"] >= 8]
    self.assertEqual(G.make_quest(world, t, "edo", START, random.Random(0), 1)["rank"], 2)

  def test_rank_falls_back_to_what_the_town_has(self):
    for i in range(60):  # 町に大物を受けられるもののふがいなければ、★★★★ の大物の依頼は出ない
      for q in G.draw_quests(WORLD, "odawara", START + timedelta(days=i), 1, 3, levels=[]):
        self.assertLessEqual(q["rank"], 3)  # 共通の依頼では ★★★★ は作らない（★★★ に下げる）

  def test_hunt_report_gives_reward(self):
    state = play(1, actions=0)
    quest = a_hunt()
    quest["progress"] = quest["count"]
    state["quest"] = quest
    gold = state["hero"]["gold"]
    with mock.patch.object(G.Day, "shop"):  # 買い物はしない
      day_of(state).visit_town(TOWN)
    self.assertIsNone(state["quest"])
    self.assertEqual(state["season_stats"]["quests_done"], 1)
    self.assertGreaterEqual(state["hero"]["gold"], gold + quest["reward"]["gold"])

  def test_deliver_completes_on_arrival(self):
    state = play(1, actions=0)
    to = WORLD["towns"][1]
    state["quest"] = {
      "type": "deliver",
      "name": "配達",
      "template": "deliver",
      "town": TOWN,
      "place": to,
      "report": to,
      "monster": None,
      "count": 1,
      "progress": 0,
      "level": [1, 5],
      "deadline": "2099-01-01",
      "reward": {"exp": 5, "gold": 5, "item": None},
    }
    day_of(state).visit_town(to)
    self.assertIsNone(state["quest"])
    self.assertEqual(state["home_town"], to)

  def test_same_quest_is_not_taken_twice_a_day(self):
    state = play(1, actions=0)
    state["hero"]["level"] = 99  # どの依頼も合わない…ことがないよう、高札の依頼をすべて受けられるようにする
    quests = G.board(WORLD, TOWN, START, 1)
    state["hero"]["level"] = max((q["level"][0] for q in quests if q["level"]), default=1)
    day = day_of(state)
    ids = []
    for _ in range(len(quests) + 1):
      state["quest"] = None
      day.take_quest(TOWN)
      if state["quest"]["type"] != "patrol":
        ids.append(state["quest"]["id"])
    self.assertEqual(len(ids), len(set(ids)))
    self.assertEqual(state["quest"]["type"], "patrol")  # 受けられる依頼が尽きたら見回り

  def test_quests_taken_by_others_cannot_be_taken(self):
    guild = G.new_guild()
    a, b = play(1, "a", actions=0), play(1, "b", actions=0)
    town = a["place"]
    b["place"] = town
    G.refresh_guild(WORLD, guild, START, 1)
    posted = [e["id"] for e in guild["towns"][town]]
    self.assertEqual(len(posted), G.quests_per_day(WORLD, town))
    a["hero"]["level"] = b["hero"]["level"] = 99
    a["personality"] = b["personality"] = None  # 性格の Lv の範囲で選り好みしない（高札の決まりだけを見る）
    ids = []
    for state in [a, b] * len(posted):
      if len(ids) == len(posted):
        break
      state["quest"] = None
      G.Day(WORLD, state, START, random.Random(len(ids)), guild).take_quest(town)
      ids.append(state["quest"]["id"])
    self.assertEqual(sorted(ids), sorted(posted))  # 全部の依頼が 1 度ずつ受けられた
    # 何人かで受ける大物の依頼は、受けた人を members に入れる（ほかは holder）
    held = {h for e in guild["towns"][town] for h in (e.get("members") or [e["holder"]])}
    self.assertEqual(held, {"a", "b"})
    self.assertTrue(all(e["status"] == "held" for e in guild["towns"][town] if e.get("party", 1) == 1))
    self.assertTrue(all(k not in a["quest"] for k in G.GUILD_FIELDS))  # もののふの依頼には高札の項目を持たせない
    a["quest"] = None
    day = G.Day(WORLD, a, START, random.Random(0), guild)
    day.take_quest(town)  # もう残っていない
    self.assertEqual(a["quest"]["type"], "patrol")
    self.assertIn("旅に出る", day.events[-1])

  def test_quests_per_day_depends_on_the_town(self):
    self.assertEqual(G.quests_per_day(WORLD, "kyoto"), 6)
    self.assertEqual(G.quests_per_day(WORLD, "imaicho"), 3)
    self.assertTrue(
      all(
        G.quests_per_day(WORLD, t) == WORLD["places"][t]["quests_per_day"]
        for t, p in WORLD["places"].items()
        if p["type"] == "town"
      )
    )  # どの町にも書いてある

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
    self.assertTrue(G.suitable(q, 30))  # 物の怪の強さはどこでも同じなので、弱い依頼も受ける


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

  def test_keeps_going_on_the_road_when_hp_is_low(self):
    # 甲府 →（3 行動）→ 長野 の 1 歩目。HP 1・丸薬なしでも引き返さずに進む
    state = self.low_hp_on_road("kofu", "nagano", 3)
    day = day_of(state)
    day.road_event = lambda a, b, stroll=False: True  # 道の出来事は何も起きない
    day.act()
    self.assertEqual((state["place"], state["road"]), ("kofu", {"to": "nagano", "left": 2}))
    self.assertFalse(any("引き返" in e for e in day.events))

  def test_start_town_is_random_per_hero_and_season(self):
    towns = {G.start_town(WORLD, f"h{i}", None) for i in range(30)}
    self.assertEqual(towns, set(WORLD["starts"]))  # どの候補の町からも始まる
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
    state.update(personality="diligent", trait="lucky")  # バランス: 同じ点数なら、マイナスのない安いもの
    state["inventory"] = {"wooden_helm": 1, "iron_helm": 1, "steel_sword": 1}  # 頭の品はどちらも 1 点、編笠のほうが安い
    worn = G.equipment(WORLD, state)
    self.assertEqual((worn["weapon"], worn["helmet"], worn["accessory"]), ("steel_sword", "wooden_helm", None))
    s = G.stats(WORLD, state)
    self.assertEqual(s["def"], state["hero"]["base_def"] + 1)
    self.assertEqual(s["atk"], state["hero"]["base_atk"] + 2)  # 業物の太刀は攻 +2
    state.update(personality="brave", trait="guard")  # 鉄壁 × 伸ばす: 防の上がる鉢金（#244）
    self.assertEqual(G.equipment(WORLD, state)["helmet"], "iron_helm")
    state.update(personality="careful", trait="strong")  # 怪力 × 補う: 不得意な HP。頭には HP の品がないのでバランス
    self.assertEqual(G.equipment(WORLD, state)["helmet"], "wooden_helm")

  def test_rare_item_is_recorded(self):
    state = G.new_state(WORLD)
    day = day_of(state)
    day.obtain("steel_sword", "宝箱から")
    self.assertEqual(state["season_stats"]["rares"], ["steel_sword"])
    self.assertIn("★★「業物の太刀」", day.events[0])
    day.obtain("iron_sword", "宝箱から")
    self.assertIn("宝箱から★「打刀」を手に入れた", day.events[1])  # ふつうの装備も ★
    self.assertFalse(day.events[1].endswith("！"))  # ！はレアだけ
    self.assertIn("装備した", day.events[0])

  def test_rares_drop_only_from_their_towns(self):
    world = with_rares()
    day = G.Day(world, G.new_state(world), START, random.Random("t"))  # Lv.1 でも出る（レベルの制限はない）
    self.assertEqual({day.draw([["legendary", 1]], "edo") for _ in range(50)}, {"x_legend"})
    self.assertEqual({day.draw([["epic", 1]], "edo") for _ in range(50)}, {"x_epic"})
    # 小田原には epic・legendary がないので、rare（鍬形の兜）まで下げる
    self.assertEqual({day.draw([["legendary", 1]], "odawara") for _ in range(20)}, {"steel_helm"})
    self.assertEqual({day.draw([["rare", 1]], None) for _ in range(20)}, {None})  # 宝箱（町なし）では出ない
    commons = {day.draw([["common", 1]]) for _ in range(300)}
    sold = {iid for t in WORLD["towns"] for iid in WORLD["places"][t]["shop"]}  # 開放した町の店の品だけ
    self.assertEqual(commons, sold)
    self.assertIsNone(day.draw([["none", 1]]))
    self.assertEqual(day.draw([["x_legend", 1]], "edo"), "x_legend")
    self.assertIsNone(day.draw([["x_legend", 1]], "nagano"))  # ID を直接書いても、その町のものだけ
    self.assertEqual(day.draw([["iron_sword", 1]], "nagano"), "iron_sword")

  def test_items_sold_only_in_closed_towns_do_not_drop(self):
    raw = W.load_raw()
    raw["items"].append({"id": "x_far", "name": "遠くの剣", "slot": "weapon", "rarity": "common", "atk": 1, "price": 1})
    next(p for p in raw["map"]["places"] if p["id"] == "kyoto")["shop"].append("x_far")  # 開放していない京都だけで売る
    world = W.build(raw)
    day = G.Day(world, G.new_state(world), START, random.Random("t"))
    self.assertNotIn("x_far", world["region_items"])
    self.assertNotIn("x_far", {day.draw([["common", 1]]) for _ in range(300)})
    self.assertIsNone(day.draw([["x_far", 1]], "edo"))  # ID を直接書いても出ない

  def test_every_town_gives_something_for_every_rarity(self):
    world = copy.deepcopy(WORLD)
    world["season"]["rare_drops"] = True
    day = G.Day(world, G.new_state(world), START, random.Random("t"))
    for town in world["towns"]:
      for rarity in world["rarities"]:
        self.assertIsNotNone(day.draw([[rarity, 1]], town), (town, rarity))

  def test_rare_drops_can_be_stopped(self):
    world = with_rares()
    world["season"]["rare_drops"] = False
    day = G.Day(world, G.new_state(world), START, random.Random("t"))
    for key in ("rare", "epic", "legendary", "x_epic"):  # レアは当たっても何も出ない（ID を直接書いても）
      self.assertEqual({day.draw([[key, 1]], "edo") for _ in range(20)}, {None}, key)
    self.assertIsNotNone(day.draw([["common", 1]], "edo"))  # common は出る

  def test_quest_reward_uses_the_quest_town(self):
    world = with_rares()
    state = play(1, actions=0)
    quest = a_hunt() | {"progress": 99, "town": "edo", "reward": {"exp": 0, "gold": 0, "item": [["legendary", 1]]}}
    state["quest"] = quest
    G.Day(world, state, START, random.Random("t")).report(quest)
    self.assertIn("x_legend", ids(state))

  def test_lucky_redraws_once_when_reporting_a_normal_quest(self):
    state = G.new_state(WORLD)
    state["trait"] = "lucky"
    quest = a_hunt() | {"progress": 99, "town": "edo"}
    state["quest"] = quest
    day = day_of(state)
    with mock.patch.object(day, "draw_once", side_effect=[None, "steel_sword"]) as draw:
      day.report(quest)
    self.assertEqual(ids(state), ["steel_sword"])
    self.assertEqual(draw.call_count, 2)

  def test_rarity_marks_follow_the_rarities(self):
    self.assertEqual([G.rarity_mark(WORLD, r) for r in WORLD["rarities"]], ["★", "★★", "★★★", "★★★★"])
    world = {**WORLD, "rarities": WORLD["rarities"] + ["mythic"]}  # 増やしても ★ が 1 つずつ増える
    self.assertEqual(G.rarity_mark(world, "mythic"), "★★★★★")

  def test_four_slots_are_equipped(self):
    self.assertEqual(W.SLOTS, ("weapon", "helmet", "shield", "accessory"))  # 胴の部位の ID は shield のまま
    state = G.new_state(WORLD)
    state["inventory"] = {"iron_sword": 1, "iron_helm": 1, "iron_shield": 1, "lifeblood_charm": 1, "wooden_shield": 1}
    worn = G.equipment(WORLD, state)
    self.assertEqual(list(worn.values()), ["iron_sword", "iron_helm", "wooden_shield", "lifeblood_charm"])
    s = G.stats(WORLD, state)
    self.assertEqual(s["def"], state["hero"]["base_def"] + 2 + 2)  # 鉢金 +2・刺子の着物 +2（鎖帷子は同じ 1 点で速 -1）
    self.assertEqual(s["spd"], state["hero"]["base_spd"])
    self.assertEqual(s["atk"], state["hero"]["base_atk"] + 1)
    self.assertEqual(s["max_hp"], state["hero"]["max_hp"] + 5)  # 延命の御守は HP +5

  def test_equal_points_prefer_items_without_minus(self):
    state = G.new_state(WORLD)
    state["inventory"] = {"iron_axe": 1, "iron_sword": 1}  # どちらも 1 点
    self.assertEqual(G.equipment(WORLD, state)["weapon"], "iron_sword")  # 速 -2 のない打刀

  def test_equipment_follows_the_item_points(self):
    state = G.new_state(WORLD)
    state["inventory"] = {"wooden_shield": 1, "iron_shield": 1}  # どちらも 1 点。速 -1 のない刺子の着物
    self.assertEqual(G.equipment(WORLD, state)["shield"], "wooden_shield")
    for iid in ("wooden_helm", "iron_helm", "wooden_shield", "iron_shield"):  # 頭と胴の品はどれも 1 点
      self.assertEqual(G.item_score(WORLD, WORLD["items"][iid]), 1, iid)
    self.assertEqual(G.item_score(WORLD, WORLD["items"]["iron_axe"]), 1)  # 攻 +2・速 -2 は 2 − 1


class RoadTest(unittest.TestCase):
  def test_road_battles_have_one_monster(self):
    state = play(1, actions=0)
    state["hero"].update(max_hp=999, hp=999)
    world = copy.deepcopy(WORLD)
    world["season"]["road_events"] = {"battle": 1}
    day = G.Day(world, state, START, random.Random(0))
    town = state["place"]
    for _ in range(10):  # 道の戦いは 1 体だけ（何体かと戦うのは討伐の依頼だけ）
      before = len(day.events)
      self.assertTrue(day.road_event(town, world["neighbors"][town][0]))
      self.assertEqual(sum("を倒した" in e for e in day.events[before:]), 1)

  def test_encounters_follow_the_hero_level(self):
    road = WORLD["road_info"][("nagano", "kofu")]["monsters"]
    lv = WORLD["monsters"]
    for level in (1, 5, 8, 15, 18, 20):
      got = G.encounters(WORLD, road, level)
      levels = {lv[m]["level"] for m, _ in got}
      if level <= 18:  # Lv18 なら Lv15〜19（Lv15 がいる）
        self.assertTrue(levels and all(level - 3 <= x <= level + 1 for x in levels), (level, levels))
      else:  # 道の物の怪は Lv15 まで。Lv17〜21 にはいないので、いちばん近い Lv15
        self.assertEqual(levels, {15})
      self.assertEqual([m for m, _ in got], [m for m, _ in road if m in dict(got)])  # 道の並びのまま

      def share(pairs, got=got):  # 地方ごとの割合（残った物の怪のいる地方だけで比べる）
        areas = {lv[m]["area"] for m, _ in got}
        out = {}
        for m, w in pairs:
          if lv[m]["area"] in areas:
            out[lv[m]["area"]] = out.get(lv[m]["area"], 0) + w
        return {t: round(v / sum(out.values()), 6) for t, v in out.items()}

      self.assertEqual(share(got), share(road), level)  # 地方ごとの割合は道のまま
    self.assertEqual({lv[m]["level"] for m, _ in G.encounters(WORLD, road, 1)}, {1, 2})

  def test_hunt_targets_spread_over_levels(self):
    # 共通の討伐の依頼で狙う物の怪の Lv は、その ★ になる Lv から均等に選ぶ（平原に多い Lv にかたよらない）
    t = next(t for t in WORLD["quests"] if t["type"] == "hunt")
    levels = {G.make_quest(WORLD, t, "edo", START, random.Random(i), 2)["monster"] for i in range(300)}
    lv = {WORLD["monsters"][m]["level"] for m in levels}
    self.assertGreater(len(lv), 5)  # ★★ になる Lv（3〜20 の点数になる Lv）の物の怪がいろいろ出る

  def test_every_road_has_monsters_for_every_level(self):
    top = max(WORLD["monsters"][m]["level"] for i in WORLD["road_info"].values() for m, _ in i["monsters"])
    for (a, b), info in WORLD["road_info"].items():  # 平原はどの道にもあるので、Lv1〜15 のどのもののふにも相手がいる
      for level in range(1, top + 1):
        levels = {WORLD["monsters"][m]["level"] for m, _ in G.encounters(WORLD, info["monsters"], level)}
        self.assertTrue(all(level - 3 <= x <= level + 1 for x in levels), (a, b, level, levels))

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
    state["quest"] = {
      "type": "deliver",
      "name": "配達",
      "template": "deliver",
      "town": "x",
      "place": town,
      "report": town,
      "monster": None,
      "count": 1,
      "progress": 0,
      "level": [1, 1],
      "deadline": "2099-01-01",
      "reward": {"exp": 1, "gold": 1, "item": None},
    }
    day = day_of(state)
    self.assertTrue(day.act())
    self.assertEqual(state["season_stats"]["quests_done"], 1)

  def test_hunt_takes_go_hunt_and_back(self):
    state = play(1, actions=0)
    state["hero"].update(max_hp=999, hp=999)  # 負けないようにして、段取りどおりに進むか確かめる
    state.update(place="kofu", home_town="kofu")  # 甲府から出る道は、どれも 3 行動
    quest = next(  # 長い道（3 行動）の、弱い物の怪の討伐（道の長さによらず 3 行動になるか）
      q
      for i in range(200)
      for q in [common_hunt(state["place"], 1, i)]
      if WORLD["roads"][(q["report"], q["place"])] >= 3 and WORLD["monsters"][q["monster"]]["level"] <= 2
    )
    town, road = quest["report"], WORLD["roads"][(quest["report"], quest["place"])]
    self.assertEqual(road, 3)
    self.assertEqual(quest["steps"], [["go", 1], ["hunt", 1], ["back", 1]])  # 道の長さによらず、何体いても 3 行動
    self.assertEqual(quest["cost"], 3)
    state["quest"] = quest
    day = day_of(state)
    target = WORLD["monsters"][quest["monster"]]["name"]
    for _ in range(quest["cost"]):
      self.assertIsNotNone(state["quest"])
      self.assertTrue(day.act())
      self.assertEqual(state["place"], town)  # 町からは動かない（狩り場は町の近く）
    self.assertIsNone(state["quest"])  # ちょうど必要な行動力で終わる
    self.assertEqual(sum(f"{target}" in e and "を倒した" in e for e in day.events), quest["count"])
    self.assertEqual(state["season_stats"]["quests_done"], 1)

  def test_one_quest_a_day_then_stroll(self):
    state = play(1, actions=0)
    state["hero"].update(max_hp=999, hp=999)
    town = state["place"]
    quest = next(
      q for i in range(50) for q in [common_hunt(town, 1, i)] if WORLD["monsters"][q["monster"]]["level"] <= 2
    )
    state.update(quest=quest, taken=["x"])
    day = day_of(state)
    for _ in range(quest["cost"] + 3):
      self.assertTrue(day.act())
    self.assertIsNone(state["quest"])  # 終わったあと、その日はもう受けない
    self.assertTrue(any("の周りを散策した" in e for e in day.events))

  def test_deliver_cost_is_the_distance(self):
    t = next(x for x in WORLD["quests"] if x["type"] == "deliver")
    quests = [G.make_quest(WORLD, t, "edo", START, random.Random(i), r) for i in range(40) for r in (1, 2, 3)]
    for q in quests:
      self.assertEqual(q["cost"], WORLD["dist"]["edo"][q["place"]])
    nagano = next(q for q in quests if q["place"] == "nagano")
    self.assertGreater(nagano["cost"], WORLD["roads"].get(("edo", "nagano"), 0))  # 直接の道はなく、経由する

  def test_delivery_rank_danger_and_cargo(self):
    cases = {(2, 0): 1, (4, 0): 1, (2, 1): 1, (6, 0): 2, (4, 1): 2, (2, 2): 2, (6, 1): 3, (4, 2): 3, (6, 2): 3}
    for (d, danger), rank in cases.items():  # 点数 = 距離 ×（危なさ + 1）。4 以下 ★、8 以下 ★★、それより上 ★★★
      self.assertEqual(G.deliver_rank(WORLD, d, danger), rank, (d, danger))
    t = next(x for x in WORLD["quests"] if x["type"] == "deliver")
    dv = WORLD["deliveries"]
    for rank in (1, 2, 3):
      for i in range(30):
        q = G.make_quest(WORLD, t, "edo", START, random.Random(i), rank)
        self.assertEqual(q["rank"], rank)
        self.assertEqual(G.deliver_rank(WORLD, q["cost"], q["danger"]), rank)
        self.assertIn(q["cargo"], dv["cargo"]["edo"][q["danger"]] + dv["cargo"]["_any"][q["danger"]])
        self.assertEqual(q["name"], f"{q['cargo']}の配達")
        self.assertIn(q["cargo"], q["text"])
        self.assertIn(WORLD["places"][q["place"]]["name"], q["text"])
        self.assertEqual(q.get("min_level", 0), dv["dangers"][q["danger"]]["min_level"])  # 危なさ 2 は Lv5 から
        self.assertEqual(q["reward"]["exp"], 0)

  def test_delivery_danger_raises_monster_levels_on_the_way(self):
    t = next(x for x in WORLD["quests"] if x["type"] == "deliver")
    quest = next(
      q for i in range(200) for q in [G.make_quest(WORLD, t, "edo", START, random.Random(i), 3)] if q["danger"] == 2
    )
    state = G.new_state(WORLD)
    state.update(season=1, place="edo", quest=quest)
    state["hero"]["level"] = 6
    day = G.Day(WORLD, state, START, random.Random(0))
    self.assertEqual(day.danger_shift(), 4)  # 運んでいる間は、出会う物の怪の Lv を 4 上げる
    quest["progress"] = 1
    self.assertEqual(day.danger_shift(), 0)  # 届けたあとは上げない
    state["quest"] = None
    self.assertEqual(day.danger_shift(), 0)

  def test_sea_route_has_monsters_of_both_areas(self):
    world = everything_open()  # 今は航路を開放していない
    (a, b), _ = next((k, i) for k, i in world["road_info"].items() if i["kind"] == "sea")
    state = G.new_state(world)
    state.update(season=1, place=a)
    day = G.Day(world, state, START, random.Random("sea"))
    for _ in range(30):
      day.road_event(a, b)
    areas = {world["places"][a]["area"], world["places"][b]["area"]}  # 航路も、両端の町の地方の物の怪（#265）
    names = {m["name"] for m in world["monsters"].values() if m.get("area") in areas}
    fought = [e for e in day.events if "を倒した" in e]
    self.assertTrue(fought)
    self.assertTrue(all(any(n in e for n in names) for e in fought), fought)


class PartyBattleTest(unittest.TestCase):
  """何人かで力を合わせて戦う（#72）。1 人なら Day.battle と同じ戦い。"""

  def party(self, n, level=20, personality="careful", potions=3, seed="p"):
    days = []
    for k in range(n):
      state = G.new_state(WORLD, f"h{k}")
      state.update(personality=personality)
      state["hero"]["level"] = level
      G.set_base_stats(WORLD, state)
      state["hero"].update(hp=G.stats(WORLD, state)["max_hp"], potions=potions, exp=0)
      days.append(day_of(state, seed=f"{seed}{k}"))
    return days

  def test_solo_is_the_same_as_battle(self):
    boss = WORLD["monsters"]["den_lord"]
    for seed in range(20):
      a, b = self.party(1, level=15, seed=seed), self.party(1, level=15, seed=seed)
      self.assertEqual(a[0].battle(dict(boss)), G.party_battle(b, dict(boss)))
      self.assertEqual((a[0].state["hero"], a[0].events), (b[0].state["hero"], b[0].events))

  def test_skills_of_the_gear_for_the_battle_work_after_winning(self):
    # 毒の物の怪には、毒よけの御守に着け替えて戦う。勝ったあとの毒も、着け替えた御守のスキルで防ぐ（ふだんの御守ではない）
    for seed in range(10):
      state = self.party(1, level=15, seed=seed)[0].state
      state["inventory"] = [{"id": "lifeblood_charm", "skill": None}, {"id": "soaring_leaf_charm", "skill": "antidote"}]
      self.assertNotEqual(G.worn(WORLD, state)["accessory"]["skill"], "antidote")  # ふだんは毒よけではない
      day = day_of(state, seed=seed)
      self.assertTrue(day.battle(dict(WORLD["monsters"]["venom_moth"])))
      self.assertFalse(any("毒で HP" in e for e in day.events), day.events)
      self.assertIsNone(day.gear)

  def test_only_the_one_who_finishes_gets_the_inspiration_bonus(self):
    boss = dict(WORLD["monsters"]["den_lord"], hp=600)
    days = self.party(3, seed="i")
    for d in days:  # 悟り: とどめの一撃が会心なら経験値が増える
      d.state["inventory"] = [{"id": "iron_sword", "skill": "inspiration"}]
    sides = [d.ready(dict(boss), W.trait_effect(boss)) | {"party": 3, "killing_crit": True} for d in days]
    sides[0]["killer"] = True
    try:
      for d, c in zip(days, sides):
        d.gain_exp = lambda exp, d=d: d.hero.__setitem__("exp", exp)
        d.win(dict(boss, exp=100), c)
    finally:
      for d in days:
        d.gear = None
    factor = WORLD["skills"]["inspiration"]["value"]
    self.assertEqual([d.hero["exp"] for d in days], [W.half_up(100 * factor), 100, 100])

  def test_together_they_win_and_share_the_gold(self):
    boss = dict(WORLD["monsters"]["den_lord"], hp=900)
    wins = 0
    for seed in range(30):
      days = self.party(3, seed=seed)
      gold = [d.hero["gold"] for d in days]
      if not G.party_battle(days, dict(boss)):
        continue
      wins += 1
      for d, g in zip(days, gold):
        self.assertEqual(d.hero["gold"] - g, boss["gold"] // 3)  # お金は人数で割る（切り捨て）
        self.assertGreaterEqual(d.hero["hp"], 1)  # 倒れていたもののふも起き上がる
        self.assertTrue(any("酒呑童子〔硬い〕を倒した" in e for e in d.events))
        self.assertEqual(d.state["season_stats"]["kills"], 1)
    self.assertGreater(wins, 0)
    solo = sum(G.party_battle(self.party(1, seed=seed), dict(boss)) for seed in range(30))
    self.assertLess(solo, wins)  # 3 人のほうが勝ちやすい

  def test_monster_attacks_everyone_and_all_down_is_a_loss(self):
    boss = dict(WORLD["monsters"]["den_lord"], hp=5000, atk=200)
    days = self.party(3, potions=0)
    self.assertFalse(G.party_battle(days, boss))
    for d in days:  # 全員が倒れるまで戦う（物の怪はみんなを攻撃する）
      self.assertEqual(d.hero["hp"], 0)
      self.assertTrue(any("酒呑童子〔硬い〕に倒された" in e for e in d.events), d.events)
      self.assertIsNone(d.gear)  # 戦いの外では、その戦いの装備を外す

  def test_battle_sim_party_is_repeatable(self):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import battle_sim as B

    boss = dict(WORLD["monsters"]["den_lord"], hp=900)
    pers = sorted(WORLD["season"]["personalities"])
    a = B.fight(WORLD, boss, "lucky", 20, [], 3, 20, party=3, personalities=pers)
    self.assertEqual(a, B.fight(WORLD, boss, "lucky", 20, [], 3, 20, party=3, personalities=pers))
    self.assertGreater(a[0], B.fight(WORLD, boss, "lucky", 20, [], 3, 20, party=1, personalities=pers)[0])


class PartyQuestTest(unittest.TestCase):
  """大物は 2 人、主は 3 人で受ける依頼（#72）。"""

  def setUp(self):
    self.world = copy.deepcopy(WORLD)

  def hero(self, hid, level=20, place="nagano"):
    state = G.new_state(self.world, hid, hid)
    state.update(personality="careful", place=place, home_town=place, season=1)
    state["hero"]["level"] = level
    G.set_base_stats(self.world, state)
    state["hero"].update(hp=G.stats(self.world, state)["max_hp"], potions=3)
    return state

  def elite_entry(self, guild, mid="yeti"):
    q = next(q for q in self.world["town_quests"]["nagano"] if q.get("monster") == mid)
    e = G.make_town_quest(self.world, q, "nagano", START)
    e.update(id="e1", posted=START.isoformat(), status="open", holder=None, returned=False)
    e.update(members=[], arrived=[], gather_until=None)
    guild.setdefault("towns", {}).setdefault("nagano", []).append(e)
    return e

  def at_the_hunt(self, state, entry):
    quest = copy.deepcopy({k: v for k, v in entry.items() if k not in G.GUILD_FIELDS})
    quest["steps"] = [["hunt", 1], ["back", 2]]
    quest["away"] = 2
    state["quest"] = quest

  def test_each_bando_town_has_its_own_elite(self):
    """坂東の 4 町に固有の大物を 1 体ずつ置く。どれも ★★★★・3 行動・決まった達成報酬（#297）。"""
    want = {"edo": "sea_monk", "odawara": "great_eagle", "kofu": "rock_golem", "nagano": "yeti"}
    gold = self.world["quest_reward"]["party_gold"]
    for town, mid in want.items():
      elites = [q for q in self.world["town_quests"][town] if self.world["monsters"][q["monster"]].get("elite")]
      self.assertEqual([q["monster"] for q in elites], [mid], town)
      e = G.make_town_quest(self.world, elites[0], town, START)
      self.assertEqual((e["rank"], e["cost"], e["party"]), (4, 3, 2), town)
      self.assertEqual(e["steps"], [["go", 1], ["hunt", 1], ["back", 1]], town)
      self.assertEqual(e["reward"]["gold"], gold["elite"], town)
    umi = self.world["monsters"]["sea_monk"]
    self.assertEqual(
      (umi["name"], umi["level"], umi["hp"], umi["atk"], umi["def"], umi["spd"], umi["dex"], umi["terrain"]),
      ("海坊主", 13, 1160, 60, 10, 12, 6, "sea"),
    )
    self.assertNotIn("element", umi)
    self.assertNotIn("trait", umi)
    for town in want:  # 大物の ★★★ 武具は、坂東の 4 町で同じ 4 種
      self.assertEqual(
        W.party_gear(self.world["items"], town),
        ["cross_spear", "iron_face_guard", "road_guard_charm", "yamabushi_robe"],
      )

  def test_elite_quests_made_before_297_are_dropped(self):
    """★・行動力・報酬の決まりを変える前（#297）に貼られた・受けられた大物の依頼は捨てる。黙って消さず日誌に残す。"""
    q = next(q for q in self.world["town_quests"]["nagano"] if q["monster"] == "yeti")
    e = G.make_town_quest(self.world, q, "nagano", START)
    self.assertTrue(G.quest_ok(self.world, e))
    old = copy.deepcopy(e) | {"rank": 2, "cost": 5, "steps": [["go", 2], ["hunt", 1], ["back", 2]]}
    old["reward"] = dict(e["reward"], gold=80)
    for bad in (
      old,
      e | {"rank": 2},
      e | {"cost": 5},
      e | {"reward": dict(e["reward"], gold=80)},
      e | {"template": "nagano_4"},
    ):
      self.assertFalse(G.quest_ok(self.world, bad, steps=False))
    boss = G.make_boss_quest(self.world, "den_lord", START)
    self.assertTrue(G.quest_ok(self.world, boss))
    self.assertFalse(G.quest_ok(self.world, boss | {"reward": dict(boss["reward"], gold=160)}))
    state = self.hero("a")
    state["quest"] = old
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "a.json"
      G.save_state(state, path)
      loaded = G.load_state(self.world, path, "a")
    self.assertIsNone(loaded["quest"])
    self.assertEqual(loaded["inbox"], ["受けていた「吹雪の雪女」は、決まりが変わったので取り下げられた"])

  def test_boss_quest_reward_is_fixed(self):
    boss = G.make_boss_quest(self.world, "den_lord", START)
    self.assertEqual((boss["rank"], boss["party"]), (4, 3))
    self.assertEqual(boss["steps"], [["go", 2], ["hunt", 1], ["back", 2]])  # 霊場へは山道の行動回数で行き帰り
    self.assertEqual(boss["cost"], 5)
    self.assertEqual(boss["reward"]["gold"], self.world["quest_reward"]["party_gold"]["boss"])

  def test_town_levels_count_only_heroes_in_the_town(self):
    a, b, c, d = (self.hero(h, level) for h, level in (("a", 13), ("b", 14), ("c", 15), ("d", 2)))
    b["place"] = "kofu"
    c["road"] = {"to": "kofu", "left": 1}  # 道の途中のもののふは、どの町にも数えない
    a["quest"] = {"type": "deliver"}  # 別の依頼を受けていても数える
    self.assertEqual(G.town_levels([a, b, c, d]), {"kofu": [14], "nagano": [2, 13]})

  def test_elite_quests_need_enough_heroes_in_the_town(self):
    """Lv13 以上が 2 人いても、別の町にいれば大物の依頼は出ない。同じ町にそろえば、その町の高札にだけ出る（#297）。"""

    def elites(levels):
      found = set()
      for day in range(60):
        guild = G.new_guild()
        guild["levels"] = levels
        G.refresh_guild(self.world, guild, START + timedelta(days=day), 1)
        for town, entries in guild["towns"].items():
          found |= {
            (town, e["monster"]) for e in entries if self.world["monsters"].get(e["monster"] or "", {}).get("elite")
          }
      return found

    self.assertEqual(elites({"nagano": [13], "kofu": [20]}), set())  # 別々の町にいる
    self.assertEqual(elites({"kofu": [13, 20], "edo": [3, 15]}), {("kofu", "rock_golem")})

  def test_elite_quests_need_enough_heroes(self):
    def elites(levels):
      return [
        q
        for d in range(40)
        for q in G.draw_quests(self.world, "nagano", START + timedelta(days=d), 1, 3, levels=levels)
        if self.world["monsters"][q["monster"] or "wild_dog"].get("elite")
      ]

    self.assertFalse(elites([13, 12, 3]))  # Lv13 以上が 1 人だけなら出ない
    found = elites([13, 20, 3])
    self.assertTrue(found)
    self.assertTrue(all(q["party"] == 2 and q["min_level"] == 13 for q in found))

  def test_joining_fills_the_party(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    guild["towns"]["nagano"] = []
    entry = self.elite_entry(guild)
    a, b = self.hero("a"), self.hero("b")
    for st in (a, b):
      st["personality"] = None
      G.Day(self.world, st, START, random.Random(0), guild).take_quest("nagano")
    self.assertEqual(entry["members"], ["a", "b"])
    self.assertEqual(entry["status"], "held")
    self.assertEqual(entry["gather_until"], (START + timedelta(days=2)).isoformat())  # 3 日のうちに集まる
    self.assertTrue(all(k not in a["quest"] for k in G.GUILD_FIELDS))

  def test_together_at_the_hunting_ground(self):
    self.world["monsters"]["yeti"].update(hp=50, atk=10)  # 必ず勝てる大物
    guild = G.new_guild()
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], status="held", gather_until=START.isoformat())
    a, b = self.hero("a"), self.hero("b")
    for st in (a, b):
      self.at_the_hunt(st, entry)
    others = {"a": a, "b": b}
    day_a = G.Day(self.world, a, START, random.Random(1), guild, others)
    self.assertTrue(day_a.act())  # a が着いて、b を待つ
    self.assertIn("長野と甲府の間の道で仲間を待っている（1/2 人）", day_a.events)
    self.assertTrue(day_a.waiting)
    self.assertEqual(entry["arrived"], ["a"])
    gold_a = a["hero"]["gold"]
    day_b = G.Day(self.world, b, START, random.Random(2), guild, others)
    self.assertTrue(day_b.act())  # b が着いて、2 人で戦う
    self.assertEqual(entry["status"], "done")
    self.assertEqual(guild["elite_kills"], 1)
    for st in (a, b):
      self.assertEqual(st["quest"]["steps"], [["back", 2]])  # 倒したので帰る
      self.assertEqual(st["quest"]["progress"], 1)
    self.assertEqual(a["hero"]["gold"] - gold_a, self.world["monsters"]["yeti"]["gold"] // 2)
    self.assertTrue(any("雪女〔凶暴〕を倒した" in e for e in a["inbox"]))  # a の日誌には、次に進めるときに届く

  def test_party_rewards_go_to_different_members(self):
    for kind, size, stone_count in (("elite", 2, 1), ("boss", 3, 3)):
      with self.subTest(kind=kind):
        states = [self.hero(f"h{i}") for i in range(size)]
        days = [G.Day(self.world, st, START, random.Random(i)) for i, st in enumerate(states)]
        town = "nagano" if kind == "elite" else "kofu"
        days[0].party_loot(days, town, kind)
        self.assertEqual(sum(st["stones"].get("bando", 0) for st in states), stone_count)
        self.assertEqual(sum(bool(st["stones"]) for st in states), 1)
        for st in states:
          if st["stones"]:
            self.assertFalse(st["inventory"])
          else:
            self.assertEqual(len(st["inventory"]), 1)
            obtained = st["inventory"][0]
            item = self.world["items"][obtained["id"]]
            self.assertEqual(item["rarity"], "epic")
            self.assertIn(town, item["towns"])
            fixed, skill = G.equipment_skills(self.world, obtained)
            self.assertEqual(fixed, item["fixed_skill"])
            if kind == "boss":
              self.assertEqual(self.world["skills"][skill]["slot"], item["slot"])
              self.assertNotEqual(obtained["skill"], fixed)
            else:
              self.assertIsNone(skill)

  def test_all_down_means_everyone_falls(self):
    self.world["monsters"]["yeti"].update(hp=5000, atk=300)  # 必ず負ける大物
    guild = G.new_guild()
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], status="held", arrived=["a"], gather_until=START.isoformat())
    a, b = self.hero("a"), self.hero("b")
    for st in (a, b):
      self.at_the_hunt(st, entry)
    day_b = G.Day(self.world, b, START, random.Random(2), guild, {"a": a, "b": b})
    self.assertFalse(day_b.act())
    self.assertEqual(entry["status"], "failed")
    self.assertIsNone(a["quest"])  # 仲間も力尽きる
    self.assertEqual(a["season_stats"]["deaths"], 1)
    self.assertEqual(a["fell_on"], START.isoformat())  # まだ今日の分を進めていないので、今日は休む
    G.advance(self.world, a, START, guild=guild)
    self.assertIn("仲間と戦って力尽きたので、今日は休んだ", a["last"]["events"])
    self.assertEqual(a["last"]["actions"], 0)

  def test_party_that_never_gathers_flows_without_failing(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    guild["towns"]["nagano"] = []
    entry = self.elite_entry(guild)
    entry.update(members=["a"], arrived=["a"], gather_until=START.isoformat())
    a = self.hero("a")
    self.at_the_hunt(a, entry)
    next_day = START + timedelta(days=1)
    guild["refreshed"]["nagano"] = START.isoformat()
    G.refresh_guild(self.world, guild, next_day, 1, ["nagano"])
    self.assertNotIn("e1", [e["id"] for e in guild["towns"]["nagano"]])  # 人数がそろわないまま流れた
    day = G.Day(self.world, a, next_day, random.Random(0), guild, {"a": a})
    for _ in range(4):
      day.act()
    self.assertTrue(any("依頼が流れた" in e for e in day.events))
    self.assertIn("長野に戻った", day.events)
    self.assertNotEqual(
      (a["quest"] or {}).get("id"), "e1"
    )  # 流れた依頼は終わった（戻ったあと、別の依頼を受けてもよい）
    self.assertEqual(a["season_stats"]["quests_failed"], 0)  # 失敗にはしない
    self.assertFalse(any("依頼失敗" in e for e in day.events))

  def test_boss_wakes_after_enough_elites(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    guild.update(elite_kills=self.world["season"]["party"]["boss_after_elites"] - 1)
    self.world["monsters"]["yeti"].update(hp=50, atk=10)
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], status="held", arrived=["a"], gather_until=START.isoformat())
    a, b = self.hero("a"), self.hero("b")
    for st in (a, b):
      self.at_the_hunt(st, entry)
    G.Day(self.world, b, START, random.Random(2), guild, {"a": a, "b": b}).act()
    self.assertEqual((guild["elite_kills"], guild["awake"]), (0, ["den_lord"]))
    G.refresh_guild(self.world, guild, START + timedelta(days=1), 1)
    boss = [e for e in guild["towns"]["kofu"] if e["monster"] == "den_lord"]
    self.assertEqual(len(boss), 1)  # 霊場への山道がつながる甲府に、主の依頼が出る（高札の枠とは別）
    self.assertEqual((boss[0]["place"], boss[0]["party"], boss[0]["min_level"]), ("fuji", 3, 18))
    self.assertEqual(boss[0]["steps"], [["go", 2], ["hunt", 1], ["back", 2]])
    self.assertTrue(G.quest_ok(self.world, boss[0]))
    self.assertIn("富士山の酒呑童子を討つ", G.quest_text(self.world, boss[0]))

  def test_old_solo_elite_quest_is_dropped(self):
    guild = G.new_guild()
    entry = self.elite_entry(guild)
    old = {k: v for k, v in entry.items() if k not in G.GUILD_FIELDS and k != "party"}
    self.assertFalse(G.quest_ok(self.world, old))  # 1 人で受ける前の大物の依頼は捨てる
    self.assertTrue(G.quest_ok(self.world, {k: v for k, v in entry.items() if k not in G.GUILD_FIELDS}))

  def test_waiting_does_not_spend_stored_actions(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], status="held", gather_until=(START + timedelta(days=2)).isoformat())
    a = self.hero("a")
    self.at_the_hunt(a, entry)
    a["hero"].update(stored=5, gold=5000, hp=1)  # HP が低く、旅籠代もあるが、狩り場で待っている
    a["last_date"] = (START - timedelta(days=1)).isoformat()
    G.advance(self.world, a, START, guild=guild, others={"a": a})
    self.assertEqual(a["hero"]["stored"], 5)
    self.assertEqual(a["last"]["actions"], self.world["season"]["actions_per_day"])

  def test_cannot_join_the_same_party_twice(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    guild["towns"]["nagano"] = []
    entry = self.elite_entry(guild)
    entry.update(members=["a"], gather_until=START.isoformat())
    a = self.hero("a")
    a["personality"] = None
    G.Day(self.world, a, START, random.Random(0), guild).take_quest("nagano")
    self.assertEqual(entry["members"], ["a"])  # すでに入っている依頼には入り直さない
    self.assertEqual(entry["status"], "open")

  def test_held_party_flows_after_the_deadline(self):
    guild = G.new_guild()
    G.refresh_guild(self.world, guild, START, 1)
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], arrived=["a"], status="held", gather_until=START.isoformat())
    guild["refreshed"]["nagano"] = START.isoformat()
    G.refresh_guild(self.world, guild, START + timedelta(days=1), 1, ["nagano"])
    self.assertNotIn("e1", [e["id"] for e in guild["towns"]["nagano"]])  # 仲間が来られなくても、待ち続けない

  def test_once_a_day_skills_count_across_turns(self):
    a = self.hero("a")
    day = G.Day(self.world, a, START, random.Random(0))
    day.endured = True
    day.remember("endured")
    self.assertTrue(G.Day(self.world, a, START, random.Random(1)).endured)  # 同じ日に仲間の番で戦っても、もう使えない
    self.assertFalse(G.Day(self.world, a, START + timedelta(days=1), random.Random(1)).endured)

  def test_release_orphans_frees_the_slot(self):
    guild = G.new_guild()
    entry = self.elite_entry(guild)
    entry.update(members=["a", "b"], arrived=["a"], status="held", gather_until=START.isoformat())
    a = self.hero("a")
    self.at_the_hunt(a, entry)
    G.release_orphans(guild, {"a": a, "b": self.hero("b")})  # b はもう依頼を持っていない
    self.assertEqual((entry["members"], entry["arrived"], entry["status"]), (["a"], ["a"], "open"))


class TraitTest(unittest.TestCase):
  """物の怪の性質の効き方（会心の運で結果がぶれないよう、もののふと物の怪の器用さを 0 にして、会心を出さずに確かめる）。"""

  def test_critical_hits(self):
    day = day_of(G.new_state(WORLD))
    div = WORLD["season"]["critical"]["dex_divisor"]
    self.assertEqual(day.damage(10, 0, div) % 3, 0)  # 器用さ ÷ 150 が 1 なら必ず会心: 3 倍
    self.assertGreaterEqual(day.damage(10, 0, div), 24)
    self.assertLessEqual(day.damage(10, 0, 0), 12)  # 器用さ 0 なら会心なし
    hits = sum(day.damage(10, 0, 30) > 12 for _ in range(2000))  # 器用さ 30 なら 20%
    self.assertTrue(300 <= hits <= 500, hits)

  def fight(self, trait, hp=None, potions=3, seed="t"):
    state = G.new_state(WORLD)
    state["hero"]["base_dex"] = 0
    state["hero"]["potions"] = potions
    if hp is not None:
      state["hero"]["hp"] = hp
    monster = dict(WORLD["monsters"]["wild_dog"], dex=0) | ({"trait": trait} if trait else {})
    day = day_of(state, seed=seed)
    return day.battle(monster), state, day

  def test_name_shows_the_trait(self):
    _, _, day = self.fight("poison")
    self.assertIn("送り犬〔毒〕を倒した", day.events[0])

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
        state["hero"].update(max_hp=999, hp=999, base_dex=0)
        monster = dict(WORLD["monsters"]["wild_dog"], dex=0) | ({"trait": trait} if trait else {})
        day = day_of(state, seed=str(seed))
        day.gain_exp = lambda exp: None  # レベルが上がると HP が戻るので、育てない
        day.battle(monster)
        total += 999 - state["hero"]["hp"]
      return total

    plain = hp_lost(None)
    self.assertGreater(hp_lost("fierce"), plain)  # 凶暴は攻撃が強い
    self.assertGreater(hp_lost("swarm"), plain)  # 群れは攻撃の回数が多い
    guards = {}
    for trait in (None, "armored"):  # 硬い: もののふの攻撃を受ける守りが固い
      state = G.new_state(WORLD)
      state["hero"]["base_dex"] = 0
      day = day_of(state)
      seen = []
      real = day.damage

      def spy(attack, guard, dex=0, seen=seen, real=real):
        seen.append(guard)
        return real(attack, guard, dex)

      with mock.patch.object(day, "damage", side_effect=spy):
        day.battle(dict(WORLD["monsters"]["wild_dog"], dex=0) | ({"trait": trait} if trait else {}))
      guards[trait] = min(seen)  # もののふの攻撃を受けたときの守り（物の怪の攻撃を受けるもののふの守り 6 より低い）
    self.assertGreater(guards["armored"], guards[None])


class StateFileTest(unittest.TestCase):
  def test_save_and_load(self):
    state = play(5)
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      self.assertEqual(G.load_state(WORLD, path), state)

  def test_load_renews_quest_texts_with_old_monster_names(self):
    """物の怪の名前を変える前（#253）に受けた依頼は、読み込むときに今の名前の文にする。"""
    state = play(1)
    quest = common_hunt()
    quest.update(name="野犬の討伐", text="道に野犬が出る。雪男の牙も運べ")
    state["quest"] = quest
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path)
    self.assertEqual(loaded["quest"]["name"], "送り犬の討伐")
    self.assertEqual(loaded["quest"]["text"], "道に送り犬が出る。雪女の簪も運べ")  # 長い名前（雪男の牙）から置き換える

  def test_load_repairs_references_removed_from_the_world(self):
    state = play(5)
    state.update(place="gone", home_town="gone", road={"to": "gone", "left": 1})
    G.carried(state).append({"id": "gone_item", "skill": None})
    state["quest"] = {"place": "gone", "report": None}
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path)
    self.assertEqual(
      (loaded["place"], loaded["home_town"], loaded["road"], loaded["quest"]),
      (G.start_town(WORLD, "h1", 1),) * 2 + (None, None),
    )
    self.assertNotIn("gone_item", ids(loaded))

  def test_load_drops_removed_items_from_rare_records(self):
    state = play(2)
    state["season_stats"]["rares"] = ["steel_sword", "gone_item"]
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path)
    self.assertEqual(loaded["season_stats"]["rares"], ["steel_sword"])

  def test_load_moves_heroes_out_of_closed_towns(self):
    state = play(2)
    closed = next(p for p, x in WORLD["places"].items() if x["type"] == "town" and p not in WORLD["towns"])
    state.update(
      place=closed,
      home_town=closed,
      quest={
        "type": "deliver",
        "name": "x",
        "template": "t",
        "town": state["place"],
        "place": closed,
        "report": closed,
        "monster": None,
        "count": 1,
        "progress": 0,
        "level": [1, 1],
        "deadline": None,
        "reward": {"exp": 1, "gold": 1, "item": None},
        "cost": 9,
      },
    )
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path)
    self.assertIn(loaded["place"], WORLD["towns"])
    self.assertIn(loaded["home_town"], WORLD["towns"])  # 力尽きたら戻る町も、開放した町
    self.assertIsNone(loaded["quest"])  # 開放していない町への依頼は捨てる
    day = day_of(loaded)
    day.fall()
    self.assertTrue(day.act())  # 戻った町から続けられる

  def test_load_drops_quests_whose_place_changed_type(self):
    play(2)
    base = {
      "name": "x",
      "template": "t",
      "town": TOWN,
      "report": TOWN,
      "count": 1,
      "progress": 0,
      "level": [1, 5],
      "deadline": None,
      "reward": None,
      "steps": [["hunt", 1]],
    }
    near = WORLD["neighbors"][TOWN][0]
    far = next(t for t in WORLD["towns"] if t != TOWN and (TOWN, t) not in WORLD["roads"])
    cases = [
      ({"type": "hunt", "place": near, "monster": "wild_dog"}, True),
      ({"type": "hunt", "place": far, "monster": "wild_dog"}, False),  # 道がない
      ({"type": "hunt", "place": near, "monster": "slime"}, False),  # 物の怪がいない
      ({"type": "hunt", "place": near, "monster": "den_lord"}, False),  # 主は道に出ない
      ({"type": "deliver", "place": "kumano", "monster": None}, False),  # 霊場へは配達しない
      ({"type": "deliver", "place": far, "monster": None}, True),
      ({"type": "teleport", "place": near, "monster": None}, False),
    ]
    no_report = {
      k: v for k, v in (base | {"type": "hunt", "place": near, "monster": "wild_dog"}).items() if k != "report"
    }
    self.assertFalse(G.quest_ok(WORLD, no_report))  # 報告先のない討伐は捨てる（エラーにしない）
    old = {k: v for k, v in (base | {"type": "hunt", "place": near, "monster": "wild_dog"}).items() if k != "steps"}
    self.assertFalse(G.quest_ok(WORLD, old))  # 前の形式（段取りのない討伐）は捨てる
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
  """もののふを何人か 1 シーズン動かして、狙いの範囲に収まるか確かめる（数値は world/ で調整する）。"""

  @classmethod
  def setUpClass(cls):
    # 本番と同じく、全員で 1 つの高札を取り合う（早い者勝ち。もののふを進める順は日ごとに混ぜる）
    cls.results = [G.new_state(WORLD, f"b{i}") for i in range(30)]
    guild, d = G.new_guild(), START
    while G.season_of(WORLD, d) == 1:
      for state in random.Random(d.isoformat()).sample(cls.results, len(cls.results)):
        G.advance(WORLD, state, d, guild=guild)
      d += timedelta(days=1)

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
    if not WORLD["season"].get("rare_drops", True):
      self.skipTest("レアの配布を止めている（season.json の rare_drops）")
    rares = [len(s["season_stats"]["rares"]) for s in self.results]
    # 1 シーズンに数個（中央値 9 ほど。討伐が 1 行動になって、こなす依頼が増えた #165）
    self.assertTrue(1 <= self.median(rares) <= 12, sorted(rares))

  def test_heroes_drift_apart(self):
    self.assertGreater(len({s["place"] for s in self.results}), 2)


class GuildBoardTest(unittest.TestCase):
  TOWN = "edo"

  def setUp(self):
    self.guild = G.new_guild()
    G.refresh_guild(WORLD, self.guild, START, 1)
    self.board = self.guild["towns"][self.TOWN]

  def next_day(self, n=1):
    G.refresh_guild(WORLD, self.guild, START + timedelta(days=n), 1)
    self.board = self.guild["towns"][self.TOWN]
    return {e["id"]: e for e in self.board}

  def test_old_monster_names_on_the_board_are_renewed(self):
    """名前を変える前（#253）に貼られた依頼は、翌朝の貼り替えで今の文にする（町の決まった依頼は quests.json から）。"""
    town_q = next(q for q in WORLD["town_quests"]["nagano"] if q["monster"] == "yeti")
    entry = G.make_town_quest(WORLD, town_q, "nagano", START)
    entry.update(id="old-1", posted=START.isoformat(), status="held", holder="h1", returned=False)
    entry.update(name="吹雪の雪男", text="牙の冷たさを持ち出したものだ")
    common = common_hunt()
    common.update(id="old-2", posted=START.isoformat(), status="held", holder="h2", returned=False)
    common.update(name="ゴブリンの討伐", text="ゴブリンを追い払ってくれ")
    self.guild["towns"]["nagano"].append(entry)
    self.guild["towns"][self.TOWN].append(common)
    self.next_day()
    byid = {e["id"]: e for es in self.guild["towns"].values() for e in es}
    self.assertEqual((byid["old-1"]["name"], byid["old-1"]["text"]), (town_q["name"], town_q["text"]))
    self.assertEqual((byid["old-2"]["name"], byid["old-2"]["text"]), ("小鬼の討伐", "小鬼を追い払ってくれ"))

  def test_every_town_is_filled(self):
    for town in WORLD["towns"]:
      board = self.guild["towns"][town]
      self.assertLessEqual(len(board), G.quests_per_day(WORLD, town))
      self.assertGreaterEqual(len(board), 1)
      tids = [e["template"] for e in board if e["template"] in {q["id"] for q in WORLD["town_quests"].get(town, [])}]
      self.assertEqual(len(tids), len(set(tids)))  # 同じ町の依頼は同時に 1 つだけ

  def test_open_quests_stay_until_the_open_days_end(self):
    days = WORLD["season"]["quest_open_days"]
    first = {e["id"] for e in self.board}
    self.assertEqual(G.last_open_day(WORLD, self.board[0]), START + timedelta(days=days - 1))
    self.assertEqual(set(self.next_day(days - 1)) & first, first)  # 最後の日まで残る
    self.assertFalse(set(self.next_day(days)) & first)  # 次の日に入れ替わる
    self.assertEqual(len(self.board), G.quests_per_day(WORLD, self.TOWN))

  def test_hunt_quest_level_is_the_target_level(self):
    for town, quests in WORLD["town_quests"].items():
      for quest in quests:
        if quest["type"] == "hunt":
          lv = WORLD["monsters"][quest["monster"]]["level"]
          self.assertEqual(G.make_town_quest(WORLD, quest, town, START)["level"], [lv, lv], quest["id"])
    q = next(q for i in range(200) for q in [common_hunt("kofu", 2, i)] if q["level"] == [5, 5])
    self.assertFalse(G.suitable(q, 1))  # Lv5 の物の怪の討伐: Lv1 では受けない、Lv2 から受ける
    self.assertTrue(G.suitable(q, 2))
    deliver = next(q for q in G.board(WORLD, self.TOWN, START, 1) if q["type"] == "deliver")
    self.assertIsNone(deliver["level"])  # 配達は Lv を持たない（#150）

  def test_open_quests_follow_the_monster_level(self):
    hunt = next(e for e in self.board if e["type"] == "hunt")
    hunt["level"] = [99, 99]  # 物の怪のレベルを変える前に貼られた依頼
    lv = WORLD["monsters"][hunt["monster"]]["level"]
    self.assertEqual(self.next_day()[hunt["id"]]["level"], [lv, lv])

  def test_refresh_once_a_day(self):
    self.board[0]["status"] = "done"
    G.refresh_guild(WORLD, self.guild, START, 1)  # 同じ日は貼り替えない
    self.assertEqual(self.guild["towns"][self.TOWN][0]["status"], "done")

  def test_held_quests_stay_and_done_ones_are_replaced_the_next_day(self):
    held, done = self.board[0], self.board[1]
    held.update(status="held", holder="h1")
    done.update(status="done", holder="h2")
    board = self.next_day(10)
    self.assertIn(held["id"], board)  # 受注中はずっと残る
    self.assertNotIn(done["id"], board)
    self.assertEqual(len(board), G.quests_per_day(WORLD, self.TOWN))

  def test_failed_quests_come_back_the_next_day(self):
    failed = self.board[0]
    failed.update(status="failed", holder="h1")
    board = self.next_day(2)
    back = board[failed["id"]]
    self.assertEqual((back["status"], back["holder"], back["returned"]), ("open", None, True))
    self.assertEqual(back["posted"], (START + timedelta(days=2)).isoformat())  # 受付の日数は戻った日から数える

  def test_report_and_fall_settle_the_board(self):
    state = play(1, "h1", actions=0)
    state["place"], state["hero"]["level"] = self.TOWN, 99
    day = G.Day(WORLD, state, START, random.Random(0), self.guild)
    day.take_quest(self.TOWN)
    entry = G.guild_entry(self.guild, state["quest"])
    self.assertEqual((entry["status"], entry["holder"]), ("held", "h1"))
    day.fall()
    self.assertEqual(entry["status"], "failed")
    state["quest"], state["taken"] = None, []
    day.take_quest(self.TOWN)
    entry = G.guild_entry(self.guild, state["quest"])
    state["quest"]["progress"] = state["quest"]["count"]
    day.report(state["quest"])
    self.assertEqual(entry["status"], "done")

  def test_taken_quest_does_not_share_the_steps_with_the_board(self):
    hunt = next(e for e in self.board if e["type"] == "hunt")
    for e in self.board:  # この討伐だけを受けられるようにする
      e["status"] = "open" if e is hunt else "done"
    fresh = [list(p) for p in hunt["steps"]]
    hunt["steps"] = [["back", 1]]  # 前に受けたもののふが進めたまま保存された段取り
    state = play(1, "h1", actions=0)
    state["place"], state["hero"]["level"] = self.TOWN, 99
    day = G.Day(WORLD, state, START, random.Random(0), self.guild)
    day.take_quest(self.TOWN)
    quest = state["quest"]
    self.assertEqual(quest["id"], hunt["id"])
    self.assertEqual(quest["steps"], fresh)  # 段取りは最初から作り直す
    self.assertEqual((quest["progress"], quest["away"]), (0, 0))
    quest["steps"][0][1] -= 1  # もののふが進めても、高札の依頼は変わらない
    self.assertEqual(hunt["steps"], [["back", 1]])
    self.assertIsNot(quest["reward"], hunt["reward"])

  def test_failed_quest_comes_back_fresh(self):
    hunt = next(e for e in self.board if e["type"] == "hunt")
    fresh = [list(p) for p in hunt["steps"]]
    hunt.update(status="failed", holder="h1", steps=[["back", 1]], away=1)  # 前に共有して減った段取り
    back = self.next_day()[hunt["id"]]
    self.assertEqual((back["status"], back["steps"], back["away"], back["progress"]), ("open", fresh, 0, 0))

  def test_broken_hunt_quest_is_dropped_on_load(self):
    state = play(1, "h1", actions=0)
    board = [e for es in self.guild["towns"].values() for e in es]
    quest = next(e for e in board if e["type"] == "hunt" and e["count"] >= 2)  # 2 体以上の討伐（どの町でもよい）
    ok = dict(quest, steps=[["hunt", 1], ["back", 1]], progress=1)
    self.assertTrue(G.quest_ok(WORLD, ok))
    self.assertTrue(G.quest_ok(WORLD, dict(ok, steps=[["hunt", quest["count"] - 1], ["back", 1]])))  # 前の形式も読める
    for broken in (
      dict(quest, steps=[], progress=0),  # 段取りが空なのに倒していない
      dict(quest, steps=[["back", 1]], progress=0),  # 討伐が残っていない
      dict(quest, steps=[["hunt", 1], ["back", 1]], progress=quest["count"]),  # 倒し終えたのに討伐が残っている
    ):
      self.assertFalse(G.quest_ok(WORLD, broken), broken["steps"])
      self.assertTrue(G.quest_ok(WORLD, broken, steps=False))  # 高札では段取りを見ない
      state["quest"] = broken
      with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "h1.json"
        G.save_state(state, path)
        self.assertIsNone(G.load_state(WORLD, path, "h1")["quest"])
    self.assertTrue(G.quest_ok(WORLD, dict(quest, steps=[["back", 1]], progress=quest["count"])))  # 倒し終えて帰り道

  def test_hunt_fights_all_targets_in_one_action(self):
    state = play(1, "h1", actions=0)
    state["hero"].update(max_hp=999, hp=999)
    quest = dict(
      next(q for i in range(50) for q in [common_hunt(self.TOWN, 1, i)] if q["level"][0] <= 2 and q["count"] >= 2),
      steps=[["hunt", 5], ["back", 1]],
      progress=1,
    )
    state["quest"] = quest
    day = G.Day(WORLD, state, START, random.Random(0), self.guild)
    self.assertTrue(day.hunt_step(quest))  # 前の形式の段取りでも、1 行動で残りを全部倒す
    self.assertEqual(quest["progress"], quest["count"])
    self.assertEqual(quest["steps"], [["back", 1]])
    name = WORLD["monsters"][quest["monster"]]["name"]
    self.assertEqual(sum(f"{name}" in e and "を倒した" in e for e in day.events), quest["count"] - 1)

  def test_hunt_keeps_rewards_of_defeated_targets_when_falling(self):
    state = play(1, "h1", actions=0)
    quest = dict(next(e for e in self.board if e["type"] == "hunt"), steps=[["hunt", 1], ["back", 1]], progress=0)
    quest["count"] = 3
    state["quest"] = quest
    day = G.Day(WORLD, state, START, random.Random(0), self.guild)
    wins = iter([True, False])
    gold = state["hero"]["gold"]

    def battle(monster):  # 1 体目は倒して（お金が入る）、2 体目で負ける
      won = next(wins)
      if won:
        day.hero["gold"] += 10
      return won

    day.battle = battle
    self.assertFalse(day.hunt_step(quest))
    self.assertEqual(quest["progress"], 1)
    self.assertEqual(state["hero"]["gold"], gold + 10)

  def test_hunt_step_with_no_steps_fails_the_quest(self):
    state = play(1, "h1", actions=0)
    quest = dict(next(e for e in self.board if e["type"] == "hunt"), steps=[], progress=0)
    state["quest"] = quest
    day = G.Day(WORLD, state, START, random.Random(0), self.guild)
    self.assertTrue(day.hunt_step(quest))  # 止まらない
    self.assertIsNone(state["quest"])
    self.assertIn("段取りが壊れていた", day.events[-1])

  def test_release_orphans(self):
    a, b, c = self.board[:3]
    a.update(status="held", holder="h1")
    b.update(status="held", holder="h2")
    c.update(status="held", holder="h3")
    states = {"h1": {"quest": {"id": a["id"]}}, "h2": {"quest": None}}
    self.assertEqual(G.release_orphans(self.guild, states, keep={"h3"}), 1)
    self.assertEqual([a["status"], b["status"], c["status"]], ["held", "failed", "held"])

  def test_older_days_do_not_rewind_the_board(self):
    board = self.next_day(2)
    snapshot = json.dumps(self.guild, sort_keys=True)
    G.refresh_guild(WORLD, self.guild, START + timedelta(days=1), 1)  # 遅れているもののふがさかのぼって進める
    G.refresh_guild(WORLD, self.guild, START, 0)  # 前のシーズンの日
    self.assertEqual(json.dumps(self.guild, sort_keys=True), snapshot)
    self.assertTrue(board)

  def test_new_season_reposts_everything(self):
    self.board[0].update(status="held", holder="h1")
    G.refresh_guild(WORLD, self.guild, START + timedelta(days=1), 2)
    self.assertTrue(all(e["status"] == "open" for e in self.guild["towns"][self.TOWN]))
    self.assertEqual(self.guild["season"], 2)


class BattleDepthTest(unittest.TestCase):
  """属性・素早さ・特性。"""

  def setUp(self):
    # 持ち替えの仕組みを確かめるための仮の武器（いつもの武器は大剣、持ち替える先は雷の武器の騎兵の槍）
    test_items = {
      "x_heavy": {"name": "試しの大剣", "atk": 7, "spd": -1, "hp": 5},
      "x_lance": {"name": "騎兵の槍", "atk": 6, "element": "thunder"},
      "x_bow": {"name": "試しの弓", "atk": 1, "dex": 5},
      "x_flame": {"name": "試しの炎の剣", "atk": 2, "element": "flame", "rarity": "rare"},
      "x_flame_charm": {"name": "試しの炎の御守", "slot": "accessory", "element": "flame", "rarity": "rare"},
    }
    items = {
      k: {"id": k, "slot": "weapon", "rarity": "common", "price": 100} | v for k, v in test_items.items()
    }  # 毎回作り直す
    odawara = WORLD["places"]["odawara"]
    for target, values in ((WORLD["items"], items), (odawara, {"shop": odawara["shop"] + ["x_lance"]})):
      patch = mock.patch.dict(target, values)
      patch.start()
      self.addCleanup(patch.stop)

  def hero_state(self, **hero):
    state = G.new_state(WORLD)
    state["hero"].update(hp=999, max_hp=999, potions=0, **hero)
    return state

  def monster(self, **m):
    return {
      "id": "x",
      "name": "試し",
      "level": 2,
      "hp": 30,
      "atk": 1,
      "def": 0,
      "spd": 6,
      "exp": 1,
      "gold": 0,
      "terrain": "plain",
    } | m

  def test_element_rates(self):
    rates = WORLD["season"]["element_rates"]
    self.assertEqual(G.element_rate(WORLD, "flame", "thunder"), rates["strong"])  # 炎は雷に強い
    self.assertEqual(G.element_rate(WORLD, "thunder", "flame"), rates["weak"])  # 逆は苦手
    self.assertEqual(G.element_rate(WORLD, "ice", "flame"), rates["strong"])  # 氷は炎に強い
    self.assertEqual(G.element_rate(WORLD, "thunder", "ice"), rates["strong"])  # 雷は氷に強い
    self.assertEqual(set(WORLD["elements"]), {"flame", "ice", "thunder"})  # 属性は炎・氷・雷の 3 つ
    self.assertEqual(G.element_rate(WORLD, None, "thunder"), 1.0)  # 属性なし
    self.assertEqual(G.element_rate(WORLD, "flame", None), 1.0)
    for eid, e in WORLD["elements"].items():  # どの属性も、強い相手と弱い相手が 1 つずつ
      self.assertIn(e["strong"], WORLD["elements"])
      self.assertEqual(sum(1 for x in WORLD["elements"].values() if x["strong"] == eid), 1)

  def test_hero_switches_to_the_weapon_that_works(self):
    state = self.hero_state()
    state["inventory"] = {"x_heavy": 1, "x_lance": 1}  # 大剣（攻 7）がいつもの武器、騎兵の槍（攻 6・雷）
    day = day_of(state)
    self.assertEqual(G.equipment(WORLD, state)["weapon"], "x_heavy")
    self.assertEqual(day.pick_weapon(self.monster(element="ice"), 0)[0], "x_lance")  # 雷は氷に強い
    self.assertEqual(day.pick_weapon(self.monster(), 0)[0], "x_heavy")
    day.battle(self.monster(element="ice", hp=1))
    self.assertIn("騎兵の槍で弱点を突いた", day.events[-1])

  def count_first(self, monster, spd, inventory=None):
    """もののふと物の怪の、最初の数回の攻撃の順番（"h" / "m"）。"""
    state = self.hero_state(base_spd=spd)
    state["inventory"] = G.instances(inventory or {})
    day = day_of(state)
    order = []

    def damage(attack, guard, dex=0):
      who = "m" if attack == 3 else "h"  # 物の怪の攻撃は 3
      order.append(who)
      return 1 if who == "m" else 10

    with mock.patch.object(day, "damage", side_effect=damage):
      day.battle(monster | {"hp": 30, "atk": 3})
    return "".join(order)

  def test_speed_decides_order_and_double_attacks(self):
    self.assertEqual(self.count_first(self.monster(spd=6), 6), "hmhmh")  # 同じならもののふが先
    self.assertEqual(self.count_first(self.monster(spd=7), 6), "mhmhmh")  # 物の怪が速ければ物の怪が先
    self.assertEqual(self.count_first(self.monster(spd=3), 6), "hhmh")  # 2 倍以上なら 2 回攻撃
    self.assertEqual(self.count_first(self.monster(spd=13), 6), "mmhmmhmmh")  # 物の怪も 2 倍以上なら 2 回
    # 先手の物の怪は、もののふのほうが速くても先に攻撃する（2 回攻撃は素早さで決まる）
    self.assertEqual(self.count_first(self.monster(spd=3, trait="first_strike"), 6), "mhhmh")

  def test_speed_follows_the_weapon_in_hand(self):
    state = self.hero_state()
    state["inventory"] = {"x_heavy": 1, "x_lance": 1}  # いつもは大剣（速 -1）で素早さ 5
    state["hero"]["base_spd"] = 6
    day = day_of(state)
    self.assertEqual(day.stats["spd"], 5)
    weapon, rate, held, _ = day.pick_weapon(self.monster(element="ice"), 0)  # 騎兵の槍に持ち替えると素早さ 6
    self.assertEqual((weapon, held["spd"]), ("x_lance", 6))
    self.assertEqual(self.count_first(self.monster(spd=6, element="ice"), 6, inventory=state["inventory"])[:1], "h")

  def test_weapon_choice_counts_the_real_guard(self):
    state = self.hero_state(base_atk=3)
    state["inventory"] = {"x_heavy": 1, "x_lance": 1}  # 攻 3+7=10（属性なし）と 3+6=9（雷）
    day = day_of(state)
    # 守りが高い氷の物の怪: 大剣 (10 - 8) = 2、騎兵の槍 (9 - 8) × 1.3 = 1.3 → 大剣のほうが効く
    self.assertEqual(day.pick_weapon(self.monster(element="ice"), 16)[0], "x_heavy")
    self.assertEqual(day.pick_weapon(self.monster(element="ice"), 0)[0], "x_lance")

  def test_hero_fights_barehanded_rather_than_with_a_weak_element(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"x_flame": 1}  # 試しの炎の剣（攻 +2・炎・★★）は、氷の物の怪には 0.8 倍
    day = day_of(state)
    self.assertEqual(day.pick_weapon(self.monster(element="ice"), 0)[:2], (None, 1.0))  # 22×0.8 < 20
    self.assertEqual(day.pick_weapon(self.monster(element="thunder"), 0)[0], "x_flame")
    self.assertEqual(day.pick_weapon(self.monster(), 0)[0], "x_flame")

  def test_swapped_weapon_brings_all_its_stats(self):
    state = self.hero_state(base_dex=0)
    state["inventory"] = {"x_bow": 1, "x_flame": 1}  # 試しの弓（攻 +1・器 +5）、試しの炎の剣（攻 +2・炎・★★）
    day = day_of(state)
    self.assertEqual(G.equipment(WORLD, state)["weapon"], "x_flame")  # どちらも 2 点。珍しいほう
    weapon, _, held, _ = day.pick_weapon(self.monster(element="ice"), 0)  # 氷には炎が苦手 → 試しの弓に持ち替える
    self.assertEqual(weapon, "x_bow")
    self.assertEqual((held["atk"], held["dex"]), (state["hero"]["base_atk"] + 1, 5))  # 器用さも試しの弓のもの
    _, _, held, _ = day.pick_weapon(self.monster(element="thunder"), 0)
    self.assertEqual(held["dex"], 0)  # 炎の剣のときは器用さ +0

  def test_accessory_element_goes_to_attacks_without_one(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"iron_sword": 1, "crimson_grass_charm": 1}  # 不動の御守（炎）
    day = day_of(state)
    self.assertEqual(G.attack_element(WORLD, "iron_sword", "crimson_grass_charm"), "flame")  # 属性のない武器にも炎
    self.assertEqual(G.attack_element(WORLD, None, "crimson_grass_charm"), "flame")  # 素手でも
    self.assertEqual(G.attack_element(WORLD, "x_lance", "crimson_grass_charm"), "thunder")  # 属性の武器なら、武器の属性
    _, rate, _, _ = day.pick_weapon(self.monster(element="thunder"), 0)
    self.assertEqual(rate, WORLD["season"]["element_rates"]["strong"])  # 炎は雷に強い
    day.battle(self.monster(element="thunder", hp=1))
    self.assertIn("（弱点を突いた）", day.events[-1])
    state["inventory"] = [
      x for x in G.carried(state) if x["id"] != "iron_sword"
    ]  # 素手で弱点を突いても、名前を出さない（落ちない）
    day = day_of(state)
    day.battle(self.monster(element="thunder", hp=1))
    self.assertIn("（弱点を突いた）", day.events[-1])

  def test_elemental_accessory_is_worn_unless_the_foe_resists(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"iron_sword": 1, "soaring_leaf_charm": 1, "crimson_grass_charm": 1}
    # どちらも 1 点（不動の御守は炎属性の 1 点）。珍しい不動の御守がふだんの御守
    self.assertEqual(G.equipment(WORLD, state)["accessory"], "crimson_grass_charm")
    day = day_of(state)
    _, rate, _, acc = day.pick_weapon(self.monster(element="thunder"), 0)  # 雷の相手: 着けたまま弱点を突く
    self.assertEqual((acc, rate), ("crimson_grass_charm", WORLD["season"]["element_rates"]["strong"]))
    _, rate, held, acc = day.pick_weapon(self.monster(element="ice"), 0)  # 氷には炎が苦手 → 韋駄天の御守に着け替える
    self.assertEqual((acc, rate), ("soaring_leaf_charm", 1.0))
    self.assertEqual(held["spd"], state["hero"]["base_spd"] + 2)
    _, rate, _, acc = day.pick_weapon(self.monster(), 0)  # 属性のない相手は、ふだんの御守のまま
    self.assertEqual((acc, rate), ("crimson_grass_charm", 1.0))

  def test_elemental_accessory_is_swapped_in_against_its_weak_foe(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"iron_sword": 1, "silkvine_charm": 1, "x_flame_charm": 1}  # 試しの炎の御守は 1 点の ★
    WORLD["items"]["x_flame_charm"].update(rarity="common", price=1000)  # setUp で毎回作り直す仮の品
    state.update(personality="diligent", trait="lucky")
    self.assertEqual(
      G.equipment(WORLD, state)["accessory"], "silkvine_charm"
    )  # 同じ 1 点・同じ ★ なら安いほう（バランス）
    day = day_of(state)
    _, rate, held, acc = day.pick_weapon(self.monster(element="thunder"), 0)  # 雷の相手: その戦いだけ着け替える
    self.assertEqual((acc, rate), ("x_flame_charm", WORLD["season"]["element_rates"]["strong"]))
    self.assertEqual(held["dex"], state["hero"]["base_dex"])  # その戦いは千手の御守の器 +5 がない
    day.battle(self.monster(element="thunder", hp=1))
    self.assertIn("（試しの炎の御守で弱点を突いた）", day.events[-1])
    for enemy in ("ice", "flame", None):  # 弱点を突けない相手では、ふだんの御守のまま
      _, rate, _, acc = day.pick_weapon(self.monster(element=enemy), 0)
      self.assertEqual((acc, rate), ("silkvine_charm", 1.0), enemy)

  def test_elemental_accessory_stays_on_against_neutral_foes(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"iron_sword": 1, "silkvine_charm": 1, "crimson_grass_charm": 1}  # 器 +5 の御守もある
    day = day_of(state)
    for enemy in (None, "flame"):  # 苦手でない相手なら、器 +5 のほうが少し効いても、ふだんの不動の御守のまま
      self.assertEqual(day.pick_weapon(self.monster(element=enemy), 0)[3], "crimson_grass_charm", enemy)

  def test_weak_spot_note_names_only_what_carried_the_element(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"x_lance": 1, "soaring_leaf_charm": 1, "crimson_grass_charm": 1}  # 雷の武器と炎の御守
    day = day_of(state)
    # 氷: 雷の槍で突く。炎の御守は氷に苦手なので、韋駄天の御守（速 +2 で 2 回攻撃になる）に着け替える
    weapon, _, _, acc = day.pick_weapon(self.monster(element="ice", spd=4), 0)
    self.assertEqual((weapon, acc), ("x_lance", "soaring_leaf_charm"))
    day.battle(self.monster(element="ice", spd=4, hp=1))
    self.assertNotIn("韋駄天の御守で弱点", day.events[-1])  # 属性のない御守の名前は出さない
    self.assertIn("弱点を突いた", day.events[-1])

  def test_elemental_accessory_is_taken_off_against_a_strong_foe(self):
    state = self.hero_state(base_atk=20)
    state["inventory"] = {"crimson_grass_charm": 1}  # 御守は不動の御守だけ
    day = day_of(state)
    _, rate, _, acc = day.pick_weapon(self.monster(element="ice"), 0)  # 氷には炎が苦手 → 外して戦う
    self.assertEqual((acc, rate), (None, 1.0))
    _, rate, _, acc = day.pick_weapon(self.monster(), 0)
    self.assertEqual((acc, rate), ("crimson_grass_charm", 1.0))  # 属性のない相手は、着けたまま

  def test_shop_buys_the_best_item_it_can_afford(self):
    state = self.hero_state(gold=10000)
    state.update(personality="diligent", trait="lucky")  # バランス
    state["hero"]["potions"] = 3
    day_of(state).shop("edo")  # 頭の品はどちらも 1 点。安い編笠だけを買い、鉢金は買わない（買い直さない。#244）
    self.assertIn("wooden_helm", ids(state))
    self.assertNotIn("iron_helm", ids(state))
    guard = self.hero_state(gold=10000)
    guard.update(personality="brave", trait="guard")  # 鉄壁 × 伸ばす: 防の上がる鉢金・鎖帷子を買う
    guard["hero"]["potions"] = 3
    day_of(guard).shop("edo")
    self.assertIn("iron_helm", ids(guard))
    self.assertIn("iron_shield", ids(guard))
    self.assertNotIn("wooden_helm", ids(guard))
    owner = self.hero_state(
      gold=10000
    )  # 同じ強さの品は、安くても買い直さない（宝箱で鉢金を拾っていても、編笠を買わない）
    owner.update(personality="diligent", trait="lucky")
    owner["hero"]["potions"] = 3
    owner["inventory"] = {"iron_helm": 1}
    day_of(owner).shop("edo")
    self.assertNotIn("wooden_helm", ids(owner))
    poor = self.hero_state(gold=300)  # 鉢金（400文）は買えないので、次の編笠（240文）
    poor.update(personality="brave", trait="guard")
    poor["hero"]["potions"] = 3
    poor["inventory"] = {"iron_sword": 1, "iron_shield": 1, "soaring_leaf_charm": 1}  # 頭の品だけがない
    day_of(poor).shop("edo")
    self.assertIn("wooden_helm", ids(poor))
    poor["hero"]["gold"] = 10000  # お金ができたら、防の上がる鉢金に買い替える
    day_of(poor).shop("edo")
    self.assertEqual(G.equipment(WORLD, poor)["helmet"], "iron_helm")
    rich = self.hero_state(gold=10000)
    rich["hero"]["potions"] = 3
    day_of(rich).shop("edo")
    charms = [i for i in ids(rich) if WORLD["items"][i]["slot"] == "accessory"]
    self.assertEqual(len(charms), 1)  # 同じ点数・同じ値段の御守は 1 つだけ買う

  def test_shop_buys_weapons_with_new_elements(self):
    state = self.hero_state(gold=10000)
    state["inventory"] = {"x_heavy": 1}
    day_of(state).shop("odawara")  # 騎兵の槍（雷）は大剣より弱いが、雷の武器を持っていないので買う
    self.assertIn("x_lance", ids(state))

  def test_same_element_is_neutral(self):
    self.assertEqual(G.element_rate(WORLD, "flame", "flame"), 1.0)

  def test_exp_does_not_shrink_with_level_gap(self):
    dog = WORLD["monsters"]["wild_dog"]
    state = G.new_state(WORLD)
    state["hero"]["level"] = 15  # 物の怪（Lv1）よりずっと高くても、経験値は減らない
    G.set_base_stats(WORLD, state)
    state["hero"]["hp"] = G.stats(WORLD, state)["max_hp"]
    day = day_of(state)
    got = []
    day.gain_exp = got.append
    self.assertTrue(day.battle(dict(dog)))
    self.assertEqual(got, [dog["exp"]])

  def test_exp_stops_at_max_level(self):
    state = G.new_state(WORLD)
    day = day_of(state)
    day.gain_exp(10**7)
    day.gain_exp(500)
    self.assertEqual((state["hero"]["level"], state["hero"]["exp"]), (WORLD["season"]["max_level"], 0))

  def test_trait_is_random_and_kept(self):
    traits = {G.pick_trait(WORLD, f"h{i}") for i in range(40)}
    self.assertGreater(len(traits), 2)
    state = G.new_state(WORLD, "h7")
    self.assertEqual(state["trait"], G.pick_trait(WORLD, "h7"))
    self.assertEqual(state["personality"], G.pick_personality(WORLD, "h7"))
    nxt = START
    while G.season_of(WORLD, nxt) == 1:
      nxt += timedelta(days=1)
    G.advance(WORLD, state, START, 0)
    G.advance(WORLD, state, nxt, 0)  # シーズンが変わっても同じ
    self.assertEqual(state["trait"], G.pick_trait(WORLD, "h7"))

  def test_old_state_gets_a_trait(self):
    state = play(1)
    del state["trait"]
    state["hero"].pop("base_spd")
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path)
    self.assertEqual(loaded["trait"], G.pick_trait(WORLD, "h1"))
    self.assertEqual(loaded["hero"]["base_spd"], G.base_stats(WORLD, loaded["trait"], 1)["spd"])

  def grown(self, trait, level):
    state = G.new_state(WORLD)
    state["trait"] = trait
    day = day_of(state)
    while state["hero"]["level"] < level:
      day.gain_exp(G.exp_need(WORLD, state["hero"]["level"]))
    s = G.stats(WORLD, state)
    return s["max_hp"], s["atk"], s["def"], s["spd"], s["dex"]

  def test_traits_grow_on_level_up(self):
    self.assertEqual(self.grown("lucky", 1), (30, 6, 6, 6, 6))  # Lv1 はみんな同じ
    self.assertEqual(self.grown("strong", 1), (30, 6, 6, 6, 6))
    self.assertEqual(self.grown("lucky", 20), (182, 30, 30, 30, 30))  # ふつう（能力は伸びない強運と同じ）
    self.assertEqual(self.grown("sturdy", 20), (239, 28, 30, 30, 30))  # HP 1.375 倍・攻撃 0.9 倍
    self.assertEqual(self.grown("strong", 20), (136, 36, 30, 30, 30))  # 攻撃 1.25 倍・HP 0.7 倍
    self.assertEqual(self.grown("guard", 20), (182, 30, 36, 30, 30))
    self.assertEqual(self.grown("swift", 20), (182, 30, 30, 36, 30))
    self.assertEqual(self.grown("skilled", 20), (182, 30, 30, 30, 36))
    self.assertEqual(self.grown("lucky", 15), (142, 28, 20, 26, 24))  # growth の一覧の値

  def test_base_stats_follow_the_growth_table(self):
    season = WORLD["season"]
    for stat in G.GROWN:
      self.assertEqual([G.base_stats(WORLD, None, lv)[stat] for lv in range(1, 21)], season["growth"][stat])
    self.assertEqual(G.base_stats(WORLD, "strong", 2)["atk"], 9)  # 6 + (8 - 6) × 1.25 = 8.5 → 四捨五入で 9

  def test_level_stops_at_the_max(self):
    state = G.new_state(WORLD)
    day = day_of(state)
    day.gain_exp(10**7)
    self.assertEqual(state["hero"]["level"], WORLD["season"]["max_level"])

  def test_lucky_draws_again(self):
    state = G.new_state(WORLD)
    state["trait"] = "lucky"
    day = day_of(state)
    with mock.patch.object(day, "draw_once", side_effect=[None, "iron_sword"]) as once:
      self.assertEqual(day.draw([["none", 1]]), "iron_sword")
    self.assertEqual(once.call_count, 2)
    state["trait"] = "strong"
    with mock.patch.object(day, "draw_once", side_effect=[None, "iron_sword"]):
      self.assertIsNone(day.draw([["none", 1]]))

  def test_nothing_drops_when_the_town_has_no_such_rare(self):
    world = with_rares()
    state = G.new_state(world)
    day = G.Day(world, state, START, random.Random(0))
    self.assertIsNone(day.draw_once([["rare", 1]], None))  # 町のない抽選（宝箱など）には、レアはない
    self.assertIsNone(day.draw_once([["epic", 1]], None))  # 上のレアがなく、その下のレアもなければ、何も出ない
    self.assertEqual(day.draw_once([["epic", 1]], "edo"), "x_epic")  # その町に置いてあれば出る


class FallPathsTest(unittest.TestCase):
  """力尽きる経路（道の途中・出発・討伐の行き帰り・旅）。力尽きたら依頼は失敗し、お金と持ち物は残り、
  最後に寄った町で目を覚まして、その日はもう行動しない。"""

  HOME = "edo"

  def hero_state(self, quest, place, road=None):
    state = G.new_state(WORLD)
    state.update(season=1, season_day=5, day=5, place=place, home_town=self.HOME, road=road, quest=quest)
    state["hero"].update(gold=123, potions=0)
    state["inventory"] = {"iron_sword": 1}
    return state

  def deliver(self, town, to):
    quest = {"type": "deliver", "place": to, "report": to, "monster": None, "count": 1}
    quest.update(template="deliver", name="配達", rank=1, text="")
    return G.finish_quest(WORLD, quest, town)

  def advance_until_fall(self, state):
    """1 日（3 行動）進める。最初の道の出来事で力尽きる。道の出来事が何回起きたかを返す。"""
    with mock.patch.object(G.Day, "road_event", autospec=True, return_value=False) as event:
      G.advance(WORLD, state, START, 3)
    return event.call_count

  def assert_fallen(self, state, failed=1):
    self.assertEqual((state["place"], state["road"], state["quest"]), (self.HOME, None, None))
    self.assertEqual(state["season_stats"]["deaths"], 1)
    self.assertEqual(state["season_stats"]["quests_failed"], failed)
    self.assertEqual(state["hero"]["gold"], 123)  # お金・持ち物は減らない
    self.assertEqual(ids(state), ["iron_sword"])
    self.assertEqual(state["hero"]["hp"], G.stats(WORLD, state)["max_hp"])
    self.assertTrue(any("力尽きた" in e for e in state["last"]["events"]))

  def test_fall_in_the_middle_of_a_long_road(self):
    # 甲府 →（3 行動）→ 長野 の途中で力尽きる
    state = self.hero_state(self.deliver("kofu", "nagano"), "kofu", {"to": "nagano", "left": 2})
    self.assertEqual(self.advance_until_fall(state), 1)  # 残りの行動回数があっても、その日はもう動かない
    self.assert_fallen(state)

  def test_fall_when_leaving_for_a_delivery(self):
    state = self.hero_state(self.deliver("edo", "odawara"), "edo")
    self.assertEqual(self.advance_until_fall(state), 1)
    self.assert_fallen(state)

  def test_fall_on_the_way_to_a_hunt(self):
    quest = copy.deepcopy(a_hunt())
    self.assertEqual(quest["steps"][0][0], "go")
    state = self.hero_state(quest, TOWN)
    self.assertEqual(self.advance_until_fall(state), 1)
    self.assert_fallen(state)

  def test_fall_on_the_way_back_from_a_hunt(self):
    quest = copy.deepcopy(a_hunt())
    quest.update(steps=[["back", 1]], progress=quest["count"], away=1)  # 討伐は終わって、帰り道
    state = self.hero_state(quest, TOWN)
    self.assertEqual(self.advance_until_fall(state), 1)
    self.assert_fallen(state)

  def test_fall_on_a_trip_is_not_a_failed_quest(self):
    state = self.hero_state(None, "edo")
    state["quest"] = G.patrol(WORLD, state, random.Random(0))
    self.assertEqual(state["quest"]["type"], "patrol")
    self.assertEqual(self.advance_until_fall(state), 1)
    self.assert_fallen(state, failed=0)  # 旅（報酬のない依頼）は、失敗に数えない

  def test_delivers_when_already_in_the_destination_town(self):
    # 地図が変わって、依頼の届け先の町に戻されたときなど
    state = self.hero_state(self.deliver("edo", "odawara"), "odawara")
    day = day_of(state)
    day.road_event = lambda a, b, stroll=False: True
    self.assertTrue(day.act())
    self.assertEqual(state["season_stats"]["quests_done"], 1)
    self.assertTrue(any("依頼達成" in e for e in day.events))

  def test_reports_when_the_finished_quest_is_in_the_report_town(self):
    # 進み終えた依頼が、報告先の町にいるとき（ふだんの配達は、着いたらその場で報告するので、念のための動き）
    quest = self.deliver("edo", "odawara")
    quest["progress"] = quest["count"]
    state = self.hero_state(quest, "odawara")
    day = day_of(state)
    day.road_event = lambda a, b, stroll=False: True
    self.assertTrue(day.act())
    self.assertEqual(state["season_stats"]["quests_done"], 1)

  def test_goes_to_the_report_town_when_the_quest_is_done_elsewhere(self):
    # 進み終えた依頼が、報告先とは別の町にいるとき（ふだんの配達は届け先で報告するので、念のための動き）
    quest = self.deliver("edo", "odawara")
    quest.update(place="edo", report="odawara", progress=1)
    state = self.hero_state(quest, "edo")
    day = day_of(state)
    day.road_event = lambda a, b, stroll=False: True
    self.assertTrue(day.act())
    self.assertEqual(state["road"]["to"], "odawara")

  def test_a_finished_trip_is_cleared_without_a_report(self):
    state = self.hero_state(None, "odawara")
    quest = G.patrol(WORLD, state, random.Random(0))
    quest.update(place="odawara", progress=quest["count"])  # 旅は報告しない（着いたら終わり）
    state.update(quest=quest, taken=["今日はもう受けた"])
    day = day_of(state)
    day.road_event = lambda a, b, stroll=False: True
    self.assertTrue(day.act())
    self.assertIsNone(state["quest"])


def with_personality(pid, hero_id="h1"):
  """性格 pid のもののふ（最初の町にいる。シーズン 1 の途中なので、advance で持ち物や溜めた行動力が戻らない）。"""
  state = G.new_state(WORLD, hero_id)
  state.update(personality=pid, season=G.season_of(WORLD, START), season_day=1)
  return state


class PersonalityTest(unittest.TestCase):
  """性格（season.json の personalities）と、旅籠・行動力を溜める・散策の表（Issue #95・#226）。"""

  def test_personality_is_random_kept_and_repaired(self):
    kinds = {G.pick_personality(WORLD, f"h{i}") for i in range(60)}
    self.assertEqual(kinds, set(WORLD["season"]["personalities"]))  # どの性格も付く
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h3.json"
      state = G.new_state(WORLD, "h3")
      state.pop("personality")
      state["hero"]["stored"] = 99
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path, "h3")
    self.assertEqual(loaded["personality"], G.pick_personality(WORLD, "h3"))  # ない・消えた性格は付け直す
    self.assertEqual(loaded["hero"]["stored"], G.stored_max(WORLD))  # 溜めた行動力は上限まで

  def test_suitable_uses_the_level_range(self):
    q = {"level": [5, 5]}
    self.assertTrue(G.suitable(q, 5, [None, 0]))
    self.assertFalse(G.suitable(q, 4, [None, 0]))  # 自分より強い依頼は受けない（慎重）
    self.assertTrue(G.suitable(q, 2, [None, 3]))
    self.assertFalse(G.suitable(q, 8, [-2, 0]))  # 弱すぎる依頼も受けない（勤勉）
    self.assertFalse(G.suitable({"level": [5, 5], "min_level": 6}, 5, [None, 3]))  # 大物は Lv 以上から

  def test_take_quest_follows_the_order(self):
    guild = G.new_guild()
    state = with_personality("careful")
    town = state["place"]
    state["hero"]["level"] = 5
    G.refresh_guild(WORLD, guild, START, 1)
    entries = guild["towns"][town]
    hunt = next(
      e for e in entries if e["type"] == "hunt" and not e.get("party")
    )  # 討伐 4 件の高札にする（配達は Lv を見ないので）
    entries[:] = [dict(copy.deepcopy(hunt), id=f"{hunt['id']}-{i}") for i in range(4)]
    for i, e in enumerate(entries):  # Lv と ★ を作り直して、選び方だけを見る
      e.update(level=[[5, 5], [4, 4], [5, 5], [6, 6]][i % 4], rank=[2, 1, 1, 1][i % 4], min_level=0)
    G.Day(WORLD, state, START, random.Random(0), guild).take_quest(town)
    picked = next(e for e in entries if e["status"] == "held")  # 受けた依頼（もののふの写しは、Lv を作り直す）
    self.assertEqual((picked["level"][0], picked["rank"]), (5, 1))  # Lv の高いもの（自分以下）、★の少ないもの
    first = next(e for e in entries if e["level"][0] == 5 and e["rank"] == 1)
    self.assertIs(picked, first)  # 同じ条件なら高札の上のもの

  def test_take_quest_falls_back_to_the_nearest_level(self):
    guild = G.new_guild()
    state = with_personality("diligent")  # Lv −2〜0。Lv の高いもの
    town = state["place"]
    state["hero"]["level"] = 12
    G.refresh_guild(WORLD, guild, START, 1)
    # 高札を討伐だけにする（配達は Lv を見ないので、いつも範囲に入る）
    guild["towns"][town] = [
      dict(common_hunt(town, seed=i), id=f"q{i}", posted=START.isoformat(), status="open", holder=None)
      for i in range(5)
    ]
    entries = guild["towns"][town]
    for e in entries:  # 範囲（Lv10〜12）に合う依頼がない
      e.update(level=[2, 2], rank=1, min_level=0)
    entries[0].update(level=[5, 5])
    entries[1].update(level=[7, 7])
    entries[2].update(level=[15, 15])  # 範囲より強い依頼は、近くても受けない
    G.Day(WORLD, state, START, random.Random(0), guild).take_quest(town)
    picked = next(e for e in entries if e["status"] == "held")
    self.assertEqual(picked["level"][0], 7)  # 範囲より弱い依頼のうち、いちばん近い Lv7
    # 大物（min_level）は、範囲より弱い依頼でも Lv が足りなければ受けない
    for e in entries:
      e.update(status="open", holder=None)
    entries[1].update(level=[9, 9], min_level=13)
    entries[0].update(level=[7, 7])
    state.update(quest=None, taken=[])
    G.Day(WORLD, state, START, random.Random(0), guild).take_quest(town)
    self.assertIs(next(e for e in entries if e["status"] == "held"), entries[0])
    # 範囲からの外れが同じなら、性格の選び方（勤勉は Lv の高いもの）。Lv も同じなら高札の上
    tactician = with_personality("tactician")  # Lv −1〜+1。★の多いもの
    tactician["hero"]["level"] = 12
    for e in entries:
      e.update(status="open", holder=None, level=[2, 2], rank=1, min_level=0)
    entries[1].update(level=[5, 5], rank=1)
    entries[2].update(level=[5, 5], rank=3)
    G.Day(WORLD, tactician, START, random.Random(0), guild).take_quest(town)
    self.assertIs(next(e for e in entries if e["status"] == "held"), entries[2])  # 同じ Lv5 なら ★ の多いもの
    self.assertNotEqual(tactician["quest"]["type"], "patrol")  # 旅には出ない
    self.assertEqual(G.level_gap({"level": [5, 5]}, 12, [-2, 0]), 5)
    deliver = {"type": "deliver", "level": [2, 2]}  # 配達は Lv を見ない（どの範囲にも入る）
    self.assertTrue(G.suitable(deliver, 15, [-2, 0]))
    self.assertEqual(G.level_gap(deliver, 15, [-1, 1]), 0)
    self.assertEqual(G.QUEST_ORDER["level_high"](deliver, 15), 0)  # Lv を比べるときは自分と同じ Lv
    self.assertEqual(G.quest_gap({"type": "patrol"}, 15), 0)  # Lv を持たない旅でも落ちない
    self.assertEqual(G.level_gap({"level": [5, 5]}, 12, [None, 0]), 0)

  def test_stay_too_long_and_travel(self):
    limit = WORLD["season"]["stay_days_max"]
    state = with_personality("diligent")
    town = state["place"]
    day = G.Day(WORLD, state, START + timedelta(days=limit - 1), random.Random(0))
    state["stay"] = {"town": town, "since": START.isoformat()}
    day.take_quest(town)
    self.assertNotIn("長くいた", " ".join(day.events))  # 着いた日を 0 日目として、6 日目までは依頼を受ける
    state.update(quest=None, taken=[])
    day = G.Day(WORLD, state, START + timedelta(days=limit), random.Random(0))
    day.take_quest(town)
    self.assertEqual(state["quest"]["type"], "patrol")  # 7 日目（7 日たった日）は依頼を受けずに旅に出る
    self.assertIn(state["quest"]["place"], WORLD["neighbors"][town])
    self.assertIn("長くいたので", day.events[-1])
    day.visit_town(town)  # 討伐から同じ町に戻っても数え直さない
    self.assertEqual(state["stay"], {"town": town, "since": START.isoformat()})
    other = WORLD["neighbors"][town][0]
    day.visit_town(other)  # 別の町に着いたら、その日から数え直す
    self.assertEqual(state["stay"], {"town": other, "since": (START + timedelta(days=limit)).isoformat()})

  def test_stay_waits_until_the_morning_after_a_report(self):
    limit = WORLD["season"]["stay_days_max"]
    state = with_personality("diligent")
    town = state["place"]
    state["stay"] = {"town": town, "since": START.isoformat()}
    day = G.Day(WORLD, state, START + timedelta(days=limit + 1), random.Random(0))
    day.reported = True  # 今日、依頼から帰ってきた
    day.take_quest(town)
    self.assertIsNone(state["quest"])  # 今日は旅に出ない（残りの行動をする）
    self.assertTrue(state["taken"])
    state["stay"]["since"] = None  # 着いた日が壊れていても落ちない（今日から数え直す）
    self.assertFalse(day.stayed_too_long(town))

  def test_deliveries_fit_any_range(self):
    deliver = {"type": "deliver", "level": [2, 2]}
    self.assertTrue(G.suitable(deliver, 5, [1, 3]))  # 0 を含まない範囲でも入る
    self.assertEqual(G.level_gap(deliver, 5, [1, 3]), 0)

  def test_broken_stay_is_reset_on_load(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      for bad in ({"town": "nowhere", "since": "2026-12-01"}, {"town": "edo", "since": "x"}, "x"):
        state = G.new_state(WORLD, "h1")
        state["stay"] = bad
        G.save_state(state, path)
        self.assertIsNone(G.load_state(WORLD, path, "h1")["stay"])

  def test_gear_stat_follows_trait_and_personality(self):
    state = with_personality("brave")  # 伸ばす
    for trait, stat in (
      ("strong", "atk"),
      ("sturdy", "hp"),
      ("guard", "def"),
      ("swift", "spd"),
      ("skilled", "dex"),
      ("lucky", None),
    ):
      state["trait"] = trait
      self.assertEqual(G.gear_stat(WORLD, state), stat, trait)
    state["personality"] = "careful"  # 補う
    for trait, stat in (("strong", "hp"), ("sturdy", "atk"), ("guard", None)):
      state["trait"] = trait
      self.assertEqual(G.gear_stat(WORLD, state), stat, trait)
    state["personality"] = "thrifty"  # バランス
    state["trait"] = "strong"
    self.assertIsNone(G.gear_stat(WORLD, state))
    charms = G.instances({"soaring_leaf_charm": 1, "silkvine_charm": 1, "lifeblood_charm": 1})
    for pid, trait, want in (
      ("brave", "swift", "soaring_leaf_charm"),
      ("adventurous", "skilled", "silkvine_charm"),
      ("careful", "strong", "lifeblood_charm"),
    ):
      state.update(personality=pid, trait=trait, inventory=[dict(x) for x in charms])
      self.assertEqual(G.equipment(WORLD, state)["accessory"], want, (pid, trait))

  def test_carry_limit_and_storage(self):
    state = with_personality("diligent")  # バランス: 同じ点数なら、マイナスのない安いもの
    state["trait"] = "lucky"
    state["inventory"] = G.instances({"iron_sword": 1, "iron_axe": 1, "wooden_helm": 1, "iron_helm": 1})
    G.carried(state).extend({"id": "iron_sword", "skill": None} for _ in range(3))  # 剣を 4 本・斧を 1 本
    day = day_of(state)
    day.arrange(state["place"])
    self.assertEqual([i for i in ids(state) if WORLD["items"][i]["slot"] == "weapon"], ["iron_axe", "iron_sword"])
    self.assertEqual(
      [i for i in ids(state) if WORLD["items"][i]["slot"] == "helmet"], ["wooden_helm"]
    )  # 選ぶほうを持つ
    self.assertEqual([x["id"] for x in G.stored(state)], ["iron_helm"])  # 残りは蔵
    self.assertIn("同じ装備を 3 つ手放した", day.events)  # スキルなしの同じ剣は、2 本目から手放す
    self.assertTrue(G.owns(state, "iron_helm"))  # 蔵にあっても自分のもの（店で買い直さない）
    G.start_season(WORLD, state, 2)
    self.assertEqual(len(G.owned(state)), 4)  # 持ち物と蔵はシーズンをまたいで残る

  def test_old_inventory_format_is_read(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      state = G.new_state(WORLD, "h1")
      state["inventory"] = {"iron_sword": 2, "gone": 1}
      state.pop("storage")
      G.save_state(state, path)
      loaded = G.load_state(WORLD, path, "h1")
    self.assertEqual(loaded["inventory"], [{"id": "iron_sword", "skill": None}] * 2)
    self.assertEqual(loaded["storage"], [])

  def test_potion_is_used_below_the_rate_and_zero_hp_falls(self):
    state = with_personality("careful")  # 丸薬は HP 50% 未満
    day = day_of(state)
    max_hp = day.stats["max_hp"]
    state["hero"].update(hp=max_hp // 2 - 1, potions=1)
    day.use_potion()
    self.assertEqual(state["hero"]["potions"], 0)
    self.assertEqual(state["hero"]["hp"], min(max_hp, max_hp // 2 - 1 + int(max_hp * 0.5)))
    strong = dict(WORLD["monsters"]["wild_dog"], atk=999, hp=999, spd=1)
    state["hero"].update(hp=max_hp, potions=3)
    self.assertFalse(day.battle(strong))  # 一撃で 0 になったら、丸薬があっても力尽きる
    self.assertEqual(state["hero"]["potions"], 3)

  def test_town_does_not_heal_but_the_inn_does(self):
    state = with_personality("careful")  # 旅籠は HP 70% 未満
    day = day_of(state)
    max_hp = day.stats["max_hp"]
    state["hero"].update(hp=1, gold=100)
    day.visit_town(state["place"])
    self.assertEqual(state["hero"]["hp"], 1)  # 町に着いても回復しない
    self.assertTrue(day.act())  # 依頼を持っていないので、旅籠で休憩する（1 行動）
    self.assertEqual(state["hero"]["hp"], max_hp)
    self.assertLessEqual(state["hero"]["gold"], 100 - WORLD["season"]["inn"]["price"])
    self.assertIn("旅籠で休んだ", " ".join(day.events))

  def test_spare_actions(self):
    town = TOWN
    state = with_personality("careful")  # 休む（溜める）→ 溜まりきったら散策
    state.update(place=town, taken=["x"])
    day = day_of(state)
    day.spare(town)
    self.assertEqual(state["hero"]["stored"], 1)
    state["hero"]["stored"] = G.stored_max(WORLD)
    day.road_event = lambda a, b, stroll=False: stroll or self.fail("散策ではない")
    self.assertTrue(day.spare(town))
    self.assertEqual(state["hero"]["stored"], G.stored_max(WORLD))
    mover = with_personality("adventurous")  # となりの町へ移動する
    mover.update(place=town, taken=["x"])
    mday = day_of(mover)
    mday.road_event = lambda a, b, stroll=False: True
    mday.spare(town)
    self.assertTrue(mover["road"] or mover["place"] in WORLD["neighbors"][town])
    tactician = with_personality("tactician")  # HP 70% 以上なら散策、未満なら溜める
    tactician.update(place=town, taken=["x"])
    tday = day_of(tactician)
    tday.road_event = lambda a, b, stroll=False: True
    tactician["hero"]["hp"] = 1
    tday.spare(town)
    self.assertEqual(tactician["hero"]["stored"], 1)

  def test_stored_actions_finish_a_quest(self):
    per_day = WORLD["season"]["actions_per_day"]
    state = with_personality("diligent")
    state["hero"]["stored"] = 6
    quest = common_hunt()
    G.finish_quest(WORLD, quest, TOWN)
    with mock.patch.object(G.Day, "act", lambda self: state.update(quest=quest) or True):  # 依頼が終わらない
      G.advance(WORLD, state, START)
    self.assertEqual((state["last"]["actions"], state["hero"]["stored"]), (per_day + 6, 0))  # 溜めた分を全部使う
    for trip, road in (
      ({"type": "patrol"}, None),
      (None, {"to": "odawara", "left": 2}),
    ):  # 旅・残りの行動の道には使わない
      walker = with_personality("diligent")
      walker["hero"]["stored"] = 6
      with mock.patch.object(G.Day, "act", lambda self, w=walker, t=trip, r=road: w.update(quest=t, road=r) or True):
        G.advance(WORLD, walker, START)
      self.assertEqual((walker["last"]["actions"], walker["hero"]["stored"]), (per_day, 6))
    rest = with_personality("diligent")  # 休みの日（行動回数 0）は使わない
    rest.update(quest=quest)
    rest["hero"]["stored"] = 6
    G.advance(WORLD, rest, START, actions=0)
    self.assertEqual(rest["hero"]["stored"], 6)

  def test_shop_without_personality_still_buys_potions(self):
    state = G.new_state(WORLD, "h1")
    state["personality"] = None
    price = WORLD["season"]["potion"]["price"]
    state["hero"].update(potions=0, gold=price * 3)
    day_of(state).shop(state["place"])
    self.assertEqual(state["hero"]["potions"], 3)  # 丸薬の分のお金を二重に残さない

  def test_stored_actions_reset_each_season(self):
    state = with_personality("careful")
    state["hero"]["stored"] = 4
    G.start_season(WORLD, state, 2)
    self.assertEqual(state["hero"]["stored"], 0)

  def test_stroll_uses_its_own_table(self):
    state = with_personality("brave")
    state["hero"]["level"] = 5
    world = copy.deepcopy(WORLD)
    world["season"]["stroll_events"] = {"strong_battle": 1}
    day = G.Day(world, state, START, random.Random(0))
    seen = []
    day.battle = lambda m: seen.append(m["level"]) or True
    for _ in range(20):
      day.road_event(TOWN, WORLD["neighbors"][TOWN][0], stroll=True)
    road = WORLD["road_info"][(TOWN, WORLD["neighbors"][TOWN][0])]["monsters"]
    window = [5 + d for d in WORLD["season"]["stroll_strong_levels"]]
    levels = {WORLD["monsters"][m]["level"] for m, _ in road}
    if any(window[0] <= lv <= window[1] for lv in levels):
      self.assertTrue(all(window[0] <= lv <= window[1] for lv in seen), seen)  # 強い物の怪は Lv +2〜+3
    world["season"]["stroll_events"] = {"treasure": 1}
    world["season"]["stroll_treasure"] = {"potion": 1}
    day = G.Day(world, state, START, random.Random(0))
    potions = state["hero"]["potions"]
    day.road_event(TOWN, WORLD["neighbors"][TOWN][0], stroll=True)
    self.assertEqual(state["hero"]["potions"], potions + 1)

  def test_road_and_stroll_chests_award_a_regional_stone(self):
    for mode in ("road", "stroll"):
      with self.subTest(mode=mode):
        world = copy.deepcopy(WORLD)
        world["season"][f"{mode}_events"] = {"treasure": 1}
        world["season"][f"{mode}_treasure"] = {"stone": 1}
        state = G.new_state(world)
        day = G.Day(world, state, START, random.Random(0))
        day.road_event(TOWN, world["neighbors"][TOWN][0], stroll=mode == "stroll")
        self.assertEqual(state["stones"], {"bando": 1})
        self.assertFalse(state["inventory"])

  def test_stone_loot_works_in_worlds_without_areas(self):
    world = copy.deepcopy(WORLD)
    world["areas"] = {}
    for place in world["places"].values():
      place.pop("area", None)
    state = G.new_state(world)
    day = G.Day(world, state, START, random.Random(0))
    day.obtain_stones(TOWN, 1, "宝箱から")
    self.assertEqual(state["stones"], {"": 1})
    self.assertEqual(day.events[-1], "宝箱から原石を 1 つ手に入れた")

  def test_shop_buys_potions_first_and_keeps_inn_money(self):
    state = with_personality("careful")  # 先に 3 個、旅籠は HP 70% 未満
    state["hero"].update(potions=0, gold=WORLD["season"]["potion"]["price"] * 3 + WORLD["season"]["inn"]["price"], hp=1)
    day = day_of(state)
    day.shop(state["place"])
    self.assertEqual(state["hero"]["potions"], 3)
    self.assertEqual(state["hero"]["gold"], WORLD["season"]["inn"]["price"])  # 旅籠代は残す
    brave = with_personality("brave")  # 先に買う丸薬は 0 個（装備を先に買う）
    brave["hero"].update(potions=0, gold=10_000)
    day_of(brave).shop(brave["place"])
    self.assertTrue(brave["inventory"])


def gear(*pairs):
  """装備の一覧（(ID, スキル) の組から）。"""
  return [{"id": i, "skill": k} for i, k in pairs]


class SkillTest(unittest.TestCase):
  """装備のスキル（#233）・入れ替え（#234）・性格のルール（#235）。"""

  def test_every_slot_has_ten_skills_of_every_kind(self):
    by_slot = {}
    for sk in WORLD["skills"].values():
      by_slot.setdefault(sk["slot"], []).append(sk["kind"])
    self.assertEqual(sorted(by_slot), sorted(W.SLOTS))
    for slot, kinds in by_slot.items():
      self.assertEqual(len(kinds), 10, slot)
      self.assertEqual(set(kinds), set(W.SKILL_KINDS), slot)  # どの部位にも、どの系統も 1 つ以上

  def test_smith_follows_the_personality(self):
    price = WORLD["skill_prices"]["rare"]
    brave = with_personality("brave")  # ★★ 以上・余裕 1 倍・付け替える
    brave["inventory"] = gear(("steel_sword", None))
    brave["hero"]["gold"] = 10_000
    day = day_of(brave)
    day.smith(brave["place"])
    self.assertIsNotNone(G.carried(brave)[0]["skill"])
    self.assertEqual(brave["hero"]["gold"], 10_000 - price)
    self.assertIn("鍛冶屋", day.events[-1])
    day.smith(brave["place"])  # 1 日 1 回まで
    self.assertEqual(brave["hero"]["gold"], 10_000 - price)
    poor = with_personality("brave")
    poor["inventory"] = gear(("steel_sword", None))
    poor["hero"]["gold"] = price  # 旅籠代・丸薬の分を残すと足りない
    day_of(poor).smith(poor["place"])
    self.assertIsNone(G.carried(poor)[0]["skill"])
    careful = with_personality("careful")  # ★★★ 以上なので、★★ には付けない
    careful["inventory"] = gear(("steel_sword", None))
    careful["hero"]["gold"] = 10_000
    day_of(careful).smith(careful["place"])
    self.assertIsNone(G.carried(careful)[0]["skill"])
    common = with_personality("brave")  # ★ にはスキルを付けない
    common["inventory"] = gear(("iron_sword", None))
    common["hero"]["gold"] = 10_000
    day_of(common).smith(common["place"])
    self.assertIsNone(G.carried(common)[0]["skill"])

  def test_bando_legendary_forge_uses_stones_and_gold_once_per_day(self):
    state = G.new_state(WORLD, "forge-test")
    state["place"] = state["home_town"] = "odawara"
    state["stones"] = {"bando": 20}
    reserve = WORLD["season"]["inn"]["price"] + WORLD["season"]["potion"]["price"] * WORLD["season"]["potion"]["keep"]
    state["hero"]["gold"] = 20_000 + reserve
    day = day_of(state)
    day.forge_legendary("odawara")
    self.assertEqual(state["stones"], {"bando": 10})
    self.assertEqual(state["hero"]["gold"], 10_000 + reserve)
    self.assertEqual(len(G.carried(state)), 1)
    forged = G.carried(state)[0]
    self.assertEqual(WORLD["items"][forged["id"]]["rarity"], "legendary")
    self.assertIsNone(forged["skill"])
    self.assertEqual(WORLD["items"][forged["id"]]["forge_town"], "odawara")
    resumed = day_of(state)  # 同じ日付で Day を作り直しても、保存済みの生成済み記録を使う
    resumed.forge_legendary("odawara")
    self.assertEqual(len(G.carried(state)), 1)
    self.assertEqual(state["stones"], {"bando": 10})
    self.assertEqual(state["hero"]["gold"], 10_000 + reserve)

  def test_town_visit_triggers_legendary_forge_automatically(self):
    state = G.new_state(WORLD, "automatic-forge")
    state.update(place="odawara", home_town="odawara", season=G.season_of(WORLD, START), season_day=0)
    state["stones"] = {"bando": 10}
    state["hero"]["gold"] = 100_000
    G.advance(WORLD, state, START, actions=0)
    forged = [x for x in G.owned(state) if WORLD["items"][x["id"]]["rarity"] == "legendary"]
    self.assertEqual(len(forged), 1)
    self.assertEqual(sum(state["stones"].values()), 0)
    self.assertIn("玄岳鋼を 10 個使った", state["last"]["events"][-2])

  def test_bando_legendary_forge_requires_materials_money_and_local_smithy(self):
    for town, stones, gold in (("odawara", 9, 20_000), ("odawara", 10, 10_000), ("edo", 10, 20_000)):
      with self.subTest(town=town, stones=stones, gold=gold):
        state = G.new_state(WORLD, f"forge-{town}-{stones}-{gold}")
        state["place"] = state["home_town"] = town
        state["stones"] = {"bando": stones}
        state["hero"]["gold"] = gold
        day_of(state).forge_legendary(town)
        self.assertFalse(G.carried(state))
        self.assertEqual(state["stones"], {"bando": stones})
        self.assertEqual(state["hero"]["gold"], gold)

  def test_smith_skips_gear_with_a_preferred_skill(self):
    brave = with_personality("brave")  # 武器の優先スキル: 会心の心得・二の太刀・修練
    world = copy.deepcopy(WORLD)
    world["items"]["x_epic_sword"] = {
      "id": "x_epic_sword",
      "name": "試しの剣",
      "slot": "weapon",
      "rarity": "epic",
      "atk": 1,
    }
    brave["inventory"] = gear(("steel_sword", None), ("x_epic_sword", "crit_art"))  # ★ の多い剣には優先スキル
    brave["hero"]["gold"] = 10_000
    day = G.Day(world, brave, START, random.Random(0))
    with mock.patch.object(day.rng, "choice", return_value="hunter"):
      day.smith(brave["place"])
    skills = {x["id"]: x["skill"] for x in G.carried(brave)}
    self.assertEqual(skills, {"x_epic_sword": "crit_art", "steel_sword": "hunter"})  # 優先スキルの剣は変えず、次の剣に
    self.assertNotIn("（優先スキル）", day.events[-1])
    day.smithed = False  # 次の日: 優先スキルでない手柄の刀は、また付け直す
    with mock.patch.object(day.rng, "choice", return_value="combo"):
      day.smith(brave["place"])
    self.assertEqual({x["id"]: x["skill"] for x in G.carried(brave)}["steel_sword"], "combo")
    self.assertIn("（優先スキル）", day.events[-1])
    day.smithed = False  # 全部に優先スキルが付いたら、もう付けない
    gold = brave["hero"]["gold"]
    day.smith(brave["place"])
    self.assertEqual(brave["hero"]["gold"], gold)

  def test_treasure_gold_is_a_fixed_range(self):
    state = with_personality("brave")
    state["hero"]["level"] = 18
    world = copy.deepcopy(WORLD)
    world["season"]["road_events"] = {"treasure": 1}
    world["season"]["road_treasure"] = {"gold": 1}
    day = G.Day(world, state, START, random.Random(0))
    for _ in range(30):
      gold = state["hero"]["gold"]
      day.road_event(TOWN, WORLD["neighbors"][TOWN][0])
      low, high = WORLD["season"]["treasure_gold"]
      self.assertTrue(low <= state["hero"]["gold"] - gold <= high)  # もののふの Lv に関係なく 10〜30文

  def test_prefer_index(self):
    state = with_personality("brave")
    self.assertEqual(G.prefer_index(WORLD, state, {"id": "steel_sword", "skill": "crit_art"}), 0)
    self.assertEqual(G.prefer_index(WORLD, state, {"id": "steel_sword", "skill": "hunter"}), 99)
    self.assertEqual(G.prefer_index(WORLD, state, {"id": "steel_sword", "skill": None}), 99)

  def test_arrange_brings_counters_for_the_quest(self):
    state = with_personality("tactician")
    helms = gear(("iron_helm", None), ("wooden_helm", "antidote"))  # 頭の品は 1 つしか持てない
    state["inventory"], state["storage"] = helms[:1], helms[1:]
    poison = next(m for m in WORLD["monsters"].values() if m.get("trait") == "poison")
    quest = {"type": "hunt", "monster": poison["id"]}
    day = day_of(state)
    day.arrange(state["place"], quest)
    self.assertEqual(G.carried(state)[0]["skill"], "antidote")  # 毒の物の怪の討伐なので、毒よけの頭の品を持つ

  def test_review_fixes(self):
    # ふだん着ける装備は ★ の多いもの。★ と点数が同じなら、性格の優先スキルの付いたもの
    state = with_personality("thrifty")  # 御守の優先スキル: 値切り
    state["inventory"] = gear(("crimson_grass_charm", None), ("lifeblood_charm", "haggle"))
    self.assertEqual(G.worn(WORLD, state)["accessory"]["id"], "crimson_grass_charm")  # ★★ を先に
    state["inventory"] = gear(("silkvine_charm", None), ("silkvine_charm", "haggle"))
    self.assertEqual(G.worn(WORLD, state)["accessory"]["skill"], "haggle")
    self.assertEqual(day_of(state).price(100), 90)
    # 鍛冶屋は、蔵の装備には付けない
    state = with_personality("brave")
    state["inventory"], state["storage"] = [], gear(("steel_sword", None))
    state["hero"]["gold"] = 10_000
    day_of(state).smith(state["place"])
    self.assertIsNone(G.stored(state)[0]["skill"])
    self.assertEqual(state["hero"]["gold"], 10_000)
    # 店は、蔵のもっと強い品より弱い品を買わない
    state = with_personality("brave")
    state["inventory"], state["storage"] = gear(("wooden_helm", "antidote")), gear(("iron_helm", None))
    state["hero"]["gold"] = 10_000
    day_of(state).shop(state["place"])
    self.assertFalse(G.owns(state, "x_none"))
    self.assertEqual(sum(WORLD["items"][x["id"]]["slot"] == "helmet" for x in G.owned(state)), 2)  # 頭の品は買わない
    # id のない持ち物は、読み込むときに捨てる
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "h1.json"
      bad = G.new_state(WORLD, "h1")
      bad["inventory"] = [{"skill": "pierce"}, {"id": "iron_sword", "skill": None}]
      G.save_state(bad, path)
      self.assertEqual(G.load_state(WORLD, path, "h1")["inventory"], gear(("iron_sword", None)))

  def test_review_fixes_2(self):
    # 依頼がないときは、対策スキルのために強い胴の品を蔵にしまわない
    state = with_personality("brave")
    world = copy.deepcopy(WORLD)
    world["items"]["x_leg_shield"] = {
      "id": "x_leg_shield",
      "name": "伝説の鎧",
      "slot": "shield",
      "rarity": "legendary",
      "def": 1,
    }
    state["inventory"] = gear(("x_leg_shield", None), ("steel_shield", "swarm_guard"))
    G.Day(world, state, START, random.Random(0)).arrange(state["place"])
    self.assertEqual(G.carried(state)[0]["id"], "x_leg_shield")
    # 今の依頼の物の怪に効く対策スキルは、鍛冶屋で付け直さない
    careful = with_personality("brave")
    poison = next(m for m in WORLD["monsters"].values() if m.get("trait") == "poison")
    careful["quest"] = {"type": "hunt", "monster": poison["id"]}
    careful["inventory"] = gear(("steel_helm", "antidote"))
    careful["hero"]["gold"] = 10_000
    day = day_of(careful)
    day.smith(careful["place"])
    self.assertEqual(G.carried(careful)[0]["skill"], "antidote")
    # 耐えの修行は、途中で回復しても、受けたダメージの合計で見る
    hard = with_personality("careful")
    hard["inventory"] = gear(("steel_shield", "hardship"))
    day = day_of(hard)
    max_hp = day.stats["max_hp"]
    foe = dict(WORLD["monsters"]["wild_dog"], hp=40, atk=0, spd=1, exp=10)
    hits = iter([max_hp // 2, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    with mock.patch.object(day, "damage", side_effect=lambda a, g, d=0, **k: next(hits, 0) if a == 0 else 5):
      hard["hero"]["potions"] = 3
      day.battle(foe)
    self.assertIn("+12EXP", " ".join(day.events))  # 10 × 1.2

  def test_arrange_keeps_element_weapons(self):
    world = copy.deepcopy(WORLD)
    for i, el in enumerate(("flame", "ice", "thunder")):
      world["items"][f"x_e{i}"] = {
        "id": f"x_e{i}",
        "name": f"試し{i}",
        "slot": "weapon",
        "rarity": "rare",
        "atk": 1,
        "element": el,
      }
    world["items"]["x_big"] = {"id": "x_big", "name": "大剣", "slot": "weapon", "rarity": "rare", "atk": 2}
    state = with_personality("brave")
    ice = next(f"x_e{i}" for i, el in enumerate(("flame", "ice", "thunder")) if G.element_rate(world, el, "flame") > 1)
    state["inventory"] = gear(("x_big", None), ("iron_sword", None), ("iron_axe", None), (ice, None))  # 武器 4 本
    day = G.Day(world, state, START, random.Random(0))
    day.quest_foes = lambda q: [{"element": "flame", "trait": None}]  # 炎の物の怪の依頼
    day.arrange(state["place"], {"type": "hunt"})
    self.assertIn(ice, {x["id"] for x in G.carried(state)})  # 弱点を突ける属性の武器は、蔵にしまわない
    self.assertEqual(len(G.stored(state)), 1)

  def test_battle_skills(self):
    state = with_personality("brave")
    day = day_of(state)
    poison = dict(WORLD["monsters"]["wild_dog"], trait="poison", hp=1, atk=1)
    state["inventory"] = gear(("iron_helm", "antidote"))
    hp = state["hero"]["hp"]
    self.assertTrue(day.battle(poison))
    self.assertNotIn("毒で HP", " ".join(day.events))  # 毒よけ
    self.assertGreaterEqual(state["hero"]["hp"], hp - 2)
    state["inventory"] = gear(("iron_shield", "endure"))
    strong = dict(WORLD["monsters"]["wild_dog"], atk=999, hp=999, spd=99)
    state["hero"]["hp"] = 10
    day.battle(strong)
    self.assertIn("踏ん張って", " ".join(day.events))  # 1 日 1 回、HP 1 で耐える

  def test_pierce_boosts_final_damage_only_against_armored_monsters(self):
    state = with_personality("brave")
    state["inventory"] = gear(("iron_sword", "pierce"))
    armored = dict(WORLD["monsters"]["wild_dog"], trait="armored", hp=22, spd=6, **{"def": 20})
    day = day_of(state)
    body = W.trait_effect(armored)
    equipped = day.ready(armored, body)
    self.assertEqual(equipped["weapon"]["id"], "iron_sword")
    self.assertEqual(equipped["guard"], 41)
    with mock.patch.object(day, "damage", return_value=20) as damage:
      self.assertTrue(day.battle(armored))
    damage.assert_called_once()
    self.assertEqual(damage.call_args.args[1], 41)
    self.assertIsNone(day.ready(dict(armored, trait=None), body)["pierce"])

  def test_weapons_with_pierce_are_compared_by_damage(self):
    state = with_personality("brave")
    state["inventory"] = gear(("iron_sword", "pierce"), ("iron_axe", None))
    armored = dict(WORLD["monsters"]["wild_dog"], trait="armored", spd=6, **{"def": 2})
    iid, *_ = day_of(state).pick_weapon(armored, W.trait_effect(armored)["guard"])
    self.assertEqual(iid, "iron_axe")

  def test_fixed_and_additional_skills_both_apply(self):
    state = with_personality("brave")
    state["inventory"] = gear(
      ("cross_spear", "crit_art"),
      ("iron_face_guard", "focus"),
      ("yamabushi_robe", None),
      ("road_guard_charm", "haggle"),
    )
    day = day_of(state)
    armored = WORLD["monsters"]["rock_golem"]
    equipment = day.ready(armored, W.trait_effect(armored))
    self.assertEqual((equipment["pierce"], equipment["mult"], equipment["divisor"]), (1.1, 4, 120))
    self.assertTrue(day.skill("insight"))
    self.assertEqual(day.skill("second_wind"), 0.05)
    day.gear = None
    self.assertEqual(day.event_weights(False)["battle"], WORLD["season"]["road_events"]["battle"] * 0.75)
    self.assertEqual(day.price(100), 90)

  def test_smith_excludes_fixed_skill_and_fills_old_duplicate_slot(self):
    state = with_personality("brave")
    state["hero"]["gold"] = 10_000
    state["inventory"] = gear(("cross_spear", "pierce"))
    day = day_of(state)
    obtained = state["inventory"][0]
    self.assertEqual(G.equipment_skills(WORLD, obtained), ("pierce", None))
    with mock.patch.object(day.rng, "choice", return_value="combo") as choose:
      day.smith(TOWN)
    self.assertNotIn("pierce", choose.call_args.args[0])
    self.assertEqual(G.equipment_skills(WORLD, obtained), ("pierce", "combo"))
    self.assertTrue(day.smithed)

  def test_money_and_travel_skills(self):
    state = with_personality("brave")
    state["inventory"] = gear(("lifeblood_charm", "haggle"))
    day = day_of(state)
    self.assertEqual(day.price(100), 90)  # 値切り
    state["inventory"] = gear(("lifeblood_charm", "stealth"))
    weights = day.event_weights(False)
    base = WORLD["season"]["road_events"]
    self.assertLess(weights["battle"], base["battle"])  # 忍び足
    self.assertAlmostEqual(sum(weights.values()), sum(base.values()))  # 減った分は「何もない」へ
    state["inventory"] = gear(("iron_sword", "hunter"))
    state["quest"] = common_hunt()
    G.finish_quest(WORLD, state["quest"], TOWN)
    gold = state["hero"]["gold"]
    day.report(state["quest"] | {"reward": {"exp": 0, "gold": 100, "item": None}})
    self.assertEqual(state["hero"]["gold"], gold + 120)  # 手柄


if __name__ == "__main__":
  unittest.main()
