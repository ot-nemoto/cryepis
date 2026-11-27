"""もののふの見た目（カードやページに出す文章・番付の並べ方）。状態を読むだけで、ファイルも API も触らない。
rpg.py（番付の Gist・毎日の実行）と website.py（サイト）の両方から使う。"""

from __future__ import annotations

import random
from datetime import date

import common
import game
import world as W

# カードに優先して出す出来事（前ほど優先）
HIGHLIGHTS = (
  "★★「",  # レアの武具（★★「…」以上。ふつうの装備は ★「…」、依頼のレアリティは「★ 名前」で区別する）
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
RANKING_TOP = 10  # 番付の順位表に出す人数（同じ順位でも 10 人で切る。並びは登録の早い順）
RANKINGS = (  # (見出し, カードでの見出し（None ならカードに出さない）, 並べる値, 表示する値, 単位)
  ("レベル", "Lv  ", lambda s: s["hero"]["level"], lambda s: s["hero"]["level"], ""),
  ("討伐数", "討伐", lambda s: s["season_stats"]["kills"], lambda s: s["season_stats"]["kills"], "体"),
  ("依頼達成", "依頼", lambda s: s["season_stats"]["quests_done"], lambda s: s["season_stats"]["quests_done"], "件"),
  ("レア武具", None, lambda s: len(s["season_stats"]["rares"]), lambda s: len(s["season_stats"]["rares"]), "個"),
)


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
  """特性（もののふを作るときにランダムで決まる）。"""
  t = game.hero_trait(world, state)
  return f"特性: {t.get('name', '-')}（{t.get('text', '')}）"


def personality_text(world: dict, state: dict) -> str:
  """性格（もののふを作るときにランダムで決まる）と、溜めた行動力。"""
  p = game.personality(world, state)
  stored = f"  溜めた行動力: {state['hero'].get('stored', 0)}/{game.stored_max(world)}"
  return f"性格: {p.get('name', '-')}（{p.get('text', '')}）" + stored if p else "性格: -" + stored


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
  worn = [x for x in game.worn(world, state).values() if x]
  worn.sort(key=lambda x: -rank[world["items"][x["id"]]["rarity"]])  # 安定ソートなので部位の順は保つ
  return "/".join(item_label(world, x["id"]) + game.equipment_skill_note(world, x) for x in worn) or "なし"


def build_text(world: dict, state: dict) -> str:
  """もののふの詳細の文章（サイトのもののふのページと、毎日の実行のログに出す。幅には詰めない）。
  最初の 5 行は概要（名前と Lv・居場所・HP と能力・装備・今日の出来事）。"""
  hero, s = state["hero"], game.stats(world, state)
  hp_bar = common.bar(hero["hp"], s["max_hp"], 10).replace(" ", "░")
  worn = game.equipment(world, state)
  gear = gear_text(world, state)
  season = f"S{state['season']}" if state["season"] else "旅立ち前"
  if state["last"] is None:
    today = "新たなもののふが旅立とうとしている…"
  elif state["last"]["events"]:
    today = "、".join(highlights(state["last"]["events"]))
  else:
    today = random.Random(f"teaser-{state['last_date']}").choice(TEASERS)
  lines = [
    f"⚔️ {state['name']} Lv.{hero['level']}  {season} Day {state['season_day']}",
    location(world, state),
    f"HP {hp_bar} {hero['hp']}/{s['max_hp']}  攻{s['atk']} 防{s['def']}  {hero['gold']}文",
    f"装備: {gear}",
    f"今日: {today}",
  ]
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
    + f"  丸薬: {hero['potions']}",
    f"攻撃 {s['atk']}（基本 {hero['base_atk']}）  防御 {s['def']}（基本 {hero['base_def']}）  "
    f"最大HP {s['max_hp']}（基本 {hero['max_hp']}）  素早さ {s['spd']}  器用さ {s['dex']}",
    trait_text(world, state),
    personality_text(world, state),
    "",
    "── 装備 ──",
  ]
  for slot, iid in worn.items():
    detail.append(
      f"{W.SLOT_NAMES[slot]}: "
      + (f"{item_label(world, iid)}（{item_power(world['items'][iid], world)}）" if iid else "なし")
    )
  rank = {r: i for i, r in enumerate(world["rarities"])}

  def listing(xs: list[dict]) -> list[str]:
    xs = sorted(
      xs,
      key=lambda x: (
        -rank[world["items"][x["id"]]["rarity"]],
        -game.item_score(world, world["items"][x["id"]]),
        x["id"],
      ),
    )
    return [item_label(world, x["id"]) + game.equipment_skill_note(world, x) for x in xs] or ["なし"]

  detail += ["", "── 持ち物（シーズンをまたいで持ち越す）──", *listing(game.carried(state))]
  detail += ["", "── 蔵（どの町の口入れ屋でも出し入れできる）──", *listing(game.stored(state))]
  stones = state.get("stones", {})
  if stones:
    detail += [
      "",
      "── 原石 ──",
      *(f"{game.stone_name(world, area)}: {count}個" for area, count in sorted(stones.items()) if count),
    ]
  quest = state["quest"]
  if quest and quest["type"] != "patrol":
    detail += ["", "── 受けている依頼 ──", game.quest_text(world, quest)]
    if quest.get("text"):
      detail.append(f"「{quest['text']}」（{world['places'][quest['town']]['name']}の口入れ屋）")
    left = sum(n for _, n in quest["steps"]) if quest["type"] == "hunt" else None
    detail.append(
      f"必要な行動力 {quest.get('cost', '-')}"
      + (f"（残り {left}）" if left is not None else "")
      + f"  進み具合 {quest['progress']}/{quest['count']}  "
      f"報酬 {quest['reward']['exp']}EXP {quest['reward']['gold']}文"
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
  return "\n".join(lines + detail) + "\n"


def shorten(text: str, limit: int) -> str:
  return text if common.width(text) <= limit else fit(text, limit)


def season_view(world: dict, state: dict, season: int) -> dict:
  """番付用に、今のシーズンの状態を返す（まだ今のシーズンを進めていないもののふは最初の状態として扱う）。"""
  if state["season"] == season:
    return state
  return {**state, "hero": game.new_hero(world), "season_stats": game.new_season_stats(), "season_day": 0}


def ranked(entries: list[tuple[dict, dict]], key) -> list[tuple[int, dict, dict]]:
  """(順位, もののふ, 状態) の一覧。同じ値は同じ順位（登録が早い順に並べる）。"""
  rows = sorted(entries, key=lambda e: key(e[1]), reverse=True)
  out, prev, rank = [], None, 0
  for i, (hero, state) in enumerate(rows, 1):
    if key(state) != prev:
      rank, prev = i, key(state)
    out.append((rank, hero, state))
  return out


def news(entries: list[tuple[dict, dict]], today: date, name_width: int | None = None) -> list[str]:
  """今日の出来事（もののふごとに目立つものを 1 つ）。目立つ順。name_width を渡すと、名前をその表示幅に詰める
  （番付のカードで、名前が長くても出来事が読めるように）。"""
  picks = []
  for hero, state in entries:
    last = state["last"]
    if last and last["date"] == today.isoformat() and last["events"]:
      (event,) = highlights(last["events"], 1)
      rank = next((r for r, key in enumerate(HIGHLIGHTS) if key in event), len(HIGHLIGHTS))
      name = shorten(hero["name"], name_width) if name_width else hero["name"]
      picks.append((rank, f"{name}: {event}"))
  return [text for _, text in sorted(picks, key=lambda p: p[0])]
