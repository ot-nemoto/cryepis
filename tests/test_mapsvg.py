import sys
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


if __name__ == "__main__":
    unittest.main()
