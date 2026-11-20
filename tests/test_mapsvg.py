import json
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import game  # noqa: E402
import mapsvg  # noqa: E402
import world as W  # noqa: E402

WORLD = W.load_world()
NS = "{http://www.w3.org/2000/svg}"


def hero(i, name, place):
    state = game.new_state(WORLD, f"h{i}", name)
    state["place"] = place
    return {"id": f"h{i}", "name": name}, state


class RenderTest(unittest.TestCase):
    def test_every_place_is_drawn_and_svg_is_valid(self):
        root = ET.fromstring(mapsvg.render(WORLD, []))
        ids = {g.get("id") for g in root.iter(f"{NS}g") if g.get("id")}
        self.assertEqual(ids, {f"place-{pid}" for pid in WORLD["places"]})
        texts = "".join(t.text or "" for t in root.iter(f"{NS}text"))
        for ring in WORLD["layout"]["rings"]:
            self.assertIn(ring["label"], texts)

    def test_every_road_shows_its_cost(self):
        root = ET.fromstring(mapsvg.render(WORLD, []))
        costs = [g for g in root.iter(f"{NS}g") if g.get("class") == "road-cost"]
        self.assertEqual(len(costs), len(WORLD["roads"]) // 2)
        shown = sorted(int(g.find(f"{NS}text").text) for g in costs)
        self.assertEqual(shown, sorted(d for (a, b), d in WORLD["roads"].items() if a < b))

    def test_heroes_are_marked_and_names_escaped(self):
        place = WORLD["starts"][0]
        svg = mapsvg.render(WORLD, [hero(1, "ア<&>リス", place)])
        root = ET.fromstring(svg)  # 名前の記号で壊れない
        texts = [t.text for t in root.iter(f"{NS}text")]
        self.assertIn("🧙 ア<&>リス Lv.1", texts)
        self.assertEqual(len([g for g in root.iter(f"{NS}g") if g.get("class") == "hero"]), 1)

    def test_many_heroes_in_one_place_are_summarized(self):
        place = WORLD["starts"][0]
        entries = [hero(i, f"勇者{i}", place) for i in range(mapsvg.MAX_NAMES + 2)]
        texts = [t.text for t in ET.fromstring(mapsvg.render(WORLD, entries)).iter(f"{NS}text")]
        self.assertIn("🧙 ほか 2 人", texts)

    def test_no_layout_means_no_image(self):
        self.assertIsNone(mapsvg.render({**WORLD, "layout": None}, []))

    def test_places_without_pos_are_skipped(self):
        places = {pid: dict(p) for pid, p in WORLD["places"].items()}
        del places[WORLD["starts"][0]]["pos"]
        root = ET.fromstring(mapsvg.render({**WORLD, "places": places}, []))
        ids = {g.get("id") for g in root.iter(f"{NS}g") if g.get("id")}
        self.assertNotIn(f"place-{WORLD['starts'][0]}", ids)


class BackgroundTest(unittest.TestCase):
    """ベースの地図（layout.background）を敷く。world/ を一時ディレクトリにコピーして試す。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "world"
        shutil.copytree(W.WORLD_DIR, self.dir)

    def use(self, name, data=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"/>'):
        if data is not None:
            (self.dir / name).write_bytes(data)
        mp = json.loads((self.dir / "map.json").read_text(encoding="utf-8"))
        mp["layout"]["background"] = name
        (self.dir / "map.json").write_text(json.dumps(mp, ensure_ascii=False), encoding="utf-8")
        return W.validate(W.load_raw(self.dir))

    def test_background_is_embedded_and_rings_are_not_drawn(self):
        self.assertEqual(self.use("map-base.svg"), [])
        world = W.load_world(self.dir)
        self.assertTrue(world["background"].startswith("data:image/svg+xml;base64,"))
        root = ET.fromstring(mapsvg.render(world, []))
        images = list(root.iter(f"{NS}image"))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].get("href"), world["background"])
        radii = {float(c.get("r")) for c in root.iter(f"{NS}circle")}
        self.assertNotIn(float(WORLD["layout"]["rings"][0]["radius"]), radii)  # 輪は描かない

    def test_background_errors(self):
        self.assertTrue(any("ありません" in e for e in self.use("none.svg", None)))
        self.assertTrue(any("形式" in e for e in self.use("map-base.gif", b"GIF89a")))
        self.assertTrue(any("ファイル名だけ" in e for e in self.use("../map-base.svg", None)))
        big = b"0" * (W.BACKGROUND_MAX_BYTES + 1)
        self.assertTrue(any("KB" in e for e in self.use("map-base.png", big)))

    def test_grid_shows_coordinates_and_ids(self):
        root = ET.fromstring(mapsvg.render(WORLD, [], grid=True))
        texts = [t.text or "" for t in root.iter(f"{NS}text")]
        pid = WORLD["starts"][0]
        x, y = WORLD["places"][pid]["pos"]
        self.assertIn(f"{pid} ({x:g}, {y:g})", texts)
        self.assertIn("500", texts)  # 目盛りの数字


if __name__ == "__main__":
    unittest.main()
