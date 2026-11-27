#!/usr/bin/env python3
"""GitHub Pages に載せるサイト（HTML）を書き出す。冒険は進めない（今の状態を見せるだけ）。

ページ:
  index.html          諸国絵図（拡大・移動ができる。町や霊場にマウスを乗せると説明が出る）
  ranking.html        今シーズンの番付と、今日の出来事
  heroes.html         旅をしているもののふの一覧（居場所・能力・装備・今日の出来事）
  towns.html          町の情報（全部の町を地方ごとのタブで切り替える。説明・口入れ屋の規模・特産品・店・レア・道。開放していない町は「-」）
  items.html          開放した地域で手に入る武具（装備の種類ごとのタブ。珍しさで絞り込める）
  monsters.html       開放した道に出る物の怪と主（地方ごとのタブ。街道の地形の割合・Lv・生息地・性質・属性・強さ。生息地で絞り込める）
  training.html       調練（戦闘シミュレーションの結果）
  guide.html          手引き（能力・もののふの特性・物の怪の性質・属性と相性・戦いの決まりの説明）
  guild.html          町ごとの口入れ屋の高札（地方ごとのタブ。珍しさで絞り込める。受けられた依頼は 引受済）
  heroes/<id>.html    もののふごとの詳細（能力・装備・持ち物・依頼・冒険の記録）

データは .state/（もののふの状態・口入れ屋の受けられた依頼）と world/ から読む。
毎日の実行のあとに pages.yml が書き出して公開する（書き出したファイルはコミットしない）。

使い方:
  python scripts/website.py OUT_DIR [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402
import game  # noqa: E402
import mapsvg  # noqa: E402
import store  # noqa: E402
import training  # noqa: E402
import view  # noqa: E402
import world as W  # noqa: E402

TITLE = common.SITE_TITLE
TITLE_EN = "Warriors on the Road"
VERSION = (common.ROOT / "VERSION").read_text(encoding="utf-8").strip()
PAGES = (
  ("index.html", "絵図"),
  ("ranking.html", "番付"),
  ("heroes.html", "もののふ"),
  ("towns.html", "町"),
  ("guild.html", "口入れ屋"),
  ("items.html", "武具"),
  ("monsters.html", "物の怪"),
  ("guide.html", "手引き"),
  ("training.html", "調練"),
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
html.sticky-header{scroll-padding-top:var(--site-header-offset)}
.sticky-header body > header{position:sticky;top:0;z-index:10}
.wrap{max-width:1100px;margin:0 auto;padding-inline:16px}
header .wrap{display:flex;flex-wrap:wrap;gap:4px 20px;align-items:baseline;padding-block:10px}
header h1{font-size:20px;margin:0;max-width:100%}
header h1 a{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 8px;color:inherit;text-decoration:none}
.site-title-en,.site-version{font-size:12px;font-weight:normal;color:var(--sub)}
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
.items-table{min-width:880px}
.monsters{min-width:820px}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
.mark{color:#b7791f;white-space:nowrap;width:5em}
.taken{color:var(--hero);white-space:nowrap}
.free{color:var(--sub);white-space:nowrap}
.open{color:#15803d;white-space:nowrap}
.stamp{display:inline-block;padding:1px 8px;border:2.5px solid #c0262d;border-radius:4px;color:#c0262d;font-weight:bold;
font-size:13px;letter-spacing:.12em;transform:rotate(-8deg);opacity:.85;margin:2px 0 4px;font-family:'Yu Mincho','Hiragino Mincho ProN',serif}
.filters{display:flex;flex-direction:column;gap:10px}
.f-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.f-label{font-size:13px;color:var(--sub);width:5.5em}
.chip-mark{color:#b7791f;white-space:nowrap}
.info th{width:11em;vertical-align:top;padding-top:6px}
.closed{opacity:.75}
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
td.icon .tag,.items-table td .tag{margin-left:0}
.zero{color:var(--sub)}
.pill{display:inline-block;margin:2px 6px 2px 0;padding:1px 10px;border:1.5px solid #f0a868;border-radius:999px;
  background:var(--card);color:var(--ink);text-decoration:none;font-size:13px;white-space:nowrap}
.pill:hover{border-color:#e8833a}
.guide-nav{margin:4px 0 12px}
svg.tri{display:block;max-width:100%;height:auto;color:var(--ink);margin:6px 0}
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


SITE_HEADER_JS = """
(()=>{
const header=document.querySelector('body > header'),root=document.documentElement;
let offset=0;
const sync=()=>{
  const height=Math.ceil(header.getBoundingClientRect().height),sticky=height*2<=root.clientHeight;
  const nextOffset=sticky?height+8:0;
  root.style.setProperty('--site-header-offset',nextOffset+'px');
  root.classList.toggle('sticky-header',sticky);
  if(nextOffset>offset&&location.hash){
    let id=location.hash.slice(1);
    try{id=decodeURIComponent(id);}catch(e){}
    const target=document.getElementById(id);
    const heading=target?.querySelector('h1,h2,h3,h4,h5,h6')||target;
    const box=heading?.getBoundingClientRect();
    if(box&&box.bottom>Math.max(0,offset-8)&&box.top<nextOffset){
      window.scrollBy(0,target.getBoundingClientRect().top-nextOffset);
    }
  }
  offset=nextOffset;
};
sync();
window.addEventListener('resize',sync);
if(window.ResizeObserver)new ResizeObserver(sync).observe(header);
})();
"""


def page(title: str, body: str, current: str, depth: int = 0, script: str = "") -> str:
  """ページの HTML（depth はサイトの一番上からの深さ。リンクの先頭に ../ をつける）。"""
  up = "../" * depth
  nav = "".join(
    f'<a href="{up}{href}"' + (' class="on"' if href == current else "") + f">{name}</a>" for href, name in PAGES
  )
  script = SITE_HEADER_JS + script
  return (
    f'<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">'
    f'<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<title>{escape(title)} | {TITLE}</title><style>{CSS}</style></head><body>"
    f'<header><div class="wrap"><h1><a href="{up}index.html">{escape(TITLE)} '
    f'<span class="site-title-en" lang="en">{escape(TITLE_EN)}</span></a></h1><nav>{nav}</nav></div></header>'
    f'<main class="wrap">{body}<footer class="site-version">v{escape(VERSION)}</footer></main>'
    + (f"<script>{script}</script>" if script else "")
    + "</body></html>\n"
  )


def hero_href(hero_id: str, depth: int = 0) -> str:
  return "../" * depth + f"heroes/{hero_id}.html"


def updated(today: date) -> str:
  return f'<p class="note">{today.isoformat()} の冒険を終えた状態（毎朝 7 時すぎに更新）</p>'


# ---- 絵図（最初のページ） ------------------------------------------------------------------
def build_map(world: dict, today: date) -> str:
  svg = mapsvg.render(world, [])
  body = [f"<h2>諸国絵図</h2>{updated(today)}"]
  if svg:
    svg = svg[svg.index("<svg") :]
    body.append(
      '<div id="map">' + svg + '<div class="zoom"><button id="zin" title="拡大">＋</button>'
      '<button id="zout" title="縮小">－</button><button id="zhome" class="wide">全体</button></div></div>'
      '<p class="note">ホイールやボタンで拡大、ドラッグで移動できます。町や霊場の印にマウスを乗せると説明が出ます。'
      f"道の上の数字は移動にかかる行動回数です（もののふは 1 日に {world['season']['actions_per_day']} 回行動します）。</p>"
    )
  else:
    body.append('<p class="note">絵図はまだありません。</p>')
  return page("絵図", "".join(body), "index.html", script=MAP_JS if svg else "")


# ---- 番付 ---------------------------------------------------------------------------
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
  return page("番付", "".join(body), "ranking.html")


# ---- もののふ ---------------------------------------------------------------------------------
def build_heroes(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
  """旅をしているもののふの一覧（居場所・能力・装備・今日の出来事。名前から詳細のページへ）。"""
  season = game.season_of(world, today)
  views = [(h, view.season_view(world, s, season)) for h, s in entries]
  body = [f"<h2>もののふたち</h2>{updated(today)}"]
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
      f"<div>HP {hero['hp']}/{st['max_hp']}　攻{st['atk']} 防{st['def']} 速{st['spd']} 器{st['dex']}　{hero['gold']}文</div>"
      f"<div>"
      f"特性: {escape(game.hero_trait(world, s).get('name', '-'))}　"
      f"性格: {escape(game.personality(world, s).get('name', '-'))}</div>"
      f"<div>装備: {escape(view.gear_text(world, s))}</div>"
      f'<div class="note">今日: {escape("、".join(events) or "—")}</div></div>'
    )
  body.append('<div class="grid">' + ("".join(heroes) or '<p class="note">まだもののふがいません。</p>') + "</div>")
  return page("もののふ", "".join(body), "heroes.html")


# ---- タブと絞り込み（町・口入れ屋・武具のページで使う）------------------------------------------
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

# 絞り込み（珍しさ・地形。複数選べて、何も選ばなければすべて）。表の行（tr[data-f]）を隠し、
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
  """珍しさの絞り込みのボタン（[(値, ★ の印)]）と、合うものがないときの文。"""
  return chip_filter("珍しさ", [(v, f'<span class="chip-mark">{escape(mark)}</span>') for v, mark in chips], empty)


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


# ---- 口入れ屋 -------------------------------------------------------------------------------
def guild_status(world: dict, e: dict, today: date) -> str:
  """高札の依頼の状態。誰かが受けた依頼は、名前を出さずに「引受済」のスタンプを押す。
  受付中なら、受けられる残りの日数。何人かで受ける依頼（#72）は、集まった人数と、集まるのを待つ期限。"""
  party = e.get("party", 1)
  if party > 1 and e["status"] == "open" and e.get("members"):
    until = date.fromisoformat(e["gather_until"])
    return (
      f'<span class="open">仲間を募集中 {len(e["members"])}/{party} 人</span>'
      f'<div class="note">{until.month}/{until.day} までにそろわなければ流れる</div>'
    )
  if e["status"] in ("held", "done", "failed"):
    note = {"done": "明日入れ替え", "failed": "明日また受付"}.get(e["status"])
    return '<span class="stamp">引受済</span>' + (f'<div class="note">{note}</div>' if note else "")
  last = game.last_open_day(world, e)
  left = (last - today).days
  if left < 0:
    return '<span class="free">受付終了</span>'
  when = f"あと {left} 日（{last.month}/{last.day} まで）" if left > 0 else "今日まで（明日入れ替え）"
  return f'<span class="open">受付中</span><div class="note">{when}</div>'


def build_guild(world: dict, entries: list[tuple[dict, dict]], today: date, guild: dict) -> str:
  """町ごとの口入れ屋の高札（日をまたいで残る。早い者勝ち）。地方ごとのタブで、珍しさで絞り込める。"""
  season = game.season_of(world, today)
  if (
    not guild.get("towns")
    or guild.get("season") != season
    or any(guild.get("refreshed", {}).get(t, "") < today.isoformat() for t in world["towns"])
  ):
    # まだ誰も進めていない・その日の高札になっていない: その日の朝の高札を見せる（保存はしない）
    guild = copy.deepcopy(guild) if guild.get("season") == season else game.new_guild()
    guild["levels"] = game.town_levels(st for _, st in entries)  # 毎日の実行と同じく、町ごとの居場所と Lv で抽選する
    game.refresh_guild(world, guild, today, season)
  places = world["places"]

  def card(town: str) -> str:
    p = places[town]
    rows = []
    for e in guild.get("towns", {}).get(town, []):
      mark, _, rest = game.quest_text(world, e).partition(" ")
      notes = (
        ([f"{e['party']} 人で受ける依頼（Lv{e.get('min_level')} から）"] if e.get("party", 1) > 1 else [])
        + ([f"「{escape(e['text'])}」"] if e.get("text") else [])
        + (["前のもののふが失敗した依頼"] if e.get("returned") else [])
      )
      exp_text = f"{e['reward']['exp']}EXP<br>" if e["reward"]["exp"] else ""  # 依頼の経験値は 0（お金だけ）
      rows.append(
        f'<tr data-f="{e["rank"]}"><td class="mark">{escape(mark)}</td><td>{escape(rest)}'
        + "".join(f'<div class="note">{n}</div>' for n in notes)
        + "</td>"
        f'<td class="num">{exp_text}{e["reward"]["gold"]}文</td>'
        f"<td>{guild_status(world, e, today)}</td></tr>"
      )
    return (
      f'<div class="card board" id="{escape(town)}"><h3>{escape(p.get("emoji", ""))} {name_html(p["name"], p.get("en"))}'
      f'<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span></h3>'
      '<div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em"><col>'
      '<col style="width:6.5em"><col style="width:13em"></colgroup><tr><th></th><th>依頼</th>'
      '<th class="num">報酬</th><th>状態</th></tr>' + "".join(rows) + "</table></div></div>"
    )

  groups = area_groups(world, world["towns"])
  party = world["season"].get("party") or {}
  bosses = [m for mid, m in world["monsters"].items() if m.get("boss") and game.boss_town(world, m)]
  awake = [world["monsters"][mid]["name"] for mid in guild.get("awake") or [] if mid in world["monsters"]]
  boss_note = ""
  if bosses and party:
    boss_note = (
      f'<p class="note">霊場の主: {"・".join(awake)}が目覚めている（霊場への山道がつながる町に、主の依頼が出る）。</p>'
      if awake
      else f'<p class="note">大物を討った数: {guild.get("elite_kills", 0)}/{party.get("boss_after_elites")} 体'
      "（全員合わせて。そろうと霊場の主が目覚める）。</p>"
    )
  body = [
    f"<h2>町の口入れ屋の高札（{today.isoformat()}）</h2>",
    boss_note,
    tab_bar([(aid, a["name"]) for aid, a, _ in groups]),
    rarity_filter([(str(i), r["mark"]) for i, r in enumerate(world["quest_ranks"], 1)], "条件に合う依頼はありません。"),
  ]
  for aid, area, members in groups:
    body.append(tab_panel(aid, name_html(area["name"], area.get("en")), "".join(card(t) for t in members)))
  return page("口入れ屋", "".join(body), "guild.html", script=FILTER_JS + TABS_JS)


# ---- 町 ---------------------------------------------------------------------------------
def build_towns(world: dict) -> str:
  """町の情報（説明・口入れ屋の規模・店の品ぞろえ・依頼で出るレア・道）。全部の町を地方ごとに出し
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

  p = world["season"]["potion"]  # 丸薬はどの町の店でも売る（装備のあとに出す）
  potion = (
    f'丸薬<span class="note">（{p["price"]}文。戦いで HP が減ったときに使い（使う早さは性格で決まる）、最大 HP の {p["heal_rate"]:.0%} ぶん回復。'
    f"町で {p['keep']} 個まで買い足す）</span>"
  )

  def rows_of(town: str) -> list[tuple[str, str]]:
    slots = game.quests_per_day(world, town)
    marks = " ".join(["📜"] * slots)
    size = f'<span role="img" aria-label="依頼の枠 {slots} 件">{marks}</span>'
    if town not in opened:
      return [("口入れ屋の規模", size)] + [
        (k, "-") for k in ("宝箱や依頼で出る武具", "店の品ぞろえ", "名工の鍛冶屋", "道（行き先と行動回数）")
      ]
    # 店の品ぞろえは、武具の種類（武器・頭・胴・御守）の順に、同じ種類なら値段の高い順
    shop = sorted(
      places[town].get("shop", []), key=lambda i: (W.SLOTS.index(items[i]["slot"]), -items[i].get("price", 0), i)
    )
    rares = sorted(
      (i for i, it in items.items() if town in it.get("towns", [])),
      key=lambda i: -world["rarities"].index(items[i]["rarity"]),
    )
    kinds = {"land": "陸路", "sea": "航路"}
    forge = (
      f"★★★★武具を生成（{escape(game.stone_name(world, places[town].get('area', '')))} 10 個・10,000文）"
      if places[town].get("legendary_forge")
      else "—"
    )
    roads = [
      f"{escape(places[b]['name'])}（{kinds.get(world['road_info'][(town, b)]['kind'], '')} "
      f"{world['roads'][(town, b)]}）"
      for b in world["neighbors"][town]
    ]
    roads += [
      f"{escape(places[r['from'] if r['to'] == town else r['to']]['name'])}（霊場）"
      for r in world["trails"]
      if town in (r["from"], r["to"])
    ]
    return [
      ("口入れ屋の規模", size),
      (
        "宝箱や依頼で出る武具",
        ("<br>".join(label(i) for i in rares if not items[i].get("forge_town")) or "-")
        + ("" if world["season"].get("rare_drops", True) else '<div class="note">今は珍しい武具が出ません</div>'),
      ),
      ("店の品ぞろえ", "<br>".join([label(i) for i in shop] + [potion])),
      ("名工の鍛冶屋", forge),
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


# ---- 武具 ---------------------------------------------------------------------------
def item_rows(world: dict) -> dict[str, list[dict]]:
  """開放した地域で手に入る武具（部位ごと。★ の多い順、同じ ★ なら強い順）:
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
    forge_town = it.get("forge_town")
    if iid in world["region_items"] or towns or forge_town in world["towns"]:
      rows[it["slot"]].append(
        {"id": iid, "item": it, "shops": shops.get(iid, []), "towns": towns, "forge_town": forge_town}
      )
  for slot in rows:
    rows[slot].sort(
      key=lambda r: (-rank[r["item"]["rarity"]], -game.item_score(world, r["item"]), r["item"].get("price", 0))
    )
  return rows


SLOT_EMOJI = {"weapon": "⚔️", "helmet": "🪖", "shield": "🥋", "accessory": "📿"}  # 部位の名前の先頭に付ける
ITEM_STATS = (("hp", "HP"), ("atk", "攻"), ("def", "防"), ("spd", "速"), ("dex", "器"))  # 武具ページの能力の列


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
  """開放した地域で手に入る武具の一覧。装備の種類ごとのタブで、珍しさで絞り込める。
  能力は HP・攻・防・速・器の列に分けて出す。入手方法は町の札（レアは報酬で出る依頼の町、ほかは買える店）。"""
  rows = item_rows(world)
  used = [r for r in world["rarities"] if any(x["item"]["rarity"] == r for rs in rows.values() for x in rs)]
  slots = [slot for slot in W.SLOTS if rows[slot]]  # 開放した地域で手に入る装備のない種類は、タブを出さない
  body = [
    "<h2>武具一覧</h2>",
    "" if world["season"].get("rare_drops", True) else '<p class="note">今は珍しい武具（★★ 以上）が出ません。</p>',
    (
      '<p class="note">★★ 以上の装備には、町の鍛冶屋で追加スキルを付けられる（'
      + escape("・".join(f"{game.rarity_mark(world, r)} {p}文" for r, p in world["skill_prices"].items()))
      + "。くわしくは手引きの「装備のスキル」）</p>"
      if world.get("skill_prices")
      else ""
    ),
    tab_bar([(slot, slot_label(slot)) for slot in slots]),
    rarity_filter([(r, game.rarity_mark(world, r)) for r in used], "条件に合う武具はありません。"),
  ]
  for slot in slots:
    trs = []
    for r in rows[slot]:
      it = r["item"]
      fixed = world.get("skills", {}).get(it.get("fixed_skill"))
      how = "".join(town_pill(world, t) for t in r["towns"] or r["shops"] or [r["forge_town"]]) or "-"
      trs.append(
        f'<tr data-f="{it["rarity"]}"><td class="mark">{escape(game.rarity_mark(world, it["rarity"]))}</td>'
        f"<td>{escape(it['name'])}"
        + (f'<br><span class="note">{escape(it["text"])}</span>' if it.get("text") else "")
        + f"</td><td>{skill_tag(fixed['name']) if fixed else '—'}</td>"
        + element_cell(world, it.get("element"))
        + "".join(stat_cell(it.get(k, 0)) for k, _ in ITEM_STATS)
        + f'<td class="num">{str(it["price"]) + "文" if it.get("price") else "—"}</td><td>{how}</td></tr>'
      )
    table = (
      '<div class="card board"><div class="scroll"><table class="wide-table items-table"><colgroup>'
      '<col style="width:4.5em"><col style="width:16em"><col style="width:7em"><col style="width:5em">'
      + '<col style="width:3em">' * len(ITEM_STATS)
      + '<col style="width:5.5em"><col></colgroup><tr><th></th><th>名前</th><th>固定スキル</th>'
      + '<th class="icon">属性</th>'
      + "".join(f'<th class="num">{name}</th>' for _, name in ITEM_STATS)
      + '<th class="num">値段</th><th>入手方法</th></tr>'
      + "".join(trs)
      + "</table></div></div>"
    )
    body.append(tab_panel(slot, escape(slot_label(slot)), table))
  return page("武具", "".join(body), "items.html", script=FILTER_JS + TABS_JS)


# ---- 物の怪 ---------------------------------------------------------------------------------
MONSTER_STATS = ("hp", "atk", "def", "spd", "dex", "exp", "gold")  # 物の怪のページの強さの列（Lv は名前の右に出す）
MONSTER_STAT_NAMES = ("HP", "攻", "防", "速", "器", "EXP", "文")


def terrain_label(terrain: str) -> str:
  """地形の名前に、地形の色の丸を添える（街道の円グラフと同じ色）。"""
  return f'<span class="tdot" style="background:var(--t-{escape(terrain)})"></span>' + escape(
    W.TERRAIN_NAMES.get(terrain, terrain)
  )


HABITATS = {"world": "大物", "raid": "主"}  # 道では出会わない物の怪


def habitat(monster: dict) -> str:
  """物の怪の生息地（絞り込みの値）。道の物の怪は地形、大物は world、主は raid。"""
  return "raid" if monster.get("boss") else "world" if monster.get("elite") else monster["terrain"]


def habitat_label(hid: str) -> str:
  """生息地の表示（地形は色の丸と名前、大物・主は種類を示す札）。"""
  if hid in HABITATS:
    return f'<span class="habitat">{escape(HABITATS[hid])}</span>'
  return terrain_label(hid)


def terrain_shares(monsters: dict, pool: list) -> list[tuple[str, float]]:
  """道に出る物の怪（[ID, 重み], ...）から、地形ごとの割合（戦いになったとき、その地形の物の怪が出る割合）。多い順。
  道の地形の割合（map.json）のうち、両端の地方の物の怪が住む地形だけで分け直したもの（#267）。"""
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
  """物の怪の一覧（地方ごとのタブ。Lv・生息地・性質・属性・強さ）。並びは Lv の順（同じ Lv は地形の順）。
  開放した地方は開放した道に出る物の怪、開放していない地方は、開放すると道に出るその地方の物の怪を出す（#267）。
  道で出会う物の怪はもののふの Lv でも変わるので、物の怪ごとの「出る道と割合」は出さない。道は、両端の町の地方に入れる。
  大物と主も同じ表に、道の物の怪のあとに出す（開放した町のある地方だけ。Lv は —）。"""
  places, monsters, traits = world["places"], world["monsters"], world["traits"]
  towns = [t for t, p in places.items() if p["type"] == "town"]
  order = {t: i for i, t in enumerate(W.TERRAINS)}
  place_order = {p: i for i, p in enumerate(places)}

  shown: set[str] = set()  # 表に出した物の怪の生息地（絞り込みのボタンは、表に出したものだけ）

  def boss_area(m: dict) -> str:  # 主のいる霊場の地方（地方がない・知らない地方なら、その他のまとまり）
    area = places[m["boss"]].get("area")
    return area if area in world["areas"] else OTHER_AREA

  def table(aid: str, members: list[str]) -> str:
    """この地方の物の怪: 町につながる開放した道の物の怪、町の討伐の依頼で狙う大物、霊場の主。
    並びは道の物の怪（Lv の順）→ 大物 → 主。大物・主は、開放した町のある地方だけ。
    開放した町のない地方は、まだ開放されていないと出し、その地方の道の物の怪（開放すると道に出る）の表を出す（#267）。"""
    seen: set[str] = set()
    for (a, b), info in world["road_info"].items():
      if place_order[a] > place_order[b] or not ({a, b} & set(members)):
        continue  # 同じ道は 1 度だけ。この地方（のまとまり）の町につながる道だけ
      # 地方をまたぐ道の物の怪は、その物の怪の地方のタブにだけ出す（地方のない・知らないまとまりは、道の物の怪を全部）
      seen |= {mid for mid, _ in info["monsters"] if aid not in world["areas"] or monsters[mid].get("area") == aid}
    if not set(members) & set(world["towns"]):  # 開放した町のない地方（#267）
      later = [mid for mid, m in monsters.items() if aid in world["areas"] and m.get("area") == aid and W.on_road(m)]
      later.sort(key=lambda m: (monsters[m]["level"], order.get(monsters[m]["terrain"], 99), m))
      shown.update(habitat(monsters[m]) for m in later)
      note = '<p class="note">まだ開放されていません（この地方の道は通れません）。'
      if not later:
        return f'<div class="card">{note}</p></div>'
      return (
        f'<div class="card">{note}下の物の怪は、この地方を開放すると道に出ます。</p></div>'
        + monster_table([monster_row(mid) for mid in later])
        + '<p class="note">HP・攻・防は、性質の効果がかかる前の値です。</p></div>'
      )
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
      "道で出会うのは、道の両端の町がある地方の物の怪のうち、道の地形（街道の円グラフ）に住み、もののふの Lv に近い物の怪です"
      '（<a href="guide.html">手引き</a>）。'
      "大物は道では出会わず、町の討伐の依頼で狙って戦います。"
      "主は霊場の奥にいて、大物が討たれ続けると目覚めます（口入れ屋の高札）。"
      "大物・主は 1 人では勝てず、何人かで受ける依頼で戦います。</p></div>"
    )

  def monster_row(mid: str) -> str:  # 物の怪の 1 行（大物・主の Lv は —。道の物の怪と Lv の意味が違うので）
    m = monsters[mid]
    trait = traits.get(m.get("trait"), {})
    hid = habitat(m)
    level = "—" if hid in HABITATS else m["level"]
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

  def monster_table(rows: list[str]) -> str:  # 物の怪の表（カードを開いたまま返す）
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
      + '</div><p class="note">かっこの中は移動にかかる行動回数。円グラフは、道で戦いになったときに、どの地形の物の怪が出るかの割合'
      "（その地形に住む、道の両端の町がある地方の物の怪のうち、もののふの Lv に近い物の怪が出る）。</p></div>"
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
    "<h2>物の怪の情報</h2>",
    chip_filter("生息地", chips, "条件に合う物の怪はいません。"),
    tab_bar([(aid, a["name"]) for aid, a, _ in groups]),
    guide,
    *panels,
  ]
  return page("物の怪", "".join(body), "monsters.html", script=FILTER_JS + TABS_JS)


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


def season_card(world: dict) -> tuple[str, str]:
  """手引きの「シーズン」: レベルが戻り、装備と原石が残る決まり。"""
  months = world["season"]["months"]
  return (
    "シーズン",
    f"<p>シーズンは {escape(str(months))} か月ごと。シーズンが変わると、レベル・経験値・お金・丸薬・"
    "居場所・依頼・溜めた行動力は最初に戻り、持ち物と蔵の装備、各地方の原石が残る。</p>",
  )


SPARE_NAMES = {"rest": "休む（溜める）", "stroll": "散策する", "move": "となりの町へ移動する"}
ORDER_NAMES = {
  "level_high": "Lv の高いもの",
  "level_low": "Lv の低いもの",
  "rank_high": "★の多いもの",
  "rank_low": "★の少ないもの",
}


GEAR_NAMES = {
  "grow": "伸ばす（特性の得意な能力が上がるもの）",
  "cover": "補う（特性の不得意な能力が上がるもの）",
  "balance": "偏りを避ける（能力の下がらないもの → 安いもの）",
}


def skill_tag(name: str) -> str:
  """スキルの名前の札（特性と同じ枠）。"""
  return f'<span class="tag">{escape(name)}</span>'


def element_triangle(world: dict) -> str:
  """属性の三すくみの図（SVG）。強い相手へ矢印を引く（炎→雷→氷→炎）。3 つでなければ出さない。"""
  elements = world.get("elements") or {}
  order, cur = [], next(iter(elements), None)
  while cur in elements and cur not in order:
    order.append(cur)
    cur = elements[cur]["strong"]
  if len(order) != 3 or len(elements) != 3 or cur != order[0]:
    return ""
  # 中心 (150, 125)・半径 95 の三角形。上の円（半径 28 と線）が枠に収まるように、中心を下げている
  pts = [(150 + 95 * math.sin(2 * math.pi * i / 3), 125 - 95 * math.cos(2 * math.pi * i / 3)) for i in range(3)]
  colors = {"flame": "#d9534f", "ice": "#4f8fd9", "thunder": "#b58ad9"}
  parts = []
  for i in range(3):
    (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 3]
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    ux, uy = dx / d, dy / d
    sx, sy, ex, ey = x1 + ux * 34, y1 + uy * 34, x2 - ux * 38, y2 - uy * 38
    parts.append(
      f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="currentColor" stroke-width="2.5" marker-end="url(#arrow)"/>'
    )
    mx, my = (x1 + x2) / 2 - uy * 14, (y1 + y2) / 2 + ux * 14
    parts.append(f'<text x="{mx:.1f}" y="{my:.1f}" font-size="12" text-anchor="middle" fill="currentColor">強い</text>')
  for eid, (x, y) in zip(order, pts, strict=True):
    color = colors.get(eid, "#888")
    parts.append(
      f'<circle cx="{x:.1f}" cy="{y:.1f}" r="28" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2"/>'
    )
    parts.append(
      f'<text x="{x:.1f}" y="{y + 5:.1f}" font-size="15" font-weight="bold" text-anchor="middle" fill="currentColor">'
      f"{escape(elements[eid]['name'])}</text>"
    )
  return (
    '<svg viewBox="0 0 300 230" width="300" height="230" role="img" aria-label="属性の三すくみ" class="tri">'
    '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>' + "".join(parts) + "</svg>"
  )


def skill_logic(sk: dict) -> str:
  """スキルのしくみの説明（logic の value を、値に置き換える。確率・HP の割合は %）。"""
  text, v = sk["logic"], sk.get("value")
  if v is None:
    return text
  pct = f"{v:.0%}"
  num = str(int(v)) if float(v).is_integer() else str(v)
  for a, b in (
    ("value の確率", f"{pct} の確率"),
    ("の value 回復", f"の {pct} 回復"),
    ("から value に", f"から {pct} に"),
  ):
    text = text.replace(a, b)
  return text.replace("value", num)


def skill_cards(world: dict, table) -> list[tuple[str, str]]:
  """手引きの「装備のスキル」と「持てる数と蔵」の節。"""
  skills, kinds = world.get("skills") or {}, world.get("skill_kinds") or {}
  cards = []
  carry = world["season"].get("carry")
  if carry:
    cards.append(
      (
        "持てる数と蔵",
        table(("部位", "持てる数"), [(escape(W.SLOT_NAMES[s]), f"{carry[s]}") for s in W.SLOTS])
        + '<p class="note">着けるのは部位ごとに 1 つ（武器と御守は、戦いのたびに持っている中から選ぶ）。'
        "持てる数を超えた分は、町の口入れ屋の蔵に預ける（どの町でも出し入れでき、依頼に合わせて入れ替える。"
        "依頼の物の怪の性質に効くスキル → 弱点を突ける属性 → 性格の優先スキル → ★ の多い装備、の順に持つ）。持ち物と蔵はシーズンをまたいで残る</p>",
      )
    )
  if not skills:
    return cards
  prices = world.get("skill_prices") or {}
  price_text = "・".join(f"{game.rarity_mark(world, r)} {p}文" for r, p in prices.items())
  body = (
    f"<p>追加スキルは {escape(game.rarity_mark(world, next(iter(prices), 'rare')))} 以上の装備に、町の鍛冶屋で付けられる"
    f"（代金 {escape(price_text)}。1 日 1 回まで、行動力は使わない）。部位ごとのスキルのうち、抽選で 1 つが付き、"
    "持っている装備を ★ の多い順に見て、追加枠が空か、追加枠に性格の優先スキルがない装備に付ける。"
    "優先スキルや依頼に効く対策スキルが追加枠に付いていると、その枠を維持する。スキルは点数（武具の強さ）には入らない。"
    "★★★ 武具の固定スキルは武具一覧に載っている。固定スキルと追加スキルは同時に効き、"
    "追加スキルはその武具の固定スキルを除いて抽選する。主の武具には入手時から追加スキルが付いている。</p>"
  )
  for slot in W.SLOTS:
    rows = [
      (escape(kinds.get(sk["kind"], sk["kind"])), skill_tag(sk["name"]), escape(sk["text"]), escape(skill_logic(sk)))
      for sk in skills.values()
      if sk["slot"] == slot
    ]
    body += f'<h4 class="sub">{escape(W.SLOT_NAMES[slot])}</h4>' + table(("系統", "スキル", "効果", "しくみ"), rows)
  cards.append(("装備のスキル", body))
  return cards


def personality_table(world: dict, table) -> str:
  """手引きの「もののふの性格」: 性格ごとの動き（旅籠・丸薬・買い物・依頼・残りの行動）を、性格を列にした表で。"""
  ps = list((world["season"].get("personalities") or {}).values())
  if not ps:
    return '<p class="note">性格はない</p>'

  def levels(p):
    low, high = p["quest"]["levels"]
    lo = "下限なし" if low is None else f"Lv {low:+d}"
    return f"{lo}〜Lv {high:+d}"

  def spare(p):
    text = SPARE_NAMES[p["spare"]]
    if "stroll_hp" in p:
      text = f"HP {p['stroll_hp']:.0%} 以上なら散策、未満なら{text}"
    if p["spare"] == "rest":  # 溜まりきったときの行動は、休む性格だけ
      text += f"。溜まりきったら{SPARE_NAMES[p.get('when_full', 'stroll')]}"
    return text

  rows = [
    ("旅籠に泊まる", [f"HP {p['inn_below']:.0%} 未満" for p in ps]),
    ("丸薬を使う", [f"HP {p['potion_below']:.0%} 未満" for p in ps]),
    ("装備より先に買う丸薬", [f"{p['potions_first']} 個" for p in ps]),
    (
      "選ぶ依頼（Lv は自分との差）",
      [levels(p) + "。" + ("、".join(ORDER_NAMES[o] for o in p["quest"]["order"]) or "高札の上から") for p in ps],
    ),
    ("その日の残りの行動", [spare(p) for p in ps]),
    ("同じ点数の装備", [GEAR_NAMES.get(p.get("gear", "balance"), "") for p in ps]),
    *(
      [
        (
          "スキルを付ける装備",
          [
            game.rarity_mark(world, p["skill"]["from"])
            + (" だけ" if p["skill"]["from"] == world["rarities"][-1] else " 以上")
            for p in ps
          ],
        ),
        (
          "スキルを付ける余裕",
          [("余裕があれば" if p["skill"]["margin"] <= 1 else f"代金の {p['skill']['margin']} 倍以上") for p in ps],
        ),
        *(
          (
            f"優先スキル（{W.SLOT_NAMES[slot]}）",
            [
              "".join(
                skill_tag(world["skills"][i]["name"])
                for i in p["skill"]["prefer"][slot]
                if i in world.get("skills", {})
              )
              for p in ps
            ],
          )
          for slot in W.SLOTS
        ),
      ]
      if all("skill" in p for p in ps)
      else []
    ),
  ]
  return (
    table(
      ("項目", *(p["name"] for p in ps)),
      [(escape(k), *(v if k.startswith("優先スキル") else escape(v) for v in vs)) for k, vs in rows],  # 札は HTML
    )
    + "".join(f'<p class="note">{escape(p["name"])}: {escape(p["text"])}</p>' for p in ps)
    + '<p class="note">旅立ちのときに抽選で決まり、シーズンが変わっても変わらない。'
    "選ぶ依頼の Lv は討伐だけで見る（配達は Lv を見ず、どの性格の範囲にも入る。Lv を比べるときは自分と同じ Lv として数える）。"
    "選ぶ依頼が同じ条件で並んだら、高札の上のものを選ぶ。Lv の範囲に合う依頼がなければ、範囲より弱い依頼のうち、"
    "範囲にいちばん近い Lv の依頼を受ける（範囲より強い依頼は受けない）。大物の依頼は、どの性格も大物の Lv 以上になってから受ける</p>"
  )


def build_guide(world: dict) -> str:
  """手引き: 能力・もののふの特性・物の怪の性質・属性と相性・戦いの決まりの説明（値は world/ から）。"""
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
  stat_names = {"hp": "HP", "atk": "攻", "def": "防", "spd": "速", "dex": "器"}
  hero_traits = [
    (
      tag(t["name"]),
      escape(t.get("text", "")),
      escape(stat_names.get(t.get("strong_stat"), "-")),
      escape(stat_names.get(t.get("weak_stat"), "-")),
    )
    for t in cfg["hero_traits"].values()
  ]
  monster_traits = [(tag(t["name"]), escape(t.get("text", ""))) for t in world["traits"].values()]
  elements, rates = world["elements"], cfg["element_rates"]
  element_rows = [(tag(f"{e['name']}属性"), escape(e.get("ore", "-"))) for e in elements.values()]  # 属性と鉱石
  potion = cfg["potion"]
  shifts = "・".join(  # 危ない配達で上がる物の怪の Lv（配達の設定がない世界では、行を出さない）
    f"危なさ {n} は +{d['shift']}"
    for n, d in enumerate((world.get("deliveries") or {}).get("dangers", []))
    if d.get("shift")
  )
  rules = [
    ("ダメージ", "攻撃 − 相手の防御 ÷ 2（少しだけ上下する。1 より小さくはならない）"),
    (
      "会心の一撃",
      f"器用さ ÷ {crit['dex_divisor']} の確率で、ダメージが {crit['multiplier']} 倍になる。物の怪にも器用さがある",
    ),
    ("先に攻撃", "素早いほうが先に攻撃する。性質「先手」の物の怪は、素早さに関係なく先に攻撃する"),
    ("2 回攻撃", "相手の 2 倍以上素早ければ、1 回の番に 2 回攻撃する（物の怪も同じ）"),
    (
      "何体かと戦う",
      "討伐の依頼で物の怪が何体かいたら、1 行動のなかで 1 体ずつ続けて戦う（HP は減ったまま次へ。道の戦いは 1 体だけ）。"
      "倒すたびに経験値とお金が入り、途中で力尽きても倒した分は残る",
    ),
    (
      "丸薬",
      f"HP が性格で決まった割合を下回ったら、自動で使う。最大 HP の {potion['heal_rate']:.0%} ぶん回復する"
      "（最大 HP は超えない）。HP が 0 になったら、丸薬があっても力尽きる",
    ),
    (
      "武器の持ち替え",
      "もののふは戦いのたびに、持ち物の中からいちばん効く武器（属性・2 回攻撃も考える）に持ち替える。"
      "苦手な属性の武器しかなく、持たないほうが効くときは、武器を持たずに戦う。"
      "御守は、弱点を突ける属性のものを持っていれば、その戦いだけ着け替える（苦手な属性なら外す）",
    ),
    *(
      [
        (
          "危ない配達",
          f"危なさのある配達（高価な品・狙われる品）を運んでいる間は、道で出会う物の怪の Lv が上がる（{shifts}）",
        )
      ]
      if shifts
      else []
    ),
    (
      "道で出会う物の怪",
      "戦いになったら、まず道の地形（草原・森など。道ごとの割合は物の怪のページの「街道」）を選び、その地形に住む、"
      "道の両端の町がある地方の物の怪のうち、Lv がもののふの Lv "
      + "〜".join(f"{d:+d}" for d in cfg["encounter_levels"])
      + " の物の怪から出る（地方をまたぐ道は、どちらの地方も半分ずつ）。Lv の合う物の怪のいない地形は出ず、その分ほかの地形が出る。"
      "範囲に合う物の怪がどの地形にもいなければ、いちばん近い Lv の物の怪",
    ),
    (
      "大物",
      "道では出会わない。町の討伐の依頼で、何人かで狙って戦う（1 人では勝てない）。依頼は、物の怪のレベル以上のもののふだけが受けられ、"
      "その日の朝に、その町に受けられるもののふが人数分いるときだけ高札に出る（道の途中のもののふは数えない）",
    ),
    (
      "主",
      "道では出会わない。霊場の奥にいて、大物が討たれ続けると目覚め、霊場への山道がつながる町に、何人かで受ける依頼が出る。"
      "倒すと、また大物を数え直す",
    ),
    (
      "何人かで受ける依頼",
      "受けたもののふは狩り場（霊場）へ向かい、先に着いた人は仲間を待つ（待つ間も行動回数を使う。溜めた行動力は使わない）。"
      "全員がそろったら一緒に戦う。物の怪は毎回、倒れていないもののふから 1 人を選んで攻撃する。"
      "誰かが倒せば全員の勝ちで、経験値は全員が満額、お金は人数で割った額。全員が倒れたら負け（全員力尽きる）。"
      "最初の 1 人が受けた日から決まった日数でそろわなければ、依頼は流れる（失敗にはならない）",
    ),
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
          f"（{t['name']}の物の怪はさらに × {t['gold_rate']:g}）"
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
    + f'<td class="num">{game.exp_need(world, lv) if lv < lv_max else "—"}</td>'
    + "</tr>"
    for lv in range(1, lv_max + 1)
    for b in [game.base_stats(world, None, lv)]
  )
  level_table = (
    '<div class="scroll"><table class="levels"><tr><th class="num">Lv</th>'
    + "".join(f'<th class="num">{h}</th>' for h in ("HP", "攻", "防", "速", "器", "次の Lv まで"))
    + "</tr>"
    + level_rows
    + "</table></div>"
  )
  cards = [
    (
      "能力",
      table(("能力", "説明"), stats)
      + f'<p class="note">{escape(stat_note)}</p>'
      + '<h4 class="sub">レベルごとの能力（特性の倍率がかかる前の値。強運のもののふと同じ。ほかの特性と装備で変わる）と、'
      "次のレベルまでに要る経験値</h4>" + level_table,
    ),
    (
      "もののふの特性",
      table(("特性", "効果", "得意", "不得意"), hero_traits)
      + '<p class="note">旅立ちのときに抽選で決まり、シーズンが変わっても変わらない。'
      "得意・不得意は、同じ点数の装備のどれを選ぶかに使う（伸ばすか補うかは性格で決まる）</p>",
    ),
    ("もののふの性格", personality_table(world, table)),
    *skill_cards(world, table),
    (
      "物の怪の性質",
      table(("性質", "効果"), monster_traits)
      + '<p class="note">物の怪のページの HP・攻・防は、性質の効果がかかる前の値</p>',
    ),
  ]
  if elements:
    cards.append(
      (
        "属性と相性",
        f"<p>相性（左が右に強い）: {element_chart(world)}</p>"
        + element_triangle(world)
        + table(("属性", "鉱石"), element_rows)
        + f'<p class="note">強い相手を攻めるとダメージ {rates["strong"]} 倍、苦手な相手だと {rates["weak"]} 倍。'
        "属性は物の怪と一部の武器・御守にある（属性のない物の怪・武器もある）。御守の属性は、武器に属性がないとき攻撃に付く。"
        "属性の武器は、その属性の力を持つ鉱石から作られる</p>",
      )
    )
  points = world["season"]["item_points"]
  labels = dict(ITEM_STATS)
  weight_rows = [
    (escape(labels[k]), escape(f"{v:g} 点"), escape(f"{1 / v:g}" if v else "—")) for k, v in points["weights"].items()
  ] + [("属性", escape(f"{points.get('element', 0):g} 点"), "属性 1 つ")]
  prices = world.get("skill_prices") or {}
  rarity_rows = [
    (
      escape(game.rarity_mark(world, r)),
      escape(f"{points['max'][r]:g} 点まで"),
      "なし" if r == W.COMMON else "付くことがある（武器・御守）",
      "1" if rank >= 3 else "0",
      f"1（{prices[r]}文）" if r in prices else "0",
    )
    for rank, r in enumerate(world["rarities"], 1)
  ]
  # 例には、マイナスの能力のある武具（なければ最初の武具）を使う
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
      "武具の強さ",
      "<p>武具の強さは点数で決まる。能力の増減に、能力ごとの点数を掛けて足す（マイナスの能力も同じ点数で引く）</p>"
      + table(("能力", "+1 あたり", "1 点になる量"), weight_rows)
      + '<h4 class="sub">珍しさごとの点数</h4>'
      + table(("珍しさ", "点数の上限", "属性", "固定スキル", "追加スキル"), rarity_rows)
      + '<p class="note">追加スキルの価格は鍛冶屋で1回付けるときの代金。★★★★武具は地方ごとの鍛冶屋で生成し、固定スキルは武具ごとに決まる。</p>'
      + f'<p class="note">{escape(example)}。もののふは ★ の多い武具を装備し（★ が同じなら点数の高いもの。'
      "それも同じなら、特性と性格で選ぶ。もののふの性格の「同じ点数の装備」）、店でもその順に買う（同じ強さの品は買い直さない）。"
      "スキルは点数に入らない</p>",
    )
  )
  quest_rows = [
    ("期限", "なし。必要な行動力をこなせば終わる。ふつう、失敗するのは力尽きたときだけ"),
    ("引き返す", "引き返さない。HP が減っても、依頼が終わるか力尽きるまで進む"),
    (
      "HP の回復",
      f"町に着いても HP は回復しない。依頼を持っていないとき、HP が性格で決まった割合を下回っていたら、"
      f"旅籠で休憩する（1 行動と {cfg['inn']['price']}文で全回復）。ほかに回復するのは、レベルアップと力尽きたときと丸薬だけ",
    ),
    *(
      [
        (
          "長くいた町",
          f"町に着いてから {cfg['stay_days_max']} 日たったら（着いた日を 0 日目として数える。討伐で同じ町に戻っても数え直さない）、"
          "その日は依頼を受けず、となりの町へ旅に出る。依頼の途中なら、帰ってきた次の日の朝に旅に出る",
        )
      ]
      if cfg.get("stay_days_max")
      else []
    ),
    (
      "残りの行動",
      "依頼は 1 日 1 回だけ受ける。依頼が終わって行動回数が残ったら、性格で決まった行動をする: "
      "休む（行動力を溜める）・町の周りを散策する・となりの町へ移動する",
    ),
    (
      "行動力を溜める",
      f"休むと、使わなかった行動力を {cfg['stored_days']} 日分（{game.stored_max(world)} 行動）まで溜められる（HP は回復しない）。"
      "溜めた行動力は、依頼の途中でその日の行動回数を使い切ったときと、旅籠の休憩に使う。シーズンが変わると 0 に戻る",
    ),
    (
      "散策",
      "町から動かずに、となりの道の出来事を引く。"
      + "・".join(
        f"{name} {w / sum(cfg['stroll_events'].values()):.0%}"
        for name, w in zip(
          (
            f"戦闘（Lv {cfg['encounter_levels'][0]:+d}〜{cfg['encounter_levels'][1]:+d}）",
            f"強い物の怪（Lv {cfg['stroll_strong_levels'][0]:+d}〜{cfg['stroll_strong_levels'][1]:+d}）",
            "宝箱",
            "何もない",
          ),
          (cfg["stroll_events"].get(k, 0) for k in ("battle", "strong_battle", "treasure", "nothing")),
          strict=True,
        )
      )
      + "。宝箱の中身は「武具と原石の入手」を参照",
    ),
    (
      "力尽きた",
      "最後に寄った町で目を覚ます（HP 全回復。お金・持ち物・丸薬は減らない）。受けていた依頼は失敗になり、その日はもう行動しない",
    ),
    (
      "依頼の失敗",
      "報酬はもらえない（それまでの戦いの経験値・お金・武具は残る）。高札の依頼は翌朝、最初の状態で受付中に戻る",
    ),
  ]
  party_gold = world["quest_reward"]["party_gold"]
  trip = game.HUNT_TRIP
  cost_rows = [
    ("配達", "依頼元の町から届け先までの、いちばん近い道のりの片道分"),
    (
      "討伐・大物の討伐",
      f"町から狩り場への移動 {trip} ＋ 討伐 1 ＋ 帰り {trip} で、どの道でも 1 人 {2 * trip + 1} 行動。"
      "何体と連戦しても討伐は 1 行動。大物の仲間待ちは別に数える",
    ),
    ("主の討伐", "町から霊場への山道の移動 ＋ 討伐 1 ＋ 帰りの山道。仲間待ちは別に数える"),
    ("受注・報告", "行動回数を使わない"),
    (
      "大物・主の達成報酬",
      f"★や行動回数によらず、大物 {party_gold['elite']}文・主 {party_gold['boss']}文を参加した全員が受け取る",
    ),
  ]
  cards.append(("依頼の行動回数", table(("依頼", "行動回数"), cost_rows)))
  cards.append(("依頼の進め方", table(("場面", "どうなるか"), quest_rows)))
  cards.append(("戦いの決まり", table(("決まり", "説明"), rules)))
  cards.append(("物の怪の経験値とお金", table(("項目", "決め方"), reward_rows)))

  def percent(rate: float) -> str:
    return f"{rate * 100:.4f}".rstrip("0").rstrip(".") + "%"

  rare_drops = cfg.get("rare_drops", True)
  loot_rows = []
  for mode, label in (("stroll", "散策1行動"), ("road", "移動1行動")):
    events = cfg[f"{mode}_events"]
    chest = events.get("treasure", 0) / sum(events.values())
    loot = dict(cfg[f"{mode}_treasure"])
    if not rare_drops:
      loot["gold"] = loot.get("gold", 0) + loot.pop("item", 0)

    probabilities = [percent(chest * loot.get(k, 0) / sum(loot.values())) for k in W.TREASURE_KINDS]
    loot_rows.append((label, *probabilities))
  quest_loot = []
  for rank in world["quest_ranks"]:
    weights = dict(rank["item"])
    if weights.get("rare"):
      chance_ = weights["rare"] / sum(weights.values()) if rare_drops else 0
      quest_loot.append((escape(rank["mark"]), percent(chance_), percent(1 - (1 - chance_) ** 2)))
  party = cfg["party"]
  stones = cfg["party_stones"]
  party_loot = []
  for kind, label, gear in (("elite", "大物", "★★★武具"), ("boss", "主", "抽選で決まる追加スキル付き★★★武具")):
    reward = f"原石 {stones[kind]} 個を 1 人に"
    gear_chance = 0
    if rare_drops:
      reward += f"、{gear}を残り {party[kind] - 1} 人に 1 つずつ"
      gear_chance = (party[kind] - 1) / party[kind]
    party_loot.append((label, reward, f"原石 {percent(1 / party[kind])}、武具 {percent(gear_chance)}"))
  forge_rows = []
  for town in world["towns"]:
    pool = sorted(
      (item for item in world["items"].values() if item.get("forge_town") == town),
      key=lambda item: W.SLOTS.index(item["slot"]),
    )
    if pool:
      chance = percent(1 / len(pool))
      forge_rows.extend((escape(world["places"][town]["name"]), escape(item["name"]), chance) for item in pool)
  ore_rows = [
    (escape(area["name"]), escape(area.get("stone", "—")), escape(area.get("stone_effect", "—")))
    for area in world["areas"].values()
  ]
  cards.append(
    (
      "武具と原石の入手",
      ("" if rare_drops else '<p class="note">今は★★以上の武具の配布を止めています。</p>')
      + table(("行動", "★★武具", "原石1個", "丸薬1個", "お金"), loot_rows)
      + "<p>表は1行動あたりの確率。宝探しが効くと宝箱の出現率が上がる。"
      "宝箱から出る★★武具と原石は散策した町、または出発した町で決まる。</p>"
      + table(("通常依頼の達成報告1回", "★★武具", "★★武具（強運）"), quest_loot)
      + "<p>依頼から出る★★武具は依頼元の町で決まる。強運は通常依頼の武具が外れたとき、もう1回引く。</p>"
      + table(("討伐成功1回", "一行の褒美", "各自の確率"), party_loot)
      + "<p>原石を受け取る人は参加者の中から均等に決める。原石の地方と武具の候補は依頼元の町で決まる。</p>"
      + '<h4 class="sub">地方の原石</h4>'
      + "<p>原石の性質は、将来その地方で作る武具の意匠と能力を考える手がかり。性質そのものに数値効果はなく、武具ごとの能力で決まる。</p>"
      + table(("地方", "原石", "素材の性質"), ore_rows)
      + '<h4 class="sub">★★★★武具の生成</h4>'
      + "<p>地方ごとの名工の鍛冶屋で、その地方の原石を 10 個と 10,000 文使うと、4 部位の★★★★武具から均等に 1 つ生成する。"
      + "1 日 1 回まで。行動回数は使わず、町での買い物時に行う。旅籠 1 泊分と丸薬の買い足し分のお金は残す。</p>"
      + table(("町", "生成される武具", "確率"), forge_rows)
      + "<p>現在は坂東の小田原で、玄岳鋼から生成できる。</p>",
    )
  )
  cards.append(season_card(world))
  nav = (
    '<p class="guide-nav">'
    + "".join(f'<a class="pill" href="#g{i}">{escape(h)}</a>' for i, (h, _) in enumerate(cards))
    + "</p>"
  )
  body = (
    "<h2>手引き</h2>"
    + nav
    + "".join(f'<div class="card" id="g{i}"><h3>{escape(h)}</h3>{inner}</div>' for i, (h, inner) in enumerate(cards))
  )
  return page("手引き", body, "guide.html")


# ---- 調練 ------------------------------------------------------------------------------------


def build_training(world: dict, report: dict | None) -> str:
  title = "<h2>調練</h2>"
  if report is None:
    return page("調練", title + "<p>調練結果はサイトの公開時に生成されます。</p>", "training.html")
  if not report["opponents"]:
    return page("調練", title + "<p>現在、調練の対象となる大物・主はありません。</p>", "training.html")
  trials = report["trials"]
  trait = escape(world["season"]["hero_traits"][report["trait"]]["name"])

  rows = []
  for result in report["opponents"]:
    monster = escape(world["monsters"][result["monster"]]["name"])
    rate = f"{result['baseline'] / trials:.2%}"
    rows.append(
      f'<tr><th scope="row">{monster}</th><td class="num">Lv{result["level"]}</td>'
      f'<td class="num">{result["party"]}人</td><td class="num">{rate}</td></tr>'
    )
  intro = (
    f"<p>各条件 {trials:,} 戦の勝率。ふつうのもののふ（武具・スキルなし、特性は全員{trait}）で測定。"
    f"丸薬は1人 {report['potions']} 個で、性格は順に入れ替えています。</p>"
  )
  results = (
    '<section class="card"><h3>大物・主の基準勝率</h3><div class="scroll"><table class="guide">'
    '<thead><tr><th scope="col">相手</th><th scope="col">もののふのLv</th>'
    '<th scope="col">人数</th><th scope="col">勝率</th></tr></thead><tbody>'
    + "".join(rows)
    + "</tbody></table></div></section>"
  )
  return page("調練", title + intro + results, "training.html")


# ---- もののふの詳細 ---------------------------------------------------------------------------
def build_hero(world: dict, hero: dict, state: dict) -> str:
  body = (
    f"<h2>{escape(hero['name'])}（{escape(hero['id'])}）</h2>"
    f"<pre>{escape(view.build_text(world, state))}</pre>"
    '<p><a href="../heroes.html">もののふの一覧へ戻る</a></p>'
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


# 前の地図のページ（map.html）。絵図を最初のページにしたので（#261）、index.html へ移すだけのページ
MOVED_MAP = (
  '<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">'
  '<meta http-equiv="refresh" content="0;url=index.html"><title>絵図 | ' + TITLE + "</title></head>"
  '<body><p><a href="index.html">絵図</a>へ移りました</p></body></html>\n'
)


def write_site(
  world: dict,
  entries: list[tuple[dict, dict]],
  today: date,
  guild: dict,
  out: Path,
  retired: list[tuple[dict, dict]] | None = None,
  *,
  training_report: dict | None = None,
) -> list[Path]:
  """サイトを out に書き出す（out の中身は作り直す）。書き出したファイルの一覧を返す。
  retired（引退したもののふ）は、詳細のページだけを書き出す（一覧や番付には出さない）。"""
  check_out(out)
  if out.exists():
    shutil.rmtree(out)
  (out / "heroes").mkdir(parents=True)
  files = {
    "index.html": build_map(world, today),
    "map.html": MOVED_MAP,  # 前の地図のページ（ブックマーク・外からのリンク）は、絵図（index.html）へ移す
    "ranking.html": build_ranking(world, entries, today),
    "heroes.html": build_heroes(world, entries, today),
    "guild.html": build_guild(world, entries, today, guild),
    "items.html": build_items(world),
    "monsters.html": build_monsters(world),
    "guide.html": build_guide(world),
    "training.html": build_training(world, training_report),
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
  """サイトに出す日付: もののふを最後に進めた日（毎日の実行の前に公開されても、日付が先に進んで中身が空にならないように）。"""
  days = [s["last_date"] for _, s in entries if s.get("last_date")]
  return date.fromisoformat(max(days)) if days else datetime.now(UTC).astimezone(common.JST).date()


def redirect_pages(files: list[Path], out: Path, base_url: str) -> None:
  """旧サイトの各ページから、同じページの新しい URL へ移す。"""
  redirect = urlsplit(base_url)
  if redirect.scheme != "https" or not redirect.hostname or redirect.query or redirect.fragment:
    raise SystemExit("移転先には HTTPS のサイト URL を指定してください")
  for path in files:
    target = base_url.rstrip("/") + "/" + path.relative_to(out).as_posix()
    encoded = json.dumps(target, ensure_ascii=True).replace("<", r"\u003c")
    path.write_text(
      '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
      "<title>もののふ道中の移転先</title></head><body>"
      f'<p>もののふ道中は移転しました。<a href="{escape(target)}">新しいサイトを開く</a></p>'
      f"<script>location.replace({encoded} + location.search + location.hash)</script></body></html>\n",
      encoding="utf-8",
    )


def main() -> int:
  ap = argparse.ArgumentParser(description="GitHub Pages に載せるサイトを書き出す")
  ap.add_argument("out", help="書き出す先のディレクトリ（中身は作り直す）")
  ap.add_argument("--redirect-to", help="書き出す各ページを移転先への案内にする")
  ap.add_argument("--date", help="表示する日付（既定はもののふを最後に進めた日。まだ誰も進めていなければ今日の JST）")
  ap.add_argument("--training-cache", type=Path, help="調練結果を再利用するファイル")
  a = ap.parse_args()
  out = Path(a.out)
  check_out(out)
  if a.training_cache:
    out_dir = out.resolve()
    # キャッシュへのシンボリックリンク自体が出力先にあっても、書き直しで消える。
    cache_paths = (a.training_cache, *a.training_cache.parents)
    if any(path.resolve().is_relative_to(out_dir) for path in cache_paths):
      ap.error("--training-cache は出力先のディレクトリの外に指定してください")
  world = W.load_world()
  entries = store.current_entries(world)
  today = date.fromisoformat(a.date) if a.date else shown_day(entries)
  retired = store.current_entries(world, retired=True)
  report = None if a.redirect_to else training.cached_report(world, a.training_cache)
  written = write_site(world, entries, today, store.load_guild(), out, retired, training_report=report)
  if a.redirect_to:
    redirect_pages(written, out, a.redirect_to)
  print(f"{a.out} に {len(written)} ファイルを書き出しました")
  return 0


if __name__ == "__main__":
  sys.exit(main())
