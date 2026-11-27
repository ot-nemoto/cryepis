"""戦闘の試験と、調練ページに載せる基準勝率。"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile

import common
import game
import world as W

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


TRIALS = 10_008
POTIONS = 3
TRAIT = "lucky"
TARGETS = (("sea_monk", 15, 2), ("great_eagle", 15, 2), ("rock_golem", 15, 2), ("yeti", 15, 2), ("den_lord", 20, 3))


def measure_opponent(target: tuple[str, int, int], *, world: dict, trials: int, personalities: list[str]) -> dict:
  mid, level, party = target
  rate, _ = fight(world, world["monsters"][mid], TRAIT, level, [], POTIONS, trials, party, personalities)
  return {"monster": mid, "level": level, "party": party, "baseline": round(rate * trials)}


def build_report(world: dict, trials: int = TRIALS) -> dict:
  """既存の戦闘処理で調練を試し、勝利数と比較条件を返す。"""
  personalities = sorted(world["season"]["personalities"])
  targets = [t for t in TARGETS if t[0] in world["monsters"] and t[1] <= world["season"]["max_level"]]
  measure = partial(measure_opponent, world=world, trials=trials, personalities=personalities)
  if trials < TRIALS or len(targets) < 2:
    opponents = list(map(measure, targets))
  else:
    with ProcessPoolExecutor(max_workers=len(targets)) as pool:
      opponents = list(pool.map(measure, targets))
  return {"trials": trials, "trait": TRAIT, "potions": POTIONS, "opponents": opponents}


def cache_key(world: dict) -> str:
  """戦闘・世界・検証条件の同じ結果だけを再利用する。"""
  root = common.ROOT
  inputs = [root / "scripts" / f"{stem}.py" for stem in ("common", "game", "training", "world")] + sorted(
    (root / "world").glob("*.json")
  )
  key = hashlib.sha256(sys.version.encode())
  for path in inputs:
    key.update(path.name.encode())
    key.update(path.read_bytes())
  battle_data = ("season", "starts", "monsters", "traits", "elements", "rarities", "items", "skills")
  key.update(json.dumps({name: world[name] for name in battle_data}, sort_keys=True).encode())
  return f"cryepis-training-{key.hexdigest()}"


def cached_report(world: dict, path: Path | None = None) -> dict:
  if path is None:
    return build_report(world)
  key = cache_key(world)
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
    data = None
  if isinstance(data, dict) and data.get("key") == key and isinstance(data.get("report"), dict):
    print("調練の結果をキャッシュから読み込み")
    return data["report"]

  report = build_report(world)
  path.parent.mkdir(parents=True, exist_ok=True)
  with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
    json.dump({"key": key, "report": report}, tmp, ensure_ascii=False)
  Path(tmp.name).replace(path)
  return report


if __name__ == "__main__":
  print(cache_key(W.load_world()))
