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
    self.assertEqual(boss["boss"], "fuji")
    self.assertFalse(any(m == "den_lord" for i in WORLD["road_info"].values() for m, _ in i["monsters"]))

  def test_make_hero_grows_by_trait(self):
    state = B.make_hero(WORLD, "strong", 15, [], 3)
    self.assertEqual((state["hero"]["level"], state["hero"]["potions"], state["inventory"]), (15, 3, []))
    self.assertEqual(state["hero"]["hp"], 108)  # 装備なし、怪力は HP の伸びが 0.7 倍（30 + 8×14×0.7 = 108.4）

  def test_boss_win_rates_for_a_plain_hero(self):
    # ふつう（能力の伸びない強運）・装備なし・丸薬 3 個で、Lv15 で 5 割、Lv16 で 7 割、Lv17 で 9 割、それより上はほぼ必ず勝つ
    boss = WORLD["monsters"]["den_lord"]
    rate = {lv: B.win_rate(WORLD, boss, "lucky", lv, [], 3, 400) for lv in (12, 15, 16, 17, 19)}
    self.assertLess(rate[12], 0.05)
    self.assertTrue(0.4 <= rate[15] <= 0.65, rate)
    self.assertTrue(0.6 <= rate[16] <= 0.8, rate)
    self.assertTrue(0.8 <= rate[17] <= 0.97, rate)
    self.assertGreaterEqual(rate[19], 0.98)

  def test_stat_traits_help_against_the_boss(self):
    boss = WORLD["monsters"]["den_lord"]
    base = B.even_strength(WORLD, boss, "lucky", 15, [], 3, 60)
    gains = {t: B.even_strength(WORLD, boss, t, 15, [], 3, 60) / base for t in ("strong", "sturdy", "guard", "skilled")}
    for t, g in gains.items():  # 能力の伸びる特性は、ふつうと同じか、より強い相手と五分に戦える
      self.assertGreaterEqual(g, 1.0, t)

  def test_shaped_keeps_the_ratio_and_other_stats(self):
    dog = WORLD["monsters"]["wild_dog"]
    m = B.shaped(dog, 0.5)
    self.assertEqual((m["hp"], m["atk"], m["def"]), (10, 4, 1))  # 20・7・2 の半分（四捨五入。3.5 → 4）
    self.assertEqual(B.shaped(dog, 0.625)["hp"], 13)  # 12.5 → 13（偶数に丸めない）
    self.assertEqual((m["spd"], m["dex"], m.get("trait")), (dog["spd"], dog["dex"], dog.get("trait")))
    self.assertEqual(B.shaped(dog, 0.01)["hp"], 1)  # HP と攻撃は 1 より下げない
    self.assertEqual(B.shaped(dog, 0.01)["def"], 0)

  def test_road_monsters_meet_the_hp_loss_goal(self):
    # 道の物の怪は、同じ Lv のもののふ（強運・装備なし・丸薬なし）が勝つと HP が 20% 減る強さ（docs/BALANCE.md の 7）
    ids = {m for i in WORLD["road_info"].values() for m, _ in i["monsters"]}
    for mid in ids:
      m = WORLD["monsters"][mid]
      fit, capped = B.fit_hp_loss(WORLD, m, m["level"], 0.2)
      self.assertFalse(capped, mid)
      self.assertEqual((fit["hp"], fit["atk"], fit["def"]), (m["hp"], m["atk"], m["def"]), mid)

  def test_fit_reports_when_the_goal_is_out_of_range(self):
    dog = WORLD["monsters"]["wild_dog"]
    fit, capped = B.fit_hp_loss(WORLD, dog, 1, 0.001)  # いちばん弱くしても、HP が 0.1% より多く減る
    self.assertTrue(capped)
    self.assertLessEqual(fit["hp"], B.shaped(dog, B.FIT_LOW * 1.01)["hp"])

  def test_is_capped(self):
    self.assertTrue(B.is_capped(B.HIGH))
    self.assertTrue(B.is_capped(B.LOW))
    self.assertFalse(B.is_capped(1.0))

  def test_reach_level(self):
    self.assertEqual(B.reach_level({14: 0.0, 15: 0.55, 16: 1.0}), 15)
    self.assertIsNone(B.reach_level({14: 0.0, 15: 0.4}))


if __name__ == "__main__":
  unittest.main()
