"""戦闘の比較試験と、調練ページに載せる再現可能な結果。"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

import common
import game

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


class TrialDay(game.Day):
  """武具の能力に影響を与えずに、指定したスキルを試す。"""

  def __init__(self, world, state, today, rng, trial_skill):
    super().__init__(world, state, today, rng)
    self.trial_skill = trial_skill

  def skill(self, sid, pool=None):
    if sid == self.trial_skill:
      return self.world["skills"][sid].get("value", True)
    return super().skill(sid, pool)


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
  *,
  skill: str | None = None,
  carriers: int = 0,
) -> tuple[float, float | None]:
  """n 回戦って、(勝った割合, 勝ったときに減った HP の割合の平均。1 回も勝てなければ None)。
  party 人で力を合わせて戦う（みな同じ特性・レベル・装備・丸薬）。personalities を渡すと、i 回目の k 人目は
  personalities[(i + k) % 数] の性格（性格を 1 人ずつ順に入れる）。渡さなければ性格のないもののふ。
  乱数の種は 0〜n-1（k 人目は i * party + k）なので、同じ条件なら何度やっても同じ結果（1 人なら種は i）。
  skill と carriers は効果だけを試すスキルと人数。適用する位置は性格の一巡ごとにずらす。
  減った HP は戦いの前後の差（何人かなら全員の平均）なので、丸薬を使うと、回復した分だけ少なく出る（道の物の怪は --potions 0 で測る）。"""
  wins, lost = 0, 0.0
  heroes = {p: make_hero(world, trait, level, gear, potions, p) for p in (personalities or [None])}
  max_hp = game.stats(world, next(iter(heroes.values())))["max_hp"]
  for seed in range(n):
    days = []
    first_carrier = (seed // len(heroes)) % party
    for k in range(party):
      pers = personalities[(seed + k) % len(personalities)] if personalities else None
      state = copy.deepcopy(heroes[pers])
      rng = random.Random(seed * party + k)
      if skill and (k - first_carrier) % party < carriers:
        day = TrialDay(world, state, DAY, rng, skill)
      else:
        day = game.Day(world, state, DAY, rng)
      day.gain_exp = lambda exp: None  # 勝っても育たない（戦いの結果だけを見る）
      days.append(day)
    if game.party_battle(days, dict(monster)):
      wins += 1
      lost += sum(1 - d.state["hero"]["hp"] / max_hp for d in days) / party
  return wins / n, (lost / wins if wins else None)


TRIALS = 10_008
POTIONS = 3
TRAIT = "lucky"
TARGETS = (("yeti", 15, 2), ("rock_golem", 15, 2), ("great_eagle", 15, 2), ("den_lord", 20, 3))
SPEED_GEAR = ("cross_spear", "iron_face_guard", "yamabushi_robe", "soaring_leaf_charm")
ELEMENT_GEAR = ("cross_spear", "crimson_grass_charm")
SKILL_CASES = {
  "yeti": (("combo", SPEED_GEAR), ("focus", ())),
  "rock_golem": (("pierce", ()), ("focus", ())),
  "great_eagle": (("weak_point", ELEMENT_GEAR), ("focus", ())),
  "den_lord": (("combo", SPEED_GEAR), ("pierce", ()), ("focus", ())),
}


def measure_opponent(world: dict, mid: str, level: int, party: int, trials: int, personalities: list[str]) -> dict:
  monster = world["monsters"][mid]

  def wins(gear: tuple, skill: str | None = None, carriers: int = 0) -> int:
    rate, _ = fight(
      world, monster, TRAIT, level, list(gear), POTIONS, trials, party, personalities, skill=skill, carriers=carriers
    )
    return round(rate * trials)

  baseline = wins(())
  comparisons = [
    {
      "skill": skill,
      "gear": list(gear),
      "normal": wins(gear) if gear else baseline,
      "one": wins(gear, skill, 1),
      "all": wins(gear, skill, party),
    }
    for skill, gear in SKILL_CASES[mid]
    if skill in world["skills"] and all(iid in world["items"] for iid in gear)
  ]
  return {"monster": mid, "level": level, "party": party, "baseline": baseline, "comparisons": comparisons}


def build_report(world: dict, trials: int = TRIALS) -> dict:
  """既存の戦闘処理で調練を試し、勝利数と比較条件を返す。"""
  personalities = sorted(world["season"]["personalities"])
  opponents = [
    measure_opponent(world, mid, level, party, trials, personalities)
    for mid, level, party in TARGETS
    if mid in world["monsters"] and level <= world["season"]["max_level"]
  ]
  return {"trials": trials, "trait": TRAIT, "potions": POTIONS, "opponents": opponents}


def cache_key() -> str:
  """戦闘・世界・検証条件の同じ結果だけを再利用する。"""
  root = common.ROOT
  inputs = [root / "scripts" / f"{stem}.py" for stem in ("common", "game", "training", "world")] + sorted(
    (root / "world").glob("*.json")
  )
  key = hashlib.sha256(sys.version.encode())
  for path in inputs:
    key.update(path.name.encode())
    key.update(path.read_bytes())
  return f"cryepis-training-{key.hexdigest()}"


def cached_report(world: dict, path: Path | None = None) -> dict:
  if path is None:
    return build_report(world)
  key = cache_key()
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
  print(cache_key())
