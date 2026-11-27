#!/usr/bin/env python3
"""シーズン制のテキスト RPG。もののふ（最大 MAX_HEROES 人）を 1 日分ずつ進め、番付の Gist を更新する。

もののふは heroes.json（もののふ管理シート）に登録する。世界（MAP・敵・武具・依頼）は world/ の下のデータで、
1 日分の進め方は game.py にある。もののふは毎日、乱数で決まる回数だけ行動し、町の口入れ屋の依頼をこなして育つ。
シーズン（world/season.json）が変わると、武具以外は最初に戻る。
workflow が動かなかった日があれば、次の実行で最大 CATCH_UP_DAYS 日分までさかのぼって進める。

状態は .state/heroes.json（もののふの一覧）と .state/heroes/<id>.json（もののふごとの状態）に保存し、
もののふごとの詳細（ステータス・装備・持ち物・日誌）と絵図は、サイト（scripts/website.py）で見る（Gist は作らない）。
番付の Gist の ID は .state/board.json に記録する（なければ毎日の実行で作る）。
workflow の最後のステップで master にコミットする。
乱数は「日付＋シーズン＋もののふの ID」を種にするので、同じ日に何度実行しても結果は同じ（その日の分が処理済みなら進めない）。

環境変数:
  GIST_PAT   番付の Gist の作成・更新に使う（gist スコープの Classic PAT）

使い方:
  python scripts/rpg.py                          # 登録済みのもののふを全員 1 日分進めて Gist を更新する（毎日の workflow）
  python scripts/rpg.py --dry-run --actions 4    # 記録せずに 1 日分を試す
  python scripts/rpg.py --new-game 名前                # 新しいもののふを登録する
  python scripts/rpg.py --retire 名前または ID         # もののふを引退させる（毎日の更新の対象から外れる）
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402
import game  # noqa: E402
import mapsvg  # noqa: E402
import store  # noqa: E402
import view  # noqa: E402
import world as W  # noqa: E402

BOARD_PATH = common.ROOT / ".state" / "board.json"
BOARD_FILENAME = "hero-ranking.txt"
MAX_HEROES = 30  # 同時に冒険できる（引退していない）もののふの数
BOARD_NAME_WIDTH = 8  # 番付のカードに出す名前の表示幅
MEDALS = ("🥇", "🥈", "🥉")
NAME_WIDTH_MAX = 32  # もののふの名前の表示幅（全角 = 2。全角 16 文字・半角 32 文字）
CATCH_UP_DAYS = 7  # 実行されなかった日をさかのぼって進める最大の日数


def load_world() -> dict:
  return W.load_world()


# ---- もののふ管理シート（heroes.json）-----------------------------------------------------


def clean_name(raw: str) -> str:
  """制御文字を除き、表示幅 NAME_WIDTH_MAX 以内に切り詰める。空なら SystemExit。"""
  name = "".join(c for c in raw if c.isprintable()).strip()
  while common.width(name) > NAME_WIDTH_MAX:
    name = name[:-1]
  name = name.strip()
  if not name:
    raise SystemExit("もののふの名前を指定してください")
  if re.fullmatch(r"h\d+", name):  # 引退の指定で ID と取り違えないように
    raise SystemExit(f"「{name}」はもののふの ID と紛らわしいので、名前には使えません")
  return name


def md_escape(text: str) -> str:
  """GitHub Actions のサマリー（Markdown）に名前をそのまま出さないよう、記号をエスケープする。"""
  return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~&])", r"\\\1", text)


# ---- 表示 ---------------------------------------------------------------------------


def page_url(hero_id: str) -> str:
  """もののふの詳細のページ（サイトの heroes/<id>.html）の URL。GitHub Actions の外では、サイトの中のパス。"""
  return f"{site_url() or ''}heroes/{hero_id}.html"


def site_url() -> str | None:
  """設定された公開先の URL。未設定の GitHub Actions では GitHub Pages の URL。"""
  configured = os.environ.get("RPG_SITE_URL")
  if configured is not None:
    return configured.rstrip("/") + "/" if configured else None
  repo = os.environ.get("GITHUB_REPOSITORY", "")
  if "/" not in repo:
    return None
  owner, name = repo.split("/", 1)
  if name.lower() == f"{owner.lower()}.github.io":  # ユーザー・組織のサイトのリポジトリは、ドメインの直下
    return f"https://{owner.lower()}.github.io/"
  return f"https://{owner.lower()}.github.io/{name}/"


def hero_link(hero_id: str) -> str:
  """登録・引退のときの案内に出す、もののふの詳細のページへの案内（ページはサイトの次の公開で書き出される）。"""
  url = site_url()
  return (
    f"サイトのもののふのページ（{url}heroes/{hero_id}.html。次のサイトの公開のあとに見られる）"
    if url
    else "サイトのもののふのページ"
  )


def write_map_grid(world: dict, out: str) -> int:
  """座標の目盛りと場所の ID を重ねた確認用の地図を out に書き出す（pos を決めるときに使う。コミットしない）。"""
  svg = mapsvg.render(world, store.current_entries(world), grid=True)
  if svg is None:
    raise SystemExit("world/map.json に layout がないので、地図を描けません")
  Path(out).write_text(svg, encoding="utf-8")
  print(f"{out} を書き出しました（ブラウザで開いて、場所の pos を決めるのに使ってください）")
  return 0


# ---- 番付（Pin 用の Gist）------------------------------------------------------


def build_board(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  """Pin 用の番付の Gist の中身。entries は現役のもののふの (heroes.json の行, 状態)。"""
  season = game.season_of(world, today)
  first, last = game.season_range(world, season)
  views = [(hero, view.season_view(world, state, season)) for hero, state in entries]
  if today < first:
    when = "開幕前"
  else:
    when = f"Day {(today - first).days + 1}/{(last - first).days + 1}"
  lines = [f"🏆 シーズン{season}  {when}  もののふ{len(views)}人"]
  for _, label, key, value, _ in view.RANKINGS:
    if label is None:
      continue
    top = [
      f"{MEDALS[min(r, 3) - 1]}{view.shorten(h['name'], BOARD_NAME_WIDTH)} {value(s)}"
      for r, h, s in view.ranked(views, key)[:3]
    ]
    lines.append(f"{label} " + (" ".join(top) or "まだいない"))
  today_news = view.news(views, today, BOARD_NAME_WIDTH)
  lines.append("📰 " + (today_news[0] if today_news else "今日も、もののふたちは冒険中…"))
  card = [view.fit(line) for line in lines]

  detail = ["", f"── シーズン{season}（{first}〜{last}）──"]
  if site_url():
    detail.append(
      f"サイト「{common.SITE_TITLE}」（絵図・番付・もののふ・町・口入れ屋・武具・物の怪・手引き）: {site_url()}"
    )
  for title, _, key, value, unit in view.RANKINGS:
    detail += ["", f"── {title} ──"]
    detail += [
      f"{r:>2}. {h['name']}  " + (f"Lv.{value(s)}" if title == "レベル" else f"{value(s)}{unit}")
      for r, h, s in view.ranked(views, key)[: view.RANKING_TOP]
    ]
  detail += ["", "── 今日の出来事 ──"] + (today_news or ["まだない"])
  detail += ["", "── もののふたち ──"]
  for hero, state in views:
    detail += [""] + hero_summary(world, hero, state, today)
  past = sorted({r["season"] for _, state in entries for r in state["seasons"]})
  if past:
    detail += ["", "── 歴代のシーズン ──"]
    for n in past:
      recs = [(hero, r) for hero, state in entries for r in state["seasons"] if r["season"] == n]
      lv_hero, lv = max(recs, key=lambda x: x[1]["level"])
      kill_hero, kill = max(recs, key=lambda x: x[1]["kills"])
      detail.append(
        f"シーズン{n}: 最高レベル {lv_hero['name']} Lv.{lv['level']}  討伐数 {kill_hero['name']} {kill['kills']}体"
      )
  return "\n".join(card + detail) + "\n"


def hero_summary(world: dict, entry: dict, state: dict, today: date) -> list[str]:
  """番付の本文に載せる、もののふごとの短い欄（entry は heroes.json の行）。"""
  hero, s = state["hero"], game.stats(world, state)
  rares = sorted(
    {x["id"] for x in game.owned(state) if world["items"][x["id"]]["rarity"] != "common"},
    key=lambda i: (-world["rarities"].index(world["items"][i]["rarity"]), i),
  )
  last = state["last"]
  events = view.highlights(last["events"]) if last and last["date"] == today.isoformat() and last["events"] else []
  return [
    f"■ {entry['name']}  Lv.{hero['level']}  {view.location(world, state)}",
    f"  HP {hero['hp']}/{s['max_hp']}  攻{s['atk']} 防{s['def']}  {hero['gold']}文  丸薬 {hero['potions']}",
    f"  装備: {view.gear_text(world, state)}",
    f"  レア: {'、'.join(view.item_label(world, i) for i in rares) or 'まだない'}",
    f"  今日: {'、'.join(events) or '—'}",
    f"  詳細: {page_url(entry['id'])}",
  ]


def board_description(world: dict, today: date) -> str:
  return f"もののふ番付 シーズン{game.season_of(world, today)}"


def load_board(path: Path | None = None) -> dict | None:
  path = path or BOARD_PATH
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return None
  except json.JSONDecodeError as e:
    raise SystemExit(f"{path} が JSON として読めません（マージ衝突などを確認してください）: {e}") from e


def create_board(content: str, description: str, today: date, token: str | None) -> None:
  gist = common.create_gist(token, BOARD_FILENAME, content, description)
  board = {"gist_id": gist["id"], "gist_url": gist["html_url"], "created": today.isoformat()}
  BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
  BOARD_PATH.write_text(json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  write_summary(
    f"## 番付の Gist を作りました\n\n{gist['html_url']}\n\nプロフィールの **Customize your pins** で Pin できます。"
  )


def update_board(world: dict, entries: list[tuple[dict, dict]], today: date, dry_run: bool, token: str | None) -> None:
  """番付の Gist を更新する。まだないか、消えていたら（404）作って board.json に記録する。"""
  content = build_board(world, entries, today)
  print(content)
  if dry_run:
    return
  description = board_description(world, today)
  board = load_board()
  if board is None:
    create_board(content, description, today, token)
    return
  try:
    common.update_gist(board["gist_id"], token, BOARD_FILENAME, content, description=description)
  except common.GistAPIError as e:
    if e.status != 404:
      raise
    common.warn(f"番付の Gist（{board['gist_url']}）が見つからないので、新しく作ります。Pin し直してください")
    create_board(content, description, today, token)


# ---- 実行 ---------------------------------------------------------------------------
def pending_days(state: dict, today: date) -> list[date]:
  """進める日の一覧（古い順）。last_date の翌日から today まで、最大 CATCH_UP_DAYS 日。"""
  if state["last_date"] is None:
    return [today]
  first = max(date.fromisoformat(state["last_date"]) + timedelta(days=1), today - timedelta(days=CATCH_UP_DAYS - 1))
  return [first + timedelta(days=i) for i in range((today - first).days + 1)]


def write_summary(text: str) -> None:
  print(text)
  summary = os.environ.get("GITHUB_STEP_SUMMARY")
  if summary:
    with open(summary, "a", encoding="utf-8") as f:
      f.write(text + "\n")


def new_game(world: dict, raw_name: str, today: date) -> int:
  """新しいもののふを登録し、状態を作る（冒険は翌日から進む）。"""
  name = clean_name(raw_name)
  heroes = store.load_heroes()
  active = [h for h in heroes if h["active"]]
  if len(active) >= MAX_HEROES:
    raise SystemExit(f"もののふは最大 {MAX_HEROES} 人です。先に retire-hero で引退させてください")
  if any(h["name"] == name for h in active):
    raise SystemExit(f"もののふ「{name}」はすでにいます")
  hero_id = f"h{max((int(h['id'][1:]) for h in heroes), default=0) + 1}"
  state = game.new_state(world, hero_id, name)
  heroes.append({"id": hero_id, "name": name, "created": today.isoformat(), "active": True})
  game.save_state(state, store.hero_state_path(hero_id))
  store.save_heroes(heroes)
  board = load_board()
  where = f"番付の Gist（{board['gist_url']}）" if board else "番付の Gist（次回の毎日の実行で作られます）"
  write_summary(
    f"## もののふ「{md_escape(name)}」({hero_id}) が旅立ちます\n\n"
    f"冒険は次回の毎日の実行から進みます。様子は{where}と、{hero_link(hero_id)}で見られます。"
  )
  return 0


def retire(key: str) -> int:
  """ID か名前でもののふを引退させる（状態は残し、サイトの詳細のページも残る）。"""
  heroes = store.load_heroes()
  active = [h for h in heroes if h["active"]]
  # ID を優先して照合する（名前が ID と同じもののふは作れない）
  for h in [h for h in active if h["id"] == key] + [h for h in active if h["name"] == key]:
    h["active"] = False
    store.save_heroes(heroes)
    write_summary(f"もののふ「{md_escape(h['name'])}」({h['id']}) を引退させました（記録: {hero_link(h['id'])}）")
    return 0
  raise SystemExit(f"引退させられるもののふ「{key}」がいません（ID か名前で、現役のもののふを指定してください）")


def load_hero(world: dict, hero: dict) -> dict:
  path = store.hero_state_path(hero["id"])
  if not path.exists():  # 登録済みのもののふの状態が無いのは異常。黙って最初からにしない
    raise SystemExit(f"{path} がありません（削除やマージで消えた可能性があります）")
  return game.load_state(world, path, hero["id"], hero["name"])


def advance_heroes(world: dict, loaded: dict, today: date, actions: int | None, guild: dict) -> dict:
  """もののふたちを 1 日ずつそろえて進める（loaded はもののふの ID → (heroes.json の行, 状態, 進める日)）。
  口入れ屋の依頼は早い者勝ちなので、日ごとにもののふの順番を混ぜて、毎回同じもののふが先にならないようにする。
  進められなかったもののふは、その日以降は進めない。戻り値は失敗したもののふの ID → エラー。"""
  errors: dict = {}
  days = sorted({d for _, _, ds in loaded.values() for d in ds})
  if actions is not None:  # 試すとき: 今日の分だけ、指定した回数で進める
    days = [today] if days else []
  for day in days:
    order = [hid for hid, (_, _, ds) in loaded.items() if (day in ds or actions is not None) and hid not in errors]
    random.Random(f"order-{day.isoformat()}").shuffle(order)
    # 何人かで受ける大物の依頼は、その町に受けられるもののふが人数分いるときだけ出す（その日の朝の居場所と Lv。#72・#297）
    guild["levels"] = game.town_levels(st for hid, (_, st, _) in loaded.items() if hid not in errors)
    # 一緒に戦う仲間の状態（この日を進めるもののふだけ。ほかは進めないので保存されず、一緒に戦った結果が残らない）
    others = {hid: loaded[hid][1] for hid in order}
    for hid in order:
      hero, state, _ = loaded[hid]
      # 失敗したら、そのもののふが高札とほかのもののふ（一緒に戦った仲間）に加えた変更も取り消す
      before = copy.deepcopy((guild, {h: st for h, st in others.items() if h != hid}))
      try:
        game.advance(world, state, day, actions, guild, others)
      except (Exception, SystemExit) as e:  # noqa: BLE001  もののふごとに失敗を隔離する
        errors[hid] = e
        guild.clear()
        guild.update(before[0])
        for h, st in before[1].items():
          others[h].clear()
          others[h].update(st)
        others.pop(hid, None)  # 進められなかったもののふとは、もう一緒に戦わない
  guild.pop("levels", None)  # その日の朝の居場所と Lv は保存しない
  return errors


def advance_all(world: dict, today: date, actions: int | None, dry_run: bool, *, no_gist: bool = False) -> int:
  """現役のもののふを全員進めて、状態と、番付の Gist を更新する（詳細と絵図はサイトで見る）。
  1 人が失敗しても残りは続け、1 つでも失敗したら終了コード 1。"""
  heroes = [h for h in store.load_heroes() if h["active"]]
  if not heroes:
    print("現役のもののふがいません（new-game で作れます）")
    return 0
  token = os.environ.get("GIST_PAT")
  if not token and not dry_run and not no_gist:
    raise SystemExit("GIST_PAT を環境変数で指定してください")
  failed, entries, loaded = 0, [], {}

  def fail(hero: dict, e: BaseException) -> None:
    nonlocal failed
    failed += 1
    common.warn(f"{hero['name']}（{hero['id']}）を進められませんでした: {e}")

  for hero in heroes:
    try:
      state = load_hero(world, hero)
      loaded[hero["id"]] = (hero, state, pending_days(state, today))
    except (Exception, SystemExit) as e:  # noqa: BLE001
      fail(hero, e)
  guild = store.load_guild()
  errors = advance_heroes(world, loaded, today, actions, guild)
  # 引退したもののふなどが持っていた依頼は、翌日の朝に受付中に戻す（読めなかった・進められなかったもののふの依頼は残す）
  game.release_orphans(
    guild,
    {hid: st for hid, (_, st, _) in loaded.items()},
    keep={h["id"] for h in heroes if h["id"] not in loaded} | set(errors),
  )
  for hero in heroes:
    if hero["id"] not in loaded:
      continue
    if hero["id"] in errors:
      fail(hero, errors[hero["id"]])
      continue
    _, state, days = loaded[hero["id"]]
    if not days:
      print(f"{hero['name']}: {today} の分は処理済みのため進めません（表示だけ更新します）")
    try:
      if days and not dry_run:
        game.save_state(state, store.hero_state_path(hero["id"]))
      entries.append((hero, state))
      print(view.build_text(world, state))
    except (Exception, SystemExit) as e:  # noqa: BLE001
      fail(hero, e)
  if not dry_run:
    store.save_guild(guild)
  if entries and not no_gist:
    try:
      update_board(world, entries, today, dry_run, token)
    except (Exception, SystemExit) as e:  # noqa: BLE001
      failed += 1
      common.warn(f"番付の Gist を更新できませんでした: {e}")
  return 1 if failed else 0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--dry-run", action="store_true", help="状態を保存せず、Gist も更新しない")
  ap.add_argument("--no-gist", action="store_true", help="冒険の状態は保存し、番付の Gist の作成・更新を省く")
  ap.add_argument("--date", help="進める日付（既定は今日の JST）")
  ap.add_argument(
    "--actions", type=int, help="その日の行動回数を指定する（試すとき用。ふだんは season.json の actions_per_day）"
  )
  mode = ap.add_mutually_exclusive_group()
  mode.add_argument("--new-game", metavar="NAME", help="新しいもののふを登録する（冒険は進めない）")
  mode.add_argument("--retire", metavar="NAME_OR_ID", help="もののふを引退させる")
  mode.add_argument(
    "--map-grid", metavar="OUT.svg", help="座標の目盛りを重ねた確認用の地図を書き出す（pos を決めるときに使う）"
  )
  a = ap.parse_args()
  if (a.new_game is not None or a.retire is not None or a.map_grid) and (a.dry_run or a.date or a.actions is not None):
    ap.error("--new-game / --retire / --map-grid は --dry-run / --date / --actions と一緒に指定できません")

  if a.retire is not None:
    return retire(a.retire)
  world = load_world()
  if a.map_grid:
    return write_map_grid(world, a.map_grid)
  today = date.fromisoformat(a.date) if a.date else datetime.now(UTC).astimezone(common.JST).date()
  if a.new_game is not None:
    return new_game(world, a.new_game, today)
  return advance_all(world, today, a.actions, a.dry_run, no_gist=a.no_gist)


if __name__ == "__main__":
  sys.exit(main())
