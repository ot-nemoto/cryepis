"""地図の生成（tools/worldgen）の各手順（地形・陸路・航路・行動回数・SVG・world/map.json・make.py）のテスト。
本物の生成は 45 秒ほどかかるので、小さな地形と小さな表に差し替えて動かす（本物の .cache/ や world/ には触らない）。
本物の生成を通しで動かして、コミット済みの world/map.json・map-base.svg と一致するかは、遅いテスト（CRYEPIS_SLOW_TESTS=1）。"""

import contextlib
import io
import json
import os
import pickle
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_worldgen as T  # noqa: E402  地形を読み込むモジュールを、小さな地形で読み込んである

actions, build_world, land, paths, roads, sea, terrain, vectorize = (
  T.actions,
  T.build_world,
  T.land,
  T.paths,
  T.roads,
  T.sea,
  T.terrain,
  T.vectorize,
)
_saved_path = list(sys.path)
import make  # noqa: E402  tools/worldgen は、test_worldgen が sys.path に入れてある

sys.path[:] = _saved_path  # make.py は、読み込むときに tools/worldgen を sys.path の先頭に入れる（持ち込まない）

# 小さな世界: 100 x 60 マス（STEP 2 で 200 x 120 px）。左の 40 列は海、その右の 1 列は砂浜、残りは草原（右の中ほどに森）
GW, GH, STEP = 100, 60, 2
TOWNS = [("北の町", 120, 40), ("東の町", 160, 40), ("南の町", 120, 90)]
DUNGEONS = [("東の洞", 180, 100)]
PORTS = {"港 A": (84, 40), "港 B": (84, 100)}


def toy_kind():
  rows = []
  for j in range(GH):
    row = ["sea"] * 40 + ["beach"] + ["plain"] * 59
    if 20 <= j < 40:
      row[60:75] = ["forest"] * 15
    rows.append(row)
  return rows


@contextlib.contextmanager
def toy_world(kind=None):
  """roads・sea・vectorize・build_world の地形と表を、小さな世界に差し替える。書き出し先は一時ディレクトリ。"""
  kind = kind or toy_kind()
  with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
    tmp = Path(tmp)
    for module in (roads, sea):
      stack.enter_context(mock.patch.multiple(module, kind=kind, GH=GH, GW=GW))
    stack.enter_context(
      mock.patch.multiple(
        roads,
        TOWNS=TOWNS,
        DUNGEONS=DUNGEONS,
        LAND_ROADS=[("北の町", "東の町"), ("北の町", "南の町")],
        TRAILS=[("東の洞", "東の町")],
      )
    )
    stack.enter_context(
      mock.patch.multiple(
        sea,
        pos={**{n: (x, y) for n, x, y in TOWNS}, **PORTS},
        SEA_ROUTES=[("港 A", "港 B")],
        OCEAN=sea.ocean_cells(),
      )
    )
    stack.enter_context(mock.patch.multiple(vectorize, kind=kind, GH=GH, GW=GW, height=[[0.5] * GW for _ in range(GH)]))
    stack.enter_context(mock.patch.object(vectorize, "rivers", [[(100, 10), (110, 30), (130, 60)]]))
    stack.enter_context(
      mock.patch.multiple(
        paths,
        CACHE=tmp / "cache",
        REPO=tmp / "repo",
        ROADS=tmp / "roads.json",
        SEA=tmp / "sea.json",
        BASE_SVG=tmp / "map-base.svg",
        TERRAIN=tmp / "terrain.pkl",
      )
    )
    (tmp / "repo" / "world").mkdir(parents=True)
    yield tmp


def quiet(fn, *args):
  with contextlib.redirect_stdout(io.StringIO()) as out:
    fn(*args)
  return out.getvalue()


def png_info(path):
  """PNG の (幅, 高さ, 色数, 展開した画素データの長さ)。書式を確かめながら読む。"""
  data = Path(path).read_bytes()
  assert data[:8] == b"\x89PNG\r\n\x1a\n"
  pos, chunks = 8, {}
  while pos < len(data):
    (n,) = struct.unpack(">I", data[pos : pos + 4])
    tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + n]
    (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
    assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, tag  # 壊れていない
    chunks[tag] = body
    pos += 12 + n
  w, h = struct.unpack(">II", chunks[b"IHDR"][:8])
  return w, h, len(chunks[b"PLTE"]) // 3, len(zlib.decompress(chunks[b"IDAT"]))


class SmallLand(unittest.TestCase):
  """land・terrain の大きさ（W・H・マス数）を、小さくして動かす。"""

  W, H = 160, 100

  def setUp(self):
    for module in (land, terrain):
      patcher = mock.patch.multiple(module, W=self.W, H=self.H, GW=self.W // 2, GH=self.H // 2)
      patcher.start()
      self.addCleanup(patcher.stop)
    patcher = mock.patch.multiple(land, CX=80, CY=50, SX=1.6)
    patcher.start()
    self.addCleanup(patcher.stop)


class LandGenerateTest(SmallLand):
  def test_same_seed_same_land(self):
    a, b = land.generate("seed", 0.45), land.generate("seed", 0.45)
    self.assertEqual(a, b)
    self.assertNotEqual(a[1], land.generate("other", 0.45)[1])

  def test_shape_and_kinds(self):
    elev, kind = land.generate("seed", 0.4)
    self.assertEqual((len(kind), len(kind[0])), (land.GH, land.GW))
    self.assertEqual((len(elev), len(elev[0])), (land.GH, land.GW))
    cells = {k for row in kind for k in row}
    self.assertTrue(cells <= set(land.COLOR), cells - set(land.COLOR))
    self.assertTrue({"land", "sea"} <= cells, cells)  # 陸と海がある（小さな世界は、極地の氷が多くなる）

  def test_more_land_ratio_gives_more_land(self):
    def land_cells(ratio):
      return sum(k in ("land", "beach") for row in land.generate("seed", ratio)[1] for k in row)

    self.assertLess(land_cells(0.2), land_cells(0.6))

  def test_write_png(self):
    elev, kind = land.generate("seed", 0.45)
    with tempfile.TemporaryDirectory() as tmp:
      size, colors = land.write_png(Path(tmp) / "land.png", elev, kind)
      w, h, palette, raw = png_info(Path(tmp) / "land.png")
    self.assertEqual((w, h), (self.W, self.H))
    self.assertEqual(palette, colors)
    self.assertLessEqual(colors, 256)
    self.assertEqual(raw, self.H * (1 + self.W))  # 1 行ごとに、フィルタの 1 バイト + 横の画素
    self.assertGreater(size, 100)

  def test_main_writes_a_png_and_says_so(self):
    with tempfile.TemporaryDirectory() as tmp:
      out = Path(tmp) / "x.png"
      with mock.patch.object(sys, "argv", ["land.py", str(out), "s", "0.5"]):
        text = quiet(land.main)
      self.assertTrue(out.exists())
    self.assertIn("seed=s land=0.5", text)

  def test_shade_level_is_flat_when_flat(self):
    self.assertEqual(land.shade_level([[0.3, 0.3]], [["land", "land"]], 1, 0), 2)  # FLAT の間は、陰影なし
    with mock.patch.object(land, "FLAT", False):
      self.assertEqual(land.shade_level([[-1.0, -1.0]], [["sea", "sea"]], 1, 0), 2)  # 海は陰影なし
      self.assertEqual(land.shade_level([[0.0, 0.5], [0.0, 0.5]], [["land"] * 2] * 2, 1, 1), 3)  # 上り坂は明るい
      self.assertEqual(land.shade_level([[0.5, 0.0], [0.5, 0.0]], [["land"] * 2] * 2, 1, 1), 0)  # 下りは暗い


class TerrainGenerateTest(SmallLand):
  def test_terrain_is_painted_on_the_land_and_is_repeatable(self):
    kind, height = terrain.generate()
    self.assertEqual((len(kind), len(kind[0])), (terrain.GH, terrain.GW))
    self.assertEqual((len(height), len(height[0])), (terrain.GH, terrain.GW))
    self.assertTrue(set(row_k for row in kind for row_k in row) <= set(terrain.COLOR))
    self.assertNotIn("land", {k for row in kind for k in row})  # 陸は、気候の地形に塗り分けられる
    again, _ = terrain.generate()
    self.assertEqual(kind, again)
    self.assertTrue(all(0 <= h <= 1 for row in height for h in row))

  def test_rivers_are_recorded_for_the_vector_map(self):
    terrain.generate()
    rivers = terrain.generate.rivers
    self.assertEqual(len(rivers), len(terrain.RIVERS))
    self.assertTrue(all(len(pts) > len(line) for pts, line in zip(rivers, terrain.RIVERS)))  # 曲げて細かくしてある

  def test_write_png_and_labels(self):
    kind, height = terrain.generate()
    with tempfile.TemporaryDirectory() as tmp:
      tmp = Path(tmp)
      size, colors = terrain.write_png(tmp / "t.png", kind, height)
      w, h, palette, _ = png_info(tmp / "t.png")
      terrain.write_labels(tmp / "t.svg", "t.png")
      svg = ET.parse(tmp / "t.svg").getroot()
    self.assertEqual((w, h, palette), (self.W, self.H, colors))
    self.assertTrue(svg.tag.endswith("svg"))
    texts = [e.text for e in svg.iter() if e.tag.endswith("text")]
    self.assertEqual(texts, [n for n, _, _ in terrain.LABELS])

  def test_stamp_lines_draws_a_band_around_the_line(self):
    grid = [[None] * 20 for _ in range(10)]
    terrain.stamp_lines(grid, [([(4, 10), (36, 10)], 4, "hill")], None, lambda c: c, land.Noise("flat"))
    tags = {cell[1] for row in grid for cell in row if cell}
    self.assertEqual(tags, {"hill"})
    self.assertTrue(grid[5][10] is not None)  # 線の上
    self.assertTrue(grid[0][0] is None)  # 線から遠い


class ActionsMainTest(unittest.TestCase):
  def test_writes_actions_into_roads_and_sea(self):
    with toy_world():
      roads_in = [{"km": 30, "kind": "road"}, {"km": 100, "kind": "road"}, {"km": 100, "kind": "trail"}]
      paths.ROADS.write_text(json.dumps(roads_in), encoding="utf-8")
      paths.SEA.write_text(json.dumps([{"km": 10}, {"km": 300}]), encoding="utf-8")
      actions.main()
      roads_out = json.loads(paths.ROADS.read_text(encoding="utf-8"))
      sea_out = json.loads(paths.SEA.read_text(encoding="utf-8"))
    self.assertEqual([r["actions"] for r in roads_out], [1, 3, 5])  # 山道は街道より遅い
    self.assertEqual([r["actions"] for r in sea_out], [2, 4])
    self.assertEqual([r["km"] for r in roads_out], [30, 100, 100])  # もとの値は残る


class RoadsMainTest(unittest.TestCase):
  def run_roads(self):
    with toy_world():
      roads.main()
      return json.loads(paths.ROADS.read_text(encoding="utf-8"))

  def test_roads_follow_the_route_tables(self):
    out = self.run_roads()
    self.assertEqual(
      [(r["from"], r["to"], r["kind"]) for r in out],
      [("北の町", "東の町", "road"), ("北の町", "南の町", "road"), ("東の洞", "東の町", "trail")],
    )

  def test_roads_have_costs_lengths_and_paths_on_land(self):
    kind = toy_kind()
    for r in self.run_roads():
      self.assertGreater(r["cost"], 0)
      self.assertGreaterEqual(len(r["path"]), 2)
      self.assertEqual(r["km"], roads.length_km(r["path"]))  # 長さは線の長さ
      for x, y in r["path"]:
        self.assertNotEqual(kind[y // STEP][x // STEP], "sea")  # 海は通らない

  def test_length_km_follows_the_map_scale(self):
    self.assertEqual(roads.length_km([(0, 0), (30, 40)]), round(50 * roads.japan.KM_PER_PX))

  def test_trails_take_mountains_more_easily(self):
    self.assertLess(roads.TRAIL_COST["mount"], roads.COST["mount"])
    self.assertEqual(roads.TRAIL_COST["plain"], roads.COST["plain"])

  def test_towns_separated_by_water_stop_the_build(self):
    kind = toy_kind()
    for j in range(GH):  # 海の帯で、西（北の町・南の町）と東（東の町・東の洞）に分ける
      for i in range(65, 75):
        kind[j][i] = "sea"
    with toy_world(kind), self.assertRaises(SystemExit) as cm:
      roads.main()
    self.assertIn("陸で行けません", str(cm.exception))


class SeaStagesTest(unittest.TestCase):
  def test_ocean_cells_skip_small_ponds(self):
    kind = toy_kind()
    for j in range(5, 10):  # 陸の上の小さな池（25 マス）
      for i in range(60, 65):
        kind[j][i] = "sea"
    with toy_world(kind):
      ocean = sea.OCEAN
    self.assertIn((10, 30), ocean)  # 外海
    self.assertNotIn((62, 7), ocean)  # 池は含めない

  def test_nearest_water_and_failure(self):
    with toy_world():
      cell = sea.nearest_water(84, 40)
      self.assertEqual(cell[0], 39)  # 砂浜のすぐ左の海
      with mock.patch.object(sea, "OCEAN", set()), self.assertRaises(SystemExit) as cm:
        sea.nearest_water(84, 40)
    self.assertIn("海が見つからない", str(cm.exception))

  def test_sea_path_stays_on_water_between_the_ports(self):
    with toy_world():
      pts, cost = sea.sea_path("港 A", "港 B")
      self.assertEqual((pts[0], pts[-1]), (PORTS["港 A"], PORTS["港 B"]))
      self.assertGreater(cost, 0)
      self.assertEqual(sea.check(pts), 0)  # 港の出入り以外で陸を通らない

  def test_sea_path_goes_around_a_cape(self):
    kind = toy_kind()
    for j in range(28, 33):  # 沖に突き出た陸（岬）。まっすぐ南へ行くと、岬を横切ってしまう
      for i in range(10, 40):
        kind[j][i] = "plain"
    with toy_world(kind):
      pts, _ = sea.sea_path("港 A", "港 B")
      self.assertEqual(sea.check(pts), 0)  # 岬を回り込んで、陸を通らない
      self.assertTrue(any(x < 10 * STEP for x, _ in pts[1:-1]), pts)  # 岬の先（西）まで出ている
      for x, y in pts[1:-1]:
        self.assertFalse(10 * STEP <= x < 40 * STEP and 28 * STEP <= y < 33 * STEP, (x, y))

  def test_check_counts_steps_on_land(self):
    with toy_world():
      self.assertEqual(sea.check([(0, 0), (10, 10), (20, 10), (30, 10)]), 0)  # 海の中
      self.assertGreater(sea.check([(0, 0), (150, 40), (180, 40), (190, 40)]), 0)  # 陸を横切る

  def test_main_writes_the_routes(self):
    with toy_world():
      sea.main()
      out = json.loads(paths.SEA.read_text(encoding="utf-8"))
    self.assertEqual([(r["from"], r["to"]) for r in out], [("港 A", "港 B")])
    self.assertGreater(out[0]["cost"], 0)
    self.assertEqual(out[0]["km"], roads.length_km(out[0]["path"]))

  def test_main_refuses_a_route_over_land(self):
    with toy_world(), mock.patch.object(sea, "check", return_value=3), self.assertRaises(AssertionError) as cm:
      sea.main()
    self.assertIn("陸の上", str(cm.exception))


class VectorizeMainTest(unittest.TestCase):
  def test_main_writes_a_layered_svg(self):
    with toy_world() as tmp:
      out = tmp / "base.svg"
      text = quiet(vectorize.main, str(out))
      root = ET.parse(out).getroot()
    self.assertIn("KB", text)
    self.assertEqual((root.get("width"), root.get("height")), (str(GW * STEP), str(GH * STEP)))
    tags = [e.tag.split("}")[-1] for e in root]
    self.assertEqual(tags[0], "rect")  # 一番下は深い海
    self.assertIn("path", tags)
    fills = {e.get("fill") for e in root if e.tag.endswith("path")}
    self.assertIn(vectorize.hexc(vectorize.COLOR["plain"]), fills)  # 草原
    self.assertIn(vectorize.hexc(vectorize.COLOR["forest"]), fills)  # 森
    rivers = [e for e in root if e.tag.endswith("path") and e.get("fill") == "none"]
    self.assertEqual(len(rivers), 1)  # 川は線で描く

  def test_layer_and_mask_of(self):
    kind = toy_kind()
    with toy_world(kind):
      mask = vectorize.mask_of(lambda i, j: kind[j][i] == "forest")
      self.assertEqual(sum(map(sum, mask)), 15 * 20)
      svg = vectorize.layer(mask, "#00ff00")
      self.assertTrue(svg.startswith('<path fill="#00ff00" fill-rule="evenodd" d="M'))
      self.assertEqual(vectorize.layer(vectorize.mask_of(lambda i, j: False), "#fff"), "")  # 何もなければ描かない

  def test_shade_follows_the_slope(self):
    with toy_world():
      vectorize.height[1][1] = 0.9
      self.assertEqual(vectorize.shade(1, 1), 3)  # 左上より高い → 明るい
      vectorize.height[1][1] = 0.1
      self.assertEqual(vectorize.shade(1, 1), 0)  # 低い → 暗い
      self.assertEqual(vectorize.shade(0, 0), 2)  # 端は自分との比較（平ら）

  def test_smooth_loop_drops_tiny_and_degenerate_shapes(self):
    self.assertIsNone(vectorize.smooth_loop([(0, 0), (1, 0), (1, 1)]))  # 点が少なすぎる
    self.assertIsNone(vectorize.smooth_loop([(0, 0), (1, 0), (1, 1), (0, 1)]))  # 1 マスだけの範囲は小さすぎて描かない
    big = vectorize.smooth_loop([(0, 0), (20, 0), (20, 20), (0, 20)])
    self.assertIsNotNone(big)
    self.assertGreater(vectorize.area(big), vectorize.MIN_AREA)


class BuildWorldMainTest(unittest.TestCase):
  def setup_files(self, tmp):
    path = [[100, 100], [200, 120], [400, 300]]
    paths.ROADS.write_text(
      json.dumps(
        [
          {"from": "江戸", "to": "小田原", "cost": 80, "actions": 2, "path": path, "kind": "road"},
          {"from": "富士山", "to": "甲府", "cost": 40, "actions": 1, "path": path[:2], "kind": "trail"},
        ]
      ),
      encoding="utf-8",
    )
    paths.SEA.write_text(
      json.dumps([{"from": "大坂", "to": "勝瑞", "cost": 120, "actions": 2, "path": path}]), encoding="utf-8"
    )
    paths.BASE_SVG.write_text("<svg/>", encoding="utf-8")

  def run_main(self):
    with toy_world() as tmp:
      with mock.patch.object(build_world, "kind", [["plain"] * 800 for _ in range(500)]):
        self.setup_files(tmp)
        text = quiet(build_world.main)
      mp = json.loads((paths.REPO / "world" / "map.json").read_text(encoding="utf-8"))
      raw = (paths.REPO / "world" / "map.json").read_text(encoding="utf-8")
      svg = (paths.REPO / "world" / "map-base.svg").read_text(encoding="utf-8")
    return text, mp, raw, svg

  def test_writes_map_json_and_copies_the_base_map(self):
    text, mp, raw, svg = self.run_main()
    self.assertEqual(svg, "<svg/>")
    self.assertIn("町・ダンジョン 32、道 3", text)
    self.assertEqual([p["id"] for p in mp["places"]], [p["id"] for p in build_world.build_places()])
    self.assertEqual(mp["layout"], {"size": [1600, 1000], "background": "map-base.svg"})
    self.assertEqual(mp["open"], [build_world.pid(n) for n in build_world.OPEN])

  def test_roads_come_from_the_json_files(self):
    _, mp, _, _ = self.run_main()
    land_road, trail, sea_road = mp["roads"]
    self.assertEqual(
      (land_road["from"], land_road["to"], land_road["kind"], land_road["distance"]), ("edo", "odawara", "land", 2)
    )
    self.assertEqual((trail["from"], trail["to"], trail["kind"], trail["distance"]), ("fuji", "kofu", "trail", 1))
    self.assertEqual((sea_road["kind"], sea_road["terrain"]), ("sea", [["sea", 100]]))
    self.assertEqual(land_road["terrain"], [["plain", 100]])  # 全部が草原の地形
    self.assertEqual(land_road["path"], [[100, 100], [200, 120], [400, 300]])

  def test_json_is_compact_where_it_should_be(self):
    _, _, raw, _ = self.run_main()
    self.assertNotIn("@@", raw)  # 1 行にまとめる仕組みの目印が残らない
    self.assertIn('"path": [[100,100],[200,120],[400,300]]', raw)  # 道の線は 1 行
    self.assertIn('"pos": [', raw)
    self.assertTrue(raw.endswith("}\n"))


class MakeStepsTest(unittest.TestCase):
  """make.py が、手順を決まった順に呼ぶ（各手順は、記録するだけのものに差し替える）。"""

  def fake(self, name, calls, *funcs):
    """funcs（既定は main）を呼ぶと、(モジュール名, 関数名, 引数) を calls に記録するモジュール。"""
    module = mock.MagicMock(name=name)
    for func in funcs or ("main",):
      getattr(module, func).side_effect = lambda *args, _n=name, _f=func: calls.append((_n, _f, args))
    return module

  def test_order_of_steps_and_the_cache(self):
    calls = []
    terrain_mod = self.fake("terrain", calls, "generate")
    terrain_mod.generate.side_effect = lambda: calls.append(("terrain", "generate", ())) or ([["plain"]], [[0.0]])
    terrain_mod.generate.rivers = [[(1, 2)]]
    fakes = {
      "terrain": terrain_mod,
      "roads": self.fake("roads", calls),
      "sea": self.fake("sea", calls),
      "actions": self.fake("actions", calls),
      "vectorize": self.fake("vectorize", calls),
      "build_world": self.fake("build_world", calls),
    }
    with toy_world() as tmp, mock.patch.dict(sys.modules, fakes):
      text = quiet(make.main)
      saved = pickle.loads((tmp / "terrain.pkl").read_bytes())
      base_svg = paths.BASE_SVG
    self.assertEqual(
      calls,
      [
        ("terrain", "generate", ()),
        ("roads", "main", ()),
        ("sea", "main", ()),
        ("actions", "main", ()),
        ("vectorize", "main", (base_svg,)),  # ベースの地図は、.cache に書き出す
        ("build_world", "main", ()),
      ],
    )
    self.assertEqual(
      saved, {"kind": [["plain"]], "height": [[0.0]], "rivers": [[(1, 2)]]}
    )  # 地形・高さ・川を .cache に残す
    for label in ("地形", "陸路", "航路", "行動回数", "SVG", "world/"):
      self.assertIn(label, text)
    self.assertIn("python -m unittest", text)

  def test_step_reports_the_time(self):
    text = quiet(make.step, "手順", lambda: None)
    self.assertRegex(text, r"手順: \d+\.\d 秒")


@unittest.skipUnless(os.environ.get("CRYEPIS_SLOW_TESTS"), "遅いテスト（45 秒ほど）。CRYEPIS_SLOW_TESTS=1 で動かす")
class RegenerationTest(unittest.TestCase):
  """本物の生成を通しで動かして、コミット済みの world/map.json・map-base.svg と完全に一致することを確かめる
  （乱数の種が決まっているので、同じ地図になる）。一時ディレクトリの中だけで動かし、リポジトリのファイルは書き換えない。"""

  SCRIPT = """
import sys
from pathlib import Path
tmp = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
import paths
paths.CACHE = tmp / "cache"; paths.REPO = tmp / "repo"
paths.TERRAIN = paths.CACHE / "terrain.pkl"; paths.ROADS = paths.CACHE / "roads.json"
paths.SEA = paths.CACHE / "sea.json"; paths.BASE_SVG = paths.CACHE / "map-base.svg"
(paths.REPO / "world").mkdir(parents=True); paths.CACHE.mkdir(parents=True)
import make
make.main()
"""

  def test_regenerated_map_is_the_committed_map(self):
    with tempfile.TemporaryDirectory() as tmp:
      done = subprocess.run(
        [sys.executable, "-c", self.SCRIPT, tmp, str(ROOT / "tools" / "worldgen")],
        capture_output=True,
        text=True,
        timeout=600,
      )
      self.assertEqual(done.returncode, 0, done.stderr[-2000:])
      for name in ("map.json", "map-base.svg"):
        regenerated = (Path(tmp) / "repo" / "world" / name).read_bytes()
        committed = (ROOT / "world" / name).read_bytes()
        self.assertEqual(
          regenerated,
          committed,
          f"world/{name} が、tools/worldgen から作り直した結果と違います。"
          "生成元を直したなら python tools/worldgen/make.py で作り直し、world/ の手での編集なら生成元にも反映してください",
        )


if __name__ == "__main__":
  unittest.main()
