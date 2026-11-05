import copy
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


def play(days, contributions=3, seed=0):
    state = rpg.new_state(WORLD)
    for i in range(days):
        rpg.advance(WORLD, state, DAY + timedelta(days=i + seed * 1000), contributions)
    return state


class WorldTest(unittest.TestCase):
    def test_layout(self):
        areas = WORLD["areas"]
        self.assertEqual(areas[0]["start"], 0)
        for a, b in zip(areas, areas[1:]):
            self.assertEqual(a["end"] + 1, b["start"])
        self.assertEqual(WORLD["total"], sum(a["length"] for a in areas))

    def test_steps_for(self):
        self.assertEqual([rpg.steps_for(WORLD, c) for c in (0, 1, 2, 3, 5, 6, 40)], [0, 1, 1, 2, 2, 3, 3])


class AdvanceTest(unittest.TestCase):
    def test_same_day_is_processed_once_and_deterministic(self):
        a, b = rpg.new_state(WORLD), rpg.new_state(WORLD)
        self.assertTrue(rpg.advance(WORLD, a, DAY, 3))
        self.assertFalse(rpg.advance(WORLD, a, DAY, 3))  # 同じ日は進めない
        rpg.advance(WORLD, b, DAY, 3)
        self.assertEqual(a, b)  # 同じ日・同じ周回なら同じ結果
        self.assertEqual(a["day"], 1)

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
        self.assertFalse(rpg.advance(WORLD, state, DAY, 3))
        self.assertEqual(state["day"], 3)

    def test_moves_by_steps(self):
        state = rpg.new_state(WORLD)
        rpg.advance(WORLD, state, DAY, 6)
        self.assertEqual(state["pos"], 3)

    def test_boss_blocks_until_defeated(self):
        state = rpg.new_state(WORLD)
        area = WORLD["areas"][0]
        state["pos"] = area["end"] - 1
        state["hero"]["hp"] = 1
        state["hero"]["potions"] = 0
        rpg.advance(WORLD, state, DAY, 6)  # 弱いまま挑んで負ける
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
        state = play(rpg.JOURNAL_DAYS + 5, contributions=0)
        self.assertEqual(len(state["journal"]), rpg.JOURNAL_DAYS)
        self.assertEqual(state["journal"][0]["date"], (DAY + timedelta(days=rpg.JOURNAL_DAYS + 4)).isoformat())


class BalanceTest(unittest.TestCase):
    def test_average_player_clears_in_about_three_months(self):
        days, deaths = [], []
        for seed in range(40):
            rng, state, d = random.Random(seed), rpg.new_state(WORLD), DAY + timedelta(days=seed * 1000)
            while state["lap"] == 1 and state["day"] < 1000:
                rpg.advance(WORLD, state, d, rng.choices([0, 1, 3, 6], [30, 30, 25, 15])[0])
                d += timedelta(days=1)
            self.assertEqual(state["lap"], 2, "1000 日でクリアできない")
            days.append(state["records"][0]["days"])
            deaths.append(state["records"][0]["deaths"])
        days.sort()
        self.assertTrue(70 <= days[len(days) // 2] <= 120, days)
        self.assertLess(max(deaths), 15)


class DisplayTest(unittest.TestCase):
    def test_card_fits_and_has_five_lines(self):
        for state in (rpg.new_state(WORLD), play(1), play(40), play(120, contributions=6)):
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
            path = Path(d) / "rpg.json"
            rpg.save_state(state, path)
            self.assertEqual(rpg.load_state(WORLD, path), state)
            path.write_text("<<<<<<< HEAD")
            with self.assertRaises(SystemExit):
                rpg.load_state(WORLD, path)
        self.assertEqual(rpg.load_state(WORLD, Path("/nonexistent/rpg.json")), rpg.new_state(WORLD))

    def test_load_fills_missing_keys_and_clamps_to_the_world(self):
        state = play(3)
        del state["kills"], state["hero"]["potions"]
        state.update(pos=WORLD["total"] + 5, boss_cleared=["草原", "消えたエリア"])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rpg.json"
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


class ContributionsTest(unittest.TestCase):
    def test_sums_contributions_and_falls_back_to_next_token(self):
        ok = {"data": {"user": {"contributionsCollection": {
            "totalCommitContributions": 3, "totalIssueContributions": 1, "totalPullRequestContributions": 1,
            "totalPullRequestReviewContributions": 0, "restrictedContributionsCount": 2}}}}
        denied = {"errors": [{"message": "Resource not accessible by integration"}]}
        with mock.patch.object(common, "api", side_effect=[denied, ok]) as api:
            self.assertEqual(rpg.fetch_contributions("me", DAY, ["t1", "t2"]), 7)
        body = api.call_args.args[3]
        self.assertEqual(body["variables"]["from"], "2026-10-06T00:00:00+09:00")  # JST の 1 日
        self.assertEqual(body["variables"]["to"], "2026-10-06T23:59:59+09:00")

    def test_retries_server_errors(self):
        ok = {"data": {"user": {"contributionsCollection": {"totalCommitContributions": 2}}}}
        err = rpg.urllib.error.HTTPError("u", 502, "bad", {}, None)
        with mock.patch.object(common, "api", side_effect=[err, ok]), mock.patch.object(rpg.time_module, "sleep"):
            self.assertEqual(rpg.fetch_contributions("me", DAY, ["t1"]), 2)

    def test_all_tokens_fail(self):
        with mock.patch.object(common, "api", side_effect=OSError("down")) as api, \
             mock.patch.object(rpg.time_module, "sleep"), self.assertRaises(common.FetchError):
            rpg.fetch_contributions("me", DAY, ["t1"])
        self.assertEqual(api.call_count, len(common.RETRY_WAITS) + 1)  # 通信エラーは再試行してから諦める
        with self.assertRaises(common.FetchError):
            rpg.fetch_contributions("me", DAY, [])


class MainTest(unittest.TestCase):
    def run_main(self, state, argv, env=None):
        saved = {}
        with mock.patch.object(rpg, "load_state", return_value=state), \
             mock.patch.object(rpg, "save_state", side_effect=lambda s: saved.setdefault("s", copy.deepcopy(s))), \
             mock.patch.object(common, "update_gist") as gist, \
             mock.patch.object(sys, "argv", ["rpg.py", *argv]), mock.patch.dict("os.environ", env or {}, clear=True), \
             mock.patch("sys.stdout"):
            rpg.main()
        return saved.get("s"), gist

    def test_advances_saves_and_updates_gist(self):
        saved, gist = self.run_main(rpg.new_state(WORLD), ["--date", "2026-10-06", "--contributions", "3"],
                                    {"GIST_ID_RPG": "g", "GIST_PAT": "p"})
        self.assertEqual(saved["day"], 1)
        self.assertEqual(gist.call_args.args[:3], ("g", "p", rpg.FILENAME))

    def test_already_processed_only_updates_gist(self):
        state = play(1)
        saved, gist = self.run_main(state, ["--date", DAY.isoformat()], {"GIST_ID_RPG": "g", "GIST_PAT": "p"})
        self.assertIsNone(saved)
        gist.assert_called_once()

    def test_catches_up_missed_days(self):
        state = play(1)  # last_date = DAY
        today = DAY + timedelta(days=3)
        with mock.patch.object(rpg, "fetch_contributions", return_value=3) as fetch:
            saved, gist = self.run_main(state, ["--date", today.isoformat()],
                                        {"RPG_USER": "me", "GIST_ID_RPG": "g", "GIST_PAT": "p"})
        self.assertEqual([c.args[1] for c in fetch.call_args_list],
                         [DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)])  # 各日の前日の分
        self.assertEqual((saved["day"], saved["last_date"]), (4, today.isoformat()))
        gist.assert_called_once()

    def test_stops_at_the_first_failed_day(self):
        state = play(1)
        with mock.patch.object(rpg, "fetch_contributions", side_effect=[3, common.FetchError("x")]), \
             mock.patch.object(common, "warn"):
            saved, _ = self.run_main(state, ["--date", (DAY + timedelta(days=3)).isoformat()],
                                     {"RPG_USER": "me", "GIST_ID_RPG": "g", "GIST_PAT": "p"})
        self.assertEqual(saved["last_date"], (DAY + timedelta(days=1)).isoformat())  # 失敗した日から次回やり直す

    def test_fetch_failure_skips_the_day(self):
        with mock.patch.object(rpg, "fetch_contributions", side_effect=common.FetchError("x")), \
             mock.patch.object(common, "warn"):
            saved, gist = self.run_main(rpg.new_state(WORLD), ["--date", "2026-10-06"],
                                        {"RPG_USER": "me", "GIST_ID_RPG": "g", "GIST_PAT": "p"})
        self.assertIsNone(saved)
        gist.assert_not_called()

    def test_create_gist_writes_summary(self):
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "summary.md"
            with mock.patch.object(common, "create_gist", return_value={"id": "abc", "html_url": "https://g/abc"}) as create:
                saved, gist = self.run_main(rpg.new_state(WORLD), ["--create-gist"],
                                            {"GIST_PAT": "p", "GITHUB_STEP_SUMMARY": str(summary)})
            self.assertIn("`abc`", summary.read_text(encoding="utf-8"))
        token, filename, content, _ = create.call_args.args
        self.assertEqual((token, filename), ("p", rpg.FILENAME))
        self.assertTrue(content.startswith("⚔️ Day 0"))
        self.assertIsNone(saved)  # 冒険は進めない
        gist.assert_not_called()

    def test_create_gist_refuses_when_already_set(self):
        with mock.patch.object(common, "create_gist") as create, self.assertRaises(SystemExit):
            self.run_main(rpg.new_state(WORLD), ["--create-gist"], {"GIST_PAT": "p", "GIST_ID_RPG": "g"})
        create.assert_not_called()

    def test_create_gist_needs_pat_and_rejects_dry_run(self):
        with mock.patch.object(common, "create_gist") as create:
            with self.assertRaises(SystemExit):
                self.run_main(rpg.new_state(WORLD), ["--create-gist"], {"GIST_ID_RPG": ""})  # 未設定の変数は空文字
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                self.run_main(rpg.new_state(WORLD), ["--create-gist", "--dry-run"], {"GIST_PAT": "p"})
        create.assert_not_called()

    def test_create_gist_when_variable_is_empty(self):
        with mock.patch.object(common, "create_gist", return_value={"id": "abc", "html_url": "u"}) as create:
            self.run_main(rpg.new_state(WORLD), ["--create-gist"], {"GIST_PAT": "p", "GIST_ID_RPG": ""})
        create.assert_called_once()

    def test_dry_run_writes_nothing(self):
        saved, gist = self.run_main(rpg.new_state(WORLD), ["--dry-run", "--contributions", "1"])
        self.assertIsNone(saved)
        gist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
