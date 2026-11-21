import copy
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
    self.assertIn("まだ勇者がいません", (self.out / "map.html").read_text(encoding="utf-8"))

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

  def test_map_is_inline_svg_with_hero_links(self):
    entries, guild, today = party(1, 1)
    html = website.build_map(WORLD, entries, today)
    svg = html[html.index("<svg") : html.index("</svg>") + 6]
    root = ET.fromstring(svg)  # 正しい SVG として埋め込まれている
    links = [a.get("href") for a in root.iter("{http://www.w3.org/2000/svg}a")]
    self.assertEqual(links, ["heroes/h1.html"])

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
    self.assertIn(f"あと {days - 1} 日（{last.month}/{last.day} まで）", html)  # 今日貼られた依頼

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
    self.assertNotIn("戦斧", html)  # 開放していない地域の特産は出さない
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
    knight = html.split("騎士の剣")[1].split("</tr>")[0]  # ★★騎士の剣
    self.assertIn('<span class="how">依頼の報酬</span><a class="pill" href="towns.html#muralis">🛡️ ムラリス</a>', knight)
    self.assertIn('<td class="icon"><span class="tag">雷属性</span></td>', knight)  # 属性の列（枠の札）
    head = '<th class="num">HP</th><th class="num">攻</th><th class="num">防</th><th class="num">速</th><th class="num">器</th>'
    self.assertIn(head + '<th class="num">値段</th><th>入手方法</th>', html)  # 能力ごとの列
    header = html.split("<th>名前</th>")[1].split("</tr>")[0]  # 表の見出しの行
    self.assertNotIn("性能", header)
    self.assertNotIn("手に入れ方", header)
    steel = html.split("鋼の剣")[1].split("</tr>")[0]  # 攻+7・速-1、鋼の剣を売る開放した町はムラリスだけ
    self.assertIn('<td class="num zero">0</td><td class="num">+7</td><td class="num zero">0</td>', steel)
    self.assertIn('<td class="num minus">-1</td>', steel)  # マイナスは赤字
    self.assertIn('<a class="pill" href="towns.html#muralis">🛡️ ムラリス</a><div class="note">', steel)  # 買える店
    self.assertIn(
      '<th>名前</th><th></th><th class="icon">属性</th><th class="num">HP</th>', html
    )  # 特産の列は見出しなし
    lance = html.split("騎兵の槍</td>")[1].split("</tr>")[0]  # エクエスの特産・風属性
    self.assertTrue(
      lance.startswith(
        '<td class="icon"><span class="tag" title="エクエスの特産">特産</span></td>'
        '<td class="icon"><span class="tag">風属性</span></td>'
      ),
      lance,
    )
    self.assertIn('鋼の剣</td><td class="icon"></td><td class="icon"></td>', html)  # 開放していない町でも売っている
    self.assertNotIn("🏷️", html)  # 特産・属性は絵文字にしない
    self.assertNotIn("🌪️", html)
    self.assertIn('<td class="mark">★★</td><td>騎士の剣</td>', html)  # ★ は先頭の列
    for slot in ("weapon", "helmet", "shield", "accessory"):  # ★ の多い順
      marks = [len(game.rarity_mark(WORLD, r["item"]["rarity"])) for r in website.item_rows(WORLD)[slot]]
      self.assertEqual(marks, sorted(marks, reverse=True))

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
    self.assertIn("<tr><th>特産品</th><td>-</td></tr>", aurea)
    self.assertNotIn("王国の盾", aurea)
    muralis = html[html.index('id="muralis"') : html.index('id="karun"')]  # 南大陸の中は places の順
    self.assertIn("城壁の大盾", muralis.split("依頼の報酬で出るレア")[0])  # 特産品
    self.assertIn("騎士の剣", muralis.split("店の品ぞろえ")[0])  # レア
    self.assertIn("南壁の巣穴（ダンジョン・まだ入れない）", muralis)
    self.assertIn("カルン（陸路 6）", muralis)
    shop = muralis.split("店の品ぞろえ</th><td>")[1].split("</td>")[0]  # 種類の順、同じ種類なら値段の高い順
    *equips, potion = shop.split("<br>")  # 装備のあとに回復薬
    self.assertTrue(potion.startswith("回復薬<span"))
    names = [n.split("</span>")[1].split("<span")[0] for n in equips]
    by_name = {it["name"]: it for it in WORLD["items"].values()}
    keys = [(W.SLOTS.index(by_name[n]["slot"]), -by_name[n]["price"]) for n in names]
    self.assertEqual(keys, sorted(keys))
    self.assertEqual(names[:2], ["鋼の剣", "鉄の剣"])

  def test_story_page(self):
    entries, _, today = party(2, 2)
    html = website.build_story(WORLD, entries, today)
    self.assertIn("<title>ストーリー | クリエピス戦記</title>", html)
    self.assertIn('<a href="index.html" class="on">ストーリー</a><a href="map.html">地図</a>', html)  # 地図の前
    self.assertIn('<h2 class="story-title">クリエピス戦記</h2>', html)  # world/story.md の物語
    self.assertIn("始祖竜", html)
    self.assertIn("<th>時代</th>", html)  # 歴史の表
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
    self.assertIn("サリナ〜エクエス（", south)  # 出る道と割合
    self.assertIn('<td class="icon"><span class="tag">群れ</span></td>', south)  # 性質は名前だけの枠の札
    self.assertNotIn("数が多く", south)  # 性質の説明は出さない
    self.assertIn('<th class="icon">属性</th><th class="num">HP</th>', html)  # Lv の列は出さない
    self.assertIn('<th class="num">速</th><th class="num">器</th><th class="num">EXP</th>', html)  # 器用さ
    self.assertNotIn(">Lv<", html)
    self.assertIn('<th>名前</th><th class="icon">性質</th>', html)  # 地形の列は出さない
    self.assertNotIn("<th>地形</th>", html)
    self.assertIn('<td class="icon"><span class="tag">水属性</span></td>', south)  # 沼ヒル（アイテムと同じ枠の札）
    roads, boss = south.split('<h3 class="sub">ボス</h3>')  # ボスは道の魔物とは別の表
    self.assertNotIn("巣穴の主", roads)
    self.assertIn("<td>巣穴の主</td>", boss)
    self.assertIn('<span class="tag">土属性</span>', boss)
    self.assertIn("<td>南壁の巣穴（ダンジョン・まだ挑めない）</td>", boss)
    self.assertIn("<th>いる場所</th>", boss)
    self.assertNotIn("属性の相性", html)  # 説明は手引きのページに
    self.assertIn('<a href="guide.html">手引き</a>', html)
    self.assertNotIn("性質の効果がかかる前", website.build_towns(WORLD))  # 魔物の注記は町のページに出さない
    self.assertIn("性質の効果がかかる前", html)
    crown = html[html.index('id="panel-crownlands"') :]
    self.assertIn("まだ開放されていません", crown[: crown.index("</section>")])
    self.assertEqual(html.count('<h3 class="sub">ボス</h3>'), 1)  # ボスのいない地方・開放していない地方には出さない

  def test_guide_page(self):
    html = website.build_guide(WORLD)
    self.assertIn('<a href="guide.html" class="on">手引き</a>', html)
    self.assertIn('<a href="monsters.html">魔物</a><a href="guide.html" class="on">手引き</a>', html)  # 魔物の次
    for t in WORLD["season"]["hero_traits"].values():  # 勇者の特性
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    for t in WORLD["traits"].values():  # 魔物の性質（説明はここだけに出す）
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    self.assertIn("相性（左が右に強い）: 炎→氷→風→土→雷→水→炎", html)
    self.assertIn('<td><span class="tag">風属性</span></td><td>土</td><td>氷</td>', html)  # 強い相手・苦手な相手
    self.assertIn("器用さ ÷ 150 の確率で、ダメージが 3 倍", html)
    self.assertIn("最大 HP の 50% まで回復", html)
    self.assertIn("Lv20 で 182", html)

  def test_guide_page_without_elements(self):
    world = copy.deepcopy(WORLD)
    world["elements"] = {}
    html = website.build_guide(world)  # 属性がなくても落ちない
    self.assertNotIn("属性と相性", html)

  def test_monsters_page_edge_cases(self):
    world = copy.deepcopy(WORLD)
    world["areas"] = {}  # 地方がなくても、その他のまとまりに魔物を出す
    self.assertNotIn("まだ開放されていません", website.build_monsters(world))
    world = copy.deepcopy(WORLD)
    info = world["road_info"][("salina", "eques")]
    info["monsters"] = [("wild_dog", 1.0), ("wild_dog", 1.0), ("yeti", 0.01)]  # 同じ魔物が 2 度・ごく少ない魔物
    html = website.build_monsters(world)
    self.assertEqual(html.count("サリナ〜エクエス（"), 2)  # 野犬は 1 つにまとめる
    self.assertIn("サリナ〜エクエス（1%未満）", html)

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

  def test_hero_page_is_not_folded(self):
    world = copy.deepcopy(WORLD)
    for item in world["items"].values():  # 名前を長くして、装備の行を長くする
      item["name"] *= 3
    hero = {"id": "h1", "name": "勇者"}
    state = game.new_state(world, "h1", "勇者")
    state["inventory"] = {"iron_sword": 1, "iron_helm": 1, "iron_shield": 1, "lucky_charm": 1}
    gear = "装備: " + rpg.gear_text(world, state)
    self.assertNotIn(gear, rpg.build_text(world, state))  # Gist では幅に収まるように詰める
    self.assertIn(escape(gear), website.build_hero(world, hero, state))

  def test_site_url_only_in_actions(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "Owner/cryepis"}, clear=True):
      self.assertEqual(rpg.site_url(), "https://owner.github.io/cryepis/")
      self.assertIn("https://owner.github.io/cryepis/", rpg.build_board(WORLD, [], DAY))
    with mock.patch.dict("os.environ", {}, clear=True):
      self.assertIsNone(rpg.site_url())


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
