"""戦いを何度も試して、勝率を出す（強さの調整に使う。ゲームの状態は変えない）。

もののふはレベルアップの伸び（特性つき）だけで作り、装備は指定したものだけを持たせる。戦いの処理はゲームと同じ（game.party_battle）。
--personality all なら、ゲームにいる性格を、戦うたびに 1 人ずつ順に入れる（丸薬の使い方が性格で変わる。#242）。
--party で、何人かで力を合わせて戦う（霊場の主。#72）。

使い方:
  python tools/battle_sim.py                          # 酒呑童子に、Lv15・装備なし・丸薬 3 個で、特性ごとの勝率
  python tools/battle_sim.py --level 16 --gear steel_sword,cavalry_lance
  python tools/battle_sim.py --reach                  # 特性ごとに、勝率 50% に届くレベル（とレベルごとの勝率）
  python tools/battle_sim.py --strength               # 特性ごとに、五分（勝率 50%）で戦える主の強さ（HP と攻撃の倍率）
  python tools/battle_sim.py --monster wild_dog --level 1 --potions 0 --hp-loss  # 勝ったときに減る HP（道の物の怪の調整）
  python tools/battle_sim.py --monster road --fit-hp-loss 0.2   # 道の物の怪の HP・攻撃・防御を、減る HP 20% に合わせて探す
  python tools/battle_sim.py --monster rock_golem -n 5000
  python tools/battle_sim.py --level 20 --party 3 --personality all --trait lucky   # 3 人で主と戦う（性格は順に）
  python tools/battle_sim.py --level 20 --party 3 --personality all --trait lucky --strength --goal 0.2  # 勝率 20% の強さ
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


def make_hero(
  world: dict, trait: str, level: int, gear: list[str], potions: int, personality: str | None = None
) -> dict:
  """特性 trait のもののふを、レベルアップの伸びだけで level まで育てる（装備は gear、丸薬は potions 個）。
  personality が None なら性格のないもののふ（倒れる前に丸薬を使う）。"""
  state = game.new_state(world, "sim", "試し")
  state["trait"] = trait
  state["personality"] = personality
  state["hero"]["level"] = level
  game.set_base_stats(world, state)  # レベルと特性で決まる能力（レベルアップを繰り返したのと同じ）
  state["inventory"] = [{"id": iid, "skill": None} for iid in gear]
  state["hero"].update(potions=potions, hp=game.stats(world, state)["max_hp"], exp=0)
  return state


def fight(
  world: dict,
  monster: dict,
  trait: str,
  level: int,
  gear: list[str],
  potions: int,
  n: int,
  party: int = 1,
  personalities: list[str] | None = None,
) -> tuple[float, float | None]:
  """n 回戦って、(勝った割合, 勝ったときに減った HP の割合の平均。1 回も勝てなければ None)。
  party 人で力を合わせて戦う（みな同じ特性・レベル・装備・丸薬）。personalities を渡すと、i 回目の k 人目は
  personalities[(i + k) % 数] の性格（性格を 1 人ずつ順に入れる）。渡さなければ性格のないもののふ。
  乱数の種は 0〜n-1（k 人目は i * party + k）なので、同じ条件なら何度やっても同じ結果（1 人なら種は i）。
  減った HP は戦いの前後の差（何人かなら全員の平均）なので、丸薬を使うと、回復した分だけ少なく出る（道の物の怪は --potions 0 で測る）。"""
  wins, lost = 0, 0.0
  heroes = {p: make_hero(world, trait, level, gear, potions, p) for p in (personalities or [None])}
  max_hp = game.stats(world, next(iter(heroes.values())))["max_hp"]
  for seed in range(n):
    days = []
    for k in range(party):
      pers = personalities[(seed + k) % len(personalities)] if personalities else None
      state = copy.deepcopy(heroes[pers])
      day = game.Day(world, state, DAY, random.Random(seed * party + k))
      day.gain_exp = lambda exp: None  # 勝っても育たない（戦いの結果だけを見る）
      days.append(day)
    if game.party_battle(days, dict(monster)):
      wins += 1
      lost += sum(1 - d.state["hero"]["hp"] / max_hp for d in days) / party
  return wins / n, (lost / wins if wins else None)


def win_rate(
  world: dict,
  monster: dict,
  trait: str,
  level: int,
  gear: list[str],
  potions: int,
  n: int,
  party: int = 1,
  personalities: list[str] | None = None,
) -> float:
  """n 回戦って、勝った割合。"""
  return fight(world, monster, trait, level, gear, potions, n, party, personalities)[0]


def scaled(monster: dict, factor: float) -> dict:
  """HP と攻撃を factor 倍にした物の怪。"""
  return monster | {"hp": max(1, round(monster["hp"] * factor)), "atk": max(1, round(monster["atk"] * factor))}


def even_strength(
  world: dict,
  monster: dict,
  trait: str,
  level: int,
  gear: list[str],
  potions: int,
  n: int,
  party: int = 1,
  personalities: list[str] | None = None,
  goal: float = 0.5,
) -> float:
  """勝率がちょうど goal（既定 50%）になる、物の怪の強さの倍率（HP と攻撃を同じ倍率で変える。二分探索）。
  1.0 より大きいほど、元の物の怪より強い相手と goal の勝率で戦える。
  探す範囲（LOW〜HIGH 倍）の外なら、端の値を返す（is_capped で確かめられる）。"""
  low, high = LOW, HIGH
  for _ in range(14):
    mid = (low + high) / 2
    if win_rate(world, scaled(monster, mid), trait, level, gear, potions, n, party, personalities) >= goal:
      low = mid
    else:
      high = mid
  return (low + high) / 2


LOW, HIGH = 0.3, 6.0  # even_strength が探す倍率の範囲


def is_capped(factor: float) -> bool:
  """even_strength の結果が、探す範囲の端（それより先は測っていない）か。"""
  return factor < LOW * 1.01 or factor > HIGH * 0.99


ROAD_SHAPE = {"hp": 20, "atk": 7, "def": 2}  # 道の物の怪の HP・攻撃・防御の比（この比のまま大きさだけを変える）
FIT_SEARCH_N, FIT_PICK_N = 300, 1000  # fit_hp_loss で、倍率を探すときと、候補を比べるときに戦う回数


def shaped(monster: dict, factor: float) -> dict:
  """HP・攻撃・防御を、ROAD_SHAPE の factor 倍（四捨五入した整数）にした物の怪。ほかの値（速さ・性質など）はそのまま。"""
  return monster | {k: max(1 if k != "def" else 0, W.half_up(v * factor)) for k, v in ROAD_SHAPE.items()}


FIT_LOW, FIT_HIGH = 0.2, 6.0  # fit_hp_loss が探す倍率の範囲
FIT_MIN_WIN = 0.99  # fit_hp_loss で選ぶ物の怪は、この勝率以上で勝てる強さ


def fit_hp_loss(world: dict, monster: dict, level: int, goal: float) -> tuple[dict, bool]:
  """基準のもののふ（強運・装備なし・丸薬なし）が Lv level で戦うと、勝ったときに減る HP が goal に近くなる物の怪と、
  探す範囲（FIT_LOW〜FIT_HIGH 倍）の端に当たったか（当たったら、目標に届いていない）。
  1. ROAD_SHAPE に掛ける倍率を二分探索する（勝率 99% 以上で、減る HP が goal 未満なら、もっと強くする）
  2. 探し終えた範囲の両端（goal 未満のいちばん強い倍率と、そのすぐ上の倍率）を整数にして、
     勝率 99% 以上のもののうち、goal に近いほうを選ぶ（どちらも届かなければ弱いほう）"""

  def ok(m: dict, n: int) -> tuple[float, float | None]:
    return fight(world, m, "lucky", level, [], 0, n)

  low, high = FIT_LOW, FIT_HIGH
  for _ in range(14):
    mid = (low + high) / 2
    rate, lost = ok(shaped(monster, mid), FIT_SEARCH_N)
    if rate >= FIT_MIN_WIN and lost is not None and lost < goal:
      low = mid
    else:
      high = mid
  capped = low < FIT_LOW * 1.01 or high > FIT_HIGH * 0.99
  weak, strong = shaped(monster, low), shaped(monster, high)
  picked = []
  for m in (weak, strong):
    rate, lost = ok(m, FIT_PICK_N)
    if rate >= FIT_MIN_WIN and lost is not None:
      picked.append((abs(lost - goal), m))
  return (min(picked, key=lambda x: x[0])[1] if picked else weak), capped


def reach_level(rates: dict[int, float], goal: float = 0.5) -> int | None:
  """勝率が goal に届く、いちばん低いレベル（届かなければ None）。"""
  return next((lv for lv in sorted(rates) if rates[lv] >= goal), None)


def main() -> int:
  ap = argparse.ArgumentParser(description="戦いを何度も試して、勝率を出す")
  ap.add_argument(
    "--monster",
    default="den_lord",
    help="戦う物の怪の ID（既定: 酒呑童子）。--fit-hp-loss では road＝開放した道の物の怪すべて",
  )
  ap.add_argument("--level", type=int, help="もののふのレベル（既定: 15。--fit-hp-loss では物の怪のレベル）")
  ap.add_argument("--trait", help="特性の ID（既定: 全部の特性）")
  ap.add_argument("--gear", default="", help="装備の ID を , でつなぐ（既定: 装備なし）")
  ap.add_argument("--potions", type=int, help="丸薬の数（既定: 3）")
  ap.add_argument("-n", type=int, default=2000, help="戦う回数（既定: 2000）")
  ap.add_argument("--party", type=int, default=1, help="何人で力を合わせて戦うか（既定: 1）")
  ap.add_argument(
    "--personality", default="none", help="性格の ID か、all（性格を 1 人ずつ順に入れる）、none（性格なし。既定）"
  )
  ap.add_argument("--goal", type=float, default=0.5, help="--strength で探す勝率（既定: 0.5）")
  ap.add_argument("--reach", action="store_true", help="特性ごとに、勝率 50%% に届くレベルを出す（Lv10〜最大レベル）")
  ap.add_argument("--strength", action="store_true", help="特性ごとに、五分で戦える物の怪の強さの倍率を出す")
  ap.add_argument("--hp-loss", action="store_true", help="特性ごとに、勝率と、勝ったときに減る HP の平均を出す")
  ap.add_argument(
    "--fit-hp-loss",
    type=float,
    metavar="GOAL",
    help="同じ Lv のもののふ（強運・装備なし・丸薬なし）が勝ったときに減る HP が GOAL（0.2 なら 20%%）になる HP・攻撃・防御を探す",
  )
  a = ap.parse_args()
  world = W.load_world()
  if a.fit_hp_loss is not None:
    if a.trait is not None or a.gear or a.potions is not None:
      raise SystemExit("--fit-hp-loss は強運・装備なし・丸薬なしで測るので、--trait・--gear・--potions は使えません")
    return fit_main(world, a)
  a.level = 15 if a.level is None else a.level
  a.trait = "all" if a.trait is None else a.trait
  a.potions = 3 if a.potions is None else a.potions
  if a.monster not in world["monsters"]:
    raise SystemExit(f"物の怪 {a.monster!r} がいません")
  gear = [g for g in a.gear.split(",") if g]
  for g in gear:
    if g not in world["items"]:
      raise SystemExit(f"装備 {g!r} がありません")
  if a.trait != "all" and a.trait not in world["season"]["hero_traits"]:
    raise SystemExit(f"特性 {a.trait!r} がありません（{'/'.join(sorted(world['season']['hero_traits']))}）")
  traits = sorted(world["season"]["hero_traits"]) if a.trait == "all" else [a.trait]
  pers_all = sorted(world["season"].get("personalities") or {})
  if a.personality == "all":
    pers = pers_all
  elif a.personality == "none":
    pers = None
  elif a.personality in pers_all:
    pers = [a.personality]
  else:
    raise SystemExit(f"性格 {a.personality!r} がありません（{'/'.join(pers_all)}・all・none）")
  if a.party < 1 or not 0 < a.goal < 1:
    raise SystemExit("--party は 1 以上、--goal は 0 より大きく 1 より小さい値にしてください")
  opt = {"party": a.party, "personalities": pers}
  monster = world["monsters"][a.monster]
  names = world["season"]["hero_traits"]
  print(
    f"{monster['name']} と戦う（装備: {'・'.join(world['items'][g]['name'] for g in gear) or 'なし'}、"
    f"丸薬 {a.potions} 個、{a.party} 人、性格 {a.personality}、{a.n} 回ずつ）"
  )
  if a.strength:
    # 強運＝能力が伸びない（ふつう）
    base = even_strength(world, monster, "lucky", a.level, gear, a.potions, a.n, goal=a.goal, **opt)
    for t in traits:
      f = even_strength(world, monster, t, a.level, gear, a.potions, a.n, goal=a.goal, **opt)
      note = "（探す範囲の端。正しく測れていない）" if is_capped(f) or is_capped(base) else ""
      print(
        f"  {names[t]['name']:4s} Lv{a.level}: 勝率 {a.goal:.0%} で戦える強さ ×{f:.3f}（ふつうの {f / base:.0%}）{note}"
        f"  HP{scaled(monster, f)['hp']} 攻{scaled(monster, f)['atk']}"
      )
    return 0
  if a.hp_loss:
    for t in traits:
      rate, lost = fight(world, monster, t, a.level, gear, a.potions, a.n, **opt)
      shown = "—（勝てない）" if lost is None else f"{lost:.0%}"
      print(f"  {names[t]['name']:4s} Lv{a.level}: 勝率 {rate:.1%}  勝ったときに減る HP {shown}")
    return 0
  if not a.reach:
    for t in traits:
      print(
        f"  {names[t]['name']:4s} Lv{a.level}: "
        f"勝率 {win_rate(world, monster, t, a.level, gear, a.potions, a.n, **opt):.1%}"
      )
    return 0
  levels = range(10, world["season"]["max_level"] + 1)
  print("  特性   50%に届くLv  " + " ".join(f"Lv{lv:<4d}" for lv in levels))
  for t in traits:
    rates = {lv: win_rate(world, monster, t, lv, gear, a.potions, a.n, **opt) for lv in levels}
    reach = reach_level(rates)
    print(
      f"  {names[t]['name']:4s}  {('Lv' + str(reach)) if reach else '届かない':>8s}    "
      + " ".join(f"{rates[lv]:5.0%} " for lv in levels)
    )
  return 0


def fit_main(world: dict, a: argparse.Namespace) -> int:
  """--fit-hp-loss: 物の怪ごとに、目標に合う HP・攻撃・防御と、そのときの経験値・お金を出す（データは書き換えない）。"""
  if not 0 < a.fit_hp_loss < 1:
    raise SystemExit("--fit-hp-loss は 0 より大きく 1 より小さい値にしてください（0.2 なら 20%）")
  if a.monster == "road":
    ids = sorted({m for i in world["road_info"].values() for m, _ in i["monsters"]})
    ids.sort(key=lambda m: (world["monsters"][m]["level"], m))
  elif a.monster in world["monsters"]:
    if not W.on_road(world["monsters"][a.monster]):  # 大物・主は 20:7:2 の比ではない
      raise SystemExit(f"{world['monsters'][a.monster]['name']} は大物か主です（--fit-hp-loss は道の物の怪だけ）")
    ids = [a.monster]
  else:
    raise SystemExit(f"物の怪 {a.monster!r} がいません（道の物の怪すべては road）")
  print(f"勝ったときに減る HP {a.fit_hp_loss:.0%} に合わせる（強運・装備なし・丸薬なし、{a.n} 回で確かめる）")
  for mid in ids:
    m = world["monsters"][mid]
    level = m["level"] if a.level is None else a.level
    fit, capped = fit_hp_loss(world, m, level, a.fit_hp_loss)
    rate, lost = fight(world, fit, "lucky", level, [], 0, a.n)
    exp, gold = W.monster_reward(fit, world["traits"], world["season"]["monster_reward"])
    same = all(fit[k] == m[k] for k in ROAD_SHAPE)
    shown = "—" if lost is None else f"{lost:.0%}"
    print(
      f"  Lv{level} {m['name']}（{mid}）: HP{fit['hp']} 攻{fit['atk']} 防{fit['def']}  "
      f"勝率 {rate:.1%} 減る HP {shown}  EXP{exp} {gold}文"
      + ("" if same else f"  ← 今は HP{m['hp']} 攻{m['atk']} 防{m['def']}")
      + ("  （探す範囲の端。目標に届いていない）" if capped else "")
    )
  return 0


if __name__ == "__main__":
  sys.exit(main())
