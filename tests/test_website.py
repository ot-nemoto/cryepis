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
        self.assertEqual(rels, {"index.html", "ranking.html", "guild.html", "items.html", "towns.html", "heroes/h1.html",
                                "heroes/h2.html", "heroes/h3.html"})
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
        self.assertIn("まだ勇者がいません", (self.out / "index.html").read_text(encoding="utf-8"))

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
        html = website.build_index(WORLD, entries, today)
        svg = html[html.index("<svg"):html.index("</svg>") + 6]
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
        self.assertEqual(html.count("<tr data-rank="), total)
        for town in WORLD["towns"]:  # 町とレアリティで絞り込める
            self.assertIn(f'data-town="{town}"', html)
            self.assertIn(f'<button type="button" class="chip" data-town="{town}" aria-pressed="false">', html)
        self.assertEqual(html.count('class="chip" data-rank='), len(WORLD["quest_ranks"]))
        self.assertLess(html.index('f-label">町<'), html.index('f-label">レアリティ<'))  # 町が上
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

    def test_items_page_lists_the_open_region(self):
        html = website.build_items(WORLD)
        names = {it["name"] for it in WORLD["items"].values()}
        shown = {n for n in names if f"{n}<" in html or f"{n}</td>" in html}
        expected = {WORLD["items"][i]["name"] for i in WORLD["region_items"]} | {
            it["name"] for it in WORLD["items"].values() if it["rarity"] != "common"}
        self.assertEqual(shown, expected)
        self.assertNotIn("戦斧", html)  # 開放していない地域の特産は出さない
        self.assertNotIn("鎧</h3>", html)  # 鎧の部位はない
        self.assertEqual(html.count('class="chip" data-slot='), 4)
        self.assertLess(html.index('f-label">アイテム<'), html.index('f-label">レアリティ<'))
        self.assertIn("依頼の報酬: ムラリス", html)  # ★★騎士の剣
        self.assertIn('騎兵の槍<span class="tag">特産</span>', html)
        self.assertNotIn('鋼の剣<span class="tag">特産</span>', html)  # 開放していない町でも売っている
        self.assertIn('<td class="mark">★★</td><td>騎士の剣</td>', html)  # ★ は先頭の列
        for slot in ("weapon", "helmet", "shield", "accessory"):  # ★ の多い順
            marks = [len(game.rarity_mark(WORLD, r["item"]["rarity"])) for r in website.item_rows(WORLD)[slot]]
            self.assertEqual(marks, sorted(marks, reverse=True))

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
        aurea = html[html.index('id="aurea"'):html.index('id="flumen"')]  # 開放していない町
        self.assertIn("未開放", aurea)
        self.assertIn("依頼の枠 6 件", aurea)
        self.assertIn("<tr><th>特産品</th><td>-</td></tr>", aurea)
        self.assertNotIn("王国の盾", aurea)
        muralis = html[html.index('id="muralis"'):html.index('id="karun"')]
        self.assertIn("城壁の大盾", muralis.split("依頼の報酬で出るレア")[0])  # 特産品
        self.assertIn("騎士の剣", muralis.split("店の品ぞろえ")[0])  # レア
        self.assertIn("南壁の巣穴（ダンジョン・まだ入れない）", muralis)
        self.assertIn("カルン（陸路 6）", muralis)

    def test_guild_before_anyone_played(self):
        html = website.build_guild(WORLD, [], DAY, game.new_guild())
        self.assertIn('<span class="open">受付中</span>', html)
        self.assertNotIn("ACCEPTED</span>", html)
        for p in (WORLD["places"][t] for t in WORLD["towns"]):
            self.assertIn(p["name"], html)

    def test_names_are_escaped(self):
        hero = {"id": "h1", "name": "<b>&悪</b>"}
        state = game.new_state(WORLD, "h1", hero["name"])
        for html in (website.build_ranking(WORLD, [(hero, state)], DAY), website.build_hero(WORLD, hero, state),
                     website.build_index(WORLD, [(hero, state)], DAY)):
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
