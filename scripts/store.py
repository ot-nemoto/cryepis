"""勇者管理シート・勇者の状態・ギルドの掲示板（.state/）の読み書き。
rpg.py（毎日の実行）と website.py（サイトの書き出し）の両方から使う。"""

from __future__ import annotations

import json
from pathlib import Path

import common
import game

HEROES_PATH = common.ROOT / ".state" / "heroes.json"
HERO_DIR = common.ROOT / ".state" / "heroes"
GUILD_PATH = common.ROOT / ".state" / "guild.json"  # 全員で共有する、町ごとのギルドの掲示板（早い者勝ち）


def hero_state_path(hero_id: str) -> Path:
  return HERO_DIR / f"{hero_id}.json"


def load_heroes(path: Path | None = None) -> list[dict]:
  path = path or HEROES_PATH
  try:
    return json.loads(path.read_text(encoding="utf-8"))["heroes"]
  except FileNotFoundError:
    return []
  except (json.JSONDecodeError, KeyError) as e:
    raise SystemExit(f"{path} が読めません（マージ衝突などを確認してください）: {e!r}") from e


def save_heroes(heroes: list[dict], path: Path | None = None) -> None:
  path = path or HEROES_PATH
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps({"heroes": heroes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_entries(world: dict, retired: bool = False) -> list[tuple[dict, dict]]:
  """現役の勇者（retired なら引退した勇者）の今の状態（冒険は進めない）。"""
  entries = []
  for hero in [h for h in load_heroes() if h["active"] != retired]:
    path = hero_state_path(hero["id"])
    if path.exists():
      entries.append((hero, game.load_state(world, path, hero["id"], hero["name"])))
  return entries


def load_guild(path: Path | None = None) -> dict:
  """町ごとのギルドの掲示板（日をまたいで持ち越す）。前の形式（受けられた依頼の一覧だけ）なら、新しく貼り直す。"""
  path = path or GUILD_PATH
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return game.new_guild()
  except json.JSONDecodeError as e:
    raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}") from e
  if not isinstance(data, dict) or not isinstance(data.get("towns"), dict):
    return game.new_guild()
  return {**game.new_guild(), **data}


def save_guild(guild: dict) -> None:
  """掲示板を保存する（毎日コミットするので、差分が読みやすいよう依頼 1 件を 1 行にする）。"""
  one = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
  towns = ",\n".join(
    f" {one(t)}: [\n" + ",\n".join(f"  {one(e)}" for e in es) + "\n ]" for t, es in guild.get("towns", {}).items()
  )
  head = {k: v for k, v in guild.items() if k != "towns"}
  text = "{\n" + "".join(f" {one(k)}: {one(v)},\n" for k, v in head.items()) + ' "towns": {\n' + towns + "\n }\n}\n"
  GUILD_PATH.parent.mkdir(parents=True, exist_ok=True)
  GUILD_PATH.write_text(text, encoding="utf-8")
