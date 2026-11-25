import ast
import copy
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import game  # noqa: E402
import mapsvg  # noqa: E402
import rpg  # noqa: E402
import view  # noqa: E402
import website  # noqa: E402
import world as W  # noqa: E402

WORLD = rpg.load_world()
DAY = date.fromisoformat(WORLD["season"]["start"])


def party(n=3, days=5):
  """n 人の勇者を、ギルドを共有して days 日進める。(entries, guild, 最後の日)"""
  entries = [({"id": f"h{i}", "name": f"勇者{i}"}, game.new_state(WORLD, f"h{i}", f"勇者{i}")) for i in range(1, n + 1)]
  guild = game.new_guild()
  for k in range(days):
    for _, state in entries:
      game.advance(WORLD, state, DAY + timedelta(days=k), None, guild)
  return entries, guild, DAY + timedelta(days=days - 1)


class Links(HTMLParser):
  def __init__(self):
    super().__init__()
    self.hrefs = []

  def handle_starttag(self, tag, attrs):
    if tag == "a":
      self.hrefs.append(dict(attrs).get("href"))


class SiteTest(unittest.TestCase):
  def setUp(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.out = Path(tmp.name) / "site"

  def test_pages_are_written_and_links_resolve(self):
    entries, guild, today = party()
    written = website.write_site(WORLD, entries, today, guild, self.out)
    rels = {p.relative_to(self.out).as_posix() for p in written}
    self.assertEqual(
      rels,
      {
        "index.html",
        "map.html",
        "ranking.html",
        "heroes.html",
        "guild.html",
        "items.html",
        "monsters.html",
        "guide.html",
        "towns.html",
        "heroes/h1.html",
        "heroes/h2.html",
        "heroes/h3.html",
      },
    )
    for path in written:  # サイトの中のリンクは、すべて書き出したファイルを指す
      parser = Links()
      parser.feed(path.read_text(encoding="utf-8"))
      for href in parser.hrefs:
        target = (path.parent / href.split("#")[0]).resolve()
        self.assertTrue(target.is_file(), f"{path.name} → {href}")

  def test_out_dir_is_rebuilt(self):
    website.write_site(WORLD, [], DAY, {}, self.out)
    (self.out / "old.html").write_text("x")
    website.write_site(WORLD, [], DAY, {}, self.out)
    self.assertFalse((self.out / "old.html").exists())
    self.assertIn("まだ勇者がいません", (self.out / "heroes.html").read_text(encoding="utf-8"))

  def test_does_not_delete_other_directories(self):
    other = self.out.parent / "other"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    for out in (other, rpg.common.ROOT, rpg.common.ROOT / "heroes", rpg.common.ROOT / ".state", rpg.common.ROOT.parent):
      with self.assertRaises(SystemExit):
        website.write_site(WORLD, [], DAY, {}, out)
    self.assertTrue((other / "keep.txt").exists())
    self.assertTrue((rpg.common.ROOT / "world" / "map.json").exists())
    (other / "keep.txt").unlink()  # 空のディレクトリには書き出せる
    website.write_site(WORLD, [], DAY, {}, other)
    self.assertTrue((other / "index.html").exists())

  def test_shown_day_is_the_last_advanced_day(self):
    entries, _, last = party(2, 3)
    self.assertEqual(website.shown_day(entries), last)
    entries[0][1]["last_date"] = None  # まだ進めていない勇者がいても、最後に進めた日
    self.assertEqual(website.shown_day(entries), last)

  def test_map_is_inline_svg_with_hero_marks(self):
    entries, guild, today = party(1, 1)
    html = website.build_map(WORLD, entries, today)
    svg = html[html.index("<svg") : html.index("</svg>") + 6]
    root = ET.fromstring(svg)  # 正しい SVG として埋め込まれている
    self.assertEqual(list(root.iter("{http://www.w3.org/2000/svg}a")), [])  # 地図に名札（リンク）は出さない
    self.assertEqual(len([g for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("class") == "hero"]), 1)
    self.assertNotIn("勇者の居場所", html)  # 地図のページに勇者の一覧は出さない（勇者のページで見る）
    self.assertNotIn('href="heroes/h1.html"', html)

  def test_ranking_shows_top_ten(self):
    entries = [({"id": f"h{i}", "name": f"勇者{i}"}, game.new_state(WORLD, f"h{i}", f"勇者{i}")) for i in range(1, 15)]
    for i, (_, state) in enumerate(entries):  # Lv が違う 14 人（1 人だけ 10 位と同じ Lv）
      state["season"] = 1
      state["hero"]["level"] = 20 - i if i != 10 else 11
    html = website.build_ranking(WORLD, entries, DAY)
    level = html[html.index("<h3>レベル</h3>") :]
    level = level[: level.index("</table>")]
    self.assertEqual(level.count("<tr>"), 10)  # 10 人で切る（同じ 10 位が 2 人でも、登録の早い 1 人だけ）
    self.assertIn("勇者10<", level)
    self.assertNotIn("勇者11<", level)
    self.assertNotIn("勇者12<", level)
    kills = html[html.index("<h3>討伐数</h3>") :]
    self.assertEqual(kills[: kills.index("</table>")].count("<tr>"), 10)  # 全員 0 体（同率 1 位）でも 10 人
    board = rpg.build_board(WORLD, entries, DAY)
    part = board[board.index("── レベル ──") :]
    part = part[: part.index("\n\n")]
    self.assertEqual(len(part.splitlines()) - 1, 10)  # Gist の本文も 10 人まで

  def test_guild_shows_who_took_todays_quests(self):
    entries, guild, today = party(4, 3)
    html = website.build_guild(WORLD, entries, today, guild)
    taken = [e for es in guild["towns"].values() for e in es if e["status"] != "open"]
    self.assertTrue(taken)
    self.assertEqual(html.count('<span class="stamp">ACCEPTED</span>'), len(taken))
    self.assertNotIn("heroes/", html.split("<main")[1])  # 受けた勇者の名前は出さない
    self.assertNotIn("滞在中", html)
    total = sum(len(es) for es in guild["towns"].values())
    self.assertEqual(html.count("<tr data-f="), total)
    self.assertNotIn("町ごとに決まった数の依頼", html)  # 説明文は出さない
    self.assertNotIn('data-town="', html)  # 町の絞り込みはない
    self.assertEqual(html.count('class="chip" data-f='), len(WORLD["quest_ranks"]))  # レアリティで絞り込める
    self.assertIn('data-tab="austral_land" aria-selected="true" tabindex="0">南大陸</button>', html)  # 地方のタブ
    self.assertIn('id="panel-austral_land"', html)
    self.assertNotIn('id="panel-crownlands"', html)  # 開放した町のない地方のタブは出さない
    for town in WORLD["towns"]:  # 町の名前に英語名を添える
      p = WORLD["places"][town]
      self.assertIn(f'{p["name"]} <span class="en">{p["en"]}</span>', html)
      # 町のページの「掲示板を見る」（guild.html#町の ID）が、その地方のタブの中の掲示板を指す
      panel = html[html.index(f'id="panel-{p["area"]}"') :]
      panel = panel[: panel.index("</section>")]
      self.assertIn(f'<div class="card board" id="{town}">', panel)
    days = WORLD["season"]["quest_open_days"]
    last = today + timedelta(days=days - 1)
    self.assertTrue(
      f"あと {days - 1} 日（{last.month}/{last.day} まで）" in html or "今日まで" in html or "あと 1 日" in html
    )  # 受付中の依頼には、受けられる残りの日数を出す

  def test_guild_page_for_a_later_day_than_the_board(self):
    entries, guild, today = party(2, 2)
    later = today + timedelta(days=WORLD["season"]["quest_open_days"] + 2)
    html = website.build_guild(WORLD, entries, later, guild)
    self.assertNotIn("受付終了", html)  # その日の朝の掲示板に貼り替えて見せる
    self.assertIn(f"あと {WORLD['season']['quest_open_days'] - 1} 日", html)
    self.assertEqual(guild["refreshed"][WORLD["towns"][0]], today.isoformat())  # 元の掲示板は変えない
    e = {"posted": (today - timedelta(days=10)).isoformat(), "status": "open"}
    self.assertIn("受付終了", website.guild_status(WORLD, e, today))

  def test_items_page_skips_empty_kinds(self):
    world = copy.deepcopy(WORLD)
    for town in world["towns"]:  # 開放した地域で兜を売らず、依頼でも出ないようにする
      p = world["places"][town]
      p["shop"] = [i for i in p["shop"] if world["items"][i]["slot"] != "helmet"]
    world["region_items"] = [i for i in world["region_items"] if world["items"][i]["slot"] != "helmet"]
    for it in world["items"].values():
      if it["slot"] == "helmet":
        it["towns"] = []
    html = website.build_items(world)
    self.assertNotIn('id="tab-helmet"', html)
    self.assertIn('id="tab-weapon"', html)

  def test_items_page_lists_the_open_region(self):
    html = website.build_items(WORLD)
    names = {it["name"] for it in WORLD["items"].values()}
    shown = {n for n in names if f"{n}<" in html or f"{n}</td>" in html}
    expected = {WORLD["items"][i]["name"] for i in WORLD["region_items"]} | {
      it["name"] for it in WORLD["items"].values() if it["rarity"] != "common"
    }
    self.assertEqual(shown, expected)
    self.assertNotIn('data-tab="armor"', html)  # 鎧の部位はない
    self.assertNotIn("data-slot=", html)  # 装備の種類の絞り込みはない
    self.assertNotIn("手に入る装備です", html)  # 説明文は出さない
    self.assertNotIn("回復薬", html)  # 回復薬は町の店の品ぞろえに出す
    for slot in ("weapon", "helmet", "shield", "accessory"):  # 装備の種類ごとのタブ
      self.assertIn(f'role="tab" id="tab-{slot}"', html)
      self.assertIn(f'id="panel-{slot}"', html)
    self.assertIn('data-tab="weapon" aria-selected="true" tabindex="0">⚔️ 武器</button>', html)  # 部位の先頭に絵文字
    self.assertIn('<h2 class="area">💍 装飾品</h2>', html)
    self.assertEqual(html.count('class="chip" data-f='), 2)  # ★ と ★★
    charm = html.split("紅蓮草の護符")[1].split("</tr>")[0]  # ★★紅蓮草の護符（炎属性）
    self.assertIn('<td><a class="pill" href="towns.html#muralis">🛡️ ムラリス</a></td>', charm)  # 依頼を出す町
    self.assertIn('<td class="icon"><span class="tag">炎属性</span></td>', charm)  # 属性の列（枠の札）
    head = '<th class="num">HP</th><th class="num">攻</th><th class="num">防</th><th class="num">速</th><th class="num">器</th>'
    self.assertIn(head + '<th class="num">値段</th><th>入手方法</th>', html)  # 能力ごとの列
    header = html.split("<th>名前</th>")[1].split("</tr>")[0]  # 表の見出しの行
    self.assertNotIn("性能", header)
    self.assertNotIn("手に入れ方", header)
    axe = html.split("鉄の斧")[1].split("</tr>")[0]  # 攻+2・速-2、開放した 4 町の店で売っている
    self.assertIn('<td class="num zero">0</td><td class="num">+2</td><td class="num zero">0</td>', axe)
    self.assertIn('<td class="num minus">-2</td>', axe)  # マイナスは赤字
    self.assertEqual(axe.count('class="pill"'), 4)  # 買える店
    self.assertNotIn("依頼の報酬", html)  # 入手方法の補足の文は出さない
    self.assertNotIn("宝箱", html)
    self.assertIn('<th>名前</th><th class="icon">属性</th><th class="num">HP</th>', html)  # 特産の列はない
    self.assertNotIn("特産", html)
    sword = html.split("<td>鉄の剣")[1].split("</tr>")[0]
    self.assertIn(
      '</span></td><td class="icon"></td><td class="num zero">0</td>', sword
    )  # ★ は属性なし（名前の下に説明がある）
    self.assertNotIn("🏷️", html)  # 属性は絵文字にしない
    self.assertNotIn("🌪️", html)
    self.assertIn('<td class="mark">★★</td><td>鋼鉄の剣', html)  # ★ は先頭の列
    for slot in ("weapon", "helmet", "shield", "accessory"):  # ★ の多い順
      marks = [len(game.rarity_mark(WORLD, r["item"]["rarity"])) for r in website.item_rows(WORLD)[slot]]
      self.assertEqual(marks, sorted(marks, reverse=True))

  def test_items_page_shows_the_descriptions(self):
    html = website.build_items(WORLD)
    for it in WORLD["items"].values():  # 13 個すべてに説明があり、名前の下に出る
      self.assertIn(f'<td>{escape(it["name"])}<br><span class="note">{escape(it["text"])}</span></td>', html)

  def test_item_description_is_optional_and_escaped(self):
    world = copy.deepcopy(WORLD)
    world["items"]["iron_sword"]["text"] = "<b>剣</b> & 盾"
    world["items"]["iron_axe"].pop("text")
    html = website.build_items(world)
    self.assertIn('<span class="note">&lt;b&gt;剣&lt;/b&gt; &amp; 盾</span>', html)  # エスケープする
    self.assertNotIn("<b>剣</b>", html)
    axe = html.split("<td>鉄の斧")[1].split("</tr>")[0]
    self.assertTrue(axe.startswith("</td>"), axe[:30])  # 説明がなければ、名前だけ
    self.assertNotIn('<span class="note">', axe.split("</td>")[0])

  def test_town_pill_and_stat_cell(self):
    world = copy.deepcopy(WORLD)
    world["places"]["muralis"].pop("emoji")
    self.assertEqual(website.town_pill(world, "muralis"), '<a class="pill" href="towns.html#muralis">ムラリス</a>')
    self.assertEqual(website.stat_cell(3), '<td class="num">+3</td>')
    self.assertEqual(website.stat_cell(-2), '<td class="num minus">-2</td>')
    self.assertEqual(website.stat_cell(0), '<td class="num zero">0</td>')
    self.assertIn("--minus:#f87171", website.build_items(WORLD))  # ダークモードでは明るい赤

  def test_towns_page(self):
    html = website.build_towns(WORLD)
    towns = [t for t, p in WORLD["places"].items() if p["type"] == "town"]
    self.assertEqual(len(towns), 25)
    for town in towns:  # 全部の町を出す
      p = WORLD["places"][town]
      self.assertIn(f'id="{town}"', html)
      self.assertIn(p["text"], html)
      self.assertIn(f"依頼の枠 {p['quests_per_day']} 件", html)  # ギルドの規模はどの町にもある
      self.assertEqual(f'href="guild.html#{town}"' in html, town in WORLD["towns"])
    self.assertLess(html.index("南大陸 <span"), html.index("王領 <span"))  # 開放した地方が先
    self.assertNotIn("オルドラの町の一覧です", html)  # 説明文は出さない
    self.assertLess(html.index("<h2>町の情報</h2>"), html.index('class="tabs"'))  # タブは見出しのすぐ下
    self.assertIn('data-tab="austral_land" aria-selected="true" tabindex="0">南大陸</button>', html)  # 最初は南大陸
    for aid in WORLD["areas"]:  # 地方ごとのタブと、タブにつながる中身
      self.assertIn(f'id="tab-{aid}" aria-controls="panel-{aid}"', html)
      self.assertIn(f'role="tabpanel" id="panel-{aid}" aria-labelledby="tab-{aid}" data-tab="{aid}">', html)
    self.assertIn('<div class="tabs" role="tablist" hidden>', html)  # JavaScript がなければ全部の地方を見せる
    self.assertNotIn('data-tab="crownlands" hidden', html)
    for area in WORLD["areas"].values():
      self.assertIn(f'{area["name"]} <span class="en">{area["en"]}</span>', html)
    self.assertIn('サリナ <span class="en">Salina</span>', html)
    aurea = html[html.index('id="aurea"') : html.index('id="flumen"')]  # 開放していない町
    self.assertIn("未開放", aurea)
    self.assertIn("依頼の枠 6 件", aurea)
    self.assertNotIn("特産品", html)  # 町ごとの特産はない
    self.assertNotIn("鉄の剣", aurea)
    muralis = html[html.index('id="muralis"') : html.index('id="karun"')]  # 南大陸の中は places の順
    self.assertIn("紅蓮草の護符", muralis.split("店の品ぞろえ")[0])  # レア
    self.assertIn("南壁の巣穴（ダンジョン・まだ入れない）", muralis)
    self.assertIn("カルン（陸路 6）", muralis)
    shop = muralis.split("店の品ぞろえ</th><td>")[1].split("</td>")[0]  # 種類の順、同じ種類なら値段の高い順
    *equips, potion = shop.split("<br>")  # 装備のあとに回復薬
    self.assertTrue(potion.startswith("回復薬<span"))
    names = [n.split("</span>")[1].split("<span")[0] for n in equips]
    by_name = {it["name"]: it for it in WORLD["items"].values()}
    keys = [(W.SLOTS.index(by_name[n]["slot"]), -by_name[n]["price"]) for n in names]
    self.assertEqual(keys, sorted(keys))
    self.assertEqual(names[:2], ["鉄の斧", "鉄の剣"])  # 武器が先、値段の高い順

  def test_story_page(self):
    entries, _, today = party(2, 2)
    html = website.build_story(WORLD, entries, today)
    self.assertIn("<title>ストーリー | クリエピス戦記</title>", html)
    self.assertIn('<a href="index.html" class="on">ストーリー</a><a href="map.html">地図</a>', html)  # 地図の前
    self.assertIn('<h2 class="story-title">クリエピス戦記</h2>', html)  # world/story.md の物語
    self.assertIn("始祖竜", html)
    self.assertIn("<th>時代</th>", html)  # 歴史の表
    self.assertIn('<h2 class="area">鉱石</h2>', html)  # 物語の背景の鉱石
    self.assertIn("<strong>熾焔鋼（しえんこう）</strong>【炎属性】", html)
    self.assertIn("旅をしている勇者</th><td>2 人", html)  # いまの冒険の状況
    self.assertIn('南大陸 <span class="en">Austral Land</span>', html)
    self.assertIn('href="towns.html#salina"', html)
    self.assertLess(html.index("いまの冒険"), html.index('<h2 class="area">世界オルドラ'))  # 最初の見出しの前

  def test_heroes_page(self):
    entries, _, today = party(2, 2)
    html = website.build_heroes(WORLD, entries, today)
    self.assertIn('<a href="ranking.html">ランキング</a><a href="heroes.html" class="on">勇者</a>', html)
    for h, _ in entries:  # 勇者の一覧（詳細のページへのリンク）
      self.assertIn(f'<a href="heroes/{h["id"]}.html">{h["name"]}</a>', html)
    self.assertNotIn("<h2>勇者たち</h2>", website.build_ranking(WORLD, entries, today))  # ランキングからは移した
    self.assertIn("まだ勇者がいません", website.build_heroes(WORLD, [], today))

  def test_monsters_page(self):
    html = website.build_monsters(WORLD)
    self.assertIn('<a href="monsters.html" class="on">魔物</a>', html)
    self.assertIn('data-tab="austral_land" aria-selected="true" tabindex="0">南大陸</button>', html)  # 地方のタブ
    south = html[html.index('id="panel-austral_land"') :]
    south = south[: south.index("</section>")]
    shown = {m for (a, b), i in WORLD["road_info"].items() for m, _ in i["monsters"]}
    for mid in shown:  # 開放した道に出る魔物
      self.assertIn(f"<td>{WORLD['monsters'][mid]['name']}</td>", south)
    self.assertNotIn("<td>大ダコ</td>", html)  # 開放していない航路の魔物は出さない
    self.assertNotIn("サリナ〜エクエス（", south[south.index("<table") :])  # 魔物の表には出る道と割合を出さない
    self.assertIn(
      '<tr data-f="plain"><td>野犬</td><td class="num">1</td>'
      '<td><span class="tdot" style="background:var(--t-plain)"></span>草原</td>',
      south,
    )  # 名前の右に Lv と地形（街道の円グラフと同じ色の丸）
    head = html[: html.index('class="tabs"')]  # 地形の絞り込み（タブの上。開放した道と強敵・ボスにある地形だけ）
    for t in ("plain", "wetland", "mountain", "forest", "snow"):
      self.assertIn(f'class="chip" data-f="{t}"', head)
    self.assertNotIn('data-f="sea"', head)
    self.assertIn('data-f="world"', head)  # 強敵は WORLD、ボスは RAID で絞り込める
    self.assertIn('data-f="raid"', head)
    self.assertIn(
      '<tr data-f="world"><td>雪男</td><td class="num">N/A</td><td><span class="habitat">WORLD</span></td>', south
    )
    self.assertIn(
      '<tr data-f="raid"><td>巣穴の主</td><td class="num">N/A</td><td><span class="habitat">RAID</span></td>', south
    )
    self.assertIn('<span class="f-label">生息地</span>', head)
    self.assertIn('id="f-none"', html)
    self.assertEqual(south.count('<div class="card board">'), 1)  # 強敵・ボスも道の魔物と同じ表（別の枠は作らない）
    self.assertNotIn('<h3 class="sub">', south)
    rows = south[south.index("<table") :]
    self.assertLess(rows.index("<td>ヒドラ</td>"), rows.index("<td>雪男</td>"))  # 道の魔物 → 強敵 → ボス
    self.assertLess(rows.index("<td>雪男</td>"), rows.index("<td>巣穴の主</td>"))
    roads = south[: south.index("<table")]  # 魔物の表の上に、この地方の街道と地形の割合
    self.assertIn("<h3>街道</h3>", roads)
    south_towns = {t for t, p in WORLD["places"].items() if p.get("area") == "austral_land"}
    roads_here = {
      frozenset(k) for k in WORLD["road_info"] if set(k) & south_towns
    }  # 南大陸の町につながる道（向きは 1 つ）
    self.assertEqual(roads.count('<div class="road">'), len(roads_here))
    self.assertIn("サリナ〜エクエス（2）", roads)  # 道の名前（行動回数）
    self.assertIn('<span class="tdot" style="background:var(--t-plain)"></span>草原 83%</li>', roads)
    self.assertIn('<span class="tdot" style="background:var(--t-wetland)"></span>湿地 17%</li>', roads)
    self.assertLess(south.index("<td>野犬</td>"), south.index("<td>暴れ猪</td>"))  # Lv の順
    self.assertIn('<td class="icon"><span class="tag">群れ</span></td>', south)  # 性質は名前だけの枠の札
    self.assertNotIn("数が多く", south)  # 性質の説明は出さない
    self.assertIn('<th class="icon">属性</th><th class="num">HP</th>', html)
    self.assertIn('<th class="num">速</th><th class="num">器</th><th class="num">EXP</th>', html)  # 器用さ
    self.assertIn('<th>名前</th><th class="num">Lv</th><th>生息地</th><th class="icon">性質</th>', html)
    self.assertIn('<td class="icon"><span class="tag">氷属性</span></td>', south)  # 氷狼（アイテムと同じ枠の札）
    for name in ("雪男", "岩ゴーレム", "大鷲"):  # 強敵（カルン・ムラリスの討伐の依頼）も 1 度ずつ
      self.assertEqual(south.count(f"<td>{name}</td>"), 1)
    self.assertNotIn("属性の相性", html)  # 説明は手引きのページに
    self.assertIn('<a href="guide.html">手引き</a>', html)
    self.assertNotIn("性質の効果がかかる前", website.build_towns(WORLD))  # 魔物の注記は町のページに出さない
    self.assertIn("性質の効果がかかる前", html)
    crown = html[html.index('id="panel-crownlands"') :]
    self.assertIn("まだ開放されていません", crown[: crown.index("</section>")])
    self.assertEqual(html.count("<td>巣穴の主</td>"), 1)  # ボスのいない地方・開放していない地方には出さない

  def test_guide_shows_skills_and_storage(self):
    html = website.build_guide(WORLD)
    self.assertIn("<h3>装備のスキル</h3>", html)
    self.assertIn("<h3>持てる数と保管庫</h3>", html)
    self.assertIn("鎧通し", html)
    self.assertIn("会心の倍率を 4 倍にする", html)  # しくみの value は値に置き換える
    self.assertIn("最大 HP の 5% 回復", html)  # HP の割合は %
    self.assertIn("スキルを付ける余裕", html)  # 性格の表にスキルの行
    self.assertIn("★★ 500G", html)
    items = website.build_items(WORLD)
    self.assertIn("鍛冶屋でスキルを付けられる", items)

  def test_hero_text_shows_skills_and_storage(self):
    state = game.new_state(WORLD, "h1")
    state["inventory"] = [{"id": "steel_sword", "skill": "pierce"}]
    state["storage"] = [{"id": "iron_sword", "skill": None}]
    text = view.build_text(WORLD, state)
    self.assertIn("鋼鉄の剣〔鎧通し〕", text)
    self.assertIn("── 保管庫", text)

  def test_guide_page(self):
    html = website.build_guide(WORLD)
    self.assertIn('<a href="guide.html" class="on">手引き</a>', html)
    self.assertIn('<a href="monsters.html">魔物</a><a href="guide.html" class="on">手引き</a>', html)  # 魔物の次
    for t in WORLD["season"]["hero_traits"].values():  # 勇者の特性
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    for t in WORLD["traits"].values():  # 魔物の性質（説明はここだけに出す）
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    self.assertIn("相性（左が右に強い）: 炎→雷→氷→炎", html)
    self.assertIn(
      '<td><span class="tag">雷属性</span></td><td>氷</td><td>炎</td><td>紫電鉄</td>', html
    )  # 強い相手・苦手な相手・鉱石
    self.assertIn("器用さ ÷ 150 の確率で、ダメージが 3 倍", html)
    self.assertIn("最大 HP の 50% ぶん回復", html)
    self.assertIn("<h3>依頼の進め方</h3>", html)
    self.assertIn("引き返さない。HP が減っても、依頼が終わるか力尽きるまで進む", html)
    self.assertIn("Lv20 で 182。特性で変わる", html)
    self.assertIn("<h3>魔物の経験値とお金</h3>", html)
    item = html.split("<h3>アイテムの強さ</h3>")[1].split("</div></div>")[0]  # 点数の重みと、珍しさごとの点数
    self.assertIn("<tr><td>HP</td><td>0.2 点</td><td>5</td></tr>", item)
    self.assertIn("<tr><td>防</td><td>0.5 点</td><td>2</td></tr>", item)
    self.assertIn("<tr><td>速</td><td>0.5 点</td><td>2</td></tr>", item)
    self.assertIn("<tr><td>属性</td><td>1 点</td><td>属性 1 つ</td></tr>", item)
    self.assertIn("例: 鉄の斧（攻 +2・速 -2）は 2 − 1 = 1 点", item)  # マイナスも同じ点数で引く
    self.assertIn("<tr><td>★</td><td>1 点まで</td><td>なし</td></tr>", item)
    self.assertIn("<tr><td>★★★</td><td>2 点まで</td><td>付くことがある（武器・装飾品）</td></tr>", item)
    self.assertIn("<tr><td>★★★★</td><td>3 点まで</td><td>付くことがある（武器・装飾品）</td></tr>", item)
    self.assertIn("特性の倍率がかかる前の値。強運の勇者と同じ", html)  # 「ふつうの勇者」という特性はない
    levels = html.split('<table class="levels">')[1].split("</table>")[0]  # Lv1〜20 の能力の表
    self.assertEqual(levels.count('<td class="num">Lv'), 20)
    self.assertIn('<tr><td class="num">Lv1</td><td class="num">30</td><td class="num">6</td>', levels)
    self.assertIn(
      '<tr><td class="num">Lv20</td><td class="num">182</td><td class="num">30</td><td class="num">30</td>'
      '<td class="num">30</td><td class="num">30</td></tr>',
      levels,
    )
    self.assertIn(
      '<tr><td class="num">Lv10</td><td class="num">102</td><td class="num">22</td><td class="num">13</td>'
      '<td class="num">17</td><td class="num">17</td></tr>',
      levels,
    )
    self.assertIn("HP + 攻 × 4 + 防 × 4 + 速 × 2 + 器 × 2", html)
    self.assertIn("先手は速 +10・毒は攻 +2として数える", html)
    self.assertIn("強さ点 × 強さ点 ÷ 700", html)
    rate = WORLD["traits"]["thief"]["gold_rate"]
    self.assertIn(f"経験値 × 0.75（盗みの魔物はさらに × {rate:g}）。最後に四捨五入", html)
    self.assertIn("攻は Lv1 で 6、Lv20 で 30・防は Lv1 で 6", html)  # 能力ごとの値

  def test_guide_page_follows_the_data(self):
    world = copy.deepcopy(WORLD)
    world["season"]["growth"]["def"][-1] = 40  # 防御だけ Lv20 の値を変える
    world["elements"]["ice"]["strong"] = "thunder"  # 雷に強い属性が 2 つ、炎に強い属性はなし
    html = website.build_guide(world)
    self.assertIn("防は Lv1 で 6、Lv20 で 40", html)
    self.assertIn("攻は Lv1 で 6、Lv20 で 30", html)
    self.assertIn('<td><span class="tag">雷属性</span></td><td>氷</td><td>炎・氷</td>', html)
    self.assertIn('<td><span class="tag">炎属性</span></td><td>雷</td><td>-</td>', html)

  def test_boss_without_an_area_is_in_the_other_group(self):
    world = copy.deepcopy(WORLD)
    world["places"]["wall_den"].pop("area")
    for t in world["towns"]:  # 開放した町も地方なし（その他のまとまり）
      world["places"][t].pop("area")
    html = website.build_monsters(world)
    other = html[html.index(f'id="panel-{website.OTHER_AREA}"') :]
    self.assertIn("<td>巣穴の主</td>", other[: other.index("</section>")])

  def test_guide_page_tells_the_myth(self):
    html = website.build_guide(WORLD)
    for h in ("はじまりの話", "星の巡りと加護", "三つの徳", "三つの教えと争い"):
      self.assertIn(f"<h3>{h}</h3>", html)
    self.assertIn(f"星の巡りは、{WORLD['season']['months']} か月ごとにやってくる", html)
    # 三つの徳の表は、属性と鉱石を world/ から引く
    self.assertIn('<td><span class="tag">炎属性</span></td><td>心臓</td><td>いのち</td><td>熾焔鋼</td>', html)
    self.assertIn('<td><span class="tag">氷属性</span></td><td>牙</td><td>かぎり</td><td>霧氷石</td>', html)
    self.assertIn('<td><span class="tag">雷属性</span></td><td>頭</td><td>こころざし</td><td>紫電鉄</td>', html)
    # 作者だけの秘密と設定の言葉は、サイトに出さない
    for word in ("創造主", "利用"):
      self.assertNotIn(word, html)

  def test_guide_myth_follows_the_elements(self):
    world = copy.deepcopy(WORLD)
    world["elements"]["flame"]["ore"] = "<b>火鋼</b>"
    world["season"]["months"] = 4
    html = website.build_guide(world)
    self.assertIn("<td>&lt;b&gt;火鋼&lt;/b&gt;</td>", html)
    self.assertIn("星の巡りは、4 か月ごとにやってくる", html)
    world["elements"].pop("ice")  # 氷属性のない世界でも、残りの行だけ出る
    html = website.build_guide(world)
    self.assertNotIn("霧氷石", html)
    self.assertIn("<td>いのち</td>", html)

  def test_visible_texts_do_not_reveal_the_secret(self):
    """作者だけの秘密（竜は前の人類がつくった AI で、三つの教えを埋め込んだ。docs/MYTH.md §7）は、サイトに出す文
    （物語・町の説明・依頼の文・アイテムの説明・手引き）のどこにも書かない。文字どおりの語だけを見る簡単な守り。"""
    raw = W.load_raw()
    texts = [
      (ROOT / "world" / "story.md").read_text(encoding="utf-8"),
      re.sub(r"<[^>]*>", "", website.build_guide(WORLD)),  # タグは見ない（読める文字だけ）
      *(p["text"] for p in raw["map"]["places"] if p.get("text")),
      *(i["text"] for i in raw["items"] if i.get("text")),
    ]

    def walk(value):
      if isinstance(value, str):
        texts.append(value)
      elif isinstance(value, list):
        for v in value:
          walk(v)
      elif isinstance(value, dict):
        for v in value.values():
          walk(v)

    for key in ("hunts", "deliveries", "town_quests"):
      walk(raw[key])
    self.assertGreater(len(texts), 60)
    for text in texts:
      for word in ("AI", "機械", "人工", "埋め込", "冷凍", "思惑", "創造主", "利用"):
        self.assertNotIn(word, text, word)

  def test_guide_page_without_deliveries(self):
    world = copy.deepcopy(WORLD)
    world["deliveries"] = None  # 配達の設定がない世界でも落ちない（危ない配達の行は出さない）
    html = website.build_guide(world)
    self.assertNotIn("危ない配達", html)
    self.assertIn("危ない配達", website.build_guide(WORLD))

  def test_guide_page_without_elements(self):
    world = copy.deepcopy(WORLD)
    world["elements"] = {}
    html = website.build_guide(world)  # 属性がなくても落ちない
    self.assertNotIn("属性と相性", html)

  def test_monster_page_chips_follow_the_rows(self):
    world = copy.deepcopy(WORLD)
    for qs in world["town_quests"].values():  # 強敵の依頼がなければ、WORLD のボタンは出さない
      qs[:] = [q for q in qs if not world["monsters"].get(q.get("monster"), {}).get("elite")]
    html = website.build_monsters(world)
    self.assertNotIn('data-f="world"', html)
    self.assertIn('data-f="raid"', html)
    self.assertEqual(website.share_text(0.003), "1%未満")  # ごく少ない地形も 0% にしない
    self.assertEqual(website.share_text(0.126), "13%")

  def test_pie_and_terrain_shares(self):
    shares = website.terrain_shares(WORLD["monsters"], WORLD["road_info"][("karun", "muralis")]["monsters"])
    self.assertAlmostEqual(sum(x for _, x in shares), 1.0)
    self.assertEqual([t for t, _ in shares], ["plain", "mountain", "forest", "snow"])  # 多い順
    svg = website.pie_svg(shares)
    self.assertEqual(svg.count("<path"), 4)
    self.assertIn("<title>草原 57%</title>", svg)
    one = website.pie_svg([("plain", 1.0)])  # 1 つだけなら円
    self.assertIn("<circle", one)
    self.assertNotIn("<path", one)

  def test_monsters_page_edge_cases(self):
    world = copy.deepcopy(WORLD)
    world["areas"] = {}  # 地方がなくても、その他のまとまりに魔物を出す
    self.assertNotIn("まだ開放されていません", website.build_monsters(world))
    world = copy.deepcopy(WORLD)
    for key in (("salina", "eques"), ("eques", "salina")):
      world["road_info"][key]["monsters"] = [("wild_dog", 1.0), ("wild_dog", 1.0)]  # 同じ魔物が 2 度出ても 1 行
    for key in [k for k in world["road_info"] if k not in (("salina", "eques"), ("eques", "salina"))]:
      del world["road_info"][key]
    html = website.build_monsters(world)
    self.assertEqual(html.count("<td>野犬</td>"), 1)

  def test_story_markdown(self):
    blocks = website.md_html(
      "# 題\n\n一行目\n二行目\n\n## 見出し\n\n- **太字** と <b>\n- 二つ目\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    self.assertEqual(
      blocks,
      [
        '<h2 class="story-title">題</h2>',
        '<p class="story">一行目<br>二行目</p>',
        '<h2 class="area">見出し</h2>',
        "<ul><li><strong>太字</strong> と &lt;b&gt;</li><li>二つ目</li></ul>",
        '<div class="scroll"><table class="story-table"><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table></div>',
      ],
    )

  def test_story_markdown_edge_cases(self):
    # どのかたまりにも当たらない # の行でも止まらない（### は小見出し、#1位 は段落の文字）
    self.assertEqual(website.md_html("### 小見出し\n#1位\n"), ["<h3>小見出し</h3>", '<p class="story">#1位</p>'])
    self.assertEqual(website.inline_md("**太字"), "**太字")  # 対のない ** はそのまま
    self.assertEqual(website.inline_md("**a** と **b"), "<strong>a</strong> と **b")
    table = website.md_html("| a | b |\n|---|---|\n| - | - |\n")[0]  # 中身が - だけの行は消さない
    self.assertIn("<tr><td>-</td><td>-</td></tr>", table)

  def test_story_file_with_bom(self):
    with tempfile.TemporaryDirectory() as d:
      path = Path(d) / "story.md"
      path.write_text("\ufeff# 題\n", encoding="utf-8")
      with mock.patch.object(website, "STORY_PATH", path):
        html = website.build_story(WORLD, [], DAY)
    self.assertIn('<h2 class="story-title">題</h2>', html)

  def test_guild_before_anyone_played(self):
    html = website.build_guild(WORLD, [], DAY, game.new_guild())
    self.assertIn('<span class="open">受付中</span>', html)
    self.assertNotIn("ACCEPTED</span>", html)
    for p in (WORLD["places"][t] for t in WORLD["towns"]):
      self.assertIn(p["name"], html)

  def test_names_are_escaped(self):
    hero = {"id": "h1", "name": "<b>&悪</b>"}
    state = game.new_state(WORLD, "h1", hero["name"])
    for html in (
      website.build_ranking(WORLD, [(hero, state)], DAY),
      website.build_hero(WORLD, hero, state),
      website.build_map(WORLD, [(hero, state)], DAY),
    ):
      self.assertNotIn("<b>&悪", html)
      self.assertIn("&lt;b&gt;&amp;悪", html)

  def test_hero_text_is_not_shortened(self):
    world = copy.deepcopy(WORLD)
    for item in world["items"].values():  # 名前を長くして、装備の行を長くする
      item["name"] *= 3
    hero = {"id": "h1", "name": "勇者"}
    state = game.new_state(world, "h1", "勇者")
    state["inventory"] = {"iron_sword": 1, "iron_helm": 1, "iron_shield": 1, "lifeblood_charm": 1}
    gear = "装備: " + view.gear_text(world, state)
    self.assertGreater(rpg.common.width(gear), rpg.common.LINE_MAX)  # Gist のカードの幅より長い行
    self.assertIn(gear, view.build_text(world, state))  # 毎日の実行のログでも詰めない
    self.assertIn(escape(gear), website.build_hero(world, hero, state))

  def test_site_url_only_in_actions(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "Owner/cryepis"}, clear=True):
      self.assertEqual(rpg.site_url(), "https://owner.github.io/cryepis/")
      self.assertIn("https://owner.github.io/cryepis/", rpg.build_board(WORLD, [], DAY))
    with mock.patch.dict("os.environ", {}, clear=True):
      self.assertIsNone(rpg.site_url())


class ImportsTest(unittest.TestCase):
  def test_website_does_not_depend_on_rpg(self):
    """サイトの書き出しは、勇者の登録やランキングの Gist の更新（rpg.py）を読み込まない（表示は view.py、状態は store.py）。"""
    tree = ast.parse(Path(website.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    self.assertNotIn("rpg", imported)


class MapTitleTest(unittest.TestCase):
  def test_places_have_titles(self):
    root = ET.fromstring(mapsvg.render(WORLD, []))
    ns = "{http://www.w3.org/2000/svg}"
    for g in root.iter(f"{ns}g"):
      if (g.get("id") or "").startswith("place-"):
        self.assertTrue(g.find(f"{ns}title").text)

  def test_no_links_without_links(self):
    town = WORLD["starts"][0]
    state = game.new_state(WORLD, "h1", "勇者")
    state["place"] = town
    self.assertNotIn("<a ", mapsvg.render(WORLD, [({"id": "h1", "name": "勇者"}, state)]))


if __name__ == "__main__":
  unittest.main()
