"""強さの調整用ツール（tools/stat_value・element_value・battle_sim）のテスト。
ゲームのデータや戦いの処理を変えたとき、ツールが壊れていないことを確かめる（戦う回数は少なくして、数秒で終わらせる）。"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
import battle_sim as B  # noqa: E402
import element_value as E  # noqa: E402
import game  # noqa: E402
import stat_value as S  # noqa: E402
import world as W  # noqa: E402

WORLD = W.load_world()
DOG = WORLD["monsters"]["wild_dog"]
BOSS = WORLD["monsters"]["den_lord"]


def run(main, *argv):
  """main を、引数 argv・標準出力を捕まえて動かす。(終了コード, 出力) を返す。"""
  out = io.StringIO()
  with mock.patch.object(sys, "argv", ["tool", *argv]), contextlib.redirect_stdout(out):
    code = main()
  return code, out.getvalue()


def exits(main, *argv):
  """main が SystemExit で止まるときの、メッセージ。"""
  with mock.patch.object(sys, "argv", ["tool", *argv]), contextlib.redirect_stdout(io.StringIO()):
    with unittest.TestCase().assertRaises(SystemExit) as cm:
      main()
  return str(cm.exception)


class StatValueTest(unittest.TestCase):
  def hero(self, level=10):
    b = game.base_stats(WORLD, None, level)
    return {"hp": b["max_hp"], "atk": b["atk"], "def": b["def"], "spd": b["spd"], "dex": b["dex"]}

  def test_every_stat_makes_the_hero_stronger(self):
    for level, monster in ((1, DOG), (15, BOSS)):
      h = self.hero(level)
      base = S.strength(WORLD, h, monster)
      for stat in ("hp", "atk", "dex"):
        self.assertGreater(S.strength(WORLD, h | {stat: h[stat] + 5}, monster), base, (level, stat))
      self.assertGreaterEqual(
        S.strength(WORLD, h | {"def": h["def"] + 5}, monster), base, level
      )  # 守りは増えない相手もある

  def test_gains_are_positive_and_attack_is_the_yardstick(self):
    g = S.gains(WORLD, DOG, 1)
    self.assertEqual(set(g), set(S.STATS))
    self.assertTrue(all(v > 0 for v in g.values()), g)
    self.assertGreater(g["atk"], g["hp"])  # 攻撃 +1 のほうが、HP +1 より効く（HP は桁が違う）
    self.assertIn("spd", S.gains(WORLD, DOG, 1, (*S.STATS, "spd")))

  def test_average_is_relative_to_attack(self):
    avg = S.average(WORLD)
    self.assertEqual(avg["atk"], 1.0)  # 攻撃 +1 を 1 とした比
    self.assertEqual(set(avg), {*S.STATS, "spd"})
    self.assertTrue(all(v > 0 for v in avg.values()), avg)
    self.assertLess(avg["hp"], avg["atk"])  # HP +1 は攻撃 +1 より小さい

  def test_main_prints_every_case_and_the_weights(self):
    code, out = run(S.main)
    self.assertEqual(code, 0)
    for mid, level in S.CASES:
      self.assertIn(f"Lv{level:2d} {WORLD['monsters'][mid]['name']}", out)
    self.assertIn("今の重み", out)
    self.assertIn("平均", out)

  def test_main_says_so_when_attack_does_not_help(self):
    # 守りが固すぎて、攻撃 +1 でも与えるダメージが変わらない相手は、比べられないと出す
    world = {**WORLD, "monsters": {**WORLD["monsters"], "wild_dog": DOG | {"def": 9999}}}
    with mock.patch.object(S.W, "load_world", return_value=world):
      _, out = run(S.main)
    self.assertIn("攻撃 +1 が効かない", out)


class ElementValueTest(unittest.TestCase):
  def test_encounter_shares_add_up_to_the_number_of_roads(self):
    enc = E.encounters(WORLD)
    self.assertAlmostEqual(sum(enc.values()), len(WORLD["road_info"]))  # 道ごとに、出会う割合の合計は 1
    self.assertTrue(all(WORLD["monsters"][m]["terrain"] for m in enc))
    self.assertFalse(any(WORLD["monsters"][m].get("boss") or WORLD["monsters"][m].get("elite") for m in enc))

  def test_a_weakness_is_worth_some_attack_points(self):
    boss = WORLD["monsters"]["yeti"]
    n = E.atk_equivalent(WORLD, boss, 10)
    self.assertTrue(1 <= n <= 60, n)
    self.assertEqual(E.atk_equivalent(WORLD, boss, 10, limit=2), 2)  # 探す上限で打ち切る

  def test_road_points_per_element(self):
    for eid in WORLD["elements"]:
      points, share, weak = E.road_points(WORLD, eid)
      self.assertGreaterEqual(points, 0, eid)
      self.assertTrue(0 <= share <= 1, eid)
      weak_to = WORLD["elements"][eid]["strong"]
      self.assertTrue(all(WORLD["monsters"][m]["element"] == weak_to for m in weak), eid)  # 弱点を突ける相手だけ
      self.assertEqual(bool(weak), share > 0, eid)

  def test_main_prints_both_tables(self):
    code, out = run(E.main)
    self.assertEqual(code, 0)
    for e in WORLD["elements"].values():
      self.assertIn(f"{e['name']}属性", out)
    for mid, level in E.CASES:
      self.assertIn(f"Lv{level:2d} {WORLD['monsters'][mid]['name']}", out)
    self.assertIn("今の属性の点数", out)


class BattleSimToolTest(unittest.TestCase):
  def test_fight_is_repeatable(self):
    a = B.fight(WORLD, BOSS, "lucky", 15, [], 3, 20)
    self.assertEqual(a, B.fight(WORLD, BOSS, "lucky", 15, [], 3, 20))  # 種が決まっているので、同じ結果
    rate, lost = a
    self.assertTrue(0 <= rate <= 1 and 0 <= lost <= 1, a)

  def test_never_winning_has_no_hp_loss(self):
    self.assertEqual(B.fight(WORLD, BOSS, "lucky", 1, [], 0, 10), (0.0, None))

  def test_reach_level(self):
    self.assertEqual(B.reach_level({10: 0.1, 11: 0.4, 12: 0.5, 13: 0.9}), 12)
    self.assertIsNone(B.reach_level({10: 0.1, 11: 0.4}))
    self.assertEqual(B.reach_level({10: 0.4, 11: 0.9}, goal=0.3), 10)
    self.assertEqual(B.reach_level({20: 0.9, 10: 0.6}), 10)  # 低いレベルから探す

  def test_scaled_and_shaped_monsters(self):
    self.assertEqual((B.scaled(DOG, 2)["hp"], B.scaled(DOG, 2)["atk"]), (DOG["hp"] * 2, DOG["atk"] * 2))
    self.assertEqual(B.scaled(DOG, 0.0001)["hp"], 1)  # 0 にはならない
    big = B.shaped(DOG, 2)
    self.assertEqual(big["spd"], DOG["spd"])  # 速さ・性質はそのまま
    self.assertEqual((big["hp"], big["atk"], big["def"]), (B.ROAD_SHAPE["hp"] * 2, B.ROAD_SHAPE["atk"] * 2, 4))
    self.assertEqual(B.shaped(DOG, 0.0001)["def"], 0)  # 防御は 0 まで下がる。HP・攻撃は 1 まで

  def test_even_strength_is_between_the_search_limits(self):
    f = B.even_strength(WORLD, BOSS, "lucky", 15, [], 3, 20)
    self.assertTrue(B.LOW <= f <= B.HIGH, f)
    self.assertTrue(B.is_capped(B.LOW) and B.is_capped(B.HIGH) and not B.is_capped(1.0))

  def test_fit_hp_loss_finds_a_monster_near_the_goal(self):
    with mock.patch.object(B, "FIT_SEARCH_N", 40), mock.patch.object(B, "FIT_PICK_N", 80):
      fit, capped = B.fit_hp_loss(WORLD, DOG, 1, 0.2)
    self.assertIsInstance(capped, bool)
    self.assertEqual(fit["spd"], DOG["spd"])
    rate, lost = B.fight(WORLD, fit, "lucky", 1, [], 0, 80)
    self.assertGreaterEqual(rate, 0.9)  # 勝てる強さ
    self.assertTrue(lost is not None and lost < 0.6, lost)

  def test_default_mode_prints_a_win_rate_per_trait(self):
    code, out = run(B.main, "-n", "10")
    self.assertEqual(code, 0)
    self.assertIn("巣穴の主 と戦う", out)
    for t in WORLD["season"]["hero_traits"].values():
      self.assertIn(t["name"], out)
    self.assertIn("勝率", out)

  def test_one_trait_gear_and_potions(self):
    _, out = run(
      B.main, "-n", "10", "--trait", "swift", "--gear", "iron_sword,iron_helm", "--potions", "1", "--level", "12"
    )
    self.assertIn("装備: 鉄の剣・", out)
    self.assertIn("回復薬 1 個", out)
    self.assertIn("Lv12", out)
    self.assertEqual(out.count("Lv12"), 1)  # 特性は 1 つだけ

  def test_reach_mode_prints_the_level_table(self):
    _, out = run(B.main, "-n", "10", "--reach", "--trait", "lucky")
    self.assertIn("50%に届くLv", out)
    self.assertIn("強運", out)  # 指定した特性の行
    self.assertNotIn("怪力", out)

  def test_strength_mode(self):
    _, out = run(B.main, "-n", "10", "--strength", "--trait", "strong")
    self.assertIn("怪力", out)
    self.assertIn("五分で戦える強さ", out)
    self.assertIn("ふつうの", out)

  def test_hp_loss_mode(self):
    _, out = run(B.main, "-n", "10", "--hp-loss", "--monster", "wild_dog", "--level", "5", "--potions", "0")
    self.assertIn("勝ったときに減る HP", out)
    _, out = run(B.main, "-n", "10", "--hp-loss", "--level", "1", "--potions", "0", "--trait", "lucky")  # 勝てない
    self.assertIn("—（勝てない）", out)

  def test_fit_hp_loss_mode(self):
    with mock.patch.object(B, "FIT_SEARCH_N", 40), mock.patch.object(B, "FIT_PICK_N", 80):
      code, out = run(B.main, "-n", "20", "--fit-hp-loss", "0.2", "--monster", "wild_dog")
    self.assertEqual(code, 0)
    self.assertIn("勝ったときに減る HP 20%", out)
    self.assertIn("野犬（wild_dog）", out)
    self.assertIn("EXP", out)

  def test_bad_arguments_stop_with_a_message(self):
    self.assertIn("魔物 'nobody' がいません", exits(B.main, "--monster", "nobody"))
    self.assertIn("装備 'nothing' がありません", exits(B.main, "--gear", "nothing"))
    self.assertIn("特性 'nothing' がありません", exits(B.main, "--trait", "nothing"))
    self.assertIn("使えません", exits(B.main, "--fit-hp-loss", "0.2", "--trait", "lucky"))
    self.assertIn("使えません", exits(B.main, "--fit-hp-loss", "0.2", "--gear", "iron_sword"))
    self.assertIn("使えません", exits(B.main, "--fit-hp-loss", "0.2", "--potions", "1"))
    self.assertIn("0 より大きく 1 より小さい", exits(B.main, "--fit-hp-loss", "1.5"))
    self.assertIn("強敵かボスです", exits(B.main, "--fit-hp-loss", "0.2", "--monster", "den_lord"))
    self.assertIn("がいません", exits(B.main, "--fit-hp-loss", "0.2", "--monster", "nobody"))


if __name__ == "__main__":
  unittest.main()
