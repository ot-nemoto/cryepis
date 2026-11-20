"""道の通りにくさ（cost）を、移動に使う行動回数に換算して roads.json / sea.json に書き足す。

- 陸路・ダンジョンへの道: 通りにくさ 40 ごとに 1 回（最低 1 回）
- 航路: 船は速いので 60 ごとに 1 回。乗り降りがあるので最低 2 回
"""
import json

import paths

LAND_PER_ACTION, SEA_PER_ACTION, SEA_MIN = 40, 60, 2


def land(cost):
    return max(1, round(cost / LAND_PER_ACTION))


def sea(cost):
    return max(SEA_MIN, round(cost / SEA_PER_ACTION))


def main():
    for path, fn in ((paths.ROADS, land), (paths.SEA, sea)):
        data = json.load(open(path, encoding="utf-8"))
        for r in data:
            r["actions"] = fn(r["cost"])
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
