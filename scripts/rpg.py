#!/usr/bin/env python3
"""シーズン制のテキスト RPG。勇者（最大 MAX_HEROES 人）を 1 日分ずつ進め、ランキングの Gist を更新する。

勇者は heroes.json（勇者管理シート）に登録する。世界（MAP・敵・アイテム・依頼）は world/ の下のデータで、
1 日分の進め方は game.py にある。勇者は毎日、乱数で決まる回数だけ行動し、町のギルドの依頼をこなして育つ。
シーズン（world/season.json）が変わると、アイテム以外は最初に戻る。
workflow が動かなかった日があれば、次の実行で最大 CATCH_UP_DAYS 日分までさかのぼって進める。

状態は .state/heroes.json（勇者の一覧）と .state/heroes/<id>.json（勇者ごとの状態）に保存し、
勇者ごとの詳細（ステータス・装備・持ち物・日誌）と地図は、サイト（scripts/website.py）で見る（Gist は作らない）。
ランキングの Gist の ID は .state/board.json に記録する（なければ毎日の実行で作る）。
workflow の最後のステップで master にコミットする。
乱数は「日付＋シーズン＋勇者の ID」を種にするので、同じ日に何度実行しても結果は同じ（その日の分が処理済みなら進めない）。

環境変数:
  GIST_PAT   ランキングの Gist の作成・更新に使う（gist スコープの Classic PAT）

使い方:
  python scripts/rpg.py                          # 登録済みの勇者を全員 1 日分進めて Gist を更新する（毎日の workflow）
  python scripts/rpg.py --dry-run --actions 4    # 記録せずに 1 日分を試す
  python scripts/rpg.py --new-game 名前                # 新しい勇者を登録する
  python scripts/rpg.py --retire 名前または ID         # 勇者を引退させる（毎日の更新の対象から外れる）
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
import world as W  # noqa: E402

HEROES_PATH = common.ROOT / ".state" / "heroes.json"
HERO_DIR = common.ROOT / ".state" / "heroes"
BOARD_PATH = common.ROOT / ".state" / "board.json"
GUILD_PATH = common.ROOT / ".state" / "guild.json"  # 全員で共有する、町ごとのギルドの掲示板（早い者勝ち）
BOARD_FILENAME = "hero-ranking.txt"
MAX_HEROES = 30  # 同時に冒険できる（引退していない）勇者の数
BOARD_NAME_WIDTH = 8  # ランキングのカードに出す名前の表示幅
RANKING_TOP = 10  # ランキングの順位表に出す順位（10 位まで。同じ順位の勇者は全員出す）
CARD_NAME_WIDTH = 16  # 勇者のカード（Gist）の 1 行目に出す名前の表示幅（Lv・シーズン・日が切れないように）
MEDALS = ("🥇", "🥈", "🥉")
NAME_WIDTH_MAX = 32  # 勇者の名前の表示幅（全角 = 2。全角 16 文字・半角 32 文字）
CATCH_UP_DAYS = 7  # 実行されなかった日をさかのぼって進める最大の日数
# カードに優先して出す出来事（前ほど優先）
HIGHLIGHTS = (
  "★★「",  # レアのアイテム（★★「…」以上。ふつうの装備は ★「…」、依頼のレアリティは「★ 名前」で区別する）
  "シーズン",
  "依頼達成",
  "力尽きた",
  "依頼失敗",
  "レベルが上がった",
  "手に入れた",
  "買った",
  "目標を果たした",
  "依頼を受けた",
)
TEASERS = ("新しい依頼を探している…", "次の町を目指している…", "腕が鳴る…")


def load_world() -> dict:
  return W.load_world()


def hero_state_path(hero_id: str) -> Path:
  return HERO_DIR / f"{hero_id}.json"


# ---- 勇者管理シート（heroes.json）-----------------------------------------------------
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


def clean_name(raw: str) -> str:
  """制御文字を除き、表示幅 NAME_WIDTH_MAX 以内に切り詰める。空なら SystemExit。"""
  name = "".join(c for c in raw if c.isprintable()).strip()
  while common.width(name) > NAME_WIDTH_MAX:
    name = name[:-1]
  name = name.strip()
  if not name:
    raise SystemExit("勇者の名前を指定してください")
  if re.fullmatch(r"h\d+", name):  # 引退の指定で ID と取り違えないように
    raise SystemExit(f"「{name}」は勇者の ID と紛らわしいので、名前には使えません")
  return name


def md_escape(text: str) -> str:
  """GitHub Actions のサマリー（Markdown）に名前をそのまま出さないよう、記号をエスケープする。"""
  return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~&])", r"\\\1", text)


# ---- 表示 ---------------------------------------------------------------------------
def fit(text: str, limit: int = common.LINE_MAX) -> str:
  """表示幅が limit を超えたら末尾を「…」にして詰める。"""
  if common.width(text) <= limit:
    return text
  while common.width(text) > limit - 1:
    text = text[:-1]
  return text + "…"


def highlights(events: list[str], n: int = 2) -> list[str]:
  """カードに出す出来事を n 個選ぶ（レア・依頼達成・力尽きたなどを優先し、起きた順に並べる）。"""
  ups = [i for i, e in enumerate(events) if "レベルが上がった" in e]
  events = [e for i, e in enumerate(events) if i not in ups[:-1]]  # レベルアップは最後の 1 つだけ

  def rank(i: int) -> int:
    return next((r for r, key in enumerate(HIGHLIGHTS) if key in events[i]), len(HIGHLIGHTS))

  return [events[i] for i in sorted(sorted(range(len(events)), key=rank)[:n])]


def item_label(world: dict, iid: str) -> str:
  item = world["items"][iid]
  return game.rarity_mark(world, item["rarity"]) + item["name"]


def trait_text(world: dict, state: dict) -> str:
  """特性（勇者を作るときにランダムで決まる）。"""
  t = game.hero_trait(world, state)
  return f"特性: {t.get('name', '-')}（{t.get('text', '')}）"


def item_power(item: dict, world: dict | None = None) -> str:
  """装備の性能（攻+5 防+1 HP+8 速-1 炎 など）。world を渡すと、属性を名前で出す。"""
  names = {"atk": "攻", "def": "防", "hp": "HP"}
  parts = [f"{names[k]}+{item[k]}" for k in W.STATS if item.get(k)]
  if item.get("spd"):
    parts.append(f"速{item['spd']:+d}")
  if item.get("element"):
    parts.append((world or {}).get("elements", {}).get(item["element"], {}).get("name", item["element"]) + "属性")
  return " ".join(parts)


def location(world: dict, state: dict) -> str:
  place = world["places"][state["place"]]
  text = f"📍 {place['name']}"
  if state["road"]:
    text += f"→{world['places'][state['road']['to']]['name']}"
  quest = state["quest"]
  if quest:
    if quest["type"] == "hunt":
      phase = {"go": "（狩り場へ）", "hunt": "", "back": "（帰り道）"}[quest["steps"][0][0]] if quest["steps"] else ""
      goal = f"{world['monsters'][quest['monster']]['name']} {quest['progress']}/{quest['count']}{phase}"
    elif quest["type"] == "deliver":
      goal = f"{world['places'][quest['place']]['name']}へ配達"
    else:
      goal = f"{world['places'][quest['place']]['name']}へ旅"
    done = quest["report"] and quest["progress"] >= quest["count"]
    text += f" 🎯 {goal}" + ("（報告へ）" if done else "")
  return text


def gear_text(world: dict, state: dict) -> str:
  """装備の名前を「/」でつなぐ（4 か所あるので、行が切れても見えるように ★ の多いものを先に、同じ ★ なら部位の順）。"""
  rank = {r: i for i, r in enumerate(world["rarities"])}
  worn = [iid for iid in game.equipment(world, state).values() if iid]
  worn.sort(key=lambda i: -rank[world["items"][i]["rarity"]])  # 安定ソートなので部位の順は保つ
  return "/".join(item_label(world, i) for i in worn) or "なし"


def build_text(world: dict, state: dict, fold: bool = True) -> str:
  """勇者の詳細の文章。最初の 5 行はカード（fold なら、Gist の幅に収まるように末尾を「…」で詰める）。"""
  hero, s = state["hero"], game.stats(world, state)
  hp_bar = common.bar(hero["hp"], s["max_hp"], 10).replace(" ", "░")
  worn = game.equipment(world, state)
  gear = gear_text(world, state)
  season = f"S{state['season']}" if state["season"] else "旅立ち前"
  if state["last"] is None:
    today = "新たな勇者が旅立とうとしている…"
  elif state["last"]["events"]:
    today = "、".join(highlights(state["last"]["events"]))
  else:
    today = random.Random(f"teaser-{state['last_date']}").choice(TEASERS)
  lines = [
    f"⚔️ {shorten(state['name'], CARD_NAME_WIDTH) if fold else state['name']} Lv.{hero['level']}  "
    f"{season} Day {state['season_day']}",
    location(world, state),
    f"HP {hp_bar} {hero['hp']}/{s['max_hp']}  攻{s['atk']} 防{s['def']}  {hero['gold']}G",
    f"装備: {gear}",
    f"今日: {today}",
  ]
  card = [fit(line) for line in lines] if fold else lines
  ss = state["season_stats"]
  detail = [
    "",
    "── ステータス ──",
    f"シーズン{state['season'] or '-'} {state['season_day']}日目 / 通算 {state['day']}日",
    f"Lv.{hero['level']}  "
    + (
      "EXP MAX"
      if hero["level"] >= world["season"]["max_level"]
      else f"EXP {hero['exp']}/{game.exp_need(world, hero['level'])}"
    )
    + f"  回復薬: {hero['potions']}",
    f"攻撃 {s['atk']}（基本 {hero['base_atk']}）  防御 {s['def']}（基本 {hero['base_def']}）  "
    f"最大HP {s['max_hp']}（基本 {hero['max_hp']}）  素早さ {s['spd']}  器用さ {s['dex']}",
    trait_text(world, state),
    "",
    "── 装備 ──",
  ]
  for slot, iid in worn.items():
    detail.append(
      f"{W.SLOT_NAMES[slot]}: "
      + (f"{item_label(world, iid)}（{item_power(world['items'][iid], world)}）" if iid else "なし")
    )
  rank = {r: i for i, r in enumerate(world["rarities"])}
  inv = sorted(
    state["inventory"].items(),
    key=lambda kv: (-rank[world["items"][kv[0]]["rarity"]], -game.item_score(world, world["items"][kv[0]]), kv[0]),
  )
  detail += ["", "── 持ち物（シーズンをまたいで持ち越す）──"]
  detail += [f"{item_label(world, iid)}" + (f" ×{n}" if n > 1 else "") for iid, n in inv] or ["なし"]
  quest = state["quest"]
  if quest and quest["type"] != "patrol":
    detail += ["", "── 受けている依頼 ──", game.quest_text(world, quest)]
    if quest.get("text"):
      detail.append(f"「{quest['text']}」（{world['places'][quest['town']]['name']}のギルド）")
    left = sum(n for _, n in quest["steps"]) if quest["type"] == "hunt" else None
    detail.append(
      f"必要な行動力 {quest.get('cost', '-')}"
      + (f"（残り {left}）" if left is not None else "")
      + f"  進み具合 {quest['progress']}/{quest['count']}  "
      f"報酬 {quest['reward']['exp']}EXP {quest['reward']['gold']}G"
    )
  detail += [
    "",
    "── 今シーズンの成績 ──",
    f"依頼達成 {ss['quests_done']}  依頼失敗 {ss['quests_failed']}  倒した敵 {ss['kills']}  "
    f"力尽きた回数 {ss['deaths']}",
    "手に入れたレア: " + ("、".join(item_label(world, i) for i in ss["rares"]) or "まだない"),
  ]
  detail += ["", "── 冒険の記録（日付は進めた日、カッコ内は行動回数）──"]
  detail += [f"{j['date']} ({j['actions']}回) {j['text']}" for j in state["journal"]]
  if state["seasons"]:
    detail += ["", "── 歴代のシーズン ──"]
    for r in state["seasons"]:
      rares = "、".join(item_label(world, i) for i in r["rares"] if i in world["items"]) or "なし"
      detail.append(
        f"シーズン{r['season']}: Lv.{r['level']}（{r['days']}日） 依頼達成 {r['quests_done']}  "
        f"倒した敵 {r['kills']}  力尽きた回数 {r['deaths']}  レア: {rares}"
      )
  return "\n".join(card + detail) + "\n"


def page_url(hero_id: str) -> str:
  """勇者の詳細のページ（サイトの heroes/<id>.html）の URL。GitHub Actions の外では、サイトの中のパス。"""
  return f"{site_url() or ''}heroes/{hero_id}.html"


def site_url() -> str | None:
  """GitHub Pages のサイトの URL（GitHub Actions の中だけ分かる。scripts/website.py が書き出して pages.yml が公開する）。"""
  repo = os.environ.get("GITHUB_REPOSITORY", "")
  if "/" not in repo:
    return None
  owner, name = repo.split("/", 1)
  if name.lower() == f"{owner.lower()}.github.io":  # ユーザー・組織のサイトのリポジトリは、ドメインの直下
    return f"https://{owner.lower()}.github.io/"
  return f"https://{owner.lower()}.github.io/{name}/"


def hero_link(hero_id: str) -> str:
  """登録・引退のときの案内に出す、勇者の詳細のページへの案内（ページはサイトの次の公開で書き出される）。"""
  url = site_url()
  return (
    f"サイトの勇者のページ（{url}heroes/{hero_id}.html。次のサイトの公開のあとに見られる）"
    if url
    else "サイトの勇者のページ"
  )


def current_entries(world: dict, retired: bool = False) -> list[tuple[dict, dict]]:
  """現役の勇者（retired なら引退した勇者）の今の状態（冒険は進めない）。"""
  entries = []
  for hero in [h for h in load_heroes() if h["active"] != retired]:
    path = hero_state_path(hero["id"])
    if path.exists():
      entries.append((hero, game.load_state(world, path, hero["id"], hero["name"])))
  return entries


def write_map_grid(world: dict, out: str) -> int:
  """座標の目盛りと場所の ID を重ねた確認用の地図を out に書き出す（pos を決めるときに使う。コミットしない）。"""
  svg = mapsvg.render(world, current_entries(world), grid=True)
  if svg is None:
    raise SystemExit("world/map.json に layout がないので、地図を描けません")
  Path(out).write_text(svg, encoding="utf-8")
  print(f"{out} を書き出しました（ブラウザで開いて、場所の pos を決めるのに使ってください）")
  return 0


# ---- ランキング（Pin 用の Gist）------------------------------------------------------
RANKINGS = (  # (見出し, カードでの見出し（None ならカードに出さない）, 並べる値, 表示する値, 単位)
  ("レベル", "Lv  ", lambda s: s["hero"]["level"], lambda s: s["hero"]["level"], ""),
  ("討伐数", "討伐", lambda s: s["season_stats"]["kills"], lambda s: s["season_stats"]["kills"], "体"),
  ("依頼達成", "依頼", lambda s: s["season_stats"]["quests_done"], lambda s: s["season_stats"]["quests_done"], "件"),
  ("レアアイテム", None, lambda s: len(s["season_stats"]["rares"]), lambda s: len(s["season_stats"]["rares"]), "個"),
)


def shorten(text: str, limit: int) -> str:
  return text if common.width(text) <= limit else fit(text, limit)


def season_view(world: dict, state: dict, season: int) -> dict:
  """ランキング用に、今のシーズンの状態を返す（まだ今のシーズンを進めていない勇者は最初の状態として扱う）。"""
  if state["season"] == season:
    return state
  return {**state, "hero": game.new_hero(world), "season_stats": game.new_season_stats(), "season_day": 0}


def ranked(entries: list[tuple[dict, dict]], key) -> list[tuple[int, dict, dict]]:
  """(順位, 勇者, 状態) の一覧。同じ値は同じ順位（登録が早い順に並べる）。"""
  rows = sorted(entries, key=lambda e: key(e[1]), reverse=True)
  out, prev, rank = [], None, 0
  for i, (hero, state) in enumerate(rows, 1):
    if key(state) != prev:
      rank, prev = i, key(state)
    out.append((rank, hero, state))
  return out


def news(entries: list[tuple[dict, dict]], today: date, name_width: int | None = None) -> list[str]:
  """今日の出来事（勇者ごとに目立つものを 1 つ）。目立つ順。name_width を渡すと、名前をその表示幅に詰める
  （ランキングのカードで、名前が長くても出来事が読めるように）。"""
  picks = []
  for hero, state in entries:
    last = state["last"]
    if last and last["date"] == today.isoformat() and last["events"]:
      (event,) = highlights(last["events"], 1)
      rank = next((r for r, key in enumerate(HIGHLIGHTS) if key in event), len(HIGHLIGHTS))
      name = shorten(hero["name"], name_width) if name_width else hero["name"]
      picks.append((rank, f"{name}: {event}"))
  return [text for _, text in sorted(picks, key=lambda p: p[0])]


def build_board(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  """Pin 用のランキングの Gist の中身。entries は現役の勇者の (heroes.json の行, 状態)。"""
  season = game.season_of(world, today)
  first, last = game.season_range(world, season)
  views = [(hero, season_view(world, state, season)) for hero, state in entries]
  if today < first:
    when = "開幕前"
  else:
    when = f"Day {(today - first).days + 1}/{(last - first).days + 1}"
  lines = [f"🏆 シーズン{season}  {when}  勇者{len(views)}人"]
  for _, label, key, value, _ in RANKINGS:
    if label is None:
      continue
    top = [
      f"{MEDALS[min(r, 3) - 1]}{shorten(h['name'], BOARD_NAME_WIDTH)} {value(s)}" for r, h, s in ranked(views, key)[:3]
    ]
    lines.append(f"{label} " + (" ".join(top) or "まだいない"))
  today_news = news(views, today, BOARD_NAME_WIDTH)
  lines.append("📰 " + (today_news[0] if today_news else "今日も勇者たちは冒険中…"))
  card = [fit(line) for line in lines]

  detail = ["", f"── シーズン{season}（{first}〜{last}）──"]
  if site_url():
    detail.append(f"サイト「クリエピス戦記」（ストーリー・地図・ランキング・勇者・町・ギルド・アイテム）: {site_url()}")
  for title, _, key, value, unit in RANKINGS:
    detail += ["", f"── {title} ──"]
    detail += [
      f"{r:>2}. {h['name']}  " + (f"Lv.{value(s)}" if title == "レベル" else f"{value(s)}{unit}")
      for r, h, s in ranked(views, key)
      if r <= RANKING_TOP
    ]
  detail += ["", "── 今日の出来事 ──"] + (today_news or ["まだない"])
  detail += ["", "── 勇者たち ──"]
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
  """ランキングの本文に載せる、勇者ごとの短い欄（entry は heroes.json の行）。"""
  hero, s = state["hero"], game.stats(world, state)
  rares = sorted(
    {i for i in state["inventory"] if world["items"][i]["rarity"] != "common"},
    key=lambda i: (-world["rarities"].index(world["items"][i]["rarity"]), i),
  )
  last = state["last"]
  events = highlights(last["events"]) if last and last["date"] == today.isoformat() and last["events"] else []
  return [
    f"■ {entry['name']}  Lv.{hero['level']}  {location(world, state)}",
    f"  HP {hero['hp']}/{s['max_hp']}  攻{s['atk']} 防{s['def']}  {hero['gold']}G  回復薬 {hero['potions']}",
    f"  装備: {gear_text(world, state)}",
    f"  レア: {'、'.join(item_label(world, i) for i in rares) or 'まだない'}",
    f"  今日: {'、'.join(events) or '—'}",
    f"  詳細: {page_url(entry['id'])}",
  ]


def board_description(world: dict, today: date) -> str:
  return f"勇者ランキング シーズン{game.season_of(world, today)}"


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
    f"## ランキングの Gist を作りました\n\n{gist['html_url']}\n\n"
    "プロフィールの **Customize your pins** で Pin できます。"
  )


def update_board(world: dict, entries: list[tuple[dict, dict]], today: date, dry_run: bool, token: str | None) -> None:
  """ランキングの Gist を更新する。まだないか、消えていたら（404）作って board.json に記録する。"""
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
    common.warn(f"ランキングの Gist（{board['gist_url']}）が見つからないので、新しく作ります。Pin し直してください")
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
  """新しい勇者を登録し、状態を作る（冒険は翌日から進む）。"""
  name = clean_name(raw_name)
  heroes = load_heroes()
  active = [h for h in heroes if h["active"]]
  if len(active) >= MAX_HEROES:
    raise SystemExit(f"勇者は最大 {MAX_HEROES} 人です。先に retire-hero で引退させてください")
  if any(h["name"] == name for h in active):
    raise SystemExit(f"勇者「{name}」はすでにいます")
  hero_id = f"h{max((int(h['id'][1:]) for h in heroes), default=0) + 1}"
  state = game.new_state(world, hero_id, name)
  heroes.append({"id": hero_id, "name": name, "created": today.isoformat(), "active": True})
  game.save_state(state, hero_state_path(hero_id))
  save_heroes(heroes)
  board = load_board()
  where = f"ランキングの Gist（{board['gist_url']}）" if board else "ランキングの Gist（次回の毎日の実行で作られます）"
  write_summary(
    f"## 勇者「{md_escape(name)}」({hero_id}) が旅立ちます\n\n"
    f"冒険は次回の毎日の実行から進みます。様子は{where}と、{hero_link(hero_id)}で見られます。"
  )
  return 0


def retire(key: str) -> int:
  """ID か名前で勇者を引退させる（Gist と状態は残す）。"""
  heroes = load_heroes()
  active = [h for h in heroes if h["active"]]
  # ID を優先して照合する（名前が ID と同じ勇者は作れない）
  for h in [h for h in active if h["id"] == key] + [h for h in active if h["name"] == key]:
    h["active"] = False
    save_heroes(heroes)
    write_summary(f"勇者「{md_escape(h['name'])}」({h['id']}) を引退させました（記録: {hero_link(h['id'])}）")
    return 0
  raise SystemExit(f"引退させられる勇者「{key}」がいません（ID か名前で、現役の勇者を指定してください）")


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


def load_hero(world: dict, hero: dict) -> dict:
  path = hero_state_path(hero["id"])
  if not path.exists():  # 登録済みの勇者の状態が無いのは異常。黙って最初からにしない
    raise SystemExit(f"{path} がありません（削除やマージで消えた可能性があります）")
  return game.load_state(world, path, hero["id"], hero["name"])


def advance_heroes(world: dict, loaded: dict, today: date, actions: int | None, guild: dict) -> dict:
  """勇者たちを 1 日ずつそろえて進める（loaded は勇者の ID → (heroes.json の行, 状態, 進める日)）。
  ギルドの依頼は早い者勝ちなので、日ごとに勇者の順番を混ぜて、毎回同じ勇者が先にならないようにする。
  進められなかった勇者は、その日以降は進めない。戻り値は失敗した勇者の ID → エラー。"""
  errors: dict = {}
  days = sorted({d for _, _, ds in loaded.values() for d in ds})
  if actions is not None:  # 試すとき: 今日の分だけ、指定した回数で進める
    days = [today] if days else []
  for day in days:
    order = [hid for hid, (_, _, ds) in loaded.items() if (day in ds or actions is not None) and hid not in errors]
    random.Random(f"order-{day.isoformat()}").shuffle(order)
    for hid in order:
      hero, state, _ = loaded[hid]
      before = copy.deepcopy(guild)  # 失敗したら、その勇者が掲示板に加えた変更も取り消す
      try:
        game.advance(world, state, day, actions, guild)
      except (Exception, SystemExit) as e:  # noqa: BLE001  勇者ごとに失敗を隔離する
        errors[hid] = e
        guild.clear()
        guild.update(before)
  return errors


def advance_all(world: dict, today: date, actions: int | None, dry_run: bool) -> int:
  """現役の勇者を全員進めて、状態と、ランキングの Gist を更新する（詳細と地図はサイトで見る）。
  1 人が失敗しても残りは続け、1 つでも失敗したら終了コード 1。"""
  heroes = [h for h in load_heroes() if h["active"]]
  if not heroes:
    print("現役の勇者がいません（new-game で作れます）")
    return 0
  token = os.environ.get("GIST_PAT")
  if not token and not dry_run:
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
  guild = load_guild()
  errors = advance_heroes(world, loaded, today, actions, guild)
  # 引退した勇者などが持っていた依頼は、翌日の朝に受付中に戻す（読めなかった・進められなかった勇者の依頼は残す）
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
        game.save_state(state, hero_state_path(hero["id"]))
      entries.append((hero, state))
      print(build_text(world, state))
    except (Exception, SystemExit) as e:  # noqa: BLE001
      fail(hero, e)
  if not dry_run:
    save_guild(guild)
  if entries:
    try:
      update_board(world, entries, today, dry_run, token)
    except (Exception, SystemExit) as e:  # noqa: BLE001
      failed += 1
      common.warn(f"ランキングの Gist を更新できませんでした: {e}")
  return 1 if failed else 0


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--dry-run", action="store_true", help="状態を保存せず、Gist も更新しない")
  ap.add_argument("--date", help="進める日付（既定は今日の JST）")
  ap.add_argument(
    "--actions", type=int, help="その日の行動回数を指定する（試すとき用。ふだんは season.json の actions_per_day）"
  )
  mode = ap.add_mutually_exclusive_group()
  mode.add_argument("--new-game", metavar="NAME", help="新しい勇者を登録する（冒険は進めない）")
  mode.add_argument("--retire", metavar="NAME_OR_ID", help="勇者を引退させる")
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
  return advance_all(world, today, a.actions, a.dry_run)


if __name__ == "__main__":
  sys.exit(main())
