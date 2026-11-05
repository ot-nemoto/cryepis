import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import report  # noqa: E402

SAMPLE = json.loads((ROOT / "tests" / "sample_data.json").read_text(encoding="utf-8"))


class CardTest(unittest.TestCase):
    def test_every_card_fits_the_pinned_width(self):
        for key in report.CARDS:
            lines = report.build_card(key, SAMPLE).splitlines()
            for line in lines[1:5]:  # 見出し（1 行目）以外の本文は 49 桁に収める
                self.assertLessEqual(common.width(line), common.LINE_MAX, f"{key}: {line}")

    def test_overview_shows_totals(self):
        text = report.build_card("overview", SAMPLE)
        self.assertIn("1,284", text)
        self.assertIn("312 IP", text)
        self.assertIn("11/05", text)

    def test_ranking_is_sorted_and_numbered(self):
        text = report.build_card("creds", SAMPLE)
        self.assertIn("1. 123456", text)
        self.assertIn("611", text)

    def test_ports_include_protocol(self):
        self.assertIn("22/tcp", report.build_card("ports", SAMPLE))

    def test_empty_data_does_not_crash(self):
        for key in report.CARDS:
            text = report.build_card(key, report.empty_data())
            self.assertTrue(text.endswith("\n"))


class MaskTest(unittest.TestCase):
    def test_redacts_bad_words_and_control_chars(self):
        self.assertEqual(report.mask("f\x00uck"), "***")  # 制御文字を除いたうえで NG 語を伏せる
        self.assertEqual(report.mask("a\x07b"), "ab")
        self.assertEqual(report.mask("x" * 100), "x" * 40)

    def test_long_values_are_clipped_in_cards(self):
        data = {"date": "2026-11-05", "top_http_paths": [{"v": "/" + "a" * 100, "n": 5}]}
        line = report.build_card("http", data).splitlines()[1]
        self.assertLessEqual(common.width(line), common.LINE_MAX)
        self.assertIn("…", line)


class GistIdsTest(unittest.TestCase):
    def test_parses_json_or_empty(self):
        with mock.patch.dict("os.environ", {"GIST_IDS": '{"overview":"abc"}'}, clear=True):
            self.assertEqual(report.gist_ids(), {"overview": "abc"})
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(report.gist_ids(), {})
        with mock.patch.dict("os.environ", {"GIST_IDS": "not json"}, clear=True):
            with self.assertRaises(SystemExit):
                report.gist_ids()


class MainTest(unittest.TestCase):
    def run_main(self, argv, env):
        with mock.patch.object(common, "update_gist") as upd, \
             mock.patch.object(common, "create_gist", side_effect=lambda t, fn, c, d: {"id": fn, "html_url": "u"}) as crt, \
             mock.patch.object(sys, "argv", ["report.py", *argv]), \
             mock.patch.dict("os.environ", env, clear=True), mock.patch("sys.stdout", io.StringIO()):
            rc = report.main()
        return rc, upd, crt

    def test_dry_run_updates_nothing(self):
        _, upd, _ = self.run_main(["--sample", str(ROOT / "tests" / "sample_data.json"), "--dry-run"], {})
        upd.assert_not_called()

    def test_updates_only_registered_cards(self):
        env = {"GIST_PAT": "p", "GIST_IDS": '{"overview":"g1","ports":"g2"}'}
        _, upd, _ = self.run_main(["--sample", str(ROOT / "tests" / "sample_data.json")], env)
        self.assertEqual({c.args[0] for c in upd.call_args_list}, {"g1", "g2"})

    def test_skips_when_no_ids(self):
        _, upd, _ = self.run_main(["--sample", str(ROOT / "tests" / "sample_data.json")], {"GIST_PAT": "p"})
        upd.assert_not_called()

    def test_create_gists_writes_summary_and_refuses_when_set(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "s.md"
            _, _, crt = self.run_main(["--create-gists"], {"GIST_PAT": "p", "GITHUB_STEP_SUMMARY": str(summary)})
            self.assertEqual(crt.call_count, len(report.CARDS))
            self.assertIn("overview", summary.read_text(encoding="utf-8"))
        with self.assertRaises(SystemExit):
            self.run_main(["--create-gists"], {"GIST_PAT": "p", "GIST_IDS": '{"overview":"x"}'})


if __name__ == "__main__":
    unittest.main()
