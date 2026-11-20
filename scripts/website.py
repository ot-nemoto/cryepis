#!/usr/bin/env python3
"""GitHub Pages に載せるサイト（HTML）を書き出す。冒険は進めない（今の状態を見せるだけ）。

ページ:
  index.html          世界の地図（拡大・移動ができる。場所や勇者にマウスを乗せると説明が出る）
  ranking.html        今シーズンのランキングと、勇者の一覧
  towns.html          町の情報（全部の町を地方ごとに。地方で絞り込める。説明・ギルドの規模・特産品・店・レア・道。開放していない町は「-」）
  items.html          開放した地域で手に入るアイテム（アイテムの部位とレアリティで絞り込める）
  guild.html          町ごとのギルドの掲示板（受付中・あと何日 / 受けられた依頼は ACCEPTED。町とレアリティで絞り込める）
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
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import common  # noqa: E402
import game  # noqa: E402
import mapsvg  # noqa: E402
import rpg  # noqa: E402
import world as W  # noqa: E402

TITLE = "オルドラ冒険記"
PAGES = (("index.html", "地図"), ("ranking.html", "ランキング"), ("towns.html", "町"), ("guild.html", "ギルド"),
         ("items.html", "アイテム"))

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
.en{font-weight:normal;font-style:italic;color:var(--sub);font-size:.85em;letter-spacing:.02em}
h2.area{margin-top:32px}
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
    nav = "".join(f'<a href="{up}{href}"' + (' class="on"' if href == current else "") + f">{name}</a>"
                  for href, name in PAGES)
    return (f'<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)} | {TITLE}</title><style>{CSS}</style></head><body>"
            f'<header><div class="wrap"><h1><a href="{up}index.html">{TITLE}</a></h1><nav>{nav}</nav></div></header>'
            f'<main class="wrap">{body}</main>' + (f"<script>{script}</script>" if script else "") + "</body></html>\n")


def hero_href(hero_id: str, depth: int = 0) -> str:
    return "../" * depth + f"heroes/{hero_id}.html"


def updated(today: date) -> str:
    return f'<p class="note">{today.isoformat()} の冒険を終えた状態（毎朝 7 時すぎに更新）</p>'


# ---- 地図 -------------------------------------------------------------------------------
def build_index(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
    svg = mapsvg.render(world, entries, links={h["id"]: hero_href(h["id"]) for h, _ in entries})
    body = [f"<h2>世界の地図</h2>{updated(today)}"]
    if svg:
        svg = svg[svg.index("<svg"):]
        body.append('<div id="map">' + svg + '<div class="zoom"><button id="zin" title="拡大">＋</button>'
                    '<button id="zout" title="縮小">－</button><button id="zhome" class="wide">全体</button></div></div>'
                    '<p class="note">ホイールやボタンで拡大、ドラッグで移動できます。町や勇者にマウスを乗せると説明が出ます。'
                    f'道の上の数字は移動にかかる行動回数です（勇者は 1 日に {world["season"]["actions_per_day"]} 回行動します）。</p>')
    else:
        body.append('<p class="note">地図はありません（world/map.json に layout がありません）。</p>')
    body.append("<h2>勇者の居場所</h2>")
    if entries:
        rows = "".join(f'<tr><td><a href="{hero_href(h["id"])}">{escape(h["name"])}</a></td>'
                       f'<td class="num">Lv.{s["hero"]["level"]}</td><td>{escape(rpg.location(world, s))}</td></tr>'
                       for h, s in entries)
        body.append(f'<div class="scroll"><table><tr><th>勇者</th><th class="num">レベル</th><th>居場所</th></tr>'
                    f"{rows}</table></div>")
    else:
        body.append('<p class="note">まだ勇者がいません。</p>')
    return page("地図", "".join(body), "index.html", script=MAP_JS if svg else "")


# ---- ランキング ---------------------------------------------------------------------------
def build_ranking(world: dict, entries: list[tuple[dict, dict]], today: date) -> str:
    season = game.season_of(world, today)
    first, last = game.season_range(world, season)
    views = [(h, rpg.season_view(world, s, season)) for h, s in entries]
    body = [f"<h2>シーズン{season}（{first}〜{last}）</h2>{updated(today)}"]
    news = rpg.news(views, today)
    body.append('<div class="card"><h3>📰 今日の出来事</h3>'
                + ("".join(f"<div>{escape(n)}</div>" for n in news) or '<div class="note">まだない</div>') + "</div>")
    cards = []
    for title, _, key, value, unit in rpg.RANKINGS:
        rows = "".join(f'<tr><td class="num">{r}</td><td><a href="{hero_href(h["id"])}">{escape(h["name"])}</a></td>'
                       f'<td class="num">{"Lv." if title == "レベル" else ""}{value(s)}{"" if title == "レベル" else unit}</td></tr>'
                       for r, h, s in rpg.ranked(views, key))
        cards.append(f'<div class="card"><h3>{escape(title)}</h3>'
                     + (f"<table>{rows}</table>" if rows else '<div class="note">まだいない</div>') + "</div>")
    body.append('<div class="grid">' + "".join(cards) + "</div>")
    body.append("<h2>勇者たち</h2>")
    heroes = []
    for h, s in views:
        hero, st = s["hero"], game.stats(world, s)
        last_ = s["last"]
        events = rpg.highlights(last_["events"]) if last_ and last_["date"] == today.isoformat() and last_["events"] else []
        heroes.append(f'<div class="card"><h3><a href="{hero_href(h["id"])}">{escape(h["name"])}</a> '
                      f'Lv.{hero["level"]}</h3><div>{escape(rpg.location(world, s))}</div>'
                      f'<div>HP {hero["hp"]}/{st["max_hp"]}　攻{st["atk"]} 防{st["def"]}　{hero["gold"]}G</div>'
                      f'<div>装備: {escape(rpg.gear_text(world, s))}</div>'
                      f'<div class="note">今日: {escape("、".join(events) or "—")}</div></div>')
    body.append('<div class="grid">' + ("".join(heroes) or '<p class="note">まだ勇者がいません。</p>') + "</div>")
    return page("ランキング", "".join(body), "ranking.html")


# ---- ギルド -------------------------------------------------------------------------------
# ギルドの絞り込み（町・レアリティ。どちらも複数選べて、何も選ばなければすべて）。
# 町は URL の #町の ID（複数なら , でつなぐ）でも選べる
GUILD_JS = """
(()=>{const chips=k=>[...document.querySelectorAll('.chip[data-'+k+']')],towns=chips('town'),ranks=chips('rank'),
none=document.getElementById('f-none'),clear=document.getElementById('f-clear');
const on=list=>new Set(list.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.town||c.dataset.rank));
const apply=()=>{const t=on(towns),r=on(ranks);let shown=0;
document.querySelectorAll('.board').forEach(card=>{let n=0;
card.querySelectorAll('tr[data-rank]').forEach(tr=>{const ok=!r.size||r.has(tr.dataset.rank);tr.hidden=!ok;if(ok)n++;});
card.hidden=(t.size&&!t.has(card.dataset.town))||n===0;if(!card.hidden)shown++;});
none.hidden=shown>0;clear.hidden=!t.size&&!r.size;
history.replaceState(null,'',location.pathname+location.search+(t.size?'#'+[...t].join(','):''));};
const toggle=c=>c.addEventListener('click',()=>{c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();});
towns.forEach(toggle);ranks.forEach(toggle);
clear.addEventListener('click',()=>{towns.concat(ranks).forEach(c=>c.setAttribute('aria-pressed','false'));apply();});
let raw=location.hash.slice(1);try{raw=decodeURIComponent(raw);}catch(e){}const ids=raw.split(',');towns.forEach(c=>{if(ids.includes(c.dataset.town))c.setAttribute('aria-pressed','true');});
apply();})();
"""


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
    """町ごとのギルドの掲示板（日をまたいで残る。早い者勝ち）。町とレアリティで絞り込める。"""
    season = game.season_of(world, today)
    if not guild.get("towns") or guild.get("season") != season or any(
            guild.get("refreshed", {}).get(t, "") < today.isoformat() for t in world["towns"]):
        # まだ誰も進めていない・その日の掲示板になっていない: その日の朝の掲示板を見せる（保存はしない）
        guild = copy.deepcopy(guild) if guild.get("season") == season else game.new_guild()
        game.refresh_guild(world, guild, today, season)
    places, days = world["places"], world["season"]["quest_open_days"]
    towns = "".join(f'<button type="button" class="chip" data-town="{escape(t)}" aria-pressed="false">'
                    f'{escape(places[t].get("emoji", ""))} {escape(places[t]["name"])}</button>' for t in world["towns"])
    ranks = "".join(f'<button type="button" class="chip" data-rank="{i}" aria-pressed="false">'
                    f'<span class="chip-mark">{escape(r["mark"])}</span></button>' for i, r in enumerate(world["quest_ranks"], 1))
    body = [f"<h2>町のギルドの掲示板（{today.isoformat()}）</h2>",
            '<p class="note">町ごとに決まった数の依頼が貼られていて、早い者勝ちです（受けられた依頼は、ほかの勇者は受けられません）。'
            f"誰も受けないまま {days} 日たった依頼は入れ替わります。達成された依頼は翌日に新しい依頼に入れ替わり、"
            "失敗した依頼は翌日にまた受け付けます。勇者は 1 日に 1 つ依頼を受け、必要な行動力を使ってこなします（期限はありません）。</p>",
            f'<div class="filters card"><div class="f-row"><span class="f-label">町</span>{towns}</div>'
            f'<div class="f-row"><span class="f-label">レアリティ</span>{ranks}</div>'
            '<div class="note">押して選ぶ（複数選べます。選ばなければすべて）'
            '<button type="button" id="f-clear" class="link" hidden>選択を解除</button></div></div>',
            '<p id="f-none" class="note" hidden>条件に合う依頼はありません。</p>']
    for town in world["towns"]:
        p = places[town]
        rows = []
        for e in guild.get("towns", {}).get(town, []):
            mark, _, rest = game.quest_text(world, e).partition(" ")
            notes = ([f"「{escape(e['text'])}」"] if e.get("text") else []) + (["前の勇者が失敗した依頼"] if e.get("returned") else [])
            rows.append(f'<tr data-rank="{e["rank"]}"><td class="mark">{escape(mark)}</td><td>{escape(rest)}'
                        + "".join(f'<div class="note">{n}</div>' for n in notes)
                        + f'</td><td class="num">{e["cost"]}</td>'
                        f'<td class="num">{e["reward"]["exp"]}EXP<br>{e["reward"]["gold"]}G</td>'
                        f"<td>{guild_status(world, e, today)}</td></tr>")
        body.append(f'<div class="card board" id="{escape(town)}" data-town="{escape(town)}"><h3>{escape(p.get("emoji", ""))} '
                    f'{escape(p["name"])}<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span></h3>'
                    '<div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em"><col><col style="width:4.5em">'
                    '<col style="width:6.5em"><col style="width:13em"></colgroup><tr><th></th><th>依頼</th><th class="num">行動力</th>'
                    '<th class="num">報酬</th><th>状態</th></tr>' + "".join(rows) + "</table></div></div>")
    return page("ギルド", "".join(body), "guild.html", script=GUILD_JS)


# ---- 町 ---------------------------------------------------------------------------------
# 町の絞り込み（地方。複数選べて、何も選ばなければすべて）
TOWNS_JS = """
(()=>{const chips=[...document.querySelectorAll('.chip[data-area]')],clear=document.getElementById('f-clear');
const apply=()=>{const on=new Set(chips.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.area));
document.querySelectorAll('.area-group').forEach(g=>{g.hidden=on.size>0&&!on.has(g.dataset.area);});
clear.hidden=!on.size;};
chips.forEach(c=>c.addEventListener('click',()=>{
c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
clear.addEventListener('click',()=>{chips.forEach(c=>c.setAttribute('aria-pressed','false'));apply();});
apply();})();
"""


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
        return (f'<span class="chip-mark">{escape(game.rarity_mark(world, it["rarity"]))}</span>{escape(it["name"])}'
                f'<span class="note">（{escape(rpg.item_power(it))}）</span>')

    def rows_of(town: str) -> list[tuple[str, str]]:
        size = f"依頼の枠 {game.quests_per_day(world, town)} 件"
        if town not in opened:
            return [("ギルドの規模", size)] + [(k, "-") for k in ("特産品", "依頼の報酬で出るレア", "店の品ぞろえ",
                                                                 "道（行き先と行動回数）")]
        shop = sorted(places[town].get("shop", []), key=lambda i: (-game.item_score(items[i]), i))
        rares = sorted((i for i, it in items.items() if town in it.get("towns", [])),
                       key=lambda i: -world["rarities"].index(items[i]["rarity"]))
        kinds = {"land": "陸路", "sea": "航路"}
        roads = [f'{escape(places[b]["name"])}（{kinds.get(world["road_info"][(town, b)]["kind"], "")} '
                 f'{world["roads"][(town, b)]}）' for b in world["neighbors"][town]]
        roads += [f'{escape(places[r["from"] if r["to"] == town else r["to"]]["name"])}（ダンジョン・まだ入れない）'
                  for r in world["trails"] if town in (r["from"], r["to"])]
        return [("ギルドの規模", f'{size}（<a href="guild.html#{escape(town)}">掲示板を見る</a>）'),
                ("特産品", "<br>".join(label(i) for i in shop if sellers.get(i) == 1) or "-"),
                ("依頼の報酬で出るレア", "<br>".join(label(i) for i in rares) or "-"),
                ("店の品ぞろえ", "<br>".join(label(i) for i in shop) or "-"),
                ("道（行き先と行動回数）", "・".join(roads) or "-")]

    def card(town: str) -> str:
        p, is_open = places[town], town in opened
        en = f' <span class="en">{escape(p["en"])}</span>' if p.get("en") else ""
        return (f'<div class="card{"" if is_open else " closed"}" id="{escape(town)}"><h3>{escape(p.get("emoji", ""))} '
                f'{escape(p["name"])}{en}<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span>'
                + ("" if is_open else '<span class="tag">未開放</span>') + "</h3>"
                + (f'<p>{escape(p["text"])}</p>' if p.get("text") else "")
                + '<div class="scroll"><table class="info"><colgroup><col style="width:11em"><col></colgroup>'
                + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows_of(town)) + "</table></div></div>")

    body = ["<h2>町の情報</h2>",
            '<p class="note">オルドラの町の一覧です（地方ごと）。勇者が行けるのは、開放した町（' +
            "・".join(escape(places[t]["name"]) for t in world["towns"]) + "）だけです。"
            "ギルドの規模は、掲示板に貼られる依頼の数（枠）です。特産品は、世界でその町の店だけが売る品です。</p>"]
    groups = [(aid, a, [t for t in towns if places[t].get("area") == aid]) for aid, a in world["areas"].items()]
    groups.append(("other", {"name": "その他"}, [t for t in towns if places[t].get("area") not in world["areas"]]))
    groups = [g for g in groups if g[2]]
    groups.sort(key=lambda g: not any(t in opened for t in g[2]))  # 開放した町のある地方を先に（安定ソート）
    chips = "".join(f'<button type="button" class="chip" data-area="{escape(aid)}" aria-pressed="false">'
                    f'{escape(a["name"])}</button>' for aid, a, _ in groups)
    body.append(f'<div class="filters card"><div class="f-row"><span class="f-label">地方</span>{chips}</div>'
                '<div class="note">押して選ぶ（複数選べます。選ばなければすべて）'
                '<button type="button" id="f-clear" class="link" hidden>選択を解除</button></div></div>')
    for aid, area, members in groups:
        en = f' <span class="en">{escape(area["en"])}</span>' if area.get("en") else ""
        body.append(f'<section class="area-group" data-area="{escape(aid)}"><h2 class="area">{escape(area["name"])}{en}</h2>'
                    + "".join(card(t) for t in members) + "</section>")
    return page("町", "".join(body), "towns.html", script=TOWNS_JS)


# ---- アイテム ---------------------------------------------------------------------------
# アイテムの絞り込み（アイテムの部位・レアリティ。どちらも複数選べて、何も選ばなければすべて）
ITEMS_JS = """
(()=>{const chips=k=>[...document.querySelectorAll('.chip[data-'+k+']')],slots=chips('slot'),rars=chips('rarity'),
none=document.getElementById('f-none'),clear=document.getElementById('f-clear');
const on=list=>new Set(list.filter(c=>c.getAttribute('aria-pressed')==='true').map(c=>c.dataset.slot||c.dataset.rarity));
const apply=()=>{const s=on(slots),r=on(rars);let shown=0;
document.querySelectorAll('.board').forEach(card=>{let n=0;
card.querySelectorAll('tr[data-rarity]').forEach(tr=>{const ok=!r.size||r.has(tr.dataset.rarity);tr.hidden=!ok;if(ok)n++;});
card.hidden=(s.size&&!s.has(card.dataset.slot))||n===0;if(!card.hidden)shown++;});
none.hidden=shown>0;clear.hidden=!s.size&&!r.size;};
slots.concat(rars).forEach(c=>c.addEventListener('click',()=>{
c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();}));
clear.addEventListener('click',()=>{slots.concat(rars).forEach(c=>c.setAttribute('aria-pressed','false'));apply();});
apply();})();
"""


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
            rows[it["slot"]].append({"id": iid, "item": it, "shops": shops.get(iid, []), "towns": towns,
                                     "specialty": len(sellers) == 1})  # 世界でその町だけが売る品
    for slot in rows:
        rows[slot].sort(key=lambda r: (-rank[r["item"]["rarity"]], -game.item_score(r["item"]), r["item"].get("price", 0)))
    return rows


def build_items(world: dict) -> str:
    """開放した地域で手に入るアイテムの一覧。アイテムの部位とレアリティで絞り込める。"""
    rows = item_rows(world)
    used = [r for r in world["rarities"] if any(x["item"]["rarity"] == r for rs in rows.values() for x in rs)]
    slot_chips = "".join(f'<button type="button" class="chip" data-slot="{s}" aria-pressed="false">{W.SLOT_NAMES[s]}</button>'
                         for s in W.SLOTS)
    rarity_chips = "".join(f'<button type="button" class="chip" data-rarity="{r}" aria-pressed="false">'
                           f'<span class="chip-mark">{escape(game.rarity_mark(world, r))}</span></button>'
                           for r in used)
    potion = world["season"]["potion"]
    body = ["<h2>アイテム一覧</h2>",
            '<p class="note">開放した地域（' + "・".join(escape(world["places"][t]["name"]) for t in world["towns"]) +
            "）で手に入る装備です。部位は武器・兜・盾・装飾品の 4 つで、部位ごとにいちばん強いものを自動で装備します。"
            "★ の装備は町の店で買え、宝箱や依頼の報酬でも出ます（開放した地域の店の品だけ）。"
            "★★ 以上のレアは、その町のギルドの依頼の報酬でしか出ません。</p>",
            f'<div class="filters card"><div class="f-row"><span class="f-label">アイテム</span>{slot_chips}</div>'
            f'<div class="f-row"><span class="f-label">レアリティ</span>{rarity_chips}</div>'
            '<div class="note">押して選ぶ（複数選べます。選ばなければすべて）'
            '<button type="button" id="f-clear" class="link" hidden>選択を解除</button></div></div>',
            '<p id="f-none" class="note" hidden>条件に合うアイテムはありません。</p>']
    for slot in W.SLOTS:
        trs = []
        for r in rows[slot]:
            it = r["item"]
            if r["towns"]:
                how = "依頼の報酬: " + "・".join(escape(t) for t in r["towns"])
            else:
                how = "店: " + ("・".join(escape(t) for t in r["shops"]) or "なし") + '<div class="note">宝箱・依頼の報酬でも出る</div>'
            special = r["specialty"]
            trs.append(f'<tr data-rarity="{it["rarity"]}"><td class="mark">{escape(game.rarity_mark(world, it["rarity"]))}</td>'
                       f'<td>{escape(it["name"])}' + ('<span class="tag">特産</span>' if special else "") + "</td>"
                       f'<td>{escape(rpg.item_power(it))}</td>'
                       f'<td class="num">{str(it["price"]) + "G" if it.get("price") else "—"}</td><td>{how}</td></tr>')
        body.append(f'<div class="card board" data-slot="{slot}"><h3>{W.SLOT_NAMES[slot]}</h3>'
                    '<div class="scroll"><table class="wide-table"><colgroup><col style="width:4.5em"><col style="width:13em">'
                    '<col style="width:9em"><col style="width:5.5em"><col></colgroup>'
                    '<tr><th></th><th>名前</th><th>性能</th><th class="num">値段</th>'
                    '<th>手に入れ方</th></tr>' + "".join(trs) + "</table></div></div>")
    body.append(f'<div class="card"><h3>回復薬</h3><div>どの町の店でも {potion["price"]}G。戦いで倒れそうになったときに使い、'
                f'最大 HP の {potion["heal_rate"]:.0%} まで回復します（町で {potion["keep"]} 個まで買い足します）。</div></div>')
    return page("アイテム", "".join(body), "items.html", script=ITEMS_JS)


# ---- 勇者の詳細 ---------------------------------------------------------------------------
def build_hero(world: dict, hero: dict, state: dict) -> str:
    body = (f'<h2>{escape(hero["name"])}（{escape(hero["id"])}）</h2>'
            f"<pre>{escape(rpg.build_text(world, state, fold=False))}</pre>"
            '<p><a href="../ranking.html">ランキングへ戻る</a></p>')
    return page(hero["name"], body, "", depth=1)


# ---- 書き出し -----------------------------------------------------------------------------
def check_out(out: Path) -> None:
    """書き出す先を消してよいか確かめる（中身を作り直すので、リポジトリやほかのディレクトリを消さないように）。
    消してよいのは、まだないか、空か、前に書き出したサイト（index.html と guild.html がある）だけ。"""
    target, root = out.resolve(), common.ROOT.resolve()
    if target == root or target in root.parents or (root in target.parents and target.relative_to(root).parts[0]
                                                    in ("heroes", ".state", "world", "scripts", "tests", "docs", ".github", "tools")):
        raise SystemExit(f"{out} には書き出せません（リポジトリのファイルを消してしまうため。_site などを指定してください）")
    if out.exists() and (not out.is_dir() or (any(out.iterdir())
                                              and not ((out / "index.html").is_file() and (out / "guild.html").is_file()))):
        raise SystemExit(f"{out} は前に書き出したサイトではないので、消して作り直せません（空のディレクトリか、新しい名前を指定してください）")


def write_site(world: dict, entries: list[tuple[dict, dict]], today: date, guild: dict, out: Path) -> list[Path]:
    """サイトを out に書き出す（out の中身は作り直す）。書き出したファイルの一覧を返す。"""
    check_out(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "heroes").mkdir(parents=True)
    files = {
        "index.html": build_index(world, entries, today),
        "ranking.html": build_ranking(world, entries, today),
        "guild.html": build_guild(world, entries, today, guild),
        "items.html": build_items(world),
        "towns.html": build_towns(world),
    }
    for hero, state in entries:
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
    return date.fromisoformat(max(days)) if days else datetime.now(timezone.utc).astimezone(common.JST).date()


def main() -> int:
    ap = argparse.ArgumentParser(description="GitHub Pages に載せるサイトを書き出す")
    ap.add_argument("out", help="書き出す先のディレクトリ（中身は作り直す）")
    ap.add_argument("--date", help="表示する日付（既定は勇者を最後に進めた日。まだ誰も進めていなければ今日の JST）")
    a = ap.parse_args()
    world = rpg.load_world()
    entries = rpg.current_entries(world)
    today = date.fromisoformat(a.date) if a.date else shown_day(entries)
    written = write_site(world, entries, today, rpg.load_guild(), Path(a.out))
    print(f"{a.out} に {len(written)} ファイルを書き出しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
