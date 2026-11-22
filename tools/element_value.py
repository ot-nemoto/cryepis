"""属性が、点数でいうとどのくらい効くかを計算する（season.json の item_points.element を決めるのに使った）。

属性が効くのは、弱点を突ける相手と戦ったときだけ（苦手な相手のときは、持ち替えたり外したりするので損はない）。
1. 弱点を突けたとき: ダメージ 1.5 倍が、攻撃 +いくつに当たるか（tools/stat_value.py と同じ強さの見積もり）
2. 道での平均: 開放した道で出会う割合で重みをつけ、Lv1〜最大 Lv で平均した効き目を、攻撃 +1 を 1 点として出す
docs/BALANCE.md の「8. アイテムの強さ」の「属性の点数」を参照。

使い方:
  python tools/element_value.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import game  # noqa: E402
import stat_value as S  # noqa: E402
import world as W  # noqa: E402

CASES = [("will_o_wisp", 3), ("ice_wolf", 4), ("frost_sprite", 4), ("yeti", 10), ("great_eagle", 10)]


def hero(world: dict, level: int) -> dict:
  b = game.base_stats(world, None, level)
  return {"hp": b["max_hp"], "atk": b["atk"], "def": b["def"], "spd": b["spd"], "dex": b["dex"]}


def encounters(world: dict) -> Counter:
  """開放した道で、魔物ごとに出会う割合（道ごとの重みを、その道の合計で割って足したもの）。"""
  out: Counter = Counter()
  for info in world["road_info"].values():
    total = sum(x for _, x in info["monsters"])
    for mid, x in info["monsters"]:
      out[mid] += x / total
  return out


def atk_equivalent(world: dict, monster: dict, level: int, limit: int = 60) -> int:
  """弱点を突いたとき（ダメージ × strong）が、攻撃 +いくつに当たるか。"""
  h = hero(world, level)
  target = S.strength(world, h, monster) * world["season"]["element_rates"]["strong"]
  return next((n for n in range(limit + 1) if S.strength(world, h | {"atk": h["atk"] + n}, monster) >= target), limit)


def road_points(world: dict, element: str) -> tuple[float, float, list[str]]:
  """属性 element の道での点数（攻撃 +1 を 1 点）と、弱点を突ける魔物に出会う割合と、その魔物。"""
  enc = encounters(world)
  weak_to = world["elements"][element]["strong"]
  weak = [mid for mid in enc if world["monsters"][mid].get("element") == weak_to]
  share = sum(enc[m] for m in weak) / sum(enc.values())
  strong = world["season"]["element_rates"]["strong"] - 1
  gain_e = gain_a = 0.0
  for level in range(1, world["season"]["max_level"] + 1):
    h = hero(world, level)
    for mid, x in enc.items():
      m = world["monsters"][mid]
      gain_a += x * (S.strength(world, h | {"atk": h["atk"] + 1}, m) / S.strength(world, h, m) - 1)
      gain_e += x * (strong if mid in weak else 0)
  return gain_e / gain_a, share, weak


def main() -> int:
  world = W.load_world()
  print("1. 弱点を突けたときの効き目（ダメージ 1.5 倍が、攻撃 +いくつに当たるか）")
  for mid, lv in CASES:
    m = world["monsters"][mid]
    print(f"  Lv{lv:2d} {m['name']}（{world['elements'][m['element']]['name']}）: 攻 +{atk_equivalent(world, m, lv)}")
  print("2. 道での平均（開放した道で出会う割合で重みづけ、Lv1〜最大 Lv の平均、攻撃 +1 を 1 点）")
  for eid, e in world["elements"].items():
    pts, share, weak = road_points(world, eid)
    names = "・".join(world["monsters"][m]["name"] for m in weak) or "なし"
    print(f"  {e['name']}属性（弱点を突ける道の魔物: {names}、出会う割合 {share:.0%}）: {pts:.2f} 点")
  print(f"今の属性の点数（season.json の item_points.element）: {world['season']['item_points']['element']:g}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
