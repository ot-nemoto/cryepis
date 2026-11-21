"""戦いを何度も試して、勝率を出す（強さの調整に使う。ゲームの状態は変えない）。

勇者はレベルアップの伸び（特性つき）だけで作り、装備は指定したものだけを持たせる。戦いの処理はゲームと同じ（game.Day.battle）。

使い方:
  python tools/battle_sim.py                          # 巣穴の主に、Lv15・装備なし・回復薬 3 個で、特性ごとの勝率
  python tools/battle_sim.py --level 16 --gear steel_sword,cavalry_lance
  python tools/battle_sim.py --reach                  # 特性ごとに、勝率 50% に届くレベル（とレベルごとの勝率）
  python tools/battle_sim.py --strength               # 特性ごとに、五分（勝率 50%）で戦えるボスの強さ（HP と攻撃の倍率）
  python tools/battle_sim.py --monster wild_dog --level 1 --potions 0 --hp-loss  # 勝ったときに減る HP（道の魔物の調整）
  python tools/battle_sim.py --monster rock_golem -n 5000
"""

from __future__ import annotations

import argparse
import copy
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import game  # noqa: E402
import world as W  # noqa: E402

DAY = date(2026, 12, 1)


def make_hero(world: dict, trait: str, level: int, gear: list[str], potions: int) -> dict:
  """特性 trait の勇者を、レベルアップの伸びだけで level まで育てる（装備は gear、回復薬は potions 個）。"""
  state = game.new_state(world, "sim", "試し")
  state["trait"] = trait
  state["hero"]["level"] = level
  game.set_base_stats(world, state)  # レベルと特性で決まる能力（レベルアップを繰り返したのと同じ）
  state["inventory"] = {iid: 1 for iid in gear}
  state["hero"].update(potions=potions, hp=game.stats(world, state)["max_hp"], exp=0)
  return state


def fight(
  world: dict, monster: dict, trait: str, level: int, gear: list[str], potions: int, n: int
) -> tuple[float, float | None]:
  """n 回戦って、(勝った割合, 勝ったときに減った HP の割合の平均。1 回も勝てなければ None)。
  乱数の種は 0〜n-1 なので、同じ条件なら何度やっても同じ結果。
  減った HP は戦いの前後の差なので、回復薬を使うと、回復した分だけ少なく出る（道の魔物は --potions 0 で測る）。"""
  wins, lost = 0, 0.0
  hero = make_hero(world, trait, level, gear, potions)
  max_hp = game.stats(world, hero)["max_hp"]
  for seed in range(n):
    state = copy.deepcopy(hero)
    day = game.Day(world, state, DAY, random.Random(seed))
    day.gain_exp = lambda exp: None  # 勝っても育たない（戦いの結果だけを見る）
    if day.battle(dict(monster)):
      wins += 1
      lost += 1 - state["hero"]["hp"] / max_hp
  return wins / n, (lost / wins if wins else None)


def win_rate(world: dict, monster: dict, trait: str, level: int, gear: list[str], potions: int, n: int) -> float:
  """n 回戦って、勝った割合。"""
  return fight(world, monster, trait, level, gear, potions, n)[0]


def scaled(monster: dict, factor: float) -> dict:
  """HP と攻撃を factor 倍にした魔物。"""
  return monster | {"hp": max(1, round(monster["hp"] * factor)), "atk": max(1, round(monster["atk"] * factor))}


def even_strength(world: dict, monster: dict, trait: str, level: int, gear: list[str], potions: int, n: int) -> float:
  """勝率がちょうど 50% になる、魔物の強さの倍率（HP と攻撃を同じ倍率で変える。二分探索）。
  1.0 より大きいほど、元の魔物より強い相手と五分に戦える。
  探す範囲（LOW〜HIGH 倍）の外なら、端の値を返す（is_capped で確かめられる）。"""
  low, high = LOW, HIGH
  for _ in range(14):
    mid = (low + high) / 2
    if win_rate(world, scaled(monster, mid), trait, level, gear, potions, n) >= 0.5:
      low = mid
    else:
      high = mid
  return (low + high) / 2


LOW, HIGH = 0.3, 3.0  # even_strength が探す倍率の範囲


def is_capped(factor: float) -> bool:
  """even_strength の結果が、探す範囲の端（それより先は測っていない）か。"""
  return factor < LOW * 1.01 or factor > HIGH * 0.99


def reach_level(rates: dict[int, float], goal: float = 0.5) -> int | None:
  """勝率が goal に届く、いちばん低いレベル（届かなければ None）。"""
  return next((lv for lv in sorted(rates) if rates[lv] >= goal), None)


def main() -> int:
  ap = argparse.ArgumentParser(description="戦いを何度も試して、勝率を出す")
  ap.add_argument("--monster", default="den_lord", help="戦う魔物の ID（既定: 巣穴の主）")
  ap.add_argument("--level", type=int, default=15, help="勇者のレベル（既定: 15）")
  ap.add_argument("--trait", default="all", help="特性の ID（既定: all＝全部の特性）")
  ap.add_argument("--gear", default="", help="装備の ID を , でつなぐ（既定: 装備なし）")
  ap.add_argument("--potions", type=int, default=3, help="回復薬の数（既定: 3）")
  ap.add_argument("-n", type=int, default=2000, help="戦う回数（既定: 2000）")
  ap.add_argument("--reach", action="store_true", help="特性ごとに、勝率 50%% に届くレベルを出す（Lv10〜最大レベル）")
  ap.add_argument("--strength", action="store_true", help="特性ごとに、五分で戦える魔物の強さの倍率を出す")
  ap.add_argument("--hp-loss", action="store_true", help="特性ごとに、勝率と、勝ったときに減る HP の平均を出す")
  a = ap.parse_args()
  world = W.load_world()
  if a.monster not in world["monsters"]:
    raise SystemExit(f"魔物 {a.monster!r} がいません")
  gear = [g for g in a.gear.split(",") if g]
  for g in gear:
    if g not in world["items"]:
      raise SystemExit(f"装備 {g!r} がありません")
  if a.trait != "all" and a.trait not in world["season"]["hero_traits"]:
    raise SystemExit(f"特性 {a.trait!r} がありません（{'/'.join(sorted(world['season']['hero_traits']))}）")
  traits = sorted(world["season"]["hero_traits"]) if a.trait == "all" else [a.trait]
  monster = world["monsters"][a.monster]
  names = world["season"]["hero_traits"]
  print(
    f"{monster['name']} と戦う（装備: {'・'.join(world['items'][g]['name'] for g in gear) or 'なし'}、"
    f"回復薬 {a.potions} 個、{a.n} 回ずつ）"
  )
  if a.strength:
    base = even_strength(world, monster, "lucky", a.level, gear, a.potions, a.n)  # 強運＝能力が伸びない（ふつう）
    for t in traits:
      f = even_strength(world, monster, t, a.level, gear, a.potions, a.n)
      note = "（探す範囲の端。正しく測れていない）" if is_capped(f) or is_capped(base) else ""
      print(f"  {names[t]['name']:4s} Lv{a.level}: 五分で戦える強さ ×{f:.3f}（ふつうの {f / base:.0%}）{note}")
    return 0
  if a.hp_loss:
    for t in traits:
      rate, lost = fight(world, monster, t, a.level, gear, a.potions, a.n)
      shown = "—（勝てない）" if lost is None else f"{lost:.0%}"
      print(f"  {names[t]['name']:4s} Lv{a.level}: 勝率 {rate:.1%}  勝ったときに減る HP {shown}")
    return 0
  if not a.reach:
    for t in traits:
      print(
        f"  {names[t]['name']:4s} Lv{a.level}: 勝率 {win_rate(world, monster, t, a.level, gear, a.potions, a.n):.0%}"
      )
    return 0
  levels = range(10, world["season"]["max_level"] + 1)
  print("  特性   50%に届くLv  " + " ".join(f"Lv{lv:<4d}" for lv in levels))
  for t in traits:
    rates = {lv: win_rate(world, monster, t, lv, gear, a.potions, a.n) for lv in levels}
    reach = reach_level(rates)
    print(
      f"  {names[t]['name']:4s}  {('Lv' + str(reach)) if reach else '届かない':>8s}    "
      + " ".join(f"{rates[lv]:5.0%} " for lv in levels)
    )
  return 0


if __name__ == "__main__":
  sys.exit(main())
