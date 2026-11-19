import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import rpg  # noqa: E402

WORLD = rpg.load_world()
DAY = date(2026, 10, 6)


def play(days, steps=2, seed=0):
    state = rpg.new_state(WORLD)
    for i in range(days):
        rpg.advance(WORLD, state, DAY + timedelta(days=i + seed * 1000), steps)
    return state


class WorldTest(unittest.TestCase):
    def test_layout(self):
        areas = WORLD["areas"]
        self.assertEqual(areas[0]["start"], 0)
        for a, b in zip(areas, areas[1:]):
            self.assertEqual(a["end"] + 1, b["start"])
        self.assertEqual(WORLD["total"], sum(a["length"] for a in areas))

    def test_roll_steps_follows_weights(self):
        rng = random.Random(0)
        rolls = [rpg.roll_steps(WORLD, rng) for _ in range(2000)]
        self.assertEqual(set(rolls), {0, 1, 2, 3})
        self.assertGreater(rolls.count(1), rolls.count(3))


class AdvanceTest(unittest.TestCase):
    def test_same_day_is_processed_once_and_deterministic(self):
        a, b = rpg.new_state(WORLD), rpg.new_state(WORLD)
        self.assertTrue(rpg.advance(WORLD, a, DAY, 2))
        self.assertFalse(rpg.advance(WORLD, a, DAY, 2))  # 同じ日は進めない
        rpg.advance(WORLD, b, DAY, 2)
        self.assertEqual(a, b)  # 同じ日・同じ周回なら同じ結果
        self.assertEqual(a["day"], 1)

    def test_hero_id_changes_the_dice(self):
        results = set()
        for i in range(8):
            state = rpg.new_state(WORLD, f"h{i}")
            rpg.advance(WORLD, state, DAY)
            results.add(state["last"]["steps"])
        self.assertGreater(len(results), 1)  # 勇者ごとに歩数が違う日がある

    def test_rest_day_heals_and_does_not_move(self):
        state = rpg.new_state(WORLD)
        state["hero"]["hp"] = 1
        rpg.advance(WORLD, state, DAY, 0)
        self.assertEqual(state["pos"], 0)
        self.assertGreater(state["hero"]["hp"], 1)
        self.assertIn(state["last"]["events"][-1], WORLD["rest"])

    def test_first_day_shops_in_the_first_town(self):
        state = rpg.new_state(WORLD)
        rpg.advance(WORLD, state, DAY, 0)
        self.assertEqual(state["hero"]["weapon"]["name"], "銅の剣")
        self.assertIn("はじまりの村", state["last"]["events"][0])

    def test_past_date_is_not_processed(self):
        state = play(3)
        self.assertFalse(rpg.advance(WORLD, state, DAY, 2))
        self.assertEqual(state["day"], 3)

    def test_moves_by_steps(self):
        state = rpg.new_state(WORLD)
        rpg.advance(WORLD, state, DAY, 3)
        self.assertEqual(state["pos"], 3)

    def test_boss_blocks_until_defeated(self):
        state = rpg.new_state(WORLD)
        area = WORLD["areas"][0]
        state["pos"] = area["end"] - 1
        state["hero"]["hp"] = 1
        state["hero"]["potions"] = 0
        rpg.advance(WORLD, state, DAY, 3)  # 弱いまま挑んで負ける
        self.assertEqual(state["pos"], area["start"])  # 町に戻される
        self.assertEqual(state["deaths"], 1)
        self.assertNotIn(area["name"], state["boss_cleared"])

    def test_fall_halves_gold(self):
        state = rpg.new_state(WORLD)
        state["pos"] = WORLD["areas"][1]["start"] + 5
        state["hero"].update(gold=101, potions=2, hp=0)
        day = rpg.Day(WORLD, state, random.Random(0))
        day.fall()
        self.assertEqual((state["pos"], state["hero"]["gold"]), (WORLD["areas"][1]["start"], 50))
        self.assertEqual(state["hero"]["hp"], state["hero"]["max_hp"])

    def test_fall_shops_in_the_town(self):
        state = rpg.new_state(WORLD)
        state["pos"] = WORLD["areas"][1]["start"] + 5
        state["hero"].update(gold=400, potions=2, hp=0)
        rpg.Day(WORLD, state, random.Random(0)).fall()
        self.assertEqual(state["hero"]["weapon"]["name"], "鉄の剣")  # 半分の 200G で買える

    def test_town_heals_and_buys_better_gear(self):
        state = rpg.new_state(WORLD)
        state["hero"].update(hp=1, gold=1000)
        day = rpg.Day(WORLD, state, random.Random(0))
        day.town(WORLD["areas"][1])
        hero = state["hero"]
        self.assertEqual(hero["hp"], hero["max_hp"])
        self.assertEqual(hero["weapon"]["name"], "鉄の剣")
        self.assertEqual(hero["armor"]["name"], "鎖かたびら")
        day.town(WORLD["areas"][0])  # 弱い装備には買い替えない
        self.assertEqual(hero["weapon"]["name"], "鉄の剣")

    def test_clear_records_and_restarts(self):
        state = rpg.new_state(WORLD)
        last = WORLD["areas"][-1]
        state["pos"] = last["end"]
        state["boss_cleared"] = [a["name"] for a in WORLD["areas"][:-1]]
        state["hero"].update(level=30, base_atk=200, base_def=100, max_hp=999, hp=999)
        rpg.advance(WORLD, state, DAY, 1)
        self.assertEqual(state["lap"], 2)
        self.assertEqual((state["pos"], state["lap_day"], state["boss_cleared"]), (0, 0, []))
        self.assertEqual(state["hero"]["level"], 1)  # レベルと装備はリセット
        self.assertEqual(state["records"][0]["lap"], 1)
        self.assertIn("クリア", state["last"]["events"][-1])
        rpg.advance(WORLD, state, DAY + timedelta(days=1), 1)  # 次の日から 2 周目が進む
        self.assertEqual(state["lap_day"], 1)

    def test_journal_is_capped(self):
        state = play(rpg.JOURNAL_DAYS + 5, steps=0)
        self.assertEqual(len(state["journal"]), rpg.JOURNAL_DAYS)
        self.assertEqual(state["journal"][0]["date"], (DAY + timedelta(days=rpg.JOURNAL_DAYS + 4)).isoformat())


class BalanceTest(unittest.TestCase):
    def test_average_player_clears_in_about_three_months(self):
        days, deaths = [], []
        for seed in range(40):
            state, d = rpg.new_state(WORLD, f"h{seed}"), DAY + timedelta(days=seed * 1000)
            while state["lap"] == 1 and state["day"] < 1000:
                rpg.advance(WORLD, state, d, None)
                d += timedelta(days=1)
            self.assertEqual(state["lap"], 2, "1000 日でクリアできない")
            days.append(state["records"][0]["days"])
            deaths.append(state["records"][0]["deaths"])
        days.sort()
        self.assertTrue(70 <= days[len(days) // 2] <= 120, days)
        self.assertLess(max(deaths), 15)


class DisplayTest(unittest.TestCase):
    def test_card_fits_and_has_five_lines(self):
        for state in (rpg.new_state(WORLD), play(1), play(40), play(120, steps=3)):
            lines = rpg.build_text(WORLD, state).splitlines()
            for line in lines[:5]:
                self.assertLessEqual(common.width(line), common.LINE_MAX, line)
            self.assertEqual(lines[5], "")  # カードの 5 行のあとに詳細が続く
            self.assertTrue(lines[0].startswith("⚔️ Day"))

    def test_highlights_prefer_important_events(self):
        events = ["フクロウがこちらを見ている", "トレントに敗れた", "力尽きた… 森の隠れ里に運ばれた", "森の隠れ里で休んだ"]
        self.assertEqual(rpg.highlights(events), events[1:3])
        self.assertEqual(rpg.highlights(["a", "b", "c"]), ["a", "b"])

    def test_fit(self):
        self.assertEqual(rpg.fit("あいう", 6), "あいう")
        self.assertEqual(rpg.fit("あいうえお", 6), "あい…")

    def test_teaser_before_boss(self):
        state = rpg.new_state(WORLD)
        state["pos"] = WORLD["areas"][0]["end"] - 1
        state["lap_day"] = 1
        self.assertIn("キングスライム", rpg.teaser(WORLD, state))


class StateTest(unittest.TestCase):
    def test_save_and_load(self):
        state = play(3)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h1.json"
            rpg.save_state(state, path)
            self.assertEqual(rpg.load_state(WORLD, path), state)
            path.write_text("<<<<<<< HEAD")
            with self.assertRaises(SystemExit):
                rpg.load_state(WORLD, path)
        loaded = rpg.load_state(WORLD, Path("/nonexistent/h9.json"), "h9", "アリス")
        self.assertEqual(loaded, rpg.new_state(WORLD, "h9", "アリス"))

    def test_load_fills_missing_keys_and_clamps_to_the_world(self):
        state = play(3)
        del state["kills"], state["hero"]["potions"]
        state.update(pos=WORLD["total"] + 5, boss_cleared=["草原", "消えたエリア"])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h1.json"
            rpg.save_state(state, path)
            loaded = rpg.load_state(WORLD, path)
        self.assertEqual((loaded["kills"], loaded["hero"]["potions"]), (0, WORLD["hero"]["potions"]))
        self.assertEqual((loaded["pos"], loaded["boss_cleared"]), (WORLD["total"] - 1, ["草原"]))

    def test_pending_days(self):
        state = rpg.new_state(WORLD)
        self.assertEqual(rpg.pending_days(state, DAY), [DAY])
        state["last_date"] = (DAY - timedelta(days=3)).isoformat()
        self.assertEqual(rpg.pending_days(state, DAY), [DAY - timedelta(days=2), DAY - timedelta(days=1), DAY])
        state["last_date"] = (DAY - timedelta(days=30)).isoformat()
        self.assertEqual(len(rpg.pending_days(state, DAY)), rpg.CATCH_UP_DAYS)
        state["last_date"] = DAY.isoformat()
        self.assertEqual(rpg.pending_days(state, DAY), [])


class HeroesTest(unittest.TestCase):
    """勇者管理シート（heroes.json）と状態ファイルを一時ディレクトリに向けて、main を動かす。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target, value in (("HEROES_PATH", self.dir / "heroes.json"), ("HERO_DIR", self.dir / "heroes")):
            patcher = mock.patch.object(rpg, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, argv, env=None):
        with mock.patch.object(sys, "argv", ["rpg.py", *argv]), mock.patch.dict("os.environ", env or {}, clear=True), \
             mock.patch("sys.stdout"):
            return rpg.main()

    def new_game(self, name, gist_id="g1"):
        gist = {"id": gist_id, "html_url": f"https://g/{gist_id}"}
        with mock.patch.object(common, "create_gist", return_value=gist) as create:
            self.run_main(["--new-game", name], {"GIST_PAT": "p"})
        return create

    def test_clean_name(self):
        self.assertEqual(rpg.clean_name("  アリス\n"), "アリス")
        self.assertLessEqual(common.width(rpg.clean_name("あ" * 30)), rpg.NAME_WIDTH_MAX)
        with self.assertRaises(SystemExit):
            rpg.clean_name(" \t ")
        with self.assertRaises(SystemExit):
            rpg.clean_name("h12")  # ID と紛らわしい

    def test_md_escape(self):
        self.assertEqual(rpg.md_escape("[x](u)<b>"), r"\[x\]\(u\)\<b\>")

    def test_new_game_registers_hero_and_creates_gist(self):
        create = self.new_game("アリス")
        token, filename, content, description = create.call_args.args
        self.assertEqual((token, filename, description), ("p", rpg.FILENAME, "アリス"))  # 説明は勇者の名前
        self.assertTrue(content.startswith("⚔️ Day 0  アリス Lv.1"))
        (hero,) = rpg.load_heroes()
        self.assertEqual((hero["id"], hero["name"], hero["gist_id"], hero["active"]), ("h1", "アリス", "g1", True))
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h1"))["name"], "アリス")

    def test_new_game_needs_pat_and_valid_name(self):
        with mock.patch.object(common, "create_gist") as create:
            with self.assertRaises(SystemExit):
                self.run_main(["--new-game", "アリス"])  # GIST_PAT なし
            with self.assertRaises(SystemExit):
                self.run_main(["--new-game", " "], {"GIST_PAT": "p"})
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                self.run_main(["--new-game", "アリス", "--dry-run"], {"GIST_PAT": "p"})
        create.assert_not_called()
        self.assertEqual(rpg.load_heroes(), [])

    def test_new_game_rejects_duplicates_and_more_than_max(self):
        self.new_game("アリス", "g1")
        with mock.patch.object(common, "create_gist") as create, self.assertRaises(SystemExit):
            self.run_main(["--new-game", "アリス"], {"GIST_PAT": "p"})
        create.assert_not_called()
        for i in range(2, rpg.MAX_HEROES + 1):
            self.new_game(f"勇者{i}", f"g{i}")
        with mock.patch.object(common, "create_gist") as create, self.assertRaises(SystemExit):
            self.run_main(["--new-game", "あふれた勇者"], {"GIST_PAT": "p"})
        create.assert_not_called()
        self.assertEqual(len(rpg.load_heroes()), rpg.MAX_HEROES)

    def test_retire_frees_a_slot_and_ids_are_not_reused(self):
        for i in range(1, rpg.MAX_HEROES + 1):
            self.new_game(f"勇者{i}", f"g{i}")
        self.run_main(["--retire", "勇者2"])
        self.assertEqual([h["active"] for h in rpg.load_heroes()], [True, False, True, True, True, True])
        self.new_game("新人", "g7")
        self.assertEqual(rpg.load_heroes()[-1]["id"], "h7")
        with self.assertRaises(SystemExit):
            self.run_main(["--retire", "勇者2"])  # 引退済み

    def test_daily_advances_every_active_hero_and_updates_each_gist(self):
        self.new_game("アリス", "g1")
        self.new_game("ボブ", "g2")
        self.new_game("引退者", "g3")
        self.run_main(["--retire", "h3"])
        with mock.patch.object(common, "update_gist") as gist:
            self.assertEqual(self.run_main(["--date", "2026-10-06"], {"GIST_PAT": "p"}), 0)
        self.assertEqual([c.args[:3] for c in gist.call_args_list],
                         [("g1", "p", rpg.FILENAME), ("g2", "p", rpg.FILENAME)])
        self.assertEqual([c.kwargs["description"] for c in gist.call_args_list], ["アリス", "ボブ"])
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h1"))["day"], 1)
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h3"))["day"], 0)  # 引退者は進まない

    def test_daily_is_idempotent_and_catches_up(self):
        self.new_game("アリス")
        with mock.patch.object(common, "update_gist"):
            self.run_main(["--date", "2026-10-06"], {"GIST_PAT": "p"})
            self.run_main(["--date", "2026-10-06"], {"GIST_PAT": "p"})  # 同じ日は進まない
            self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h1"))["day"], 1)
            self.run_main(["--date", "2026-10-09"], {"GIST_PAT": "p"})  # 3 日空いた
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h1"))["day"], 4)

    def test_one_failing_hero_does_not_stop_the_others(self):
        self.new_game("アリス", "g1")
        self.new_game("ボブ", "g2")

        def fail_first(gist_id, *_, **__):
            if gist_id == "g1":
                raise SystemExit("Gist API エラー 404")

        with mock.patch.object(common, "update_gist", side_effect=fail_first) as gist, \
             mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.run_main(["--date", "2026-10-06"], {"GIST_PAT": "p"}), 1)
        self.assertEqual(gist.call_count, 2)
        warn.assert_called_once()
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h2"))["day"], 1)

    def test_retire_matches_id_first(self):
        self.new_game("アリス", "g1")
        self.new_game("ボブ", "g2")
        self.run_main(["--retire", "h2"])
        self.assertEqual([h["active"] for h in rpg.load_heroes()], [True, False])

    def test_missing_state_file_is_an_error_not_a_reset(self):
        self.new_game("アリス")
        rpg.hero_state_path("h1").unlink()
        with mock.patch.object(common, "update_gist") as gist, mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.run_main(["--date", "2026-10-06"], {"GIST_PAT": "p"}), 1)
        gist.assert_not_called()
        warn.assert_called_once()
        self.assertFalse(rpg.hero_state_path("h1").exists())

    def test_no_heroes_is_a_noop(self):
        with mock.patch.object(common, "update_gist") as gist:
            self.assertEqual(self.run_main([], {"GIST_PAT": "p"}), 0)
        gist.assert_not_called()

    def test_dry_run_writes_nothing(self):
        self.new_game("アリス")
        with mock.patch.object(common, "update_gist") as gist:
            self.run_main(["--dry-run", "--steps", "1"])
        gist.assert_not_called()
        self.assertEqual(rpg.load_state(WORLD, rpg.hero_state_path("h1"))["day"], 0)


if __name__ == "__main__":
    unittest.main()
