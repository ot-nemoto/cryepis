#!/usr/bin/env python3
"""GitHub Pages に載せるサイト（HTML）を書き出す。冒険は進めない（今の状態を見せるだけ）。

ページ:
  index.html          ストーリー（world/story.md の物語・世界・歴史と、いまの冒険の状況）
  map.html            世界の地図（拡大・移動ができる。場所や勇者の印にマウスを乗せると説明が出る）
  ranking.html        今シーズンのランキングと、今日の出来事
  heroes.html         旅をしている勇者の一覧（居場所・能力・装備・今日の出来事）
  towns.html          町の情報（全部の町を地方ごとのタブで切り替える。説明・ギルドの規模・特産品・店・レア・道。開放していない町は「-」）
  items.html          開放した地域で手に入るアイテム（装備の種類ごとのタブ。レアリティで絞り込める）
  monsters.html       開放した道に出る魔物とボス（地方ごとのタブ。街道の地形の割合・Lv・生息地・性質・属性・強さ。生息地で絞り込める）
  guide.html          手引き（能力・勇者の特性・魔物の性質・属性と相性・戦いの決まりの説明）
  guild.html          町ごとのギルドの掲示板（地方ごとのタブ。レアリティで絞り込める。受けられた依頼は ACCEPTED）
  heroes/<id>.html    勇者ごとの詳細（ステータス・装備・持ち物・依頼・冒険の記録）

データは .state/（勇者の状態・ギルドの受けられた依頼）と world/ から読む。
毎日の実行のあとに pages.yml が書き出して公開する（書き出したファイルはコミットしない）。

使い方:
  python scripts/website.py OUT_DIR [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import shutil
import sys
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402
import game  # noqa: E402
import mapsvg  # noqa: E402
import store  # noqa: E402
import view  # noqa: E402
import world as W  # noqa: E402

TITLE = "クリエピス戦記"
STORY_PATH = W.WORLD_DIR / "story.md"  # ストーリーのページの文（Markdown の一部だけ使える。md_html を参照）
PAGES = (
  ("index.html", "ストーリー"),
  ("map.html", "地図"),
  ("ranking.html", "ランキング"),
  ("heroes.html", "勇者"),
  ("towns.html", "町"),
  ("guild.html", "ギルド"),
  ("items.html", "アイテム"),
  ("monsters.html", "魔物"),
  ("guide.html", "手引き"),
)

CSS = """
:root{--bg:#f6efdc;--card:#fffaf0;--ink:#3f2f22;--sub:#6b5a48;--line:#d9c9a8;--accent:#7a5530;--hero:#b91c1c;
--minus:#c0262d;color-scheme:light;
--t-plain:#a9c25d;--t-forest:#2f6b3a;--t-jungle:#3f8f4f;--t-desert:#d9b36c;--t-wetland:#4f9a8c;--t-snow:#8fc3e0;
--t-mountain:#8a7462;--t-volcano:#c4553a;--t-sea:#3f74b5}
@media (prefers-color-scheme:dark){:root{--bg:#1f1a14;--card:#2a231b;--ink:#efe4cf;--sub:#bfae92;--line:#4a3d2c;
--accent:#e0b878;--hero:#f87171;--minus:#f87171;color-scheme:dark;
--t-plain:#b8d16a;--t-forest:#4f9a5c;--t-jungle:#5fb06e;--t-desert:#e3c483;--t-wetland:#62b5a6;--t-snow:#a8d4ee;
--t-mountain:#a68d79;--t-volcano:#e07258;--t-sea:#6a9ad6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 'Hiragino Sans','Hiragino Kaku Gothic ProN',
'Noto Sans JP','Yu Gothic',Meiryo,sans-serif}
header{border-bottom:1px solid var(--line);background:var(--card)}
.wrap{max-width:1100px;margin:0 auto;padding-inline:16px}
header .wrap{display:flex;flex-wrap:wrap;gap:4px 20px;align-items:baseline;padding-block:10px}
header h1{font-size:20px;margin:0}
header h1 a{color:inherit;text-decoration:none}
nav{display:flex;gap:14px;flex-wrap:wrap}
nav a{color:var(--sub);text-decoration:none;padding:2px 0;border-bottom:2px solid transparent}
nav a.on{color:var(--ink);border-color:var(--accent);font-weight:bold}
main{padding-block:16px 40px}
h2{font-size:18px;margin:28px 0 8px;border-left:4px solid var(--accent);padding-left:8px}
h3{font-size:16px;margin:0}
a{color:var(--accent)}
.note{color:var(--sub);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.grid .card{margin:0}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--sub);font-weight:normal;font-size:13px;white-space:nowrap}
.wide-table{min-width:640px;table-layout:fixed}
.monsters{min-width:820px}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
.mark{color:#b7791f;white-space:nowrap;width:5em}
.taken{color:var(--hero);white-space:nowrap}
.free{color:var(--sub);white-space:nowrap}
.open{color:#15803d;white-space:nowrap}
.stamp{display:inline-block;padding:1px 8px;border:2.5px solid #c0262d;border-radius:4px;color:#c0262d;font-weight:bold;
font-size:13px;letter-spacing:.12em;transform:rotate(-8deg);opacity:.85;margin:2px 0 4px;font-family:Georgia,'Times New Roman',serif}
.filters{display:flex;flex-direction:column;gap:10px}
.f-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.f-label{font-size:13px;color:var(--sub);width:5.5em}
.chip-mark{color:#b7791f;white-space:nowrap}
.info th{width:11em;vertical-align:top;padding-top:6px}
.closed{opacity:.75}
.story-title{font-size:24px;border-left:0;padding-left:0;margin-top:12px;letter-spacing:.05em}
p.story{line-height:2;margin:10px 0 18px}
.story-table td:first-child{white-space:nowrap;font-weight:bold}
ul.plain{margin:0;padding-left:1.2em}
.en{font-weight:normal;font-style:italic;color:var(--sub);font-size:.85em;letter-spacing:.02em}
.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:2px solid var(--line);margin:4px 0 14px}
.tab{font:inherit;font-weight:bold;color:var(--sub);background:none;border:0;border-bottom:3px solid transparent;
padding:6px 12px;margin-bottom:-2px;cursor:pointer}
.tab:hover{color:var(--ink)}
.tab[aria-selected="true"]{color:var(--ink);border-bottom-color:#e8833a}
h2.area{font-size:17px;margin:6px 0 10px;border-left:0;padding-left:0}
.minus{color:var(--minus);font-weight:bold}
h3.sub{font-size:15px;margin:18px 0 0}
.guide th:first-child,.guide td:first-child{width:9em;white-space:nowrap}
.card h3+p,.card h3+.scroll{margin-top:8px}
.icon{text-align:center;white-space:nowrap}
.roads{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px 16px;margin-top:8px}
.road{display:flex;gap:10px;align-items:center}
.road svg{width:64px;height:64px;flex:none}
.road .name{font-weight:bold;font-size:14px}
.road ul{list-style:none;margin:2px 0 0;padding:0;font-size:13px}
.habitat{display:inline-block;padding:0 6px;border:1px solid var(--sub);border-radius:4px;font-size:11px;
font-weight:bold;letter-spacing:.06em;color:var(--sub)}
.tdot{display:inline-block;width:.7em;height:.7em;border-radius:50%;margin-right:5px;vertical-align:-.02em}
h4.sub{font-size:14px;margin:16px 0 4px}
.levels{max-width:420px}
td.icon .tag{margin-left:0}
.zero{color:var(--sub)}
.pill{display:inline-block;margin:2px 6px 2px 0;padding:1px 10px;border:1.5px solid #f0a868;border-radius:999px;
  background:var(--card);color:var(--ink);text-decoration:none;font-size:13px;white-space:nowrap}
.pill:hover{border-color:#e8833a}
.tag{display:inline-block;margin-left:6px;padding:0 6px;border:1px solid #e8833a;border-radius:4px;color:#c2621c;font-size:11px}
.chip{font:inherit;font-size:14px;font-weight:bold;color:var(--ink);background:var(--card);border:1.5px solid #f0a868;
border-radius:999px;padding:4px 14px;cursor:pointer;box-shadow:0 0 0 2px rgba(240,168,104,.15)}
.chip:hover{border-color:#e8833a}
.chip[aria-pressed="true"]{background:#e8833a;border-color:#e8833a;color:#fff}
.chip[aria-pressed="true"] .chip-mark{color:#fff}
.link{font:inherit;background:none;border:0;color:var(--accent);text-decoration:underline;cursor:pointer;margin-left:10px}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto;
font:13.5px/1.6 ui-monospace,'SFMono-Regular',Menlo,Consolas,'Noto Sans Mono CJK JP',monospace;white-space:pre-wrap}
#map{position:relative;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#f6efdc;
touch-action:pan-y;cursor:grab}
#map.zoomed{touch-action:none}
#map svg{display:block;width:100%;height:auto;user-select:none}
#map.drag{cursor:grabbing}
.zoom{position:absolute;right:8px;top:8px;display:flex;gap:4px}
.zoom button{width:34px;height:34px;border:1px solid #a08b70;border-radius:6px;background:#fffaf0;color:#3f2f22;
font-size:18px;cursor:pointer}
.zoom button.wide{width:auto;padding:0 10px;font-size:13px}
"""

# 地図の拡大・移動（ホイール・ドラッグ・ボタン）。viewBox を変えるだけ。
# 縮小しきった地図の上では、ホイールやスマホの縦のスワイプでページをスクロールできる
MAP_JS = """
(()=>{const box=document.getElementById('map'),svg=box.querySelector('svg');if(!svg)return;
const full=svg.viewBox.baseVal,home=[full.x,full.y,full.width,full.height];let v=home.slice();
const set=()=>{svg.setAttribute('viewBox',v.join(' '));box.classList.toggle('zoomed',v[2]<home[2]);};
const clamp=()=>{v[2]=Math.min(home[2],Math.max(home[2]/8,v[2]));v[3]=v[2]*home[3]/home[2];
v[0]=Math.min(home[0]+home[2]-v[2],Math.max(home[0],v[0]));v[1]=Math.min(home[1]+home[3]-v[3],Math.max(home[1],v[1]));};
const zoom=(k,fx=.5,fy=.5)=>{const w=v[2]*k,h=v[3]*k;v[0]+=(v[2]-w)*fx;v[1]+=(v[3]-h)*fy;v[2]=w;v[3]=h;clamp();set();};
box.addEventListener('wheel',e=>{if(e.deltaY>0&&v[2]>=home[2])return;e.preventDefault();
const r=svg.getBoundingClientRect();zoom(e.deltaY<0?0.8:1.25,(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height);},{passive:false});
let drag=null;
box.addEventListener('pointerdown',e=>{if(e.target.closest('a,button'))return;drag=[e.clientX,e.clientY];
box.setPointerCapture(e.pointerId);box.classList.add('drag');});
box.addEventListener('pointermove',e=>{if(!drag)return;const r=svg.getBoundingClientRect();
v[0]-=(e.clientX-drag[0])*v[2]/r.width;v[1]-=(e.clientY-drag[1])*v[3]/r.height;drag=[e.clientX,e.clientY];clamp();set();});
const end=()=>{drag=null;box.classList.remove('drag');};
box.addEventListener('pointerup',end);box.addEventListener('pointercancel',end);
document.getElementById('zin').onclick=()=>zoom(0.7);document.getElementById('zout').onclick=()=>zoom(1/0.7);
document.getElementById('zhome').onclick=()=>{v=home.slice();set();};})();
"""


def page(title: str, body: str, current: str, depth: int = 0, script: str = "") -> str:
  """ページの HTML（depth はサイトの一番上からの深さ。リンクの先頭に ../ をつける）。"""
  up = "../" * depth
  nav = "".join(
    f'<a href="{up}{href}"' + (' class="on"' if href == current else "") + f">{name}</a>" for href, name in PAGES
  )
  return (
    f'<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">'
    f'<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<title>{escape(title)} | {TITLE}</title><style>{CSS}</style></head><body>"
    f'<header><div class="wrap"><h1><a href="{up}index.html">{TITLE}</a></h1><nav>{nav}</nav></div></header>'
    f'<main class="wrap">{body}</main>' + (f"<script>{script}</script>" if script else "") + "</body></html>\n"
  )


def hero_href(hero_id: str, depth: int = 0) -> str:
  return "../" * depth + f"heroes/{hero_id}.html"


def updated(today: date) -> str:
  return f'<p class="note">{today.isoformat()} の冒険を終えた状態（毎朝 7 時すぎに更新）</p>'


# ---- ストーリー ---------------------------------------------------------------------------
def inline_md(text: str) -> str:
  """1 行の中の書式（**太字** だけ）。対になっていない ** は、そのまま出す。"""
  parts = escape(text).split("**")
  if len(parts) % 2 == 0:  # ** が奇数個: 最後の ** は対がないので、文字として戻す
    parts[-2:] = [parts[-2] + "**" + parts[-1]]
  return "".join(f"<strong>{p}</strong>" if i % 2 else p for i, p in enumerate(parts))


def md_html(text: str) -> list[str]:
  """Markdown のごく一部を HTML のかたまりの一覧にする: # 見出し・## 見出し・### 見出し・- 箇条書き・| 表 |・
  段落（**太字**）。段落の中の改行はそのまま改行にする。ほかの書き方は、段落の文字としてそのまま出す。"""
  blocks: list[str] = []
  lines = [line.rstrip() for line in text.splitlines()]
  headings = (("### ", "<h3>{}</h3>"), ("## ", '<h2 class="area">{}</h2>'), ("# ", '<h2 class="story-title">{}</h2>'))

  def special(line: str) -> bool:  # 段落以外のかたまりの始まり
    return line.startswith(("- ", "|")) or any(line.startswith(p) for p, _ in headings)

  i = 0
  while i < len(lines):
    line = lines[i]
    heading = next((h for h in headings if line.startswith(h[0])), None)
    if not line.strip():
      i += 1
    elif heading:
      blocks.append(heading[1].format(inline_md(line[len(heading[0]) :])))
      i += 1
    elif line.startswith("- "):
      items = []
      while i < len(lines) and lines[i].startswith("- "):
        items.append(f"<li>{inline_md(lines[i][2:].strip())}</li>")
        i += 1
      blocks.append(f"<ul>{''.join(items)}</ul>")
    elif line.startswith("|"):
      rows, start = [], i
      while i < len(lines) and lines[i].startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        is_rule = all(c and set(c) <= set("-: ") for c in cells)
        if not (i == start + 1 and is_rule):  # 見出しのすぐ下の行の区切り（|---|）だけ飛ばす
          rows.append(cells)
        i += 1
      head, *body = rows
      blocks.append(
        '<div class="scroll"><table class="story-table"><tr>'
        + "".join(f"<th>{inline_md(c)}</th>" for c in head)
        + "</tr>"
        + "".join("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in r) + "</tr>" for r in body)
        + "</table></div>"
      )
    else:
      para = [inline_md(line.strip())]  # 最初の行は必ず取る（どのかたまりにも当たらない行で止まらないように）
      i += 1
      while i < len(lines) and lines[i].strip() and not special(lines[i]):
        para.append(inline_md(lines[i].strip()))
        i += 1
      blocks.append(f'<p class="story">{"<br>".join(para)}</p>')
  return blocks


def build_story(world: dict, entries: list[tuple[dict, dict]], today: date, story: str | None = None) -> str:
  """最初のページ: 物語（world/story.md）と、いまの冒険の状況（シーズン・勇者・開放した地方と町）。"""
  if story is None:
    # Windows のエディタが付ける BOM があっても読めるように utf-8-sig
    story = STORY_PATH.read_text(encoding="utf-8-sig") if STORY_PATH.is_file() else f"# {TITLE}\n"
  blocks = md_html(story)
  season = game.season_of(world, today)
  first, last = game.season_range(world, season)
  day = "開幕前" if today < first else f"{(today - first).days + 1} 日目（{(last - first).days + 1} 日のうち）"
  places = world["places"]
  areas = []
  for _, area, members in area_groups(world, world["towns"]):
    towns = "・".join(
      f'<a href="towns.html#{escape(t)}">{escape(places[t].get("emoji", ""))} {escape(places[t]["name"])}</a>'
      for t in members
    )
    areas.append(f"<li>{name_html(area['name'], area.get('en'))}: {towns}</li>")
  status = (
    '<div class="card"><h3>いまの冒険</h3><table class="info"><colgroup><col style="width:11em"><col></colgroup>'
    f"<tr><th>星の巡り</th><td>シーズン{season}（{first}〜{last}）・{day}</td></tr>"
    f'<tr><th>旅をしている勇者</th><td>{len(entries)} 人（<a href="heroes.html">勇者の一覧</a>）</td></tr>'
    f'<tr><th>旅のできる地方</th><td><ul class="plain">{"".join(areas)}</ul></td></tr>'
    "</table></div>"
  )
  # 物語の最初の見出し（## …）の前に、いまの冒険の状況を入れる
  at = next((i for i, b in enumerate(blocks) if b.startswith('<h2 class="area">')), len(blocks))
  body = blocks[:at] + [status] + blocks[at:]
  return page("ストーリー", "".join(body), "index.html")


# ---- 地図 -------------------------------------------------------------------------------
def build_map(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  svg = mapsvg.render(world, entries)
  body = [f"<h2>世界の地図</h2>{updated(today)}"]
  if svg:
    svg = svg[svg.index("<svg") :]
    body.append(
      '<div id="map">' + svg + '<div class="zoom"><button id="zin" title="拡大">＋</button>'
      '<button id="zout" title="縮小">－</button><button id="zhome" class="wide">全体</button></div></div>'
      '<p class="note">ホイールやボタンで拡大、ドラッグで移動できます。町や勇者の印（赤い丸）にマウスを乗せると説明が出ます。'
      f"道の上の数字は移動にかかる行動回数です（勇者は 1 日に {world['season']['actions_per_day']} 回行動します）。</p>"
    )
  else:
    body.append('<p class="note">地図はありません（world/map.json に layout がありません）。</p>')
  return page("地図", "".join(body), "map.html", script=MAP_JS if svg else "")


# ---- ランキング ---------------------------------------------------------------------------
def build_ranking(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  season = game.season_of(world, today)
  first, last = game.season_range(world, season)
  views = [(h, view.season_view(world, s, season)) for h, s in entries]
  body = [f"<h2>シーズン{season}（{first}〜{last}）</h2>{updated(today)}"]
  news = view.news(views, today)
  body.append(
    '<div class="card"><h3>📰 今日の出来事</h3>'
    + ("".join(f"<div>{escape(n)}</div>" for n in news) or '<div class="note">まだない</div>')
    + "</div>"
  )
  cards = []
  for title, _, key, value, unit in view.RANKINGS:
    rows = "".join(
      f'<tr><td class="num">{r}</td><td><a href="{hero_href(h["id"])}">{escape(h["name"])}</a></td>'
      f'<td class="num">{"Lv." if title == "レベル" else ""}{value(s)}{"" if title == "レベル" else unit}</td></tr>'
      for r, h, s in view.ranked(views, key)[: view.RANKING_TOP]
    )
    cards.append(
      f'<div class="card"><h3>{escape(title)}</h3>'
      + (f"<table>{rows}</table>" if rows else '<div class="note">まだいない</div>')
      + "</div>"
    )
  body.append('<div class="grid">' + "".join(cards) + "</div>")
  return page("ランキング", "".join(body), "ranking.html")


# ---- 勇者 ---------------------------------------------------------------------------------
def build_heroes(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  """旅をしている勇者の一覧（居場所・能力・装備・今日の出来事。名前から詳細のページへ）。"""
  season = game.season_of(world, today)
  views = [(h, view.season_view(world, s, season)) for h, s in entries]
  body = [f"<h2>勇者たち</h2>{updated(today)}"]
  heroes = []
  for h, s in views:
    hero, st = s["hero"], game.stats(world, s)
    last_ = s["last"]
    events = (
      view.highlights(last_["events"]) if last_ and last_["date"] == today.isoformat() and last_["events"] else []
    )
    heroes.append(
      f'<div class="card"><h3><a href="{hero_href(h["id"])}">{escape(h["name"])}</a> '
      f"Lv.{hero['level']}</h3><div>{escape(view.location(world, s))}</div>"
      f"<div>HP {hero['hp']}/{st['max_hp']}　攻{st['atk']} 防{st['def']} 速{st['spd']} 器{st['dex']}　{hero['gold']}G</div>"
      f"<div>"
      f"特性: {escape(game.hero_trait(world, s).get('name', '-'))}</div>"
      f"<div>装備: {escape(view.gear_text(world, s))}</div>"
      f'<div class="note">今日: {escape("、".join(events) or "—")}</div></div>'
    )
  body.append('<div class="grid">' + ("".join(heroes) or '<p class="note">まだ勇者がいません。</p>') + "</div>")
  return page("勇者", "".join(body), "heroes.html")


# ---- タブと絞り込み（町・ギルド・アイテムのページで使う）------------------------------------------
# タブ（1 つずつ切り替える。URL の #タブの ID か、中の要素の ID（町など）で、開くタブを選べる。
# JavaScript がなければタブは出さず、全部の中身を順に見せる）。切り替えると tabchange を送る
TABS_JS = """
(()=>{const bar=document.querySelector('.tabs'),tabs=[...document.querySelectorAll('.tab[data-tab]')],
panels=[...document.querySelectorAll('.tab-panel')];if(!bar||!tabs.length)return;bar.hidden=false;
const show=(id,push)=>{tabs.forEach(t=>{const on=t.dataset.tab===id;t.setAttribute('aria-selected',on?'true':'false');
t.tabIndex=on?0:-1;});panels.forEach(g=>{g.hidden=g.dataset.tab!==id;});
if(push)history.replaceState(null,'',location.pathname+location.search+'#'+id);
document.dispatchEvent(new Event('tabchange'));};
tabs.forEach((t,i)=>{t.addEventListener('click',()=>show(t.dataset.tab,true));
t.addEventListener('keydown',e=>{const d={ArrowRight:1,ArrowLeft:-1}[e.key];if(!d)return;e.preventDefault();
const n=tabs[(i+d+tabs.length)%tabs.length];show(n.dataset.tab,true);n.focus();});});
const fromHash=()=>{let raw=location.hash.slice(1);try{raw=decodeURIComponent(raw);}catch(e){}
const el=raw&&document.getElementById(raw),panel=el&&el.closest('.tab-panel');
show(tabs.some(t=>t.dataset.tab===raw)?raw:panel?panel.dataset.tab:tabs[0].dataset.tab,false);
if(panel&&el!==panel)el.scrollIntoView();};
window.addEventListener('hashchange',fromHash);fromHash();})();
"""

# 絞り込み（レアリティ・地形。複数選べて、何も選ばなければすべて）。表の行（tr[data-f]）を隠し、
# 絞り込んで行のなくなったカード（.board）も隠す（絞り込んでいなければ、空のカードも出す）。
# 合うものがないという文は、今のタブにカードがある（まだ開放されていない、などでない）ときだけ出す
FILTER_JS = """
(()=>{const chips=[...document.querySelectorAll('.chip[data-f]')],none=document.getElementById('f-none'),
clear=document.getElementById('f-clear');if(!chips.length)return;
const apply=()=>{const on=new Set(chips.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.f));let shown=0;
document.querySelectorAll('.board').forEach(card=>{let n=0;
card.querySelectorAll('tr[data-f]').forEach(tr=>{const ok=!on.size||on.has(tr.dataset.f);tr.hidden=!ok;if(ok)n++;});
card.hidden=on.size>0&&n===0;const panel=card.closest('.tab-panel');if(!card.hidden&&!(panel&&panel.hidden))shown++;});
const vis=[...document.querySelectorAll('.tab-panel')].filter(p=>!p.hidden);
const boards=!vis.length||vis.some(p=>p.querySelector('.board'));none.hidden=!on.size||shown>0||!boards;clear.hidden=!on.size;};
chips.forEach(c=>c.addEventListener('click',()=>{
c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
clear.addEventListener('click',()=>{chips.forEach(c=>c.setAttribute('aria-pressed','false'));apply();});
document.addEventListener('tabchange',apply);apply();})();
"""
OTHER_AREA = "_other"  # 地方が決まっていない町のまとまり（地方の ID は _ で始められないので、ぶつからない）


def tab_bar(tabs: list[tuple[str, str]]) -> str:
  """タブの並び（[(ID, 名前)]。最初のタブを選ぶ）。JavaScript が出す（なければ全部の中身を順に見せる）。"""
  buttons = "".join(
    f'<button type="button" class="tab" role="tab" id="tab-{escape(tid)}" aria-controls="panel-{escape(tid)}" '
    f'data-tab="{escape(tid)}" aria-selected="{"true" if i == 0 else "false"}" tabindex="{0 if i == 0 else -1}">'
    f"{escape(name)}</button>"
    for i, (tid, name) in enumerate(tabs)
  )
  return f'<div class="tabs" role="tablist" hidden>{buttons}</div>'


def tab_panel(tid: str, heading: str, inner: str) -> str:
  """タブの中身（heading は見出しの HTML）。"""
  return (
    f'<section class="tab-panel" role="tabpanel" id="panel-{escape(tid)}" aria-labelledby="tab-{escape(tid)}" '
    f'data-tab="{escape(tid)}"><h2 class="area">{heading}</h2>{inner}</section>'
  )


def rarity_filter(chips: list[tuple[str, str]], empty: str) -> str:
  """レアリティの絞り込みのボタン（[(値, ★ の印)]）と、合うものがないときの文。"""
  return chip_filter("レアリティ", [(v, f'<span class="chip-mark">{escape(mark)}</span>') for v, mark in chips], empty)


def chip_filter(label: str, chips: list[tuple[str, str]], empty: str) -> str:
  """絞り込みのボタン（[(値, ボタンの中の HTML)]）と、合うものがないときの文（FILTER_JS で動かす。
  表の行は tr[data-f]、隠す単位のカードは .board）。"""
  buttons = "".join(
    f'<button type="button" class="chip" data-f="{escape(v)}" aria-pressed="false">{inner}</button>'
    for v, inner in chips
  )
  return (
    f'<div class="filters card"><div class="f-row"><span class="f-label">{escape(label)}</span>{buttons}</div>'
    '<div class="note">押して選ぶ（複数選べます。選ばなければすべて）'
    '<button type="button" id="f-clear" class="link" hidden>選択を解除</button></div></div>'
    f'<p id="f-none" class="note" hidden>{escape(empty)}</p>'
  )


def area_groups(world: dict, towns: list[str]) -> list[tuple[str, dict, list[str]]]:
  """町を地方ごとにまとめる: [(地方の ID, 地方, 町)]。開放した町のある地方を先に（同じなら areas の順）。"""
  places, opened = world["places"], set(world["towns"])
  groups = [(aid, a, [t for t in towns if places[t].get("area") == aid]) for aid, a in world["areas"].items()]
  groups.append((OTHER_AREA, {"name": "その他"}, [t for t in towns if places[t].get("area") not in world["areas"]]))
  groups = [g for g in groups if g[2]]
  groups.sort(key=lambda g: not any(t in opened for t in g[2]))  # 安定ソート
  return groups


def name_html(name: str, en: str | None) -> str:
  """名前に英語名を添える。"""
  return escape(name) + (f' <span class="en">{escape(en)}</span>' if en else "")


# ---- ギルド -------------------------------------------------------------------------------
def guild_status(world: dict, e: dict, today: date) -> str:
  """掲示板の依頼の状態。誰かが受けた依頼は、名前を出さずに「ACCEPTED」のスタンプを押す。
  受付中なら、受けられる残りの日数。"""
  if e["status"] in ("held", "done", "failed"):
    note = {"done": "明日入れ替え", "failed": "明日また受付"}.get(e["status"])
    return '<span class="stamp">ACCEPTED</span>' + (f'<div class="note">{note}</div>' if note else "")
  last = game.last_open_day(world, e)
  left = (last - today).days
  if left < 0:
    return '<span class="free">受付終了</span>'
  when = f"あと {left} 日（{last.month}/{last.day} まで）" if left > 0 else "今日まで（明日入れ替え）"
  return f'<span class="open">受付中</span><div class="note">{when}</div>'


def build_guild(world: dict, entries: list[tuple[dict, dict]], today: date, guild: dict) -> str:
  """町ごとのギルドの掲示板（日をまたいで残る。早い者勝ち）。地方ごとのタブで、レアリティで絞り込める。"""
  season = game.season_of(world, today)
  if (
    not guild.get("towns")
    or guild.get("season") != season
    or any(guild.get("refreshed", {}).get(t, "") < today.isoformat() for t in world["towns"])
  ):
    # まだ誰も進めていない・その日の掲示板になっていない: その日の朝の掲示板を見せる（保存はしない）
    guild = copy.deepcopy(guild) if guild.get("season") == season else game.new_guild()
    game.refresh_guild(world, guild, today, season)
  places = world["places"]

  def card(town: str) -> str:
    p = places[town]
    rows = []
    for e in guild.get("towns", {}).get(town, []):
      mark, _, rest = game.quest_text(world, e).partition(" ")
      notes = ([f"「{escape(e['text'])}」"] if e.get("text") else []) + (
        ["前の勇者が失敗した依頼"] if e.get("returned") else []
      )
      exp_text = f"{e['reward']['exp']}EXP<br>" if e["reward"]["exp"] else ""  # 依頼の経験値は 0（お金だけ）
      rows.append(
        f'<tr data-f="{e["rank"]}"><td class="mark">{escape(mark)}</td><td>{escape(rest)}'
        + "".join(f'<div class="note">{n}</div>' for n in notes)
        + f'</td><td class="num">{e["cost"]}</td>'
        f'<td class="num">{exp_text}{e["reward"]["gold"]}G</td>'
        f"<td>{guild_status(world, e, today)}</td></tr>"
      )
    return (
      f'<div class="card board" id="{escape(town)}"><h3>{escape(p.get("emoji", ""))} {name_html(p["name"], p.get("en"))}'
      f'<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span></h3>'
      '<div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em"><col><col style="width:4.5em">'
      '<col style="width:6.5em"><col style="width:13em"></colgroup><tr><th></th><th>依頼</th><th class="num">行動力</th>'
      '<th class="num">報酬</th><th>状態</th></tr>' + "".join(rows) + "</table></div></div>"
    )

  groups = area_groups(world, world["towns"])
  body = [
    f"<h2>町のギルドの掲示板（{today.isoformat()}）</h2>",
    tab_bar([(aid, a["name"]) for aid, a, _ in groups]),
    rarity_filter([(str(i), r["mark"]) for i, r in enumerate(world["quest_ranks"], 1)], "条件に合う依頼はありません。"),
  ]
  for aid, area, members in groups:
    body.append(tab_panel(aid, name_html(area["name"], area.get("en")), "".join(card(t) for t in members)))
  return page("ギルド", "".join(body), "guild.html", script=FILTER_JS + TABS_JS)


# ---- 町 ---------------------------------------------------------------------------------
def build_towns(world: dict) -> str:
  """町の情報（説明・ギルドの規模・店の品ぞろえ・依頼で出るレア・道）。全部の町を地方ごとに出し
  （開放した町のある地方が先）、開放していない町は、店の品ぞろえなどを「-」にする。"""
  places, items = world["places"], world["items"]
  opened = set(world["towns"])
  towns = [t for t, p in places.items() if p["type"] == "town"]

  def label(iid: str) -> str:
    it = items[iid]
    return (
      f'<span class="chip-mark">{escape(game.rarity_mark(world, it["rarity"]))}</span>{escape(it["name"])}'
      f'<span class="note">（{escape(view.item_power(it, world))}）</span>'
    )

  p = world["season"]["potion"]  # 回復薬はどの町の店でも売る（装備のあとに出す）
  potion = (
    f'回復薬<span class="note">（{p["price"]}G。戦いで倒れそうになったときに使い、最大 HP の {p["heal_rate"]:.0%} まで回復。'
    f"町で {p['keep']} 個まで買い足す）</span>"
  )

  def rows_of(town: str) -> list[tuple[str, str]]:
    size = f"依頼の枠 {game.quests_per_day(world, town)} 件"
    if town not in opened:
      return [("ギルドの規模", size)] + [
        (k, "-") for k in ("依頼の報酬で出るレア", "店の品ぞろえ", "道（行き先と行動回数）")
      ]
    # 店の品ぞろえは、アイテムの種類（武器・兜・盾・装飾品）の順に、同じ種類なら値段の高い順
    shop = sorted(
      places[town].get("shop", []), key=lambda i: (W.SLOTS.index(items[i]["slot"]), -items[i].get("price", 0), i)
    )
    rares = sorted(
      (i for i, it in items.items() if town in it.get("towns", [])),
      key=lambda i: -world["rarities"].index(items[i]["rarity"]),
    )
    kinds = {"land": "陸路", "sea": "航路"}
    roads = [
      f"{escape(places[b]['name'])}（{kinds.get(world['road_info'][(town, b)]['kind'], '')} "
      f"{world['roads'][(town, b)]}）"
      for b in world["neighbors"][town]
    ]
    roads += [
      f"{escape(places[r['from'] if r['to'] == town else r['to']]['name'])}（ダンジョン・まだ入れない）"
      for r in world["trails"]
      if town in (r["from"], r["to"])
    ]
    return [
      ("ギルドの規模", f'{size}（<a href="guild.html#{escape(town)}">掲示板を見る</a>）'),
      (
        "依頼の報酬で出るレア",
        ("<br>".join(label(i) for i in rares) or "-")
        + ("" if world["season"].get("rare_drops", True) else '<div class="note">今はレアの配布を止めています</div>'),
      ),
      ("店の品ぞろえ", "<br>".join([label(i) for i in shop] + [potion])),
      ("道（行き先と行動回数）", "・".join(roads) or "-"),
    ]

  def card(town: str) -> str:
    p, is_open = places[town], town in opened
    return (
      f'<div class="card{"" if is_open else " closed"}" id="{escape(town)}"><h3>{escape(p.get("emoji", ""))} '
      f'{name_html(p["name"], p.get("en"))}<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span>'
      + ("" if is_open else '<span class="tag">未開放</span>')
      + "</h3>"
      + (f"<p>{escape(p['text'])}</p>" if p.get("text") else "")
      + '<div class="scroll"><table class="info"><colgroup><col style="width:11em"><col></colgroup>'
      + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows_of(town))
      + "</table></div></div>"
    )

  groups = area_groups(world, towns)
  body = ["<h2>町の情報</h2>", tab_bar([(aid, a["name"]) for aid, a, _ in groups])]
  for aid, area, members in groups:
    body.append(tab_panel(aid, name_html(area["name"], area.get("en")), "".join(card(t) for t in members)))
  return page("町", "".join(body), "towns.html", script=TABS_JS)


# ---- アイテム ---------------------------------------------------------------------------
def item_rows(world: dict) -> dict[str, list[dict]]:
  """開放した地域で手に入るアイテム（部位ごと。★ の多い順、同じ ★ なら強い順）:
  {部位: [{id, item, shops, towns}]}。
  shops は売っている町、towns は依頼の報酬で出る町（どちらも開放した町の ID）。"""
  places, items = world["places"], world["items"]
  shops: dict[str, list[str]] = {}
  for t in world["towns"]:
    for iid in places[t].get("shop", []):
      shops.setdefault(iid, []).append(t)
  rank = {r: i for i, r in enumerate(world["rarities"])}
  rows: dict[str, list[dict]] = {slot: [] for slot in W.SLOTS}
  for iid, it in items.items():
    towns = [t for t in it.get("towns", []) if t in world["towns"]]
    if iid in world["region_items"] or towns:
      rows[it["slot"]].append({"id": iid, "item": it, "shops": shops.get(iid, []), "towns": towns})
  for slot in rows:
    rows[slot].sort(
      key=lambda r: (-rank[r["item"]["rarity"]], -game.item_score(world, r["item"]), r["item"].get("price", 0))
    )
  return rows


SLOT_EMOJI = {"weapon": "⚔️", "helmet": "🪖", "shield": "🛡️", "accessory": "💍"}  # 部位の名前の先頭に付ける
ITEM_STATS = (("hp", "HP"), ("atk", "攻"), ("def", "防"), ("spd", "速"), ("dex", "器"))  # アイテムページの能力の列


def slot_label(slot: str) -> str:
  """部位の名前（先頭に絵文字。町の名前と同じ形）。"""
  return f"{SLOT_EMOJI.get(slot, '')} {W.SLOT_NAMES[slot]}".strip()


def town_pill(world: dict, town: str) -> str:
  """町の楕円の札（絵文字と名前。町のページへのリンク）。"""
  p = world["places"][town]
  label = f"{p.get('emoji', '')} {p['name']}".strip()
  return f'<a class="pill" href="towns.html#{escape(town)}">{escape(label)}</a>'


def stat_cell(value: int) -> str:
  """能力の値の欄（装備で増える・減る値なので +7・-1 のように符号を付ける。マイナスは赤字、0 は薄く）。"""
  cls = "num minus" if value < 0 else "num zero" if value == 0 else "num"
  return f'<td class="{cls}">{value:+d}</td>' if value else f'<td class="{cls}">0</td>'


def element_cell(world: dict, element: str | None) -> str:
  """属性の欄（「炎属性」などの枠の札）。属性がなければ空。"""
  e = world["elements"].get(element)
  return f'<td class="icon"><span class="tag">{escape(e["name"])}属性</span></td>' if e else '<td class="icon"></td>'


def element_label(world: dict, element: str | None) -> str:
  """属性の名前（炎 など）。属性がなければ -。"""
  e = world["elements"].get(element)
  return e["name"] if e else "-"


def build_items(world: dict) -> str:
  """開放した地域で手に入るアイテムの一覧。装備の種類ごとのタブで、レアリティで絞り込める。
  能力は HP・攻・防・速・器の列に分けて出す。入手方法は町の札（レアは報酬で出る依頼の町、ほかは買える店）。"""
  rows = item_rows(world)
  used = [r for r in world["rarities"] if any(x["item"]["rarity"] == r for rs in rows.values() for x in rs)]
  slots = [slot for slot in W.SLOTS if rows[slot]]  # 開放した地域で手に入る装備のない種類は、タブを出さない
  body = [
    "<h2>アイテム一覧</h2>",
    "" if world["season"].get("rare_drops", True) else '<p class="note">今はレア（★★ 以上）の配布を止めています。</p>',
    tab_bar([(slot, slot_label(slot)) for slot in slots]),
    rarity_filter([(r, game.rarity_mark(world, r)) for r in used], "条件に合うアイテムはありません。"),
  ]
  for slot in slots:
    trs = []
    for r in rows[slot]:
      it = r["item"]
      how = "".join(town_pill(world, t) for t in r["towns"] or r["shops"]) or "-"  # レアは依頼を出す町、ほかは店
      trs.append(
        f'<tr data-f="{it["rarity"]}"><td class="mark">{escape(game.rarity_mark(world, it["rarity"]))}</td>'
        f"<td>{escape(it['name'])}</td>"
        + element_cell(world, it.get("element"))
        + "".join(stat_cell(it.get(k, 0)) for k, _ in ITEM_STATS)
        + f'<td class="num">{str(it["price"]) + "G" if it.get("price") else "—"}</td><td>{how}</td></tr>'
      )
    table = (
      '<div class="card board"><div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em">'
      '<col style="width:13em"><col style="width:5em">'
      + '<col style="width:3em">' * len(ITEM_STATS)
      + '<col style="width:5.5em"><col></colgroup><tr><th></th><th>名前</th>'
      + '<th class="icon">属性</th>'
      + "".join(f'<th class="num">{name}</th>' for _, name in ITEM_STATS)
      + '<th class="num">値段</th><th>入手方法</th></tr>'
      + "".join(trs)
      + "</table></div></div>"
    )
    body.append(tab_panel(slot, escape(slot_label(slot)), table))
  return page("アイテム", "".join(body), "items.html", script=FILTER_JS + TABS_JS)


# ---- 魔物 ---------------------------------------------------------------------------------
MONSTER_STATS = ("hp", "atk", "def", "spd", "dex", "exp", "gold")  # 魔物のページの強さの列（Lv は名前の右に出す）
MONSTER_STAT_NAMES = ("HP", "攻", "防", "速", "器", "EXP", "G")


def terrain_label(terrain: str) -> str:
  """地形の名前に、地形の色の丸を添える（街道の円グラフと同じ色）。"""
  return f'<span class="tdot" style="background:var(--t-{escape(terrain)})"></span>' + escape(
    W.TERRAIN_NAMES.get(terrain, terrain)
  )


HABITATS = {"world": "WORLD", "raid": "RAID"}  # 道では出会わない魔物の生息地（強敵は WORLD、ボスは RAID）


def habitat(monster: dict) -> str:
  """魔物の生息地（絞り込みの値）。道の魔物は地形、強敵は world、ボスは raid。"""
  return "raid" if monster.get("boss") else "world" if monster.get("elite") else monster["terrain"]


def habitat_label(hid: str) -> str:
  """生息地の表示（地形は色の丸と名前、強敵・ボスは WORLD・RAID の札）。"""
  if hid in HABITATS:
    return f'<span class="habitat">{escape(HABITATS[hid])}</span>'
  return terrain_label(hid)


def terrain_shares(monsters: dict, pool: list) -> list[tuple[str, float]]:
  """道に出る魔物（[ID, 重み], ...）から、地形ごとの割合（戦いになったとき、その地形が選ばれる割合）。多い順。"""
  out: dict[str, float] = {}
  for mid, w in pool:
    out[monsters[mid]["terrain"]] = out.get(monsters[mid]["terrain"], 0) + w
  total = sum(out.values()) or 1
  return sorted(((t, w / total) for t, w in out.items()), key=lambda x: (-x[1], W.TERRAINS.index(x[0])))


def share_text(share: float) -> str:
  """割合の表示（四捨五入で 0% にならないように、1% 未満は「1%未満」）。"""
  return "1%未満" if share < 0.01 else f"{share:.0%}"


def pie_svg(shares: list[tuple[str, float]]) -> str:
  """地形の割合の円グラフ（12 時から時計回り。色は地形の色）。"""
  name = lambda t: W.TERRAIN_NAMES.get(t, t)  # noqa: E731
  if len(shares) == 1:
    t = shares[0][0]
    body = f'<circle r="1" fill="var(--t-{escape(t)})"><title>{escape(name(t))} 100%</title></circle>'
  else:
    body, start = "", 0.0
    for t, share in shares:
      end = start + share
      x0, y0 = math.sin(2 * math.pi * start), -math.cos(2 * math.pi * start)
      x1, y1 = math.sin(2 * math.pi * end), -math.cos(2 * math.pi * end)
      large = 1 if share > 0.5 else 0
      body += (
        f'<path d="M0,0 L{x0:.4f},{y0:.4f} A1,1 0 {large} 1 {x1:.4f},{y1:.4f} Z" fill="var(--t-{escape(t)})" '
        f'stroke="var(--card)" stroke-width="0.03"><title>{escape(name(t))} {share_text(share)}</title></path>'
      )
      start = end
  return f'<svg viewBox="-1.05 -1.05 2.1 2.1" role="img" aria-label="地形の割合">{body}</svg>'


def build_monsters(world: dict) -> str:
  """開放した道に出る魔物の一覧（地方ごとのタブ。Lv・生息地・性質・属性・強さ）。並びは Lv の順（同じ Lv は地形の順）。
  道で出会う魔物は勇者の Lv でも変わるので、魔物ごとの「出る道と割合」は出さない。道は、両端の町の地方に入れる。開放した道のない地方は、まだ開放されていないと出す。
  強敵（生息地 WORLD）とボス（RAID）も同じ表に、道の魔物のあとに出す（開放した町のある地方だけ。Lv は N/A）。"""
  places, monsters, traits = world["places"], world["monsters"], world["traits"]
  towns = [t for t, p in places.items() if p["type"] == "town"]
  order = {t: i for i, t in enumerate(W.TERRAINS)}
  place_order = {p: i for i, p in enumerate(places)}

  shown: set[str] = set()  # 表に出した魔物の生息地（絞り込みのボタンは、表に出したものだけ）

  def boss_area(m: dict) -> str:  # ボスのいるダンジョンの地方（地方がない・知らない地方なら、その他のまとまり）
    area = places[m["boss"]].get("area")
    return area if area in world["areas"] else OTHER_AREA

  def table(aid: str, members: list[str]) -> str:
    """この地方の魔物: 町につながる開放した道の魔物、町の討伐の依頼で狙う強敵（WORLD）、ダンジョンのボス（RAID）。
    並びは道の魔物（Lv の順）→ 強敵 → ボス。強敵・ボスは、開放した町のある地方だけ。"""
    seen: set[str] = set()
    for (a, b), info in world["road_info"].items():
      if place_order[a] > place_order[b] or not ({a, b} & set(members)):
        continue  # 同じ道は 1 度だけ。この地方（のまとまり）の町につながる道だけ
      seen |= {mid for mid, _ in info["monsters"]}
    if not seen:
      return '<div class="card"><p class="note">まだ開放されていません（この地方の道は通れません）。</p></div>'
    elites = [
      q["monster"]
      for town in members
      if town in world["towns"]
      for q in world["town_quests"].get(town, [])
      if q.get("type") == "hunt" and monsters.get(q.get("monster"), {}).get("elite")
    ]
    bosses = [mid for mid, m in monsters.items() if m.get("boss") in places and boss_area(m) == aid]
    road = sorted(seen, key=lambda m: (monsters[m]["level"], order.get(monsters[m]["terrain"], 99), m))
    others = [m for m in list(dict.fromkeys(elites)) + bosses if m not in seen]
    shown.update(habitat(monsters[m]) for m in road + others)
    rows = [monster_row(mid) for mid in road + others]
    return (
      monster_table(rows)
      + '<p class="note">HP・攻・防は、性質の効果がかかる前の値です（たとえば「硬い」は戦いで守りが固くなります）。'
      '道で出会うのは、その道の地形の魔物のうち、勇者の Lv に近い魔物です（<a href="guide.html">手引き</a>）。'
      "生息地が WORLD の魔物（強敵）は道では出会わず、町の討伐の依頼で狙って戦います。"
      "RAID の魔物（ボス）はダンジョンの奥にいます（まだ挑めません）。</p></div>"
    )

  def monster_row(mid: str) -> str:  # 魔物の 1 行（強敵・ボスの Lv は N/A。道の魔物と Lv の意味が違うので）
    m = monsters[mid]
    trait = traits.get(m.get("trait"), {})
    hid = habitat(m)
    level = "N/A" if hid in HABITATS else m["level"]
    return (
      f'<tr data-f="{escape(hid)}"><td>{escape(m["name"])}</td>'
      f'<td class="num">{level}</td><td>{habitat_label(hid)}</td>'
      + (
        f'<td class="icon"><span class="tag">{escape(trait["name"])}</span></td>' if trait else '<td class="icon"></td>'
      )
      + element_cell(world, m.get("element"))
      + "".join(f'<td class="num">{m[k]}</td>' for k in MONSTER_STATS)
      + "</tr>"
    )

  def monster_table(rows: list[str]) -> str:  # 魔物の表（カードを開いたまま返す）
    return (
      '<div class="card board"><div class="scroll"><table class="wide-table monsters"><colgroup><col style="width:7em">'
      '<col style="width:3em"><col style="width:5em"><col style="width:5em"><col style="width:5em">'
      + '<col style="width:3.5em">'
      * len(MONSTER_STATS)
      + '</colgroup><tr><th>名前</th><th class="num">Lv</th><th>生息地</th>'
      '<th class="icon">性質</th><th class="icon">属性</th>'
      + "".join(f'<th class="num">{name}</th>' for name in MONSTER_STAT_NAMES)
      + "</tr>"
      + "".join(rows)
      + "</table></div>"
    )

  def road_card(members: list[str]) -> str:  # この地方の町につながる、開放した街道と、その地形の割合
    items = []
    for (a, b), info in world["road_info"].items():
      if place_order[a] > place_order[b] or not ({a, b} & set(members)):
        continue
      shares = terrain_shares(monsters, info["monsters"])
      legend = "".join(f"<li>{terrain_label(t)} {share_text(share)}</li>" for t, share in shares)
      items.append(
        f'<div class="road">{pie_svg(shares)}<div><div class="name">{escape(places[a]["name"])}〜'
        f"{escape(places[b]['name'])}（{world['roads'][(a, b)]}）</div><ul>{legend}</ul></div></div>"
      )
    if not items:
      return ""
    return (
      '<div class="card"><h3>街道</h3><div class="roads">'
      + "".join(items)
      + '</div><p class="note">かっこの中は移動にかかる行動回数。円グラフは、道で戦いになったときに、どの地形の魔物が出るかの割合'
      "（その地形の魔物のうち、勇者の Lv に近い魔物が出る）。</p></div>"
    )

  groups = area_groups(world, towns)
  guide = '<p class="note">性質・属性・素早さなどの説明は、<a href="guide.html">手引き</a>にまとめています。</p>'
  panels = []
  for aid, area, members in groups:
    panels.append(
      tab_panel(
        aid,
        name_html(area["name"], area.get("en")),
        road_card(members) + table(aid, members),
      )
    )
  chips = [(h, habitat_label(h)) for h in (*W.TERRAINS, *HABITATS) if h in shown]  # 表に出した生息地だけ
  body = [
    "<h2>魔物の情報</h2>",
    chip_filter("生息地", chips, "条件に合う魔物はいません。"),
    tab_bar([(aid, a["name"]) for aid, a, _ in groups]),
    guide,
    *panels,
  ]
  return page("魔物", "".join(body), "monsters.html", script=FILTER_JS + TABS_JS)


# ---- 手引き -------------------------------------------------------------------------------
def element_chart(world: dict) -> str:
  """属性の相性の並び（炎→氷→…→炎。左が右に強い）。輪になっていなければ、組ごとに出す。"""
  elements = world["elements"]
  chart, cur = [], next(iter(elements), None)  # 強い相手をたどって、輪にする
  while cur in elements and cur not in chart:
    chart.append(cur)
    cur = elements[cur]["strong"]
  if chart and len(chart) == len(elements) and cur == chart[0]:
    return "→".join(escape(element_label(world, e)) for e in chart + [cur])
  return "・".join(
    f"{escape(element_label(world, eid))}→{escape(element_label(world, e['strong']))}"
    for eid, e in elements.items()
    if e["strong"] in elements
  )


# 属性と竜の部位・徳の対応（docs/MYTH.md）。属性の id は world/elements.json のもの
MYTH_ELEMENT = {"flame": ("心臓", "いのち"), "ice": ("牙", "かぎり"), "thunder": ("頭", "こころざし")}


def build_myth_cards(world, table):
  """手引きの「世界のなりたち」の節（カードのリスト）。語りは民話の調子で、確かでないことは「言われている」と書く"""
  months = world["season"]["months"]
  cards = [
    (
      "はじまりの話",
      "<p>はじめに、海だけがあった。海は「おおうみ」といい、海そのものがからだだったという。"
      "おおうみは、底からてのひらをさしあげて、大地をつくった。てのひらの上には星をならべ、"
      "最後に息を吹いて、はじまりの竜（始祖竜）を生んだ。</p>"
      "<p>そののち、竜は星といさかいをして、大地に落ちた。竜気が、いまも獣を魔物にしている。</p>"
      '<p class="note">くわしい話は、世界のページの「はじまりの話」にある</p>',
    ),
    (
      "星の巡りと加護",
      f"<p>星の巡りは、{escape(str(months))} か月ごとにやってくる。星が勇者に加護（レベル）を貸し、"
      "巡りが変わると、加護は空へ還る。勇者の力が最初からやりなおしになるのは、このため。</p>"
      "<p>人が死ぬと、からだは海へ帰り、名は空へ還り、形は大地に残ると言われている。"
      "名は空へ還るので、レベルは巡りごとに戻る。形は大地に残るので、アイテムは残る。</p>",
    ),
  ]
  rows = []
  for eid, (part, virtue) in MYTH_ELEMENT.items():
    el = world.get("elements", {}).get(eid)
    if el:
      rows.append(
        (
          f'<span class="tag">{escape(el["name"])}属性</span>',
          escape(part),
          escape(virtue),
          escape(el.get("ore", "")),
        )
      )
  if rows:
    cards.append(
      (
        "三つの徳",
        table(("属性", "始祖竜の部位", "徳", "鉱石"), rows)
        + '<p class="note">始祖竜は、いのち・かぎり・こころざしを一つに持っていたが、'
        "大地に落ちて三つにちぎれた。かぎりはいのちを鎮め（氷→炎）、いのちはこころざしを焼き（炎→雷）、"
        "こころざしはかぎりを砕く（雷→氷）。属性の相性は、この関係そのもの</p>",
      )
    )
  cards.append(
    (
      "三つの教えと争い",
      "<p>竜と星のいさかいの理由は、教えによって語りが違う。どれも、そう言われている、という話。</p>"
      + table(
        ("教え", "いさかいの理由（そう言われている）", "重んじる徳"),
        [
          (escape("星読み"), escape("竜が傲り、めぐりを呑もうとした。星はめぐりを守った"), escape("かぎり")),
          (escape("竜の民"), escape("竜は生き物を解き放とうとした。星は看守だった"), escape("いのち")),
          (
            escape("王国の教え"),
            escape("竜も星も被造物。法と秩序を守って、おおうみが目覚める日に備える"),
            escape("こころざし"),
          ),
          (escape("土地の古い信仰"), escape("神は土地ごとにいる。竜の血が根になり、風になった"), escape("土地")),
        ],
      )
      + '<p class="note">人の争いも、三つの徳の相性に重なる。星読みは竜の民を抑え、竜の民の熱は王国の秩序を揺るがし、'
      "王国は星読みを従わせようとする。どこにも決定的な勝者はいない</p>"
      + '<p class="note">年寄りの言葉: 「星の灯りは、ただではない」「貸した手より、返す手のほうが冷たい」</p>',
    )
  )
  return cards


def build_guide(world: dict) -> str:
  """手引き: 能力・勇者の特性・魔物の性質・属性と相性・戦いの決まりの説明（値は world/ から）。"""
  cfg = world["season"]
  growth, lv_max = cfg["growth"], cfg["max_level"]

  def table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:  # rows は HTML（エスケープ済み）
    return (
      '<div class="scroll"><table class="guide"><tr>'
      + "".join(f"<th>{h}</th>" for h in head)
      + "</tr>"
      + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
      + "</table></div>"
    )

  def tag(name: str) -> str:
    return f'<span class="tag">{escape(name)}</span>'

  hp1, hp_up = cfg["hero"]["max_hp"], cfg["level_up"]["hp"]
  crit = cfg["critical"]
  stats = [
    (
      "HP",
      f"体力。0 になると力尽きる。Lv1 で {hp1}、レベルが上がるごとに +{hp_up}（Lv{lv_max} で {hp1 + hp_up * (lv_max - 1)}。"
      "特性で変わる）",
    ),
    ("攻", "攻撃。与えるダメージが増える"),
    ("防", "防御。受けるダメージが減る（相手の攻撃から、防御の半分を引く）"),
    ("速", "素早さ。先に攻撃できるか、2 回攻撃できるかが決まる"),
    ("器", f"器用さ。会心の一撃の出やすさ（器用さ ÷ {crit['dex_divisor']} の確率）"),
  ]
  names = {"atk": "攻", "def": "防", "spd": "速", "dex": "器"}
  stat_note = (
    "・".join(f"{names[k]}は Lv1 で {growth[k][0]}、Lv{lv_max} で {growth[k][-1]}" for k in names if k in growth)
    + "。伸びる時期は能力ごとに違い、特性で伸び方が変わる。装備でも増える・減る"
  )
  hero_traits = [(tag(t["name"]), escape(t.get("text", ""))) for t in cfg["hero_traits"].values()]
  monster_traits = [(tag(t["name"]), escape(t.get("text", ""))) for t in world["traits"].values()]
  elements, rates = world["elements"], cfg["element_rates"]
  weak_to: dict[str, list[str]] = {}  # その属性に強い属性（いくつあってもよい）
  for eid, e in elements.items():
    weak_to.setdefault(e["strong"], []).append(eid)
  element_rows = [
    (
      tag(f"{e['name']}属性"),
      escape(element_label(world, e["strong"])),
      escape("・".join(element_label(world, w) for w in weak_to.get(eid, [])) or "-"),
      escape(e.get("ore", "-")),
    )
    for eid, e in elements.items()
  ]
  potion = cfg["potion"]
  shifts = "・".join(  # 危ない配達で上がる魔物の Lv（配達の設定がない世界では、行を出さない）
    f"危なさ {n} は +{d['shift']}"
    for n, d in enumerate((world.get("deliveries") or {}).get("dangers", []))
    if d.get("shift")
  )
  rules = [
    ("ダメージ", "攻撃 − 相手の防御 ÷ 2（少しだけ上下する。1 より小さくはならない）"),
    (
      "会心の一撃",
      f"器用さ ÷ {crit['dex_divisor']} の確率で、ダメージが {crit['multiplier']} 倍になる。魔物にも器用さがある",
    ),
    ("先に攻撃", "素早いほうが先に攻撃する。性質「先手」の魔物は、素早さに関係なく先に攻撃する"),
    ("2 回攻撃", "相手の 2 倍以上素早ければ、1 回の番に 2 回攻撃する（魔物も同じ）"),
    (
      "何体かと戦う",
      "討伐の依頼で魔物が何体かいたら、1 行動のなかで 1 体ずつ続けて戦う（HP は減ったまま次へ。道の戦いは 1 体だけ）。"
      "倒すたびに経験値とお金が入り、途中で力尽きても倒した分は残る",
    ),
    ("回復薬", f"HP が 0 になりそうなとき、自動で使う。最大 HP の {potion['heal_rate']:.0%} まで回復する"),
    (
      "武器の持ち替え",
      "勇者は戦いのたびに、持ち物の中からいちばん効く武器（属性・2 回攻撃も考える）に持ち替える。"
      "苦手な属性の武器しかなく、持たないほうが効くときは、武器を持たずに戦う。"
      "装飾品は、弱点を突ける属性のものを持っていれば、その戦いだけ着け替える（苦手な属性なら外す）",
    ),
    *(
      [
        (
          "危ない配達",
          f"危なさのある配達（高価な品・狙われる品）を運んでいる間は、道で出会う魔物の Lv が上がる（{shifts}）",
        )
      ]
      if shifts
      else []
    ),
    (
      "道で出会う魔物",
      "戦いになったら、まず道の地形（草原・森など。道ごとの割合は魔物のページの「街道」）を選び、その地形の魔物のうち、Lv が勇者の Lv "
      + "〜".join(f"{d:+d}" for d in cfg["encounter_levels"])
      + " の魔物から出る（同じ地形の中では同じくらいずつ）。Lv の合う魔物のいない地形は出ず、その分ほかの地形が出る。"
      "範囲に合う魔物がどの地形にもいなければ、いちばん近い Lv の魔物",
    ),
    ("強敵", "道では出会わない。町の討伐の依頼で狙って戦う。依頼は、魔物のレベル以上の勇者だけが受けられる"),
    ("ボス", "道では出会わない。ダンジョンの奥にいる"),
  ]
  reward = cfg["monster_reward"]
  names_all = {"hp": "HP", "atk": "攻", "def": "防", "spd": "速", "dex": "器"}
  score_text = " + ".join(f"{names_all[k]}" + (f" × {w}" if w != 1 else "") for k, w in reward["weights"].items() if w)
  trait_bonus = "・".join(
    f"{t['name']}は" + "・".join(f"{names_all.get(k, k)} {v:+g}" for k, v in t["score"].items())
    for t in world["traits"].values()
    if t.get("score")
  )
  reward_rows = [
    (
      "強さ点",
      escape(
        f"{score_text}。性質の効果がかかったあとの値で数える（凶暴・硬い・群れ）"
        + (f"。{trait_bonus}として数える" if trait_bonus else "")
      ),
    ),
    ("経験値", escape(f"強さ点 × 強さ点 ÷ {reward['exp_divisor']:g}（四捨五入）")),
    (
      "お金",
      escape(
        f"経験値 × {reward['gold_rate']:g}"
        + "".join(
          f"（{t['name']}の魔物はさらに × {t['gold_rate']:g}）"
          for t in world["traits"].values()
          if t.get("gold_rate", 1) != 1
        )
        + "。最後に四捨五入"
      ),
    ),
  ]
  level_rows = "".join(
    f'<tr><td class="num">Lv{lv}</td>'
    + "".join(f'<td class="num">{v}</td>' for v in (b["max_hp"], b["atk"], b["def"], b["spd"], b["dex"]))
    + "</tr>"
    for lv in range(1, lv_max + 1)
    for b in [game.base_stats(world, None, lv)]
  )
  level_table = (
    '<div class="scroll"><table class="levels"><tr><th class="num">Lv</th>'
    + "".join(f'<th class="num">{h}</th>' for h in ("HP", "攻", "防", "速", "器"))
    + "</tr>"
    + level_rows
    + "</table></div>"
  )
  cards = [
    (
      "能力",
      table(("能力", "説明"), stats)
      + f'<p class="note">{escape(stat_note)}</p>'
      + '<h4 class="sub">レベルごとの能力（特性の倍率がかかる前の値。強運の勇者と同じ。ほかの特性と装備で変わる）</h4>'
      + level_table,
    ),
    (
      "勇者の特性",
      table(("特性", "効果"), hero_traits)
      + '<p class="note">勇者を作るときにランダムで決まり、シーズンが変わっても変わらない</p>',
    ),
    (
      "魔物の性質",
      table(("性質", "効果"), monster_traits)
      + '<p class="note">魔物のページの HP・攻・防は、性質の効果がかかる前の値</p>',
    ),
  ]
  if elements:
    cards.append(
      (
        "属性と相性",
        f"<p>相性（左が右に強い）: {element_chart(world)}</p>"
        + table(("属性", "強い相手", "苦手な相手", "鉱石"), element_rows)
        + f'<p class="note">強い相手を攻めるとダメージ {rates["strong"]} 倍、苦手な相手だと {rates["weak"]} 倍。'
        "属性は魔物と一部の武器・装飾品にある（属性のない魔物・武器もある）。装飾品の属性は、武器に属性がないとき攻撃に付く。"
        "属性の武器は、その属性の力を持つ鉱石から作られる</p>",
      )
    )
  points = world["season"]["item_points"]
  labels = dict(ITEM_STATS)
  weight_rows = [
    (escape(labels[k]), escape(f"{v:g} 点"), escape(f"{1 / v:g}" if v else "—")) for k, v in points["weights"].items()
  ] + [("属性", escape(f"{points.get('element', 0):g} 点"), "属性 1 つ")]
  rarity_rows = [
    (
      escape(game.rarity_mark(world, r)),
      escape(f"{points['max'][r]:g} 点まで"),
      escape("なし" if r == W.COMMON else "付くことがある（武器・装飾品）"),
    )
    for r in world["rarities"]
  ]
  # 例には、マイナスの能力のあるアイテム（なければ最初のアイテム）を使う
  items = list(world["items"].values())
  sample = next((i for i in items if any(i.get(k, 0) < 0 for k, _ in ITEM_STATS)), items[0])
  parts = [(labels[k], sample[k], points["weights"][k] * sample[k]) for k, _ in ITEM_STATS if sample.get(k)]
  example = (
    f"例: {sample['name']}（{'・'.join(f'{n} {v:+d}' for n, v, _ in parts)}）は "
    + " ".join(f"{'−' if p < 0 else '+'} {abs(p):g}" for _, _, p in parts).lstrip("+ ")
    + f" = {W.item_points(sample, points):g} 点"
  )
  cards.append(
    (
      "アイテムの強さ",
      "<p>アイテムの強さは点数で決まる。能力の増減に、能力ごとの点数を掛けて足す（マイナスの能力も同じ点数で引く）</p>"
      + table(("能力", "+1 あたり", "1 点になる量"), weight_rows)
      + '<h4 class="sub">珍しさごとの点数</h4>'
      + table(("珍しさ", "点数の上限", "属性"), rarity_rows)
      + f'<p class="note">{escape(example)}。勇者は点数の高いアイテムを装備し、店でも点数の高いものを買う'
      "（同じ点数なら、マイナスの能力のないもの）</p>",
    )
  )
  quest_rows = [
    ("期限", "なし。必要な行動力をこなせば終わる。ふつう、失敗するのは力尽きたときだけ"),
    ("引き返す", "引き返さない。HP が減っても、依頼が終わるか力尽きるまで進む"),
    (
      "力尽きた",
      "最後に寄った町で目を覚ます（HP 全回復。お金・持ち物・回復薬は減らない）。受けていた依頼は失敗になり、その日はもう行動しない",
    ),
    (
      "依頼の失敗",
      "報酬はもらえない（それまでの戦いの経験値・お金・アイテムは残る）。掲示板の依頼は翌朝、最初の状態で受付中に戻る",
    ),
  ]
  cards.append(("依頼の進め方", table(("場面", "どうなるか"), quest_rows)))
  cards.append(("戦いの決まり", table(("決まり", "説明"), rules)))
  cards.append(("魔物の経験値とお金", table(("項目", "決め方"), reward_rows)))
  cards.extend(build_myth_cards(world, table))
  body = "<h2>手引き</h2>" + "".join(f'<div class="card"><h3>{escape(h)}</h3>{inner}</div>' for h, inner in cards)
  return page("手引き", body, "guide.html")


# ---- 勇者の詳細 ---------------------------------------------------------------------------
def build_hero(world: dict, hero: dict, state: dict) -> str:
  body = (
    f"<h2>{escape(hero['name'])}（{escape(hero['id'])}）</h2>"
    f"<pre>{escape(view.build_text(world, state))}</pre>"
    '<p><a href="../heroes.html">勇者の一覧へ戻る</a></p>'
  )
  return page(hero["name"], body, "", depth=1)


# ---- 書き出し -----------------------------------------------------------------------------
def check_out(out: Path) -> None:
  """書き出す先を消してよいか確かめる（中身を作り直すので、リポジトリやほかのディレクトリを消さないように）。
  消してよいのは、まだないか、空か、前に書き出したサイト（index.html と guild.html がある）だけ。"""
  target, root = out.resolve(), common.ROOT.resolve()
  if (
    target == root
    or target in root.parents
    or (
      root in target.parents
      and target.relative_to(root).parts[0]
      in ("heroes", ".state", "world", "scripts", "tests", "docs", ".github", "tools")
    )
  ):
    raise SystemExit(
      f"{out} には書き出せません（リポジトリのファイルを消してしまうため。_site などを指定してください）"
    )
  if out.exists() and (
    not out.is_dir() or (any(out.iterdir()) and not ((out / "index.html").is_file() and (out / "guild.html").is_file()))
  ):
    raise SystemExit(
      f"{out} は前に書き出したサイトではないので、消して作り直せません（空のディレクトリか、新しい名前を指定してください）"
    )


def write_site(
  world: dict,
  entries: list[tuple[dict, dict]],
  today: date,
  guild: dict,
  out: Path,
  retired: list[tuple[dict, dict]] | None = None,
) -> list[Path]:
  """サイトを out に書き出す（out の中身は作り直す）。書き出したファイルの一覧を返す。
  retired（引退した勇者）は、詳細のページだけを書き出す（一覧やランキングには出さない）。"""
  check_out(out)
  if out.exists():
    shutil.rmtree(out)
  (out / "heroes").mkdir(parents=True)
  files = {
    "index.html": build_story(world, entries, today),
    "map.html": build_map(world, entries, today),
    "ranking.html": build_ranking(world, entries, today),
    "heroes.html": build_heroes(world, entries, today),
    "guild.html": build_guild(world, entries, today, guild),
    "items.html": build_items(world),
    "monsters.html": build_monsters(world),
    "guide.html": build_guide(world),
    "towns.html": build_towns(world),
  }
  for hero, state in entries + (retired or []):
    files[f"heroes/{hero['id']}.html"] = build_hero(world, hero, state)
  written = []
  for rel, text in files.items():
    path = out / rel
    path.write_text(text, encoding="utf-8")
    written.append(path)
  return written


def shown_day(entries: list[tuple[dict, dict]]) -> date:
  """サイトに出す日付: 勇者を最後に進めた日（毎日の実行の前に公開されても、日付が先に進んで中身が空にならないように）。"""
  days = [s["last_date"] for _, s in entries if s.get("last_date")]
  return date.fromisoformat(max(days)) if days else datetime.now(UTC).astimezone(common.JST).date()


def main() -> int:
  ap = argparse.ArgumentParser(description="GitHub Pages に載せるサイトを書き出す")
  ap.add_argument("out", help="書き出す先のディレクトリ（中身は作り直す）")
  ap.add_argument("--date", help="表示する日付（既定は勇者を最後に進めた日。まだ誰も進めていなければ今日の JST）")
  a = ap.parse_args()
  world = W.load_world()
  entries = store.current_entries(world)
  today = date.fromisoformat(a.date) if a.date else shown_day(entries)
  retired = store.current_entries(world, retired=True)
  written = write_site(world, entries, today, store.load_guild(), Path(a.out), retired)
  print(f"{a.out} に {len(written)} ファイルを書き出しました")
  return 0


if __name__ == "__main__":
  sys.exit(main())
