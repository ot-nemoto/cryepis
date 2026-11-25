"""壊れたファイル（マージ衝突など）・ないファイルを読んだときの動き。分かりやすいメッセージで止まる（ファイルは壊さない）。
（毎日の実行で、状態を保存できないもののふがいても、ほかのもののふが進むことは test_rpg.py の HeroesTest）"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
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
      ("もののふ管理シート", store.load_heroes),
      ("ランキングの Gist の記録", rpg.load_board),
      ("口入れ屋の高札", store.load_guild),
      ("もののふの状態", lambda path: game.load_state(WORLD, path)),
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
    self.assertEqual(store.load_guild(gone), game.new_guild())  # 高札は貼り直す
    self.assertEqual(game.load_state(WORLD, gone, "h3", "アリス"), game.new_state(WORLD, "h3", "アリス"))
    with self.assertRaises(SystemExit) as cm:
      W.read_json(gone)  # world/ のデータは、なくてはならない
    self.assertIn("がありません", str(cm.exception))

  def test_heroes_file_without_the_heroes_key(self):
    self.path.write_text('{"もののふ": []}', encoding="utf-8")
    with self.assertRaises(SystemExit) as cm:
      store.load_heroes(self.path)
    self.assertIn(str(self.path), str(cm.exception))


if __name__ == "__main__":
  unittest.main()
