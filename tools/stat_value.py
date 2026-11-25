"""能力 +1 が、戦いでどれだけ効くかを計算する（アイテムの点数の重み、season.json の item_points を決めるのに使った）。

乱数を使わない見積もり。勝ち負けは「相手を倒すまでの手数」と「自分が倒れるまでの手数」の比で決まるとみて、
  強さ = (もののふの HP ÷ 1 手で受けるダメージ) × (1 手で与えるダメージ ÷ 物の怪の HP)
とし、能力を 1 だけ上げたときに強さが何 % 上がるかを出す。ダメージは小数のまま（守りの半分を引く・会心の分を平均で足す）。
素早さは、相手を追い越す・2 倍になるときだけ急に効く（少しずつは効かない）ので、相手ごとの表には出さず、
Lv1〜20 × 相手（開放した道の物の怪と、強敵・ボス）の平均で出す（2 回攻撃だけを見る。先に攻撃できる分は入らない）。
docs/BALANCE.md の「8. アイテムの強さ」を参照。

使い方:
  python tools/stat_value.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import game  # noqa: E402
import world as W  # noqa: E402

STATS = ("hp", "atk", "def", "dex")
# 比べる相手と、もののふのレベル（道の物の怪は同じ Lv、強敵は Lv10、ボスは Lv15・Lv20）
CASES = [
  ("wild_dog", 1),
  ("mud_golem", 3),
  ("forest_wolf", 5),
  ("treant", 6),
  ("yeti", 10),
  ("rock_golem", 10),
  ("great_eagle", 10),
  ("den_lord", 15),
  ("den_lord", 20),
]


def strength(world: dict, hero: dict, monster: dict) -> float:
  """もののふの強さの見積もり（大きいほど勝ちやすい。比だけに意味がある）。"""
  crit = world["season"]["critical"]
  bonus = crit["multiplier"] - 1  # 会心で増える分（平均で足す）
  e = W.trait_effect(monster)
  dealt = max(1, hero["atk"] - e["guard"] / 2) * (1 + bonus * hero["dex"] / crit["dex_divisor"])
  dealt *= 2 if hero["spd"] >= 2 * monster["spd"] else 1
  taken = max(1, e["atk"] - hero["def"] / 2) * (1 + bonus * monster.get("dex", 6) / crit["dex_divisor"]) * e["hits"]
  taken *= 2 if monster["spd"] >= 2 * hero["spd"] else 1
  return hero["hp"] / taken * dealt / e["hp"]


def gains(world: dict, monster: dict, level: int, stats: tuple[str, ...] = STATS) -> dict[str, float]:
  """能力ごとに、+1 で強さが何倍になるか − 1（0.05 なら 5% 強くなる）。もののふはふつう（特性なし）・装備なし。"""
  b = game.base_stats(world, None, level)
  hero = {"hp": b["max_hp"], "atk": b["atk"], "def": b["def"], "spd": b["spd"], "dex": b["dex"]}
  base = strength(world, hero, monster)
  return {k: strength(world, hero | {k: hero[k] + 1}, monster) / base - 1 for k in stats}


def average(world: dict) -> dict[str, float]:
  """素早さも含めた、能力 +1 の効き目の平均（攻撃 +1 を 1 とした比）。Lv1〜最大 Lv × 相手（道の物の怪・強敵・ボス）。"""
  foes = {m for i in world["road_info"].values() for m, _ in i["monsters"]}
  foes |= {mid for mid, m in world["monsters"].items() if m.get("elite") or m.get("boss")}
  keys = (*STATS, "spd")
  total = dict.fromkeys(keys, 0.0)
  for level in range(1, world["season"]["max_level"] + 1):
    for mid in sorted(foes):
      for k, v in gains(world, world["monsters"][mid], level, keys).items():
        total[k] += v
  return {k: v / total["atk"] for k, v in total.items()}


def main() -> int:
  world = W.load_world()
  weights = world["season"]["item_points"]["weights"]
  print("能力 +1 で強さが何 % 上がるか（かっこの中は、攻撃 +1 を 1 としたときの比）")
  for mid, lv in CASES:
    g = gains(world, world["monsters"][mid], lv)
    if g["atk"] <= 0:  # 守りが固すぎて、攻撃 +1 でも与えるダメージ（最低 1）が変わらない
      print(f"  Lv{lv:2d} {world['monsters'][mid]['name']:6s} 攻撃 +1 が効かない（比べられない）")
      continue
    cells = "  ".join(f"{k}+1 {v:6.2%}（{v / g['atk']:.2f}）" for k, v in g.items())
    print(f"  Lv{lv:2d} {world['monsters'][mid]['name']:6s} {cells}")
  avg = average(world)
  print(
    "Lv1〜20 × 相手（道の物の怪・強敵・ボス）の平均（攻撃 +1 を 1。素早さは 2 回攻撃だけ）: "
    + "  ".join(f"{k} {v:.2f}" for k, v in avg.items())
  )
  print("今の重み（season.json の item_points.weights）: " + "  ".join(f"{k} {v:g}" for k, v in weights.items()))
  return 0


if __name__ == "__main__":
  sys.exit(main())
