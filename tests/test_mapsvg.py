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
    texts = [t.text or "" for t in root.iter(f"{NS}text")]
    for p in WORLD["places"].values():  # 町は名前と呼び名、ダンジョンは名前
      self.assertIn(p["name"], texts)
      if p.get("alias"):
        self.assertIn(p["alias"], texts)

  def test_every_road_shows_its_cost(self):
    root = ET.fromstring(mapsvg.render(WORLD, []))
    costs = [g for g in root.iter(f"{NS}g") if g.get("class") == "road-cost"]
    expected = [d for (a, b), d in WORLD["roads"].items() if a < b] + [r["distance"] for r in WORLD["trails"]]
    shown = sorted(int(g.find(f"{NS}text").text) for g in costs)
    self.assertEqual(shown, sorted(expected))

  def test_road_kinds_are_drawn(self):
    root = ET.fromstring(mapsvg.render(WORLD, []))
    kinds = [p.get("class") for p in root.iter(f"{NS}path") if (p.get("class") or "").startswith("road-")]
    self.assertEqual(
      kinds.count("road-sea"), sum(1 for (a, b), i in WORLD["road_info"].items() if a < b and i["kind"] == "sea")
    )
    self.assertEqual(kinds.count("road-trail"), len(WORLD["trails"]))

  def test_hero_at_the_hunting_ground_is_drawn_on_the_road(self):
    town = WORLD["starts"][0]
    _, state = hero(1, "アリス", town)
    t = next(x for x in WORLD["quests"] if x["type"] == "hunt")
    q = game.make_quest(WORLD, t, town, game.date(2026, 12, 1), game.random.Random(0), 1)
    state["quest"] = q | {"away": 1}
    info, total = WORLD["road_info"][(town, q["place"])], WORLD["roads"][(town, q["place"])]
    self.assertEqual(mapsvg.hero_spot(WORLD, state), mapsvg.point_along(info["path"], 1 / total))

  def test_hero_on_a_road_is_drawn_along_the_road(self):
    town = WORLD["starts"][0]
    to = WORLD["neighbors"][town][0]
    info, total = WORLD["road_info"][(town, to)], WORLD["roads"][(town, to)]
    _, state = hero(1, "アリス", town)
    state["road"] = {"to": to, "left": total}
    self.assertEqual(mapsvg.hero_spot(WORLD, state), tuple(info["path"][0]))  # 出発したばかり
    state["road"] = {"to": to, "left": 0}
    spot = mapsvg.hero_spot(WORLD, state)
    self.assertAlmostEqual(spot[0], info["path"][-1][0])  # 着く直前
    self.assertAlmostEqual(spot[1], info["path"][-1][1])

  def test_heroes_are_marked_and_names_escaped(self):
    place = WORLD["starts"][0]
    svg = mapsvg.render(WORLD, [hero(1, "ア<&>リス", place)])
    root = ET.fromstring(svg)  # 名前の記号で壊れない
    texts = [t.text for t in root.iter(f"{NS}text")]
    self.assertFalse(any("ア<&>リス" in (t or "") for t in texts))  # 名前の札は出さない
    marks = [g for g in root.iter(f"{NS}g") if g.get("class") == "hero"]
    self.assertEqual(len(marks), 1)
    self.assertEqual(marks[0].find(f"{NS}title").text, "ア<&>リス Lv.1")  # マウスを乗せると名前と Lv
    self.assertEqual(marks[0].find(f"{NS}circle").get("pointer-events"), "all")  # 丸の中も当たり（町の印に負けない）

  def test_heroes_in_one_place_share_one_mark(self):
    place = WORLD["starts"][0]
    entries = [hero(i, f"勇者{i}", place) for i in range(6)]
    marks = [g for g in ET.fromstring(mapsvg.render(WORLD, entries)).iter(f"{NS}g") if g.get("class") == "hero"]
    self.assertEqual(len(marks), 1)
    self.assertEqual(marks[0].find(f"{NS}title").text.count("勇者"), 6)

  def test_canvas_fits_the_map(self):
    root = ET.fromstring(mapsvg.render(WORLD, []))
    self.assertEqual(root.get("width"), str(WORLD["layout"]["size"][0] + 2 * mapsvg.PAD_X))

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

  def test_background_is_embedded(self):
    self.assertEqual(self.use("map-base.svg"), [])
    world = W.load_world(self.dir)
    self.assertTrue(world["background"].startswith("data:image/svg+xml;base64,"))
    root = ET.fromstring(mapsvg.render(world, []))
    images = list(root.iter(f"{NS}image"))
    self.assertEqual(len(images), 1)
    self.assertEqual(images[0].get("href"), world["background"])

  def test_without_background_the_sea_is_drawn(self):
    root = ET.fromstring(mapsvg.render({**WORLD, "background": None}, []))
    self.assertEqual(list(root.iter(f"{NS}image")), [])

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
