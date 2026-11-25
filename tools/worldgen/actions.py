"""道の長さ（km）を、移動に使う行動回数に換算して roads.json / sea.json に書き足す。

1 日に進む距離を 1 回とする（移動日数＝行動回数。端数は四捨五入）。
- 街道: 徒歩で 1 日 36km（約 9 里）。最低 1 回
- ダンジョンへの道: 山道なので 1 日 20km。最低 1 回
- 航路: 帆船で 1 日 70km。乗り降りがあるので最低 2 回
"""

import json

import paths

LAND_KM, TRAIL_KM, SEA_KM, SEA_MIN = 36, 20, 70, 2


def land(km):
  return max(1, round(km / LAND_KM))


def trail(km):
  return max(1, round(km / TRAIL_KM))


def sea(km):
  return max(SEA_MIN, round(km / SEA_KM))


def main():
  with open(paths.ROADS, encoding="utf-8") as f:
    roads = json.load(f)
  for r in roads:
    r["actions"] = (trail if r["kind"] == "trail" else land)(r["km"])
  with open(paths.ROADS, "w", encoding="utf-8") as f:
    json.dump(roads, f, ensure_ascii=False, indent=1)
  with open(paths.SEA, encoding="utf-8") as f:
    routes = json.load(f)
  for r in routes:
    r["actions"] = sea(r["km"])
  with open(paths.SEA, "w", encoding="utf-8") as f:
    json.dump(routes, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
  main()
