import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
import battle_sim as B  # noqa: E402
import world as W  # noqa: E402

WORLD = W.load_world()


class BattleSimTest(unittest.TestCase):
  def test_boss_is_not_on_roads(self):
    boss = WORLD["monsters"]["den_lord"]
    self.assertEqual(boss["boss"], "wall_den")
    self.assertFalse(any(m == "den_lord" for i in WORLD["road_info"].values() for m, _ in i["monsters"]))

  def test_make_hero_grows_by_trait(self):
    state = B.make_hero(WORLD, "strong", 15, [], 3)
    self.assertEqual((state["hero"]["level"], state["hero"]["potions"], state["inventory"]), (15, 3, {}))
    self.assertEqual(state["hero"]["hp"], 142)  # 装備なし、ふつうの HP の伸び

  def test_boss_win_rates_for_a_plain_hero(self):
    # ふつう（能力の伸びない強運）・装備なし・回復薬 3 個で、Lv16 で 7 割、Lv18 で 9 割、それより上はほぼ必ず勝つ
    boss = WORLD["monsters"]["den_lord"]
    rate = {lv: B.win_rate(WORLD, boss, "lucky", lv, [], 3, 400) for lv in (12, 16, 18, 20)}
    self.assertLess(rate[12], 0.05)
    self.assertTrue(0.6 <= rate[16] <= 0.8, rate)
    self.assertTrue(0.85 <= rate[18] <= 0.97, rate)
    self.assertGreaterEqual(rate[20], 0.99)

  def test_stat_traits_are_balanced_against_the_boss(self):
    boss = WORLD["monsters"]["den_lord"]
    base = B.even_strength(WORLD, boss, "lucky", 15, [], 3, 60)
    gains = {t: B.even_strength(WORLD, boss, t, 15, [], 3, 60) / base for t in ("strong", "sturdy", "guard")}
    for t, g in gains.items():  # 能力の伸びる特性は、ふつうより強い相手と五分に戦える
      self.assertTrue(1.03 <= g <= 1.2, (t, g))
    self.assertLess(max(gains.values()) - min(gains.values()), 0.08)

  def test_reach_level(self):
    self.assertEqual(B.reach_level({14: 0.0, 15: 0.55, 16: 1.0}), 15)
    self.assertIsNone(B.reach_level({14: 0.0, 15: 0.4}))


if __name__ == "__main__":
  unittest.main()
