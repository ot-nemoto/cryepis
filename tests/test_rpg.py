import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import game  # noqa: E402
import rpg  # noqa: E402

WORLD = rpg.load_world()
DAY = date.fromisoformat(WORLD["season"]["start"])


def play(days, hero_id="h1"):
    state = game.new_state(WORLD, hero_id)
    for i in range(days):
        game.advance(WORLD, state, DAY + timedelta(days=i))
    return state


def load(hero_id):
    return game.load_state(WORLD, rpg.hero_state_path(hero_id))


class DisplayTest(unittest.TestCase):
    def test_card_fits_and_has_five_lines(self):
        states = [game.new_state(WORLD, "h1", "あ" * 8)] + [play(n, f"h{n}") for n in (1, 10, 40, 90)]
        for state in states:
            lines = rpg.build_text(WORLD, state).splitlines()
            for line in lines[:5]:
                self.assertLessEqual(common.width(line), common.LINE_MAX, line)
            self.assertEqual(lines[5], "")  # カードの 5 行のあとに詳細が続く
            self.assertTrue(lines[0].startswith("⚔️ "))
            self.assertTrue(lines[1].startswith("📍 "))
            self.assertTrue(lines[3].startswith("装備: "))
            self.assertTrue(lines[4].startswith("今日: "))

    def test_card_before_the_journey(self):
        lines = rpg.build_text(WORLD, game.new_state(WORLD, "h1", "アリス")).splitlines()
        self.assertEqual(lines[0], "⚔️ アリス Lv.1  旅立ち前 Day 0")
        self.assertIn("旅立とうとしている", lines[4])

    def test_detail_lists_items_and_past_seasons(self):
        state = play(3)
        state["inventory"]["blue_flame_sword"] = 1
        state["seasons"].append({"season": 0, "days": 90, "level": 20, "quests_done": 30, "quests_failed": 2,
                                 "kills": 140, "deaths": 1, "rares": ["blue_flame_sword"], "ended_on": "2026-11-30"})
        text = rpg.build_text(WORLD, state)
        self.assertIn("★蒼炎の剣", text)
        self.assertIn("シーズン0: Lv.20", text)

    def test_board_card_fits_and_ranks(self):
        names = ["アリス", "ボブ", "ゴールドヒルデガルト", "あ" * 8]
        entries = [({"id": f"h{i}", "name": n, "gist_url": f"https://g/{i}"}, play(30, f"h{i}"))
                   for i, n in enumerate(names)]
        today = DAY + timedelta(days=29)
        lines = rpg.build_board(WORLD, entries, today).splitlines()
        for line in lines[:5]:
            self.assertLessEqual(common.width(line), common.LINE_MAX, line)
        self.assertEqual(lines[5], "")
        first, last = game.season_range(WORLD, 1)
        self.assertEqual(lines[0], f"🏆 シーズン1  Day 30/{(last - first).days + 1}  勇者4人")
        self.assertTrue(lines[1].startswith("Lv   🥇"))
        self.assertTrue(lines[2].startswith("討伐 🥇"))
        self.assertTrue(lines[4].startswith("📰 "))
        text = "\n".join(lines)
        self.assertIn("ゴールドヒルデガルト", text)  # 本文は名前を切り詰めない
        self.assertIn("詳細: heroes/h0.md", text)  # 勇者ごとの欄と、詳細のページへのリンク
        self.assertIn("■ アリス  Lv.", text)

    def test_board_before_the_season_and_for_heroes_not_started(self):
        entries = [({"id": "h1", "name": "アリス", "gist_url": "u"}, game.new_state(WORLD, "h1", "アリス"))]
        lines = rpg.build_board(WORLD, entries, DAY - timedelta(days=3)).splitlines()
        self.assertIn("開幕前", lines[0])
        self.assertIn("今日も勇者たちは冒険中", lines[4])

    def test_same_level_gets_the_same_medal(self):
        entries = []
        for i, exp in enumerate((5, 30, 10)):
            state = play(1, f"h{i}")
            state["hero"].update(level=13, exp=exp)
            entries.append(({"id": f"h{i}", "name": f"勇者{i}", "gist_url": "u"}, state))
        line = rpg.build_board(WORLD, entries, DAY).splitlines()[1]
        self.assertEqual(line.count("🥇"), 3, line)

    def test_ranked_shares_rank_on_ties(self):
        rows = [({"name": n}, {"v": v}) for n, v in (("a", 5), ("b", 7), ("c", 5), ("d", 1))]
        self.assertEqual([(r, h["name"]) for r, h, _ in rpg.ranked(rows, lambda s: s["v"])],
                         [(1, "b"), (2, "a"), (2, "c"), (4, "d")])

    def test_board_shows_past_season_champions(self):
        state = play(3)
        state["seasons"] = [{"season": 1, "days": 90, "level": 22, "quests_done": 30, "quests_failed": 1,
                             "kills": 150, "deaths": 0, "rares": [], "ended_on": "2027-02-28"}]
        text = rpg.build_board(WORLD, [({"id": "h1", "name": "アリス", "gist_url": "u"}, state)], DAY)
        self.assertIn("シーズン1: 最高レベル アリス Lv.22  討伐数 アリス 150体", text)

    def test_map_page_has_image_and_hero_positions(self):
        state = play(5)
        text = rpg.build_map(WORLD, [({"id": "h1", "name": "アリス", "gist_url": "u"}, state)])
        self.assertIn(f"![世界の MAP]({rpg.MAP_IMAGE})", text)
        self.assertNotIn("mermaid", text)
        self.assertIn(f"- 🧙 アリス Lv.{state['hero']['level']}  📍 {WORLD['places'][state['place']]['name']}", text)

    def test_map_page_without_heroes_or_layout(self):
        text = rpg.build_map({**WORLD, "layout": None}, [])
        self.assertNotIn("![", text)
        self.assertIn("- まだいない", text)

    def test_highlights_prefer_important_events_and_merge_level_ups(self):
        events = ["スライムを倒した", "レベルが上がった！ Lv.2", "レベルが上がった！ Lv.3", "依頼達成！x", "景色"]
        self.assertEqual(rpg.highlights(events), ["レベルが上がった！ Lv.3", "依頼達成！x"])
        self.assertEqual(rpg.highlights(["a", "宝箱から★「蒼炎の剣」を手に入れた！", "力尽きた…"]),
                         ["宝箱から★「蒼炎の剣」を手に入れた！", "力尽きた…"])

    def test_fit(self):
        self.assertEqual(rpg.fit("abc", 5), "abc")
        self.assertEqual(common.width(rpg.fit("あ" * 30)), common.LINE_MAX)

    def test_pending_days(self):
        state = game.new_state(WORLD)
        self.assertEqual(rpg.pending_days(state, DAY), [DAY])
        state["last_date"] = (DAY - timedelta(days=3)).isoformat()
        self.assertEqual(rpg.pending_days(state, DAY), [DAY - timedelta(days=2), DAY - timedelta(days=1), DAY])
        state["last_date"] = (DAY - timedelta(days=30)).isoformat()
        self.assertEqual(len(rpg.pending_days(state, DAY)), rpg.CATCH_UP_DAYS)
        state["last_date"] = DAY.isoformat()
        self.assertEqual(rpg.pending_days(state, DAY), [])


class HeroesTest(unittest.TestCase):
    """勇者管理シート（heroes.json）・状態・詳細のページを一時ディレクトリに向けて、main を動かす。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for target, value in (("HEROES_PATH", self.dir / "heroes.json"), ("HERO_DIR", self.dir / "heroes"),
                              ("BOARD_PATH", self.dir / "board.json"), ("PAGE_DIR", self.dir / "pages")):
            patcher = mock.patch.object(rpg, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Gist を作るのはランキングだけ（毎日の実行の最初）
        patcher = mock.patch.object(common, "create_gist", return_value={"id": "board", "html_url": "https://g/board"})
        self.create_board = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(common, "update_gist")
        self.update_gist = patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv, env=None):
        with mock.patch.object(sys, "argv", ["rpg.py", *argv]), mock.patch.dict("os.environ", env or {}, clear=True), \
             mock.patch("sys.stdout"):
            return rpg.main()

    def new_game(self, name):
        return self.run_main(["--new-game", name])

    def daily(self, day="2026-12-01"):
        return self.run_main(["--date", day], {"GIST_PAT": "p"})

    def test_clean_name(self):
        self.assertEqual(rpg.clean_name("  アリス\n"), "アリス")
        self.assertLessEqual(common.width(rpg.clean_name("あ" * 30)), rpg.NAME_WIDTH_MAX)
        with self.assertRaises(SystemExit):
            rpg.clean_name(" \t ")
        with self.assertRaises(SystemExit):
            rpg.clean_name("h12")  # ID と紛らわしい

    def test_md_escape(self):
        self.assertEqual(rpg.md_escape("[x](u)<b>"), r"\[x\]\(u\)\<b\>")

    def test_new_game_registers_hero_without_a_gist(self):
        self.assertEqual(self.new_game("アリス"), 0)  # GIST_PAT は要らない
        self.create_board.assert_not_called()
        (hero,) = rpg.load_heroes()
        self.assertEqual(hero, {"id": "h1", "name": "アリス", "created": hero["created"], "active": True})
        self.assertEqual(load("h1")["name"], "アリス")
        page = rpg.page_path("h1").read_text(encoding="utf-8")
        self.assertTrue(page.startswith("# アリス（h1）\n\n```text\n⚔️ アリス Lv.1"))

    def test_new_game_rejects_bad_names_and_options(self):
        with self.assertRaises(SystemExit):
            self.new_game(" ")
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            self.run_main(["--new-game", "アリス", "--dry-run"])
        self.assertEqual(rpg.load_heroes(), [])

    def test_new_game_rejects_duplicates_and_more_than_max(self):
        self.new_game("アリス")
        with self.assertRaises(SystemExit):
            self.new_game("アリス")
        for i in range(2, rpg.MAX_HEROES + 1):
            self.new_game(f"勇者{i}")
        with self.assertRaises(SystemExit):
            self.new_game("あふれた勇者")
        self.assertEqual(len(rpg.load_heroes()), rpg.MAX_HEROES)

    def test_retire_frees_a_slot_and_ids_are_not_reused(self):
        for i in range(1, rpg.MAX_HEROES + 1):
            self.new_game(f"勇者{i}")
        self.run_main(["--retire", "勇者2"])
        self.assertEqual([h["active"] for h in rpg.load_heroes()], [True, False] + [True] * (rpg.MAX_HEROES - 2))
        self.new_game("新人")
        self.assertEqual(rpg.load_heroes()[-1]["id"], f"h{rpg.MAX_HEROES + 1}")
        with self.assertRaises(SystemExit):
            self.run_main(["--retire", "勇者2"])  # 引退済み

    def test_daily_advances_active_heroes_writes_pages_and_only_the_board_gist(self):
        self.new_game("アリス")
        self.new_game("ボブ")
        self.new_game("引退者")
        self.run_main(["--retire", "h3"])
        retired_page = rpg.page_path("h3").read_text(encoding="utf-8")
        self.assertEqual(self.daily(), 0)
        # Gist はランキングの 1 つだけ（最初の実行で作られ、board.json に記録される）
        self.assertEqual(self.create_board.call_count, 1)
        self.assertEqual(self.create_board.call_args.args[1], rpg.BOARD_FILENAME)
        self.update_gist.assert_not_called()
        self.assertEqual(rpg.load_board()["gist_id"], "board")
        board = self.create_board.call_args.args[2]
        self.assertIn("■ アリス", board)
        self.assertNotIn("引退者", board)
        self.assertEqual((load("h1")["day"], load("h3")["day"]), (1, 0))  # 引退者は進まない
        self.assertIn("S1 Day 1", rpg.page_path("h1").read_text(encoding="utf-8"))
        self.assertEqual(rpg.page_path("h3").read_text(encoding="utf-8"), retired_page)

    def test_page_url_points_to_github_in_actions(self):
        env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_REF_NAME": "master"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(rpg.page_url("h1"), "https://github.com/o/r/blob/master/heroes/h1.md")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(rpg.page_url("h1"), "heroes/h1.md")

    def test_daily_is_idempotent_and_catches_up(self):
        self.new_game("アリス")
        self.daily("2026-12-01")
        self.daily("2026-12-01")  # 同じ日は進まない
        self.assertEqual(load("h1")["day"], 1)
        self.daily("2026-12-04")  # 3 日空いた
        self.assertEqual(load("h1")["day"], 4)

    def test_one_failing_hero_does_not_stop_the_others(self):
        self.new_game("アリス")
        self.new_game("ボブ")
        rpg.hero_state_path("h1").write_text("{壊れた", encoding="utf-8")
        with mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.daily(), 1)
        warn.assert_called_once()
        self.assertEqual(load("h2")["day"], 1)
        board = self.create_board.call_args.args[2]
        self.assertIn("■ ボブ", board)  # 進められた勇者だけでランキングを作る
        self.assertNotIn("■ アリス", board)

    def test_board_is_updated_after_it_is_created(self):
        self.new_game("アリス")
        self.daily("2026-12-01")
        self.daily("2026-12-02")
        self.assertEqual(self.create_board.call_count, 1)
        (call,) = self.update_gist.call_args_list
        self.assertEqual(call.args[:3], ("board", "p", rpg.BOARD_FILENAME))
        self.assertEqual(call.kwargs["description"], "勇者ランキング シーズン1")

    def test_board_is_recreated_when_the_gist_is_gone(self):
        self.new_game("アリス")
        self.daily("2026-12-01")
        self.create_board.return_value = {"id": "board2", "html_url": "https://g/board2"}
        self.update_gist.side_effect = common.GistAPIError(404, "Gist API エラー 404")
        with mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.daily("2026-12-02"), 0)
        warn.assert_called_once()
        self.assertEqual(rpg.load_board()["gist_id"], "board2")

    def test_board_other_errors_are_not_recreated(self):
        self.new_game("アリス")
        self.daily("2026-12-01")
        self.update_gist.side_effect = common.GistAPIError(403, "Gist API エラー 403")
        with mock.patch.object(common, "warn"):
            self.assertEqual(self.daily("2026-12-02"), 1)
        self.assertEqual(self.create_board.call_count, 1)
        self.assertEqual(rpg.load_board()["gist_id"], "board")

    def test_board_failure_fails_the_run_but_keeps_heroes(self):
        self.new_game("アリス")
        self.create_board.side_effect = SystemExit("Gist API エラー 403")
        with mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.daily(), 1)
        warn.assert_called_once()
        self.assertEqual(load("h1")["day"], 1)
        self.assertTrue(rpg.page_path("h1").exists())
        self.assertIsNone(rpg.load_board())

    def test_daily_needs_pat(self):
        self.new_game("アリス")
        with self.assertRaises(SystemExit):
            self.run_main(["--date", "2026-12-01"])
        self.assertEqual(load("h1")["day"], 0)

    def test_retire_matches_id_first(self):
        self.new_game("アリス")
        self.new_game("ボブ")
        self.run_main(["--retire", "h2"])
        self.assertEqual([h["active"] for h in rpg.load_heroes()], [True, False])

    def test_missing_state_file_is_an_error_not_a_reset(self):
        self.new_game("アリス")
        rpg.hero_state_path("h1").unlink()
        with mock.patch.object(common, "warn") as warn:
            self.assertEqual(self.daily(), 1)
        self.create_board.assert_not_called()  # 進められた勇者がいなければ、ランキングも作らない
        warn.assert_called_once()
        self.assertFalse(rpg.hero_state_path("h1").exists())

    def test_no_heroes_is_a_noop(self):
        self.assertEqual(self.daily(), 0)
        self.create_board.assert_not_called()

    def test_daily_writes_the_map_with_positions(self):
        self.new_game("アリス")
        self.daily()
        text = (rpg.PAGE_DIR / rpg.MAP_NAME).read_text(encoding="utf-8")
        self.assertIn("🧙 アリス Lv.", text)
        self.assertIn(f"![世界の MAP]({rpg.MAP_IMAGE})", text)  # 地図の画像を貼る
        self.assertIn("🧙 アリス Lv.", (rpg.PAGE_DIR / rpg.MAP_IMAGE).read_text(encoding="utf-8"))

    def test_map_option_writes_only_the_map(self):
        self.new_game("アリス")
        self.assertEqual(self.run_main(["--map"]), 0)
        self.assertIn("🧙 アリス Lv.1", (rpg.PAGE_DIR / rpg.MAP_NAME).read_text(encoding="utf-8"))
        self.assertEqual(load("h1")["day"], 0)  # 冒険は進めない
        self.create_board.assert_not_called()

    def test_map_grid_writes_a_file_without_advancing(self):
        self.new_game("アリス")
        out = self.dir / "grid.svg"
        self.assertEqual(self.run_main(["--map-grid", str(out)]), 0)
        self.assertIn("town_", out.read_text(encoding="utf-8"))
        self.assertEqual(load("h1")["day"], 0)
        self.assertFalse((rpg.PAGE_DIR / rpg.MAP_IMAGE).exists())  # 毎日の地図は書き換えない

    def test_dry_run_writes_nothing(self):
        self.new_game("アリス")
        page = rpg.page_path("h1").read_text(encoding="utf-8")
        self.run_main(["--dry-run", "--actions", "1"])
        self.create_board.assert_not_called()
        self.update_gist.assert_not_called()
        self.assertEqual(load("h1")["day"], 0)
        self.assertEqual(rpg.page_path("h1").read_text(encoding="utf-8"), page)
        self.assertIsNone(rpg.load_board())
        self.assertFalse((rpg.PAGE_DIR / rpg.MAP_NAME).exists())


if __name__ == "__main__":
    unittest.main()
