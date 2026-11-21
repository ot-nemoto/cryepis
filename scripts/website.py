#!/usr/bin/env python3
"""GitHub Pages に載せるサイト（HTML）を書き出す。冒険は進めない（今の状態を見せるだけ）。

ページ:
  index.html          ストーリー（world/story.md の物語・世界・歴史と、いまの冒険の状況）
  map.html            世界の地図（拡大・移動ができる。場所や勇者にマウスを乗せると説明が出る）
  ranking.html        今シーズンのランキングと、今日の出来事
  heroes.html         旅をしている勇者の一覧（居場所・能力・装備・今日の出来事）
  towns.html          町の情報（全部の町を地方ごとのタブで切り替える。説明・ギルドの規模・特産品・店・レア・道。開放していない町は「-」）
  items.html          開放した地域で手に入るアイテム（装備の種類ごとのタブ。レアリティで絞り込める）
  monsters.html       開放した道に出る魔物（地方ごとのタブ。地形・性質・強さ・出る道）
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
import rpg  # noqa: E402
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
)

CSS = """
:root{--bg:#f6efdc;--card:#fffaf0;--ink:#3f2f22;--sub:#6b5a48;--line:#d9c9a8;--accent:#7a5530;--hero:#b91c1c;
color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#1f1a14;--card:#2a231b;--ink:#efe4cf;--sub:#bfae92;--line:#4a3d2c;
--accent:#e0b878;--hero:#f87171;color-scheme:dark}}
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
.monsters{min-width:900px}
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
  svg = mapsvg.render(world, entries, links={h["id"]: hero_href(h["id"]) for h, _ in entries})
  body = [f"<h2>世界の地図</h2>{updated(today)}"]
  if svg:
    svg = svg[svg.index("<svg") :]
    body.append(
      '<div id="map">' + svg + '<div class="zoom"><button id="zin" title="拡大">＋</button>'
      '<button id="zout" title="縮小">－</button><button id="zhome" class="wide">全体</button></div></div>'
      '<p class="note">ホイールやボタンで拡大、ドラッグで移動できます。町や勇者にマウスを乗せると説明が出ます。'
      f"道の上の数字は移動にかかる行動回数です（勇者は 1 日に {world['season']['actions_per_day']} 回行動します）。</p>"
    )
  else:
    body.append('<p class="note">地図はありません（world/map.json に layout がありません）。</p>')
  body.append("<h2>勇者の居場所</h2>")
  if entries:
    rows = "".join(
      f'<tr><td><a href="{hero_href(h["id"])}">{escape(h["name"])}</a></td>'
      f'<td class="num">Lv.{s["hero"]["level"]}</td><td>{escape(rpg.location(world, s))}</td></tr>'
      for h, s in entries
    )
    body.append(
      f'<div class="scroll"><table><tr><th>勇者</th><th class="num">レベル</th><th>居場所</th></tr>{rows}</table></div>'
    )
  else:
    body.append('<p class="note">まだ勇者がいません。</p>')
  return page("地図", "".join(body), "map.html", script=MAP_JS if svg else "")


# ---- ランキング ---------------------------------------------------------------------------
def build_ranking(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  season = game.season_of(world, today)
  first, last = game.season_range(world, season)
  views = [(h, rpg.season_view(world, s, season)) for h, s in entries]
  body = [f"<h2>シーズン{season}（{first}〜{last}）</h2>{updated(today)}"]
  news = rpg.news(views, today)
  body.append(
    '<div class="card"><h3>📰 今日の出来事</h3>'
    + ("".join(f"<div>{escape(n)}</div>" for n in news) or '<div class="note">まだない</div>')
    + "</div>"
  )
  cards = []
  for title, _, key, value, unit in rpg.RANKINGS:
    rows = "".join(
      f'<tr><td class="num">{r}</td><td><a href="{hero_href(h["id"])}">{escape(h["name"])}</a></td>'
      f'<td class="num">{"Lv." if title == "レベル" else ""}{value(s)}{"" if title == "レベル" else unit}</td></tr>'
      for r, h, s in rpg.ranked(views, key)
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
  views = [(h, rpg.season_view(world, s, season)) for h, s in entries]
  body = [f"<h2>勇者たち</h2>{updated(today)}"]
  heroes = []
  for h, s in views:
    hero, st = s["hero"], game.stats(world, s)
    last_ = s["last"]
    events = rpg.highlights(last_["events"]) if last_ and last_["date"] == today.isoformat() and last_["events"] else []
    heroes.append(
      f'<div class="card"><h3><a href="{hero_href(h["id"])}">{escape(h["name"])}</a> '
      f"Lv.{hero['level']}</h3><div>{escape(rpg.location(world, s))}</div>"
      f"<div>HP {hero['hp']}/{st['max_hp']}　攻{st['atk']} 防{st['def']}　{hero['gold']}G</div>"
      f"<div>装備: {escape(rpg.gear_text(world, s))}</div>"
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

# レアリティの絞り込み（複数選べて、何も選ばなければすべて）。表の行（tr[data-f]）を隠し、
# 絞り込んで行のなくなったカードも隠す（絞り込んでいなければ、空のカードも出す）
FILTER_JS = """
(()=>{const chips=[...document.querySelectorAll('.chip[data-f]')],none=document.getElementById('f-none'),
clear=document.getElementById('f-clear');if(!chips.length)return;
const apply=()=>{const on=new Set(chips.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.f));let shown=0;
document.querySelectorAll('.board').forEach(card=>{let n=0;
card.querySelectorAll('tr[data-f]').forEach(tr=>{const ok=!on.size||on.has(tr.dataset.f);tr.hidden=!ok;if(ok)n++;});
card.hidden=on.size>0&&n===0;const panel=card.closest('.tab-panel');if(!card.hidden&&!(panel&&panel.hidden))shown++;});
none.hidden=!on.size||shown>0;clear.hidden=!on.size;};
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
  buttons = "".join(
    f'<button type="button" class="chip" data-f="{escape(v)}" aria-pressed="false">'
    f'<span class="chip-mark">{escape(mark)}</span></button>'
    for v, mark in chips
  )
  return (
    f'<div class="filters card"><div class="f-row"><span class="f-label">レアリティ</span>{buttons}</div>'
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
      rows.append(
        f'<tr data-f="{e["rank"]}"><td class="mark">{escape(mark)}</td><td>{escape(rest)}'
        + "".join(f'<div class="note">{n}</div>' for n in notes)
        + f'</td><td class="num">{e["cost"]}</td>'
        f'<td class="num">{e["reward"]["exp"]}EXP<br>{e["reward"]["gold"]}G</td>'
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
  """町の情報（説明・ギルドの規模・特産品・店の品ぞろえ・依頼で出るレア・道）。全部の町を地方ごとに出し
  （開放した町のある地方が先）、開放していない町は、特産品などを「-」にする。"""
  places, items = world["places"], world["items"]
  opened = set(world["towns"])
  towns = [t for t, p in places.items() if p["type"] == "town"]
  sellers: dict[str, int] = {}
  for t in towns:
    for iid in places[t].get("shop", []):
      sellers[iid] = sellers.get(iid, 0) + 1

  def label(iid: str) -> str:
    it = items[iid]
    return (
      f'<span class="chip-mark">{escape(game.rarity_mark(world, it["rarity"]))}</span>{escape(it["name"])}'
      f'<span class="note">（{escape(rpg.item_power(it))}）</span>'
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
        (k, "-") for k in ("特産品", "依頼の報酬で出るレア", "店の品ぞろえ", "道（行き先と行動回数）")
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
      ("特産品", "<br>".join(label(i) for i in shop if sellers.get(i) == 1) or "-"),
      ("依頼の報酬で出るレア", "<br>".join(label(i) for i in rares) or "-"),
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
      + "</table></div>"
      '<p class="note">Lv・HP・攻・防は、性質の効果がかかる前の値です（たとえば「硬い」は戦いで守りが固くなります）。'
      "出会う割合は、その道で戦いになったときに、その魔物が出る割合です。</p></div>"
    )

  groups = area_groups(world, towns)
  body = ["<h2>町の情報</h2>", tab_bar([(aid, a["name"]) for aid, a, _ in groups])]
  for aid, area, members in groups:
    body.append(tab_panel(aid, name_html(area["name"], area.get("en")), "".join(card(t) for t in members)))
  return page("町", "".join(body), "towns.html", script=TABS_JS)


# ---- アイテム ---------------------------------------------------------------------------
def item_rows(world: dict) -> dict[str, list[dict]]:
  """開放した地域で手に入るアイテム（部位ごと。★ の多い順、同じ ★ なら強い順）: {部位: [{id, item, shops, towns}]}。"""
  places, items = world["places"], world["items"]
  shops: dict[str, list[str]] = {}
  for t in world["towns"]:
    for iid in places[t].get("shop", []):
      shops.setdefault(iid, []).append(places[t]["name"])
  rank = {r: i for i, r in enumerate(world["rarities"])}
  rows: dict[str, list[dict]] = {slot: [] for slot in W.SLOTS}
  for iid, it in items.items():
    towns = [places[t]["name"] for t in it.get("towns", []) if t in world["towns"]]
    if iid in world["region_items"] or towns:
      sellers = [t for t, p in places.items() if p["type"] == "town" and iid in p.get("shop", [])]
      rows[it["slot"]].append(
        {"id": iid, "item": it, "shops": shops.get(iid, []), "towns": towns, "specialty": len(sellers) == 1}
      )  # 世界でその町だけが売る品
  for slot in rows:
    rows[slot].sort(key=lambda r: (-rank[r["item"]["rarity"]], -game.item_score(r["item"]), r["item"].get("price", 0)))
  return rows


def build_items(world: dict) -> str:
  """開放した地域で手に入るアイテムの一覧。装備の種類ごとのタブで、レアリティで絞り込める。"""
  rows = item_rows(world)
  used = [r for r in world["rarities"] if any(x["item"]["rarity"] == r for rs in rows.values() for x in rs)]
  slots = [slot for slot in W.SLOTS if rows[slot]]  # 開放した地域で手に入る装備のない種類は、タブを出さない
  body = [
    "<h2>アイテム一覧</h2>",
    tab_bar([(slot, W.SLOT_NAMES[slot]) for slot in slots]),
    rarity_filter([(r, game.rarity_mark(world, r)) for r in used], "条件に合うアイテムはありません。"),
  ]
  for slot in slots:
    trs = []
    for r in rows[slot]:
      it = r["item"]
      if r["towns"]:
        how = "依頼の報酬: " + "・".join(escape(t) for t in r["towns"])
      else:
        how = (
          "店: "
          + ("・".join(escape(t) for t in r["shops"]) or "なし")
          + '<div class="note">宝箱・依頼の報酬でも出る</div>'
        )
      trs.append(
        f'<tr data-f="{it["rarity"]}"><td class="mark">{escape(game.rarity_mark(world, it["rarity"]))}</td>'
        f"<td>{escape(it['name'])}" + ('<span class="tag">特産</span>' if r["specialty"] else "") + "</td>"
        f"<td>{escape(rpg.item_power(it))}</td>"
        f'<td class="num">{str(it["price"]) + "G" if it.get("price") else "—"}</td><td>{how}</td></tr>'
      )
    table = (
      '<div class="card board"><div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em">'
      '<col style="width:13em"><col style="width:9em"><col style="width:5.5em"><col></colgroup>'
      '<tr><th></th><th>名前</th><th>性能</th><th class="num">値段</th><th>手に入れ方</th></tr>'
      + "".join(trs)
      + "</table></div></div>"
    )
    body.append(tab_panel(slot, escape(W.SLOT_NAMES[slot]), table))
  return page("アイテム", "".join(body), "items.html", script=FILTER_JS + TABS_JS)


# ---- 魔物 ---------------------------------------------------------------------------------
def build_monsters(world: dict) -> str:
  """開放した道に出る魔物の一覧（地方ごとのタブ。地形・性質・強さ・出る道と、その道で出会う割合）。
  道は、両端の町の地方に入れる。開放した道のない地方は、まだ開放されていないと出す。"""
  places, monsters, traits = world["places"], world["monsters"], world["traits"]
  towns = [t for t, p in places.items() if p["type"] == "town"]
  order = {t: i for i, t in enumerate(W.TERRAINS)}
  place_order = {p: i for i, p in enumerate(places)}

  def rate(r: float) -> str:  # 出会う割合（四捨五入で 0% にならないように）
    return "1%未満" if r < 0.01 else f"{r:.0%}"

  def table(members: list[str]) -> str:
    seen: dict[str, list[str]] = {}
    for (a, b), info in world["road_info"].items():
      if place_order[a] > place_order[b] or not ({a, b} & set(members)):
        continue  # 同じ道は 1 度だけ。この地方（のまとまり）の町につながる道だけ
      total = sum(w for _, w in info["monsters"]) or 1
      weights: dict[str, float] = {}  # 同じ魔物が地形の重複などで 2 度出ても、1 つにまとめる
      for mid, w in info["monsters"]:
        weights[mid] = weights.get(mid, 0) + w
      road = f"{places[a]['name']}〜{places[b]['name']}"
      for mid, w in weights.items():
        seen.setdefault(mid, []).append(f"{escape(road)}（{rate(w / total)}）")
    if not seen:
      return '<div class="card"><p class="note">まだ開放されていません（この地方の道は通れません）。</p></div>'
    rows = []
    for mid in sorted(seen, key=lambda m: (order.get(monsters[m]["terrain"], 99), m)):
      m = monsters[mid]
      trait = traits.get(m.get("trait"), {})
      rows.append(
        f"<tr><td>{escape(m['name'])}</td><td>{escape(W.TERRAIN_NAMES.get(m['terrain'], m['terrain']))}</td>"
        + (
          f'<td>{escape(trait["name"])}<div class="note">{escape(trait.get("text", ""))}</div></td>'
          if trait
          else "<td>-</td>"
        )
        + "".join(f'<td class="num">{m[k]}</td>' for k in ("level", "hp", "atk", "def", "exp", "gold"))
        + f"<td>{'<br>'.join(seen[mid])}</td></tr>"
      )
    return (
      '<div class="card"><div class="scroll"><table class="wide-table monsters"><colgroup><col style="width:7em">'
      '<col style="width:4em"><col style="width:12em">'
      + '<col style="width:3.5em">'
      * 6
      + "<col></colgroup><tr><th>名前</th><th>地形</th><th>性質</th>"
      '<th class="num">Lv</th><th class="num">HP</th><th class="num">攻</th><th class="num">防</th>'
      '<th class="num">EXP</th><th class="num">G</th><th>出る道（その道で出会う割合）</th></tr>'
      + "".join(rows)
      + "</table></div></div>"
    )

  groups = area_groups(world, towns)
  body = ["<h2>魔物の情報</h2>", tab_bar([(aid, a["name"]) for aid, a, _ in groups])]
  for aid, area, members in groups:
    body.append(tab_panel(aid, name_html(area["name"], area.get("en")), table(members)))
  return page("魔物", "".join(body), "monsters.html", script=TABS_JS)


# ---- 勇者の詳細 ---------------------------------------------------------------------------
def build_hero(world: dict, hero: dict, state: dict) -> str:
  body = (
    f"<h2>{escape(hero['name'])}（{escape(hero['id'])}）</h2>"
    f"<pre>{escape(rpg.build_text(world, state, fold=False))}</pre>"
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
  world = rpg.load_world()
  entries = rpg.current_entries(world)
  today = date.fromisoformat(a.date) if a.date else shown_day(entries)
  retired = rpg.current_entries(world, retired=True)
  written = write_site(world, entries, today, rpg.load_guild(), Path(a.out), retired)
  print(f"{a.out} に {len(written)} ファイルを書き出しました")
  return 0


if __name__ == "__main__":
  sys.exit(main())
