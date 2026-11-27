import ast
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
import training  # noqa: E402
import view  # noqa: E402
import website  # noqa: E402
import world as W  # noqa: E402

WORLD = rpg.load_world()
DAY = date.fromisoformat(WORLD["season"]["start"])


def party(n=3, days=5):
  """n 人のもののふを、口入れ屋を共有して days 日進める。(entries, guild, 最後の日)"""
  entries = [
    ({"id": f"h{i}", "name": f"もののふ{i}"}, game.new_state(WORLD, f"h{i}", f"もののふ{i}")) for i in range(1, n + 1)
  ]
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
        "training.html",
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
        if href.startswith("#"):  # 同じページの中の移動（手引きの項目へのボタン）は、その id があること
          self.assertIn(f'id="{href[1:]}"', path.read_text(encoding="utf-8"), f"{path.name} → {href}")
          continue
        target = (path.parent / href.split("#")[0]).resolve()
        self.assertTrue(target.is_file(), f"{path.name} → {href}")

  def test_training_page_shows_only_the_baseline_win_rates(self):
    report = training.build_report(WORLD, trials=12)
    html = website.build_training(WORLD, report)
    self.assertIn('<a href="training.html" class="on">調練</a>', html)
    self.assertIn("各条件 12 戦の勝率", html)
    self.assertIn("武具・スキルなし", html)
    names = [WORLD["monsters"][result["monster"]]["name"] for result in report["opponents"]]
    self.assertEqual(names, ["海坊主", "大天狗", "だいだらぼっち", "雪女", "酒呑童子"])
    for name, row in zip(names, report["opponents"], strict=True):
      self.assertIn(
        f'<th scope="row">{name}</th><td class="num">Lv{row["level"]}</td>'
        f'<td class="num">{row["party"]}人</td><td class="num">{row["baseline"] / 12:.2%}</td>',
        html,
      )
      self.assertNotIn("comparisons", row)
    self.assertNotIn("1人に適用", html)
    self.assertNotIn("全員に適用", html)

  def test_training_cache_is_reused_and_invalidated_by_key(self):
    path = self.out.parent / "training.json"
    expected = training.build_report(WORLD, trials=12)
    with mock.patch.object(training, "build_report", return_value=expected) as build:
      self.assertEqual(training.cached_report(WORLD, path), expected)
      self.assertEqual(training.cached_report(WORLD, path), expected)
      self.assertEqual(build.call_count, 1)
      changed = copy.deepcopy(WORLD)
      changed["skills"]["pierce"]["value"] = 1.2
      self.assertEqual(training.cached_report(changed, path), expected)
      self.assertEqual(build.call_count, 2)

  def test_site_rejects_a_training_cache_inside_the_output(self):
    self.out.mkdir()
    link = self.out / "linked-training.json"
    link.symlink_to(self.out.parent / "training.json")
    for cache in (self.out / "training.json", link):
      with (
        self.subTest(cache=cache),
        mock.patch.object(sys, "argv", ["website.py", str(self.out), "--training-cache", str(cache)]),
        mock.patch.object(training, "cached_report") as load,
        self.assertRaises(SystemExit),
      ):
        website.main()
      load.assert_not_called()

  def test_out_dir_is_rebuilt(self):
    website.write_site(WORLD, [], DAY, {}, self.out)
    (self.out / "old.html").write_text("x")
    website.write_site(WORLD, [], DAY, {}, self.out)
    self.assertFalse((self.out / "old.html").exists())
    self.assertIn("まだもののふがいません", (self.out / "heroes.html").read_text(encoding="utf-8"))

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
    entries[0][1]["last_date"] = None  # まだ進めていないもののふがいても、最後に進めた日
    self.assertEqual(website.shown_day(entries), last)

  def test_map_is_inline_svg_without_hero_marks(self):
    entries, _, today = party(1, 1)
    website.write_site(WORLD, entries, today, {}, self.out)
    html = (self.out / "index.html").read_text(encoding="utf-8")
    svg = html[html.index("<svg") : html.index("</svg>") + 6]
    root = ET.fromstring(svg)  # 正しい SVG として埋め込まれている
    self.assertEqual(list(root.iter("{http://www.w3.org/2000/svg}a")), [])  # 地図に名札（リンク）は出さない
    self.assertIsNone(root.find('.//{http://www.w3.org/2000/svg}g[@class="hero"]'))
    self.assertIn("街道と霊場", svg)
    self.assertNotIn("もののふがいる場所", svg)
    self.assertNotIn("まだ入れない", svg)
    self.assertNotIn("もののふの居場所", html)  # 地図のページにもののふの一覧は出さない（もののふのページで見る）
    self.assertNotIn('href="heroes/h1.html"', html)

  def test_ranking_shows_top_ten(self):
    entries = [
      ({"id": f"h{i}", "name": f"もののふ{i}"}, game.new_state(WORLD, f"h{i}", f"もののふ{i}")) for i in range(1, 15)
    ]
    for i, (_, state) in enumerate(entries):  # Lv が違う 14 人（1 人だけ 10 位と同じ Lv）
      state["season"] = 1
      state["hero"]["level"] = 20 - i if i != 10 else 11
    html = website.build_ranking(WORLD, entries, DAY)
    level = html[html.index("<h3>レベル</h3>") :]
    level = level[: level.index("</table>")]
    self.assertEqual(level.count("<tr>"), 10)  # 10 人で切る（同じ 10 位が 2 人でも、登録の早い 1 人だけ）
    self.assertIn("もののふ10<", level)
    self.assertNotIn("もののふ11<", level)
    self.assertNotIn("もののふ12<", level)
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
    self.assertEqual(html.count('<span class="stamp">引受済</span>'), len(taken))
    self.assertNotIn("heroes/", html.split("<main")[1])  # 受けたもののふの名前は出さない
    self.assertNotIn("滞在中", html)
    total = sum(len(es) for es in guild["towns"].values())
    self.assertEqual(html.count("<tr data-f="), total)
    self.assertNotIn("町ごとに決まった数の依頼", html)  # 説明文は出さない
    self.assertNotIn('data-town="', html)  # 町の絞り込みはない
    self.assertEqual(html.count('class="chip" data-f='), len(WORLD["quest_ranks"]))  # レアリティで絞り込める
    self.assertIn('data-tab="bando" aria-selected="true" tabindex="0">坂東</button>', html)  # 地方のタブ
    self.assertIn('id="panel-bando"', html)
    self.assertNotIn('id="panel-kinai"', html)  # 開放した町のない地方のタブは出さない
    for town in WORLD["towns"]:  # 町の名前に英語名を添える
      p = WORLD["places"][town]
      self.assertIn(f'{p["name"]} <span class="en">{p["en"]}</span>', html)
      # 町ごとの高札は、対応する地方のタブにある
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
    self.assertNotIn("受付終了", html)  # その日の朝の高札に貼り替えて見せる
    self.assertIn(f"あと {WORLD['season']['quest_open_days'] - 1} 日", html)
    self.assertEqual(guild["refreshed"][WORLD["towns"][0]], today.isoformat())  # 元の高札は変えない
    e = {"posted": (today - timedelta(days=10)).isoformat(), "status": "open"}
    self.assertIn("受付終了", website.guild_status(WORLD, e, today))

  def test_items_page_skips_empty_kinds(self):
    world = copy.deepcopy(WORLD)
    for town in world["towns"]:  # 開放した地域で頭の品を売らず、依頼でも出ないようにする
      p = world["places"][town]
      p["shop"] = [i for i in p["shop"] if world["items"][i]["slot"] != "helmet"]
    world["region_items"] = [i for i in world["region_items"] if world["items"][i]["slot"] != "helmet"]
    for it in world["items"].values():
      if it["slot"] == "helmet":
        it["towns"] = []
        it.pop("forge_town", None)
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
    self.assertNotIn('data-tab="armor"', html)  # armor という古いタブ ID は使わない（胴の部位の ID は shield）
    self.assertNotIn("data-slot=", html)  # 装備の種類の絞り込みはない
    self.assertNotIn("手に入る装備です", html)  # 説明文は出さない
    self.assertNotIn("丸薬", html)  # 丸薬は町の店の品ぞろえに出す
    for slot in ("weapon", "helmet", "shield", "accessory"):  # 装備の種類ごとのタブ
      self.assertIn(f'role="tab" id="tab-{slot}"', html)
      self.assertIn(f'id="panel-{slot}"', html)
    self.assertIn('data-tab="weapon" aria-selected="true" tabindex="0">⚔️ 武器</button>', html)  # 部位の先頭に絵文字
    self.assertIn('<h2 class="area">📿 御守</h2>', html)
    self.assertEqual(html.count('class="chip" data-f='), 4)  # ★〜★★★★
    charm = html.split("不動の御守")[1].split("</tr>")[0]  # ★★不動の御守（炎属性）
    self.assertIn('<td><a class="pill" href="towns.html#kofu">🏯 甲府</a></td>', charm)  # 依頼を出す町
    self.assertIn('<td class="icon"><span class="tag">炎属性</span></td>', charm)  # 属性の列（枠の札）
    head = '<th class="num">HP</th><th class="num">攻</th><th class="num">防</th><th class="num">速</th><th class="num">器</th>'
    self.assertIn(head + '<th class="num">値段</th><th>入手方法</th>', html)  # 能力ごとの列
    header = html.split("<th>名前</th>")[1].split("</tr>")[0]  # 表の見出しの行
    self.assertNotIn("性能", header)
    self.assertNotIn("手に入れ方", header)
    axe = html.split("<td>鉞")[1].split("</tr>")[0]  # 攻+2・速-2、開放した 4 町の店で売っている
    self.assertIn('<td class="num zero">0</td><td class="num">+2</td><td class="num zero">0</td>', axe)
    self.assertIn('<td class="num minus">-2</td>', axe)  # マイナスは赤字
    self.assertEqual(axe.count('class="pill"'), 4)  # 買える店
    self.assertNotIn("依頼の報酬", html)  # 入手方法の補足の文は出さない
    self.assertNotIn("宝箱", html)
    self.assertIn(
      '<th>名前</th><th>固定スキル</th><th class="icon">属性</th><th class="num">HP</th>', html
    )  # 特産の列はない
    self.assertNotIn("特産", html)
    sword = html.split("<td>打刀")[1].split("</tr>")[0]
    self.assertIn(
      '</span></td><td>—</td><td class="icon"></td><td class="num zero">0</td>', sword
    )  # ★ は属性なし（名前の下に説明がある）
    self.assertNotIn("🏷️", html)  # 属性は絵文字にしない
    self.assertNotIn("🌪️", html)
    self.assertIn('<td class="mark">★★</td><td>業物の太刀', html)  # ★ は先頭の列
    for slot in ("weapon", "helmet", "shield", "accessory"):  # ★ の多い順
      marks = [len(game.rarity_mark(WORLD, r["item"]["rarity"])) for r in website.item_rows(WORLD)[slot]]
      self.assertEqual(marks, sorted(marks, reverse=True))

  def test_items_page_shows_descriptions_and_fixed_skills_in_separate_cells(self):
    html = website.build_items(WORLD)
    for it in WORLD["items"].values():  # すべてに説明があり、名前の下に出る
      row = html.split(f"<td>{escape(it['name'])}", 1)[1].split("</tr>", 1)[0]
      description, after = row.split("</td>", 1)
      self.assertIn(f'<br><span class="note">{escape(it["text"])}</span>', description)
      fixed = WORLD["skills"].get(it.get("fixed_skill"))
      skill = f'<span class="tag">{escape(fixed["name"])}</span>' if fixed else "—"
      self.assertTrue(after.startswith(f"<td>{skill}</td>"), row)

  def test_item_description_is_optional_and_escaped(self):
    world = copy.deepcopy(WORLD)
    world["items"]["iron_sword"]["text"] = "<b>剣</b> & 鎧"
    world["items"]["iron_axe"].pop("text")
    html = website.build_items(world)
    self.assertIn('<span class="note">&lt;b&gt;剣&lt;/b&gt; &amp; 鎧</span>', html)  # エスケープする
    self.assertNotIn("<b>剣</b>", html)
    axe = html.split("<td>鉞")[1].split("</tr>")[0]
    self.assertTrue(axe.startswith("</td>"), axe[:30])  # 説明がなければ、名前だけ
    self.assertNotIn('<span class="note">', axe.split("</td>")[0])

  def test_town_pill_and_stat_cell(self):
    world = copy.deepcopy(WORLD)
    world["places"]["kofu"].pop("emoji")
    self.assertEqual(website.town_pill(world, "kofu"), '<a class="pill" href="towns.html#kofu">甲府</a>')
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
      slots = p["quests_per_day"]
      card = html.split(f'id="{town}"', 1)[1].split("</table>", 1)[0]
      marks = " ".join(["📜"] * slots)
      self.assertIn(f'<span role="img" aria-label="依頼の枠 {slots} 件">{marks}</span>', card)
      self.assertNotIn(f'href="guild.html#{town}"', html)
    self.assertLess(html.index("坂東 <span"), html.index("畿内 <span"))  # 開放した地方が先
    self.assertNotIn("オルドラの町の一覧です", html)  # 説明文は出さない
    self.assertLess(html.index("<h2>町の情報</h2>"), html.index('class="tabs"'))  # タブは見出しのすぐ下
    self.assertIn('data-tab="bando" aria-selected="true" tabindex="0">坂東</button>', html)  # 最初は坂東
    for aid in WORLD["areas"]:  # 地方ごとのタブと、タブにつながる中身
      self.assertIn(f'id="tab-{aid}" aria-controls="panel-{aid}"', html)
      self.assertIn(f'role="tabpanel" id="panel-{aid}" aria-labelledby="tab-{aid}" data-tab="{aid}">', html)
    self.assertIn('<div class="tabs" role="tablist" hidden>', html)  # JavaScript がなければ全部の地方を見せる
    self.assertNotIn('data-tab="kinai" hidden', html)
    for area in WORLD["areas"].values():
      self.assertIn(f'{area["name"]} <span class="en">{area["en"]}</span>', html)
    self.assertIn('江戸 <span class="en">Edo</span>', html)
    kyoto = html[html.index('id="kyoto"') : html.index('id="azuchi"')]  # 開放していない町
    self.assertIn("未開放", kyoto)
    self.assertIn("依頼の枠 6 件", kyoto)
    self.assertNotIn("特産品", html)  # 町ごとの特産はない
    self.assertIn("★★★★武具を生成", html)
    self.assertIn("玄岳鋼 10 個・10,000文", html)
    self.assertNotIn("打刀", kyoto)

    kofu = html[html.index('id="kofu"') : html.index('id="nagano"')]  # 坂東の中は places の順
    self.assertIn("不動の御守", kofu.split("店の品ぞろえ")[0])  # レア
    self.assertIn("富士山（霊場）", kofu)
    self.assertIn("長野（陸路 3）", kofu)
    shop = kofu.split("店の品ぞろえ</th><td>")[1].split("</td>")[0]  # 種類の順、同じ種類なら値段の高い順
    *equips, potion = shop.split("<br>")  # 装備のあとに丸薬
    self.assertTrue(potion.startswith("丸薬<span"))
    names = [n.split("</span>")[1].split("<span")[0] for n in equips]
    by_name = {it["name"]: it for it in WORLD["items"].values()}
    keys = [(W.SLOTS.index(by_name[n]["slot"]), -by_name[n]["price"]) for n in names]
    self.assertEqual(keys, sorted(keys))
    self.assertEqual(names[:2], ["鉞", "打刀"])  # 武器が先、値段の高い順

  def test_legendary_forged_items_are_listed_with_fixed_skills(self):
    html = website.build_items(WORLD)
    for iid in ("gengaku_odachi", "gengaku_helmet", "gengaku_armor", "gengaku_charm"):
      item = WORLD["items"][iid]
      self.assertIn(item["name"], html)
      self.assertIn(WORLD["skills"][item["fixed_skill"]]["name"], html)
    self.assertIn('href="towns.html#odawara"', html)

  def test_guide_lists_legendary_forge_probability(self):
    html = website.build_guide(WORLD)
    for iid in ("gengaku_odachi", "gengaku_helmet", "gengaku_armor", "gengaku_charm"):
      self.assertIn(f"<td>{WORLD['items'][iid]['name']}</td><td>25%</td>", html)
    for area in WORLD["areas"].values():
      self.assertIn(f"<td>{area['name']}</td><td>{area['stone']}</td><td>{area['stone_effect']}</td>", html)
    self.assertIn("性質そのものに数値効果はなく", html)

  def test_first_page_is_the_map(self):
    """ホームはなくして、最初のページ（index.html）は絵図（#261）。"""
    html = website.build_map(WORLD, DAY)
    self.assertIn("<title>絵図 | もののふ道中</title>", html)
    self.assertIn(
      '<h1><a href="index.html">もののふ道中 <span class="site-title-en" lang="en">Warriors on the Road</span></a></h1>',
      html,
    )
    self.assertIn('<a href="index.html" class="on">絵図</a><a href="ranking.html">番付</a>', html)  # メニューの最初
    self.assertIn("<h2>諸国絵図</h2>", html)
    self.assertNotIn("ホーム", html)
    self.assertNotIn("map.html", html)  # メニューに地図のページはない

  def test_old_map_page_moves_to_the_first_page(self):
    """前の地図のページ（map.html）は、絵図（index.html）へ移すだけのページにする（ブックマークを 404 にしない）。"""
    self.assertIn('<meta http-equiv="refresh" content="0;url=index.html">', website.MOVED_MAP)
    self.assertIn('<a href="index.html">絵図</a>', website.MOVED_MAP)

  def test_heroes_page(self):
    entries, _, today = party(2, 2)
    html = website.build_heroes(WORLD, entries, today)
    self.assertIn('<a href="ranking.html">番付</a><a href="heroes.html" class="on">もののふ</a>', html)
    for h, _ in entries:  # もののふの一覧（詳細のページへのリンク）
      self.assertIn(f'<a href="heroes/{h["id"]}.html">{h["name"]}</a>', html)
    self.assertNotIn("<h2>もののふたち</h2>", website.build_ranking(WORLD, entries, today))  # 番付からは移した
    self.assertIn("まだもののふがいません", website.build_heroes(WORLD, [], today))

  def test_monsters_page(self):
    html = website.build_monsters(WORLD)
    self.assertIn('<a href="monsters.html" class="on">物の怪</a>', html)
    self.assertIn('data-tab="bando" aria-selected="true" tabindex="0">坂東</button>', html)  # 地方のタブ
    south = html[html.index('id="panel-bando"') :]
    south = south[: south.index("</section>")]
    shown = {m for (a, b), i in WORLD["road_info"].items() for m, _ in i["monsters"]}
    for mid in shown:  # 開放した道に出る物の怪
      self.assertIn(f"<td>{WORLD['monsters'][mid]['name']}</td>", south)
    self.assertNotIn("<td>蛸入道</td>", south)  # ほかの地方の物の怪は、坂東のタブに出さない
    tokai = html[html.index('id="panel-tokai"') :]
    tokai = tokai[: tokai.index("</section>")]
    self.assertIn("まだ開放されていません", tokai)  # 開放していない地方も、その地方の物の怪の表を出す（#267）
    self.assertIn("<td>蛸入道</td>", tokai)
    self.assertEqual(tokai.count("<tr data-f="), 30)  # 道の物の怪 30 体（大物・主はまだいない）
    self.assertNotIn("<h3>街道</h3>", tokai)  # 通れる道がないので、街道の円グラフは出さない
    self.assertNotIn("江戸〜小田原（", south[south.index("<table") :])  # 物の怪の表には出る道と割合を出さない
    self.assertIn(
      '<tr data-f="plain"><td>送り犬</td><td class="num">1</td>'
      '<td><span class="tdot" style="background:var(--t-plain)"></span>草原</td>',
      south,
    )  # 名前の右に Lv と地形（街道の円グラフと同じ色の丸）
    head = html[: html.index('class="tabs"')]  # 地形の絞り込み（タブの上。表に出した物の怪の地形だけ）
    for t in ("plain", "mountain", "forest", "snow", "sea"):  # 海はまだ開放していない地方の物の怪
      self.assertIn(f'class="chip" data-f="{t}"', head)
    self.assertNotIn('data-f="wetland"', head)  # どの地方の道にもない地形の物の怪はいない（#267）
    self.assertIn('data-f="world" aria-pressed="false"><span class="habitat">大物</span>', head)
    self.assertIn('data-f="raid" aria-pressed="false"><span class="habitat">主</span>', head)
    self.assertIn(
      '<tr data-f="world"><td>雪女</td><td class="num">—</td><td><span class="habitat">大物</span></td>', south
    )
    self.assertIn(
      '<tr data-f="raid"><td>酒呑童子</td><td class="num">—</td><td><span class="habitat">主</span></td>', south
    )
    self.assertIn('<span class="f-label">生息地</span>', head)
    self.assertIn('id="f-none"', html)
    self.assertEqual(south.count('<div class="card board">'), 1)  # 大物・主も道の物の怪と同じ表（別の枠は作らない）
    self.assertNotIn('<h3 class="sub">', south)
    rows = south[south.index("<table") :]
    self.assertLess(rows.index("<td>送り犬</td>"), rows.index("<td>雪女</td>"))  # 道の物の怪 → 大物 → 主
    self.assertLess(rows.index("<td>雪女</td>"), rows.index("<td>酒呑童子</td>"))
    roads = south[: south.index("<table")]  # 物の怪の表の上に、この地方の街道と地形の割合
    self.assertIn("<h3>街道</h3>", roads)
    south_towns = {t for t, p in WORLD["places"].items() if p.get("area") == "bando"}
    roads_here = {
      frozenset(k) for k in WORLD["road_info"] if set(k) & south_towns
    }  # 坂東の町につながる道（向きは 1 つ）
    self.assertEqual(roads.count('<div class="road">'), len(roads_here))
    self.assertIn("江戸〜小田原（2）", roads)  # 道の名前（行動回数）
    self.assertIn('<span class="tdot" style="background:var(--t-plain)"></span>草原 88%</li>', roads)
    self.assertIn('<span class="tdot" style="background:var(--t-snow)"></span>雪原 21%</li>', roads)
    self.assertLess(south.index("<td>送り犬</td>"), south.index("<td>猪笹王</td>"))  # Lv の順
    self.assertIn('<td class="icon"><span class="tag">群れ</span></td>', south)  # 性質は名前だけの枠の札
    self.assertNotIn("数が多く", south)  # 性質の説明は出さない
    self.assertIn('<th class="icon">属性</th><th class="num">HP</th>', html)
    self.assertIn('<th class="num">速</th><th class="num">器</th><th class="num">EXP</th>', html)  # 器用さ
    self.assertIn('<th>名前</th><th class="num">Lv</th><th>生息地</th><th class="icon">性質</th>', html)
    self.assertIn('<td class="icon"><span class="tag">氷属性</span></td>', south)  # 雪狼（武具と同じ枠の札）
    for name in ("雪女", "だいだらぼっち", "大天狗"):  # 大物（長野・甲府の討伐の依頼）も 1 度ずつ
      self.assertEqual(south.count(f"<td>{name}</td>"), 1)
    self.assertNotIn("属性の相性", html)  # 説明は手引きのページに
    self.assertIn('<a href="guide.html">手引き</a>', html)
    self.assertNotIn("性質の効果がかかる前", website.build_towns(WORLD))  # 物の怪の注記は町のページに出さない
    self.assertIn("性質の効果がかかる前", html)
    crown = html[html.index('id="panel-kinai"') :]
    self.assertIn("まだ開放されていません", crown[: crown.index("</section>")])
    self.assertEqual(html.count("<td>酒呑童子</td>"), 1)  # 主のいない地方・開放していない地方には出さない

  def test_guide_shows_skills_and_storage(self):
    html = website.build_guide(WORLD)
    self.assertIn("<h3>装備のスキル</h3>", html)
    self.assertIn("<h3>持てる数と蔵</h3>", html)
    self.assertIn("鎧通し", html)
    self.assertIn("会心の倍率を 4 倍にする", html)  # しくみの value は値に置き換える
    self.assertIn("最大 HP の 5% 回復", html)  # HP の割合は %
    self.assertIn("スキルを付ける余裕", html)  # 性格の表にスキルの行
    self.assertIn("追加枠に性格の優先スキルがない装備", html)
    self.assertIn("★★ 500文", html)
    items = website.build_items(WORLD)
    self.assertIn("鍛冶屋で追加スキルを付けられる", items)

  def test_hero_text_shows_skills_and_storage(self):
    state = game.new_state(WORLD, "h1")
    state["inventory"] = [{"id": "steel_sword", "skill": "pierce"}]
    state["storage"] = [{"id": "iron_sword", "skill": None}]
    text = view.build_text(WORLD, state)
    self.assertIn("業物の太刀〔鎧通し〕", text)
    self.assertIn("── 蔵", text)

  def test_hero_displays_fixed_and_additional_skill(self):
    state = game.new_state(WORLD)
    state["inventory"] = [{"id": "cross_spear", "skill": "combo"}]
    self.assertIn("十文字槍〔固定 鎧通し〕〔追加 二の太刀〕", view.build_text(WORLD, state))

  def test_guide_page(self):
    html = website.build_guide(WORLD)
    self.assertIn('<a href="guide.html" class="on">手引き</a>', html)
    self.assertIn('<a href="monsters.html">物の怪</a><a href="guide.html" class="on">手引き</a>', html)  # 物の怪の次
    for t in WORLD["season"]["hero_traits"].values():  # もののふの特性
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    for t in WORLD["traits"].values():  # 物の怪の性質（説明はここだけに出す）
      self.assertIn(f'<td><span class="tag">{t["name"]}</span></td><td>{t["text"]}</td>', html)
    self.assertIn("相性（左が右に強い）: 炎→雷→氷→炎", html)
    self.assertIn(
      '<td><span class="tag">雷属性</span></td><td>紫電鉄</td>', html
    )  # 属性・鉱石（強い・苦手は図で見せる）
    self.assertIn('aria-label="属性の三すくみ"', html)
    self.assertIn('<a class="pill" href="#g0">能力</a>', html)  # 上の移動ボタン
    self.assertIn('<div class="card" id="g0">', html)
    self.assertIn(f'<td class="num">{game.exp_need(WORLD, 1)}</td>', html)  # 次の Lv までの経験値
    self.assertIn("<th>固定スキル</th><th>追加スキル</th>", html)
    self.assertIn('<span class="tag">鎧通し</span>', html)  # スキルも札で囲む
    self.assertIn("器用さ ÷ 150 の確率で、ダメージが 3 倍", html)
    self.assertIn("最大 HP の 50% ぶん回復", html)
    self.assertIn("<h3>依頼の進め方</h3>", html)
    self.assertIn("引き返さない。HP が減っても、依頼が終わるか力尽きるまで進む", html)
    self.assertIn("Lv20 で 182。特性で変わる", html)
    self.assertIn("<h3>物の怪の経験値とお金</h3>", html)
    item = html.split("<h3>武具の強さ</h3>")[1].split("</div></div>")[0]  # 点数の重みと、珍しさごとの点数
    self.assertIn("<tr><td>HP</td><td>0.2 点</td><td>5</td></tr>", item)
    self.assertIn("<tr><td>防</td><td>0.5 点</td><td>2</td></tr>", item)
    self.assertIn("<tr><td>速</td><td>0.5 点</td><td>2</td></tr>", item)
    self.assertIn("<tr><td>属性</td><td>1 点</td><td>属性 1 つ</td></tr>", item)
    self.assertIn("例: 鉞（攻 +2・速 -2）は 2 − 1 = 1 点", item)  # マイナスも同じ点数で引く
    self.assertIn("<tr><td>★</td><td>1 点まで</td><td>なし</td><td>0</td><td>0</td></tr>", item)
    for rarity, cap, fixed, price in (("★★", 2, 0, 500), ("★★★", 2, 1, 800), ("★★★★", 3, 1, 1000)):
      self.assertIn(
        f"<tr><td>{rarity}</td><td>{cap} 点まで</td><td>付くことがある（武器・御守）</td>"
        f"<td>{fixed}</td><td>1（{price}文）</td></tr>",
        item,
      )
    self.assertIn("特性の倍率がかかる前の値。強運のもののふと同じ", html)  # 「ふつうのもののふ」という特性はない
    levels = html.split('<table class="levels">')[1].split("</table>")[0]  # Lv1〜20 の能力の表
    self.assertEqual(levels.count('<td class="num">Lv'), 20)
    self.assertIn('<tr><td class="num">Lv1</td><td class="num">30</td><td class="num">6</td>', levels)
    self.assertIn(
      '<tr><td class="num">Lv20</td><td class="num">182</td><td class="num">30</td><td class="num">30</td>'
      '<td class="num">30</td><td class="num">30</td><td class="num">—</td></tr>',  # 最大 Lv は次の経験値なし
      levels,
    )
    self.assertIn(
      '<tr><td class="num">Lv10</td><td class="num">102</td><td class="num">22</td><td class="num">13</td>'
      '<td class="num">17</td><td class="num">17</td><td class="num">316</td></tr>',  # 次の Lv まで 10×10^1.5
      levels,
    )
    self.assertIn("HP + 攻 × 4 + 防 × 4 + 速 × 2 + 器 × 2", html)
    self.assertIn("先手は速 +10・毒は攻 +2として数える", html)
    self.assertIn("強さ点 × 強さ点 ÷ 700", html)
    rate = WORLD["traits"]["thief"]["gold_rate"]
    self.assertIn(f"経験値 × 0.75（盗みの物の怪はさらに × {rate:g}）。最後に四捨五入", html)
    self.assertIn("攻は Lv1 で 6、Lv20 で 30・防は Lv1 で 6", html)  # 能力ごとの値

  def test_guide_page_follows_the_data(self):
    world = copy.deepcopy(WORLD)
    world["season"]["growth"]["def"][-1] = 40  # 防御だけ Lv20 の値を変える
    world["elements"]["ice"]["strong"] = "thunder"  # 雷に強い属性が 2 つ、炎に強い属性はなし
    html = website.build_guide(world)
    self.assertIn("防は Lv1 で 6、Lv20 で 40", html)
    self.assertIn("攻は Lv1 で 6、Lv20 で 30", html)
    self.assertNotIn('aria-label="属性の三すくみ"', html)  # 輪になっていなければ、三すくみの図は出さない

  def test_boss_without_an_area_is_in_the_other_group(self):
    world = copy.deepcopy(WORLD)
    world["places"]["fuji"].pop("area")
    for t in world["towns"]:  # 開放した町も地方なし（その他のまとまり）
      world["places"][t].pop("area")
    html = website.build_monsters(world)
    other = html[html.index(f'id="panel-{website.OTHER_AREA}"') :]
    self.assertIn("<td>酒呑童子</td>", other[: other.index("</section>")])

  def test_guide_shows_loot_when_chests_or_rares_are_disabled(self):
    world = copy.deepcopy(WORLD)
    for key in ("stroll_events", "road_events"):
      world["season"][key].pop("treasure")
    html = website.build_guide(world)
    for action in ("散策", "移動"):
      self.assertIn(f"<td>{action}1行動</td>" + "<td>0%</td>" * 4, html)

    world = copy.deepcopy(WORLD)
    world["season"]["rare_drops"] = False
    html = website.build_guide(world)
    self.assertIn("今は★★以上の武具の配布を止めています", html)
    self.assertIn("<td>散策1行動</td><td>0%</td><td>0.3%</td><td>3.75%</td><td>10.95%</td>", html)
    self.assertIn("<td>移動1行動</td><td>0%</td><td>0.1%</td><td>1.92%</td><td>5.98%</td>", html)
    self.assertIn("<td>★★★</td><td>0%</td><td>0%</td>", html)
    self.assertIn("<td>大物</td><td>原石 1 個を 1 人に</td><td>原石 50%、武具 0%</td>", html)
    self.assertIn("<td>主</td><td>原石 3 個を 1 人に</td><td>原石 33.3333%、武具 0%</td>", html)

  def test_guide_page_season_and_elements(self):
    html = website.build_guide(WORLD)
    self.assertIn("<h3>シーズン</h3>", html)
    self.assertIn(f"シーズンは {WORLD['season']['months']} か月ごと", html)
    # 属性と相性の表は、鉱石を world/ から引く
    self.assertIn('<td><span class="tag">炎属性</span></td><td>熾焔鋼</td>', html)
    self.assertIn('<td><span class="tag">氷属性</span></td><td>霧氷石</td>', html)
    for word in ("星の巡り", "竜の部位", "三つの徳", "ストーリー"):  # 物語はいったん外した（#249）
      self.assertNotIn(word, html)

  def test_guide_follows_the_elements(self):
    world = copy.deepcopy(WORLD)
    world["elements"]["flame"]["ore"] = "<b>火鋼</b>"
    world["season"]["months"] = 4
    html = website.build_guide(world)
    self.assertIn("<td>&lt;b&gt;火鋼&lt;/b&gt;</td>", html)
    self.assertIn("シーズンは 4 か月ごと", html)
    world["elements"].pop("ice")  # 氷属性のない世界でも、残りの行だけ出る
    html = website.build_guide(world)
    self.assertNotIn('<td><span class="tag">氷属性</span></td><td>霧氷石</td>', html)
    self.assertIn('<td><span class="tag">炎属性</span></td><td>&lt;b&gt;火鋼&lt;/b&gt;</td>', html)

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
    for qs in world["town_quests"].values():  # 大物の依頼がなければ、大物のボタンは出さない
      qs[:] = [q for q in qs if not world["monsters"].get(q.get("monster"), {}).get("elite")]
    html = website.build_monsters(world)
    self.assertNotIn('data-f="world"', html)
    self.assertIn('data-f="raid"', html)
    self.assertEqual(website.share_text(0.003), "1%未満")  # ごく少ない地形も 0% にしない
    self.assertEqual(website.share_text(0.126), "13%")

  def test_pie_and_terrain_shares(self):
    shares = website.terrain_shares(WORLD["monsters"], WORLD["road_info"][("nagano", "kofu")]["monsters"])
    self.assertAlmostEqual(sum(x for _, x in shares), 1.0)
    self.assertEqual([t for t, _ in shares], ["forest", "mountain", "snow", "plain"])  # 多い順
    svg = website.pie_svg(shares)
    self.assertEqual(svg.count("<path"), 4)
    self.assertIn("<title>森 39%</title>", svg)
    one = website.pie_svg([("plain", 1.0)])  # 1 つだけなら円
    self.assertIn("<circle", one)
    self.assertNotIn("<path", one)

  def test_opened_area_without_matching_foes_is_still_open(self):
    world = copy.deepcopy(WORLD)
    for info in world["road_info"].values():
      info["monsters"] = []  # 開放した道に、坂東の物の怪がひとりも住んでいなくても
    html = website.build_monsters(world)
    bando = html[html.index('id="panel-bando"') :]
    bando = bando[: bando.index("</section>")]
    self.assertNotIn("まだ開放されていません", bando)  # 開放した町のある地方は、開放した地方として出す
    self.assertIn("<td>雪女</td>", bando)  # 町の討伐で狙う大物も出す

  def test_monsters_page_edge_cases(self):
    world = copy.deepcopy(WORLD)
    world["areas"] = {}  # 地方がなくても、その他のまとまりに物の怪を出す
    self.assertNotIn("まだ開放されていません", website.build_monsters(world))
    world = copy.deepcopy(WORLD)
    for key in (("edo", "odawara"), ("odawara", "edo")):
      world["road_info"][key]["monsters"] = [("wild_dog", 1.0), ("wild_dog", 1.0)]  # 同じ物の怪が 2 度出ても 1 行
    for key in [k for k in world["road_info"] if k not in (("edo", "odawara"), ("odawara", "edo"))]:
      del world["road_info"][key]
    html = website.build_monsters(world)
    self.assertEqual(html.count("<td>送り犬</td>"), 1)

  def test_guild_before_anyone_played(self):
    html = website.build_guild(WORLD, [], DAY, game.new_guild())
    self.assertIn('<span class="open">受付中</span>', html)
    self.assertNotIn("引受済</span>", html)
    for p in (WORLD["places"][t] for t in WORLD["towns"]):
      self.assertIn(p["name"], html)

  def test_names_are_escaped(self):
    hero = {"id": "h1", "name": "<b>&悪</b>"}
    state = game.new_state(WORLD, "h1", hero["name"])
    for html in (
      website.build_ranking(WORLD, [(hero, state)], DAY),
      website.build_hero(WORLD, hero, state),
    ):
      self.assertNotIn("<b>&悪", html)
      self.assertIn("&lt;b&gt;&amp;悪", html)

  def test_hero_text_is_not_shortened(self):
    world = copy.deepcopy(WORLD)
    for item in world["items"].values():  # 名前を長くして、装備の行を長くする
      item["name"] *= 3
    hero = {"id": "h1", "name": "もののふ"}
    state = game.new_state(world, "h1", "もののふ")
    state["inventory"] = {"iron_sword": 1, "iron_helm": 1, "iron_shield": 1, "lifeblood_charm": 1}
    gear = "装備: " + view.gear_text(world, state)
    self.assertGreater(rpg.common.width(gear), rpg.common.LINE_MAX)  # Gist のカードの幅より長い行
    self.assertIn(gear, view.build_text(world, state))  # 毎日の実行のログでも詰めない
    self.assertIn(escape(gear), website.build_hero(world, hero, state))

  def test_configured_site_url_is_used_for_the_board_and_hero_pages(self):
    with mock.patch.dict("os.environ", {"RPG_SITE_URL": "https://example.pages.dev"}, clear=True):
      self.assertIn("https://example.pages.dev/", rpg.build_board(WORLD, [], DAY))
      self.assertEqual(rpg.page_url("h1"), "https://example.pages.dev/heroes/h1.html")

  def test_redirect_preserves_paths_query_and_fragment(self):
    files = website.write_site(WORLD, [], DAY, {}, self.out)
    website.redirect_pages(files, self.out, "https://example.pages.dev/")
    guide = (self.out / "guide.html").read_text()
    self.assertIn('href="https://example.pages.dev/guide.html"', guide)
    self.assertIn('location.replace("https://example.pages.dev/guide.html" + location.search + location.hash)', guide)

  def test_site_url_only_in_actions(self):
    with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "Owner/warriors-road"}, clear=True):
      self.assertEqual(rpg.site_url(), "https://owner.github.io/warriors-road/")
      board = rpg.build_board(WORLD, [], DAY)
      self.assertIn("https://owner.github.io/warriors-road/", board)
      self.assertIn("サイト「もののふ道中」", board)
    with mock.patch.dict("os.environ", {}, clear=True):
      self.assertIsNone(rpg.site_url())


class ImportsTest(unittest.TestCase):
  def test_website_does_not_depend_on_rpg(self):
    """サイトの書き出しは、もののふの登録や番付の Gist の更新（rpg.py）を読み込まない（表示は view.py、状態は store.py）。"""
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
    state = game.new_state(WORLD, "h1", "もののふ")
    state["place"] = town
    self.assertNotIn("<a ", mapsvg.render(WORLD, [({"id": "h1", "name": "もののふ"}, state)]))


if __name__ == "__main__":
  unittest.main()
