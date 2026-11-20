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
        self.assertEqual(rels, {"index.html", "ranking.html", "guild.html", "heroes/h1.html", "heroes/h2.html",
                                "heroes/h3.html"})
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
        held = [e for es in guild["towns"].values() for e in es if e["status"] == "held"]
        self.assertTrue(held)
        for e in held:
            self.assertIn(f'<span class="taken">受注中</span> <a href="heroes/{e["holder"]}.html">', html)
        total = sum(len(es) for es in guild["towns"].values())
        self.assertEqual(html.count("<tr><td class=\"mark\">"), total)
        days = WORLD["season"]["quest_open_days"]
        last = today + timedelta(days=days - 1)
        self.assertIn(f"あと {days - 1} 日（{last.month}/{last.day} まで）", html)  # 今日貼られた依頼

    def test_guild_before_anyone_played(self):
        html = website.build_guild(WORLD, [], DAY, game.new_guild())
        self.assertIn('<span class="open">受付中</span>', html)
        self.assertNotIn('<span class="taken">受注中</span>', html)
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
        hero = {"id": "h1", "name": "勇者"}
        state = game.new_state(WORLD, "h1", "勇者")
        best = {}
        for iid, item in WORLD["items"].items():  # 部位ごとに名前の長いレアを持たせて、装備の行を長くする
            if item["rarity"] != "common" and len(item["name"]) > len(WORLD["items"].get(best.get(item["slot"]), {"name": ""})["name"]):
                best[item["slot"]] = iid
        state["inventory"] = {iid: 1 for iid in best.values()}
        gear = "装備: " + rpg.gear_text(WORLD, state)
        self.assertNotIn(gear, rpg.build_text(WORLD, state))  # Gist では幅に収まるように詰める
        self.assertIn(escape(gear), website.build_hero(WORLD, hero, state))

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
