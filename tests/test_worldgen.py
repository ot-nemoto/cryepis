"""地図の生成（tools/worldgen）のテスト。地形の生成（1 分ほどかかる）や .cache/ は使わず、次を確かめる。
- 町・霊場・名前・説明・店などの表どうしの整合性と、コミット済みの world/map.json との一致
- 小さな入力で確かめられる関数（行動回数の換算・線の間引き・ダイクストラ・地形の割合・図形・ノイズ）
地形を読み込むモジュール（roads・sea・build_world・vectorize）は、小さな地形（TOY）を使って読み込む。"""

import json
import math
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(
  str(ROOT / "tools" / "worldgen")
)  # 名前が一般的（names・paths・land など）なので、ほかより先には探さない
import actions  # noqa: E402
import land  # noqa: E402
import lore  # noqa: E402
import names  # noqa: E402
import paths  # noqa: E402
import places  # noqa: E402
import terrain  # noqa: E402
import world as W  # noqa: E402

# 小さな地形（[y][x]）。左の 3 列は海、右は陸（上の行は草原、下の行は森）
TOY_W, TOY_H = 12, 8
TOY_KIND = [["sea"] * 3 + (["plain"] * 9 if j < 4 else ["forest"] * 9) for j in range(TOY_H)]
TOY_HEIGHT = [[0.5] * TOY_W for _ in range(TOY_H)]


def import_with_toy_terrain():
  """地形のファイル（.cache/terrain.pkl）を読み込むモジュールを、小さな地形で読み込む（なければ、あとで .cache/ を探さない）。"""
  original, recursion = paths.TERRAIN, sys.getrecursionlimit()
  with tempfile.TemporaryDirectory() as tmp:
    toy = Path(tmp) / "terrain.pkl"
    toy.write_bytes(pickle.dumps({"kind": TOY_KIND, "height": TOY_HEIGHT, "rivers": []}))
    paths.TERRAIN = toy
    try:
      import build_world
      import roads
      import sea
      import vectorize
    finally:
      paths.TERRAIN = original
      sys.setrecursionlimit(recursion)  # vectorize は、読み込むときに再帰の上限を上げる（ほかのテストに持ち込まない）
  return build_world, roads, sea, vectorize


build_world, roads, sea, vectorize = import_with_toy_terrain()
MAP = json.loads((ROOT / "world" / "map.json").read_text(encoding="utf-8"))
MAP_PLACES = {p["id"]: p for p in MAP["places"]}


class TablesTest(unittest.TestCase):
  """表どうしが食い違わない（町・霊場を 1 つ足したとき、書き忘れた表が見つかる）。"""

  TOWN_NAMES = [n for n, _, _ in places.TOWNS]
  DUNGEON_NAMES = [n for n, _, _ in places.DUNGEONS]
  ALL = TOWN_NAMES + DUNGEON_NAMES

  def test_names_are_unique_and_positions_are_inside_the_map(self):
    self.assertEqual(len(self.ALL), len(set(self.ALL)))
    w, h = MAP["layout"]["size"]
    spots = [(x, y) for _, x, y in places.TOWNS + places.DUNGEONS]
    self.assertEqual(len(spots), len(set(spots)))  # 同じ座標に 2 つ置かない
    for x, y in spots:
      self.assertTrue(0 <= x < w and 0 <= y < h, (x, y))

  def test_every_town_has_its_tables(self):
    towns = set(self.TOWN_NAMES)
    for label, table in (
      ("NAMES", names.NAMES),
      ("TEXT", lore.TEXT),
      ("TOWN_INFO", build_world.TOWN_INFO),
      ("QUESTS_PER_DAY", build_world.QUESTS_PER_DAY),
    ):
      self.assertEqual(set(table), towns, label)  # 足りない町も、余分な町もない

  def test_every_dungeon_has_its_tables(self):
    dungeons = set(self.DUNGEON_NAMES)
    self.assertEqual(set(build_world.DUNGEON_INFO), dungeons)
    self.assertEqual(set(lore.DUNGEON_EN), dungeons)

  def test_ids_are_unique(self):
    ids = [i for i, *_ in build_world.TOWN_INFO.values()] + [i for i, _ in build_world.DUNGEON_INFO.values()]
    self.assertEqual(len(ids), len(set(ids)))
    for pid in ids:
      self.assertRegex(pid, r"^[a-z][a-z_]*$")

  def test_areas_contain_every_place_exactly_once(self):
    members = [m for _, _, _, ms in lore.AREAS for m in ms]
    self.assertEqual(sorted(members), sorted(self.ALL))
    self.assertEqual(len({a for a, *_ in lore.AREAS}), len(lore.AREAS))  # 地方の ID は重ならない

  def test_sea_routes_connect_towns(self):
    towns = set(self.TOWN_NAMES)
    for a, b in places.SEA_ROUTES:
      self.assertTrue(a in towns and b in towns and a != b, (a, b))

  def test_routes_connect_known_places(self):
    towns, dungeons = set(self.TOWN_NAMES), set(self.DUNGEON_NAMES)
    for a, b in places.LAND_ROADS:
      self.assertTrue(a in towns and b in towns and a != b, (a, b))
    for d, t in places.TRAILS:
      self.assertTrue(d in dungeons and t in towns, (d, t))
    self.assertEqual({d for d, _ in places.TRAILS}, dungeons)  # どの霊場にも道がある
    pairs = [frozenset(p) for p in places.LAND_ROADS + places.TRAILS + places.SEA_ROUTES]
    self.assertEqual(len(pairs), len(set(pairs)))  # 同じ道を 2 度書かない

  def test_labels_and_open_towns(self):
    for name, side in places.LABEL.items():
      self.assertIn(name, self.ALL)
      self.assertIn(side, W.LABEL_SIDES)
    for name in (*build_world.OPEN, *build_world.STARTS):
      self.assertIn(name, self.TOWN_NAMES)
    self.assertTrue(set(build_world.STARTS) <= set(build_world.OPEN))  # 最初の町は開放した町から

  def test_guild_sizes_are_in_range(self):
    self.assertTrue(all(1 <= n <= 50 for n in build_world.QUESTS_PER_DAY.values()))

  def test_shops_sell_known_items(self):
    items = json.loads((ROOT / "world" / "items.json").read_text(encoding="utf-8"))
    ids = {i["id"] for i in items["items"]}
    self.assertTrue(set(build_world.SHOP) <= ids)


class CommittedMapTest(unittest.TestCase):
  """作り直した結果が、コミット済みの world/map.json と一致する（map.json を手で直したら、生成元も直す）。"""

  def test_places_match(self):
    built = build_world.build_places()
    self.assertEqual([p["id"] for p in built], [p["id"] for p in MAP["places"]])  # 並びも同じ
    for p in built:
      self.assertEqual(p, MAP_PLACES[p["id"]], p["id"])

  def test_areas_open_towns_and_layout_match(self):
    self.assertEqual(
      MAP["areas"],
      [
        {"id": a, "name": n, "en": en}
        | (
          {"stone": build_world.AREA_STONES[a][0], "stone_effect": build_world.AREA_STONES[a][1]}
          if a in build_world.AREA_STONES
          else {}
        )
        for a, n, en, _ in lore.AREAS
      ],
    )
    self.assertEqual(MAP["open"], [build_world.pid(n) for n in build_world.OPEN])
    self.assertEqual(MAP["starts"], [build_world.pid(n) for n in build_world.STARTS])
    self.assertEqual(MAP["layout"], {"size": [1600, 1000], "background": "map-base.svg"})

  def test_roads_match_the_route_tables(self):
    """道の端点は表の町・霊場・航路と合う（道の線そのものは、地形から作るので、ここでは見ない）。"""
    pid = build_world.pid
    sea_roads = {frozenset((r["from"], r["to"])) for r in MAP["roads"] if r["kind"] == "sea"}
    self.assertEqual(sea_roads, {frozenset((pid(a), pid(b))) for a, b in places.SEA_ROUTES})
    land_roads = {frozenset((r["from"], r["to"])) for r in MAP["roads"] if r["kind"] != "sea"}
    self.assertEqual(land_roads, {frozenset((pid(a), pid(b))) for a, b in places.LAND_ROADS + places.TRAILS})
    dungeons = {pid(n) for n, _, _ in places.DUNGEONS}
    for r in MAP["roads"]:
      self.assertEqual(r["kind"] == "trail", bool({r["from"], r["to"]} & dungeons), r)  # 霊場への道だけ trail
    self.assertEqual(len(MAP["roads"]), len({frozenset((r["from"], r["to"])) for r in MAP["roads"]}))  # 重複なし


class TownTextTest(unittest.TestCase):
  """町の説明（lore.TEXT）は、サイトの町のページと docs/WORLD.md の町の一覧に出る。"""

  def test_texts_are_short_single_lines(self):
    for name, text in lore.TEXT.items():
      self.assertTrue(text.strip() == text and "\n" not in text, name)
      self.assertTrue(20 <= len(text) <= 120, (name, len(text)))  # 1〜2 文。町のカードに収まる長さ

  def test_texts_do_not_use_game_words(self):
    for name, text in lore.TEXT.items():
      for banned in ("シーズン", "レベル", "もののふ"):
        self.assertNotIn(banned, text, name)

  def test_world_md_lists_the_same_texts(self):
    """docs/WORLD.md の町の一覧の「特徴」が、lore.TEXT と同じ（直したら、両方を直す）。"""
    doc = (ROOT / "docs" / "WORLD.md").read_text(encoding="utf-8")
    for name, text in lore.TEXT.items():
      row = next((line for line in doc.splitlines() if line.startswith("| ") and f"| {name}（" in line), None)
      self.assertIsNotNone(row, name)
      self.assertTrue(row.rstrip().endswith(f"| {text} |"), name)


class ActionsTest(unittest.TestCase):
  """移動日数＝行動回数（1 日に進む距離で割って四捨五入）。"""

  def test_land_actions(self):
    self.assertEqual([actions.land(km) for km in (0, 10, 36, 53, 55, 72, 289)], [1, 1, 1, 1, 2, 2, 8])  # 最低 1 回
    self.assertEqual([actions.land(km) for km in (90, 162)], [3, 5])  # ちょうど .5 は切り上げ（四捨五入）

  def test_trail_actions_are_slower(self):
    self.assertEqual([actions.trail(km) for km in (0, 20, 45, 50, 100)], [1, 1, 2, 3, 5])
    self.assertLess(actions.TRAIL_KM, actions.LAND_KM)  # 山道は遅い

  def test_sea_actions_have_a_minimum_for_boarding(self):
    self.assertEqual(actions.sea(0), actions.SEA_MIN)
    self.assertEqual(actions.sea(140), 2)
    self.assertEqual(actions.sea(565), 8)
    self.assertEqual(actions.sea(175), 3)  # 2.5 日は 3 回
    self.assertGreater(actions.SEA_KM, actions.LAND_KM)  # 船は速い


class SimplifyTest(unittest.TestCase):
  def test_collinear_points_are_dropped(self):
    pts = [(0, 0), (5, 0), (10, 0), (20, 0)]
    self.assertEqual(roads.simplify(pts), [(0, 0), (20, 0)])

  def test_corners_are_kept(self):
    pts = [(0, 0), (10, 0), (10, 10)]
    self.assertEqual(roads.simplify(pts), pts)

  def test_small_wiggles_are_dropped_but_big_ones_are_kept(self):
    self.assertEqual(roads.simplify([(0, 0), (5, 1), (10, 0)], eps=2.5), [(0, 0), (10, 0)])
    self.assertEqual(roads.simplify([(0, 0), (5, 4), (10, 0)], eps=2.5), [(0, 0), (5, 4), (10, 0)])

  def test_short_lists_are_returned_as_they_are(self):
    self.assertEqual(roads.simplify([(1, 1)]), [(1, 1)])
    self.assertEqual(roads.simplify([(1, 1), (2, 2)]), [(1, 1), (2, 2)])

  def test_endpoints_never_move(self):
    pts = [(0, 0), (3, 2), (6, 5), (9, 4), (14, 9), (20, 10)]
    out = roads.simplify(pts, eps=1.0)
    self.assertEqual((out[0], out[-1]), (pts[0], pts[-1]))
    self.assertTrue(set(out) <= set(pts))


class DijkstraTest(unittest.TestCase):
  """roads.dijkstra は、モジュールの地形（kind）の上で道を探す。小さな地形に差し替えて確かめる。"""

  def setUp(self):
    self.saved = (roads.kind, roads.GH, roads.GW)
    self.addCleanup(lambda: setattr(roads, "kind", self.saved[0]))
    self.addCleanup(lambda: (setattr(roads, "GH", self.saved[1]), setattr(roads, "GW", self.saved[2])))

  def use(self, grid):
    roads.kind, roads.GH, roads.GW = grid, len(grid), len(grid[0])

  def test_cost_of_terrain(self):
    self.use([["plain", "mount", "sea"]])
    self.assertEqual((roads.cost(0, 0), roads.cost(1, 0)), (1, 7))
    self.assertIsNone(roads.cost(2, 0))  # 海は通れない

  def test_straight_line_on_plain(self):
    self.use([["plain"] * 6])
    dist, prev = roads.dijkstra(1, 1, [(5, 0)])
    self.assertAlmostEqual(dist[(5, 0)], 5.0)
    p = roads.path(prev, (0, 0), (5, 0))
    self.assertEqual((p[0], p[-1]), ((1, 1), (11, 1)))  # マスの中心（STEP の倍数 + 1）
    self.assertEqual(len(p), 6)

  def test_goes_around_water_and_avoids_mountains(self):
    # 真ん中の行は [草原, 海, 山, 海, 草原]。海は通れず、山は高くつくので、上の行の草原を回る
    grid = [["plain"] * 5, ["plain", "sea", "mount", "sea", "plain"], ["plain"] * 5]
    self.use(grid)
    dist, prev = roads.dijkstra(1, 3, [(4, 1)])
    route = [(4, 1)]
    while route[-1] != (0, 1):
      route.append(prev[route[-1]])
    self.assertNotIn((2, 1), route)  # 山のマスは通らない
    self.assertTrue(all(grid[j][i] != "sea" for i, j in route))
    self.assertAlmostEqual(dist[(4, 1)], 2 * 1.4142 + 2)  # 斜めに上がって、草原を 2 マス、斜めに降りる

  def test_unreachable_target_is_missing(self):
    self.use([["plain", "sea", "plain"]])
    dist, _ = roads.dijkstra(1, 1, [(2, 0)])
    self.assertNotIn((2, 0), dist)  # 海に隔てられている

  def test_diagonal_steps_cost_more(self):
    self.use([["plain"] * 3 for _ in range(3)])
    dist, _ = roads.dijkstra(1, 1, [(2, 2)])
    self.assertAlmostEqual(dist[(2, 2)], 2 * 1.4142)


class TerrainMixTest(unittest.TestCase):
  def setUp(self):
    self.saved = build_world.kind
    self.addCleanup(lambda: setattr(build_world, "kind", self.saved))

  def test_mix_counts_the_land_around_a_road_and_drops_small_shares(self):
    # 全部が草原 → plain 100%
    build_world.kind = [["plain"] * 400 for _ in range(250)]
    self.assertEqual(build_world.terrain_mix([(200, 200), (400, 200)]), [["plain", 100]])

  def test_mix_is_sorted_by_share(self):
    # y が 240 未満（マスの 120 行より上）は森、それより下は草原。道は y=230 を通るので、まわりは森のほうが多い
    build_world.kind = [["forest"] * 400 if j < 120 else ["plain"] * 400 for j in range(250)]
    mix = build_world.terrain_mix([(200, 230), (400, 230)])
    self.assertEqual([k for k, _ in mix][0], "forest")
    self.assertAlmostEqual(sum(v for _, v in mix), 100, delta=2)
    self.assertTrue(all(v >= 10 for _, v in mix))  # 10% 未満は捨てる

  def test_unknown_terrain_falls_back_to_plain(self):
    build_world.kind = [["sea"] * 400 for _ in range(250)]  # 物の怪の地形に当たらない
    self.assertEqual(build_world.terrain_mix([(200, 200), (300, 200)]), [["plain", 100]])

  def test_terrain_cells_map_to_monster_terrains(self):
    self.assertTrue(set(build_world.TERRAIN.values()) <= set(W.TERRAINS))  # 物の怪の地形（world.py の TERRAINS）にある


class GeometryTest(unittest.TestCase):
  def test_segment_distance(self):
    self.assertAlmostEqual(terrain.seg_dist(5, 3, 0, 0, 10, 0), 3)  # 線分の真上
    self.assertAlmostEqual(terrain.seg_dist(-3, 4, 0, 0, 10, 0), 5)  # 端の外は端点からの距離
    self.assertAlmostEqual(terrain.seg_dist(13, 4, 0, 0, 10, 0), 5)
    self.assertAlmostEqual(terrain.seg_dist(3, 4, 0, 0, 0, 0), 5)  # 長さ 0 の線分は点

  def test_polygon_area(self):
    self.assertAlmostEqual(vectorize.area([(0, 0), (4, 0), (4, 3), (0, 3)]), 12)
    self.assertAlmostEqual(vectorize.area([(0, 0), (4, 0), (0, 3)]), 6)

  def test_dp_keeps_corners(self):
    self.assertEqual(vectorize.dp([(0, 0), (5, 0), (10, 0)], 0.5), [(0, 0), (10, 0)])
    self.assertEqual(vectorize.dp([(0, 0), (5, 5), (10, 0)], 0.5), [(0, 0), (5, 5), (10, 0)])
    self.assertEqual(vectorize.dp([(1, 1), (1, 1), (1, 1)], 0.5), [(1, 1), (1, 1)])  # 始点と終点が同じでも落ちない

  def test_colour_to_hex_is_clamped(self):
    self.assertEqual(vectorize.hexc((178, 198, 142)), "#b2c68e")
    self.assertEqual(vectorize.hexc((200, 200, 200), 2.0), "#ffffff")  # 255 で頭打ち
    self.assertEqual(vectorize.hexc((10, 10, 10), -1), "#000000")  # 0 で下げ止まり

  def test_path_d_is_relative_and_skips_repeated_points(self):
    d = vectorize.path_d([[(10, 10), (14, 10), (14, 10.2), (14, 16)]])
    self.assertEqual(d, "M10 10l4 0 0 6z")

  def test_loops_trace_the_edge_of_a_mask(self):
    grid = [[0, 0, 0, 0], [0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]]
    saved = vectorize.GH, vectorize.GW
    vectorize.GH, vectorize.GW = len(grid), len(grid[0])
    try:
      out = vectorize.loops(grid)
    finally:
      vectorize.GH, vectorize.GW = saved
    self.assertEqual(len(out), 1)
    self.assertEqual(set(out[0]), {(1, 1), (2, 1), (3, 1), (3, 2), (3, 3), (2, 3), (1, 3), (1, 2)})  # 2x2 の外周 8 点
    self.assertAlmostEqual(vectorize.area(out[0]), 4)


class LandTest(unittest.TestCase):
  def test_noise_is_repeatable_and_bounded(self):
    a, b = land.Noise("seed"), land.Noise("seed")
    self.assertEqual([a.at(x * 0.7, x * 1.3) for x in range(20)], [b.at(x * 0.7, x * 1.3) for x in range(20)])
    self.assertNotEqual(land.Noise("other").at(1.5, 2.5), a.at(1.5, 2.5))
    for x in range(50):
      self.assertTrue(-1 <= a.at(x * 0.37, x * 0.91) <= 1)
      self.assertTrue(-1 <= a.fbm(x * 0.37, x * 0.91) <= 1)

  def test_noise_wraps_around(self):
    n = land.Noise("wrap", size=8)
    self.assertAlmostEqual(n.at(1.25, 2.5), n.at(1.25 + 8, 2.5 + 8))

  def test_sector_of_covers_the_circle(self):
    self.assertEqual(land.sector_of(-90), "北")
    self.assertEqual(land.sector_of(90), "南")
    self.assertEqual(land.sector_of(30), "南東")
    self.assertEqual(land.sector_of(270), "北")  # 一周して同じ向き
    self.assertEqual({land.sector_of(a) for a in range(0, 360, 5)}, set(land.SECTORS))  # どの扇形も現れる

  def test_band_of_is_by_distance(self):
    self.assertEqual([land.band_of(d) for d in (500, 400, 300, 250, 120, 100, 0)], [0, 0, 1, 2, 3, 4, 4])
    self.assertEqual(land.band_of(10_000), 0)
    # 境目の距離そのものは、内側の帯に入る（edge を超えたら外の帯）
    for band, edge in enumerate(land.EDGES[1:]):
      self.assertEqual(land.band_of(edge + 1), band, edge)
      self.assertEqual(land.band_of(edge), band + 1 if band + 1 < 4 else 4, edge)
    self.assertEqual(len(land.EDGES), 5)
    self.assertEqual(land.EDGES, sorted(land.EDGES, reverse=True))


class ToyTerrainTest(unittest.TestCase):
  def test_modules_loaded_the_toy_terrain_not_the_cache(self):
    for m in (roads, sea):
      self.assertEqual((m.GH, m.GW), (TOY_H, TOY_W))
    self.assertEqual(vectorize.GW, TOY_W)
    self.assertTrue(all(math.isfinite(v) for row in vectorize.height for v in row))
    self.assertEqual(paths.TERRAIN.name, "terrain.pkl")  # 元に戻してある
    self.assertEqual(paths.TERRAIN.parent, paths.CACHE)
    self.assertLess(sys.getrecursionlimit(), 100000)  # vectorize が上げた再帰の上限も、元に戻してある


if __name__ == "__main__":
  unittest.main()
