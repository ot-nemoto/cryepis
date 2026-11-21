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

  def test_boss_is_an_even_fight_for_a_plain_lv15(self):
    boss = WORLD["monsters"]["den_lord"]
    rate = B.win_rate(WORLD, boss, "lucky", 15, [], 3, 200)  # 能力の伸びない強運＝ふつう
    self.assertTrue(0.3 <= rate <= 0.7, rate)
    self.assertEqual(B.win_rate(WORLD, boss, "lucky", 12, [], 3, 50), 0.0)  # Lv12 では勝てない
    self.assertEqual(B.win_rate(WORLD, boss, "lucky", 18, [], 3, 50), 1.0)  # Lv18 なら勝てる

  def test_stat_traits_are_balanced_against_the_boss(self):
    boss = WORLD["monsters"]["den_lord"]
    base = B.even_strength(WORLD, boss, "lucky", 15, [], 3, 60)
    gains = {t: B.even_strength(WORLD, boss, t, 15, [], 3, 60) / base for t in ("strong", "sturdy", "guard")}
    for t, g in gains.items():  # 能力の伸びる特性は、ふつうより 1 割ほど強い相手と五分に戦える
      self.assertTrue(1.04 <= g <= 1.16, (t, g))
    self.assertLess(max(gains.values()) - min(gains.values()), 0.06)

  def test_reach_level(self):
    self.assertEqual(B.reach_level({14: 0.0, 15: 0.55, 16: 1.0}), 15)
    self.assertIsNone(B.reach_level({14: 0.0, 15: 0.4}))


if __name__ == "__main__":
  unittest.main()
