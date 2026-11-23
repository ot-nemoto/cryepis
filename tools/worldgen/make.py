"""世界の地図をまとめて作り直す（world/map-base.svg と world/map.json を書き出す）。

    python tools/worldgen/make.py

手順（途中のファイルは tools/worldgen/.cache/ に置く）:
1. terrain.py   陸と海（land.py。seed "e"・陸 45%）に地形を塗る → .cache/terrain.pkl（1 分ほどかかる）
2. roads.py     町どうしの陸路と、ダンジョンへの道（地形の通りにくさの最短経路）→ .cache/roads.json
3. sea.py       航路（海のマスだけを通る最短経路）→ .cache/sea.json
4. actions.py   道の通りにくさを、移動にかかる行動回数に換算する
5. vectorize.py 地形を SVG にする → .cache/map-base.svg
6. build_world.py world/map.json と world/map-base.svg を書き出す

町・ダンジョン・航路は places.py、町の名前は names.py、町の店や ID は build_world.py、
山脈・地域・川は terrain.py に書いてある。乱数の種が同じなら、何度作り直しても同じ地図になる。
"""

import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402


def step(name, fn):
  t = time.time()
  fn()
  print(f"  {name}: {time.time() - t:.1f} 秒")


def make_terrain():
  import terrain

  kind, height = terrain.generate()
  paths.CACHE.mkdir(exist_ok=True)
  with open(paths.TERRAIN, "wb") as f:
    pickle.dump({"kind": kind, "height": height, "rivers": terrain.generate.rivers}, f)


def main():
  step("地形", make_terrain)
  import roads

  step("陸路", roads.main)
  import sea

  step("航路", sea.main)
  import actions

  step("行動回数", actions.main)
  import vectorize

  step("SVG", lambda: vectorize.main(paths.BASE_SVG))
  import build_world

  step("world/", build_world.main)
  print(
    "world/map.json と world/map-base.svg を書き出しました。python -m unittest discover -s tests で確かめてください"
  )


if __name__ == "__main__":
  main()
