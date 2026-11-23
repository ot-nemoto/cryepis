"""壊れたファイル（マージ衝突など）・ないファイルを読んだときの動き。分かりやすいメッセージで止まる（ファイルは壊さない）。
状態を保存できない勇者がいても、ほかの勇者は進む。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import game  # noqa: E402
import rpg  # noqa: E402
import store  # noqa: E402
import world as W  # noqa: E402

WORLD = W.load_world()
CONFLICT = "<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> branch\n"  # マージ衝突で壊れた JSON


class LoadersTest(unittest.TestCase):
  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.path = Path(tmp.name) / "file.json"

  def loaders(self):
    """(名前, パスを受け取って読む関数)。"""
    return (
      ("勇者管理シート", store.load_heroes),
      ("ランキングの Gist の記録", rpg.load_board),
      ("ギルドの掲示板", store.load_guild),
      ("勇者の状態", lambda path: game.load_state(WORLD, path)),
      ("world/ のデータ", W.read_json),
    )

  def test_broken_json_stops_with_the_path_and_leaves_the_file_alone(self):
    self.path.write_text(CONFLICT, encoding="utf-8")
    for name, load in self.loaders():
      with self.subTest(name), self.assertRaises(SystemExit) as cm:
        load(self.path)
      self.assertIn(str(self.path), str(cm.exception), name)  # どのファイルか分かる
      self.assertEqual(self.path.read_text(encoding="utf-8"), CONFLICT, name)  # 勝手に直さない・消さない

  def test_missing_files(self):
    gone = self.path.with_name("gone.json")
    self.assertEqual(store.load_heroes(gone), [])  # まだ誰も登録していない
    self.assertIsNone(rpg.load_board(gone))  # ランキングの Gist はこれから作る
    self.assertEqual(store.load_guild(gone), game.new_guild())  # 掲示板は貼り直す
    self.assertEqual(game.load_state(WORLD, gone, "h3", "アリス"), game.new_state(WORLD, "h3", "アリス"))
    with self.assertRaises(SystemExit) as cm:
      W.read_json(gone)  # world/ のデータは、なくてはならない
    self.assertIn("がありません", str(cm.exception))

  def test_heroes_file_without_the_heroes_key(self):
    self.path.write_text('{"勇者": []}', encoding="utf-8")
    with self.assertRaises(SystemExit) as cm:
      store.load_heroes(self.path)
    self.assertIn(str(self.path), str(cm.exception))


class DailyRunTest(unittest.TestCase):
  """毎日の実行（rpg.main）を一時ディレクトリに向けて動かす。"""

  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.dir = Path(tmp.name)
    for module, target, value in (
      (store, "HEROES_PATH", self.dir / "heroes.json"),
      (store, "HERO_DIR", self.dir / "heroes"),
      (rpg, "BOARD_PATH", self.dir / "board.json"),
      (store, "GUILD_PATH", self.dir / "guild.json"),
    ):
      patcher = mock.patch.object(module, target, value)
      patcher.start()
      self.addCleanup(patcher.stop)
    patcher = mock.patch.object(common, "create_gist", return_value={"id": "board", "html_url": "https://g/board"})
    self.create_board = patcher.start()
    self.addCleanup(patcher.stop)
    patcher = mock.patch.object(common, "update_gist")
    patcher.start()
    self.addCleanup(patcher.stop)

  def run_main(self, argv, env=None):
    with (
      mock.patch.object(sys, "argv", ["rpg.py", *argv]),
      mock.patch.dict("os.environ", env or {}, clear=True),
      mock.patch("sys.stdout"),
    ):
      return rpg.main()

  def test_a_hero_whose_state_cannot_be_saved_does_not_stop_the_others(self):
    for name in ("アリス", "ボブ"):
      self.run_main(["--new-game", name])
    original = game.save_state

    def save(state, path):
      if state["id"] == "h1":
        raise OSError("ディスクがいっぱい")
      original(state, path)

    with mock.patch.object(game, "save_state", side_effect=save), mock.patch.object(common, "warn") as warn:
      self.assertEqual(self.run_main(["--date", "2026-12-01"], {"GIST_PAT": "p"}), 1)  # 失敗した勇者がいる
    warn.assert_called_once()
    self.assertIn("アリス", warn.call_args.args[0])
    self.assertIn("ディスクがいっぱい", warn.call_args.args[0])
    self.assertEqual(game.load_state(WORLD, store.hero_state_path("h2"))["day"], 1)  # ボブは進んで、保存された
    self.assertEqual(
      game.load_state(WORLD, store.hero_state_path("h1"))["day"], 0
    )  # アリスは進んでいない（次の実行でやり直す）
    board = self.create_board.call_args.args[2]
    self.assertIn("■ ボブ", board)  # 進められた勇者だけでランキングを作る
    self.assertNotIn("■ アリス", board)

  def test_current_entries_skip_heroes_without_a_state_file(self):
    self.run_main(["--new-game", "アリス"])
    store.hero_state_path("h1").unlink()
    self.assertEqual(store.current_entries(WORLD), [])  # サイトの書き出しは、状態のない勇者を飛ばす


if __name__ == "__main__":
  unittest.main()
