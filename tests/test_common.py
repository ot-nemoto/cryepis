import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402


class DisplayTest(unittest.TestCase):
    def test_width_counts_wide_chars_as_two(self):
        self.assertEqual(common.width("aあ🗻"), 5)
        self.assertEqual(common.width("★★木の剣"), 10)  # ★ も全角として数える（Gist のカードからはみ出さないように）

    def test_bar(self):
        self.assertEqual(common.bar(0, 100, 4), "    ")
        self.assertEqual(common.bar(0, 0, 4), "    ")
        self.assertEqual(common.bar(1, 100, 4), "█   ")
        self.assertEqual(common.bar(100, 100, 4), "████")


class UpdateGistTest(unittest.TestCase):
    def test_renames_single_placeholder_file(self):
        with mock.patch.object(common, "api", side_effect=[{"files": {"gistfile1.txt": {"content": "x"}}}, {}]) as api, \
             mock.patch("sys.stdout", io.StringIO()):
            common.update_gist("g", "t", "rank.txt", "new")
        self.assertEqual(api.call_args.args[3],
                         {"files": {"gistfile1.txt": {"filename": "rank.txt", "content": "new"}}})

    def test_skips_when_unchanged(self):
        with mock.patch.object(common, "api", return_value={"files": {"rank.txt": {"content": "same"}}}) as api, \
             mock.patch("sys.stdout", io.StringIO()):
            common.update_gist("g", "t", "rank.txt", "same")
        self.assertEqual(api.call_count, 1)

    def test_syncs_description_even_when_content_is_unchanged(self):
        gist = {"description": "old", "files": {"rank.txt": {"content": "same"}}}
        out = io.StringIO()
        with mock.patch.object(common, "api", side_effect=[gist, {}]) as api, mock.patch("sys.stdout", out):
            common.update_gist("g", "t", "rank.txt", "same", description="GOLD HILDR")
        self.assertEqual(api.call_args.args[3], {"description": "GOLD HILDR"})  # 中身は送らない
        self.assertIn("説明", out.getvalue())
        self.assertNotIn("rank.txt", out.getvalue())

    def test_updates_content_and_description_together(self):
        gist = {"description": "old", "files": {"rank.txt": {"content": "x"}}}
        with mock.patch.object(common, "api", side_effect=[gist, {}]) as api, mock.patch("sys.stdout", io.StringIO()):
            common.update_gist("g", "t", "rank.txt", "new", description="GOLD HILDR")
        self.assertEqual(api.call_args.args[3],
                         {"files": {"rank.txt": {"content": "new"}}, "description": "GOLD HILDR"})

    def test_skips_when_content_and_description_are_unchanged(self):
        gist = {"description": "GOLD HILDR", "files": {"rank.txt": {"content": "same"}}}
        with mock.patch.object(common, "api", return_value=gist) as api, mock.patch("sys.stdout", io.StringIO()):
            common.update_gist("g", "t", "rank.txt", "same", description="GOLD HILDR")
        self.assertEqual(api.call_count, 1)

    def test_gist_api_error_exits_with_hint(self):
        err = common.urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b"Not Found"))
        with mock.patch.object(common, "api", side_effect=err), self.assertRaises(SystemExit) as cm:
            common.update_gist("g", "t", "rank.txt", "new")
        self.assertIn("gist スコープ", str(cm.exception))

    def test_create_gist_is_public_with_one_file(self):
        with mock.patch.object(common, "api", return_value={"id": "abc"}) as api:
            self.assertEqual(common.create_gist("t", "a.txt", "body", "desc")["id"], "abc")
        self.assertEqual(api.call_args.args[:3], ("POST", "https://api.github.com/gists", "t"))
        self.assertEqual(api.call_args.args[3],
                         {"public": True, "description": "desc", "files": {"a.txt": {"content": "body"}}})

    def test_create_gist_error_exits_with_hint(self):
        err = common.urllib.error.HTTPError("u", 401, "bad", {}, io.BytesIO(b"Bad credentials"))
        with mock.patch.object(common, "api", side_effect=err), self.assertRaises(SystemExit) as cm:
            common.create_gist("t", "a.txt", "body", "desc")
        self.assertIn("gist スコープ", str(cm.exception))
        with mock.patch.object(common, "api", side_effect=common.urllib.error.URLError("down")), \
             self.assertRaises(SystemExit):
            common.create_gist("t", "a.txt", "body", "desc")


class ApiTest(unittest.TestCase):
    def test_sends_token_and_body(self):
        res = mock.MagicMock()
        res.__enter__.return_value = io.BytesIO(b'{"ok": 1}')
        with mock.patch.object(common.urllib.request, "urlopen", return_value=res) as urlopen:
            self.assertEqual(common.api("POST", "https://x", "tok", {"a": 1}), {"ok": 1})
        req = urlopen.call_args.args[0]
        self.assertEqual((req.get_method(), req.get_header("Authorization"), req.data), ("POST", "Bearer tok", b'{"a": 1}'))


if __name__ == "__main__":
    unittest.main()
