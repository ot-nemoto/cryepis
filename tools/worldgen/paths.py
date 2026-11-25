"""地図づくりの置き場所。途中のファイルは .cache/ に置く（リポジトリには入れない）。"""

import pickle
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parent.parent
CACHE = DIR / ".cache"
TERRAIN = CACHE / "terrain.pkl"  # 地形のマス目・高さ・川の線（terrain.py）
ROADS = CACHE / "roads.json"  # 陸路と霊場への道（roads.py）
SEA = CACHE / "sea.json"  # 航路（sea.py）
BASE_SVG = CACHE / "map-base.svg"  # ベースの地図（vectorize.py）


def kind():
  """地形のマス目（2px 単位、[y][x] → 地形の名前）。"""
  with open(TERRAIN, "rb") as f:
    return pickle.load(f)["kind"]
