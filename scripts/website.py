#!/usr/bin/env python3
"""GitHub Pages に載せるサイト（HTML）を書き出す。冒険は進めない（今の状態を見せるだけ）。

ページ:
  index.html          世界の地図（拡大・移動ができる。場所や勇者にマウスを乗せると説明が出る）
  ranking.html        今シーズンのランキングと、勇者の一覧
  guild.html          町ごとのギルドの、今日の依頼と、受けた勇者
  heroes/<id>.html    勇者ごとの詳細（ステータス・装備・持ち物・依頼・冒険の記録）

データは .state/（勇者の状態・ギルドの受けられた依頼）と world/ から読む。
毎日の実行のあとに pages.yml が書き出して公開する（書き出したファイルはコミットしない）。

使い方:
  python scripts/website.py OUT_DIR [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
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

TITLE = "オルドラ冒険記"
PAGES = (("index.html", "地図"), ("ranking.html", "ランキング"), ("guild.html", "ギルド"))

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
.wide-table{min-width:560px}
.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
.mark{color:#b7791f;white-space:nowrap;width:5em}
.taken{color:var(--hero);white-space:nowrap}
.free{color:var(--sub);white-space:nowrap}
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
def build_guild(world: dict, entries: list[tuple[dict, dict]], today: date, guild: dict) -> str:
    """町ごとの、今日の依頼の掲示板と、受けた勇者（早い者勝ち）。"""
    season = game.season_of(world, today)
    names = {h["id"]: h["name"] for h, _ in entries}
    places = world["places"]
    body = [f"<h2>町のギルドの依頼（{today.isoformat()}）</h2>",
            '<p class="note">依頼は町ごとに毎日決まった数だけ出て、早い者勝ちです（ほかの勇者が受けた依頼は受けられません）。'
            "勇者は 1 日に 1 つ依頼を受け、必要な行動力を使ってこなします。</p>"]
    for town in world["towns"]:
        p = places[town]
        rows = []
        for q in game.board(world, town, today, season):
            who = guild.get(q["id"])
            status = (f'<a class="taken" href="{hero_href(who)}">{escape(names[who])}</a>' if who in names
                      else '<span class="taken">受注済み</span>' if who else '<span class="free">空き</span>')
            mark, _, rest = game.quest_text(world, q).partition(" ")
            rows.append(f'<tr><td class="mark">{escape(mark)}</td><td>{escape(rest)}'
                        + (f'<div class="note">「{escape(q["text"])}」</div>' if q.get("text") else "")
                        + f'</td><td class="num">{q["cost"]}</td>'
                        f'<td class="num">{q["reward"]["exp"]}EXP<br>{q["reward"]["gold"]}G</td><td>{status}</td></tr>')
        doing = "".join(f'<li><a href="{hero_href(h["id"])}">{escape(h["name"])}</a>: {escape(game.quest_text(world, s["quest"]))}'
                        f'（{s["quest"]["progress"]}/{s["quest"]["count"]}）</li>'
                        for h, s in entries if s["quest"] and s["quest"].get("town") == town)
        here = [h for h, s in entries if s["place"] == town and not s["road"]]
        who_here = "、".join(f'<a href="{hero_href(h["id"])}">{escape(h["name"])}</a>' for h in here)
        body.append(f'<div class="card" id="{escape(town)}"><h3>{escape(p.get("emoji", ""))} {escape(p["name"])}'
                    f'<span class="note">（{escape(p.get("alias", ""))}・{escape(p.get("region", ""))}）</span></h3>'
                    + (f'<div class="note">滞在中: {who_here}</div>' if who_here else "")
                    + '<div class="scroll"><table class="wide-table"><tr><th></th><th>今日の依頼</th><th class="num">行動力</th>'
                    '<th class="num">報酬</th><th>受けた勇者</th></tr>' + "".join(rows) + "</table></div>"
                    + (f'<div class="note">このギルドの依頼をこなしている勇者:</div><ul>{doing}</ul>' if doing else "")
                    + "</div>")
    return page("ギルド", "".join(body), "guild.html")


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
    written = write_site(world, entries, today, rpg.load_guild(today), Path(a.out))
    print(f"{a.out} に {len(written)} ファイルを書き出しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
