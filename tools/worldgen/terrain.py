"""日本の陸（japan.py）に、地形（山・森・平原・湿地・川・湖など）を塗る。

世界の決まり:
- 北ほど寒い（東北の北は針葉樹が多く、九州の南と土佐は照葉樹の森）
- 山並み・火山・平野・川・湖は、実際の位置（経度・緯度）に置く
使い方: python terrain.py OUT.png [OUT_LABELS.svg]（確かめる用の PNG。ふだんは make.py から使う）
"""

import math
import struct
import sys
import zlib

import japan
import land as base

W, H, STEP = base.W, base.H, base.STEP
GW, GH = base.GW, base.GH

COLOR = dict(base.COLOR)
COLOR.update(
  {
    "tundra": (198, 202, 190),
    "taiga": (114, 142, 120),
    "forest": (118, 152, 104),
    "plain": (180, 198, 142),
    "steppe": (198, 198, 150),
    "savanna": (204, 194, 140),
    "desert": (224, 204, 156),
    "badland": (204, 166, 128),
    "jungle": (88, 126, 90),
    "wetland": (140, 164, 140),
    "marshwater": (150, 182, 186),
    "mount": (158, 148, 130),
    "hill": (176, 176, 140),
    "snowcap": (236, 238, 240),
    "volcano": (126, 98, 88),
    "lava": (156, 84, 66),
    "river": (128, 166, 188),
  }
)


def L(*pts):
  """経度・緯度の点の並びを、地図の座標の並びにする。"""
  return [japan.to_map(lon, lat) for lon, lat in pts]


def volcano(name, lon, lat, width):
  return (name, "volcano", L((lon - 0.02, lat - 0.02), (lon + 0.02, lat + 0.02)), width)


WIDEN = 1.5  # 山脈の幅の倍率（火山は除く）
# 山脈: (名前, 種類, 線, 幅)
RANGES = [
  (
    "奥羽山脈",
    "mount",
    L((140.9, 40.9), (140.85, 40.0), (140.75, 39.2), (140.5, 38.4), (140.2, 37.6), (140.0, 37.1)),
    16,
  ),
  ("出羽山地", "mount", L((140.05, 39.1), (140.0, 38.55), (139.85, 38.25)), 13),
  ("北上山地", "hill", L((141.55, 40.2), (141.6, 39.6), (141.45, 39.0)), 18),
  ("阿武隈高地", "hill", L((140.65, 37.6), (140.55, 37.0)), 15),
  ("越後山脈", "mount", L((139.5, 37.4), (139.2, 37.0), (138.85, 36.75)), 15),
  ("飛騨山脈", "mount", L((137.55, 36.85), (137.65, 36.3), (137.55, 35.95)), 15),
  ("木曽山脈", "mount", L((137.85, 35.95), (137.8, 35.55)), 12),
  ("赤石山脈", "mount", L((138.25, 35.85), (138.1, 35.3)), 13),
  ("関東山地", "mount", L((138.75, 36.05), (139.1, 35.85)), 12),
  ("八ヶ岳", "mount", L((138.3, 36.08), (138.4, 35.9)), 14),
  ("筑摩山地", "hill", L((138.0, 36.45), (138.1, 36.1)), 12),
  ("丹沢山地", "hill", L((138.95, 35.5), (139.2, 35.45)), 10),
  ("両白山地", "mount", L((136.75, 36.15), (136.55, 35.85)), 12),
  ("中国山地", "hill", L((131.9, 34.5), (132.6, 34.85), (133.4, 35.15), (134.3, 35.25), (134.9, 35.3)), 18),
  ("紀伊山地", "mount", L((135.75, 34.25), (135.9, 33.95), (135.75, 33.75)), 16),
  ("鈴鹿山脈", "hill", L((136.4, 35.2), (136.35, 34.85)), 10),
  ("四国山地", "mount", L((132.9, 33.7), (133.3, 33.8), (133.8, 33.85), (134.2, 33.85)), 14),
  ("九州山地", "mount", L((131.1, 33.1), (131.2, 32.6), (131.15, 32.2), (130.9, 31.9)), 15),
  ("筑紫山地", "hill", L((130.4, 33.4), (130.8, 33.4)), 10),
  volcano("富士山", 138.73, 35.36, 15),
  volcano("浅間山", 138.52, 36.40, 9),
  volcano("阿蘇山", 131.08, 32.88, 13),
  volcano("桜島", 130.66, 31.58, 8),
  volcano("雲仙岳", 130.29, 32.76, 8),
  volcano("岩木山", 140.30, 40.65, 8),
  volcano("鳥海山", 140.05, 39.10, 9),
  volcano("磐梯山", 140.07, 37.60, 8),
  volcano("霧島山", 130.87, 31.93, 9),
  volcano("大山", 133.54, 35.37, 9),
  volcano("御嶽山", 137.48, 35.89, 8),
]
# 地域: (名前, 地形, 中心, 半径) 後ろほど上に塗る。中心は経度・緯度、半径は px
AREAS = [
  (name, "plain", japan.to_map(*c), r)
  for name, c, r in [
    ("関東平野", (139.75, 36.0), (75, 50)),
    ("濃尾平野", (136.8, 35.25), (35, 25)),
    ("大阪平野", (135.55, 34.7), (22, 18)),
    ("越後平野", (139.1, 37.8), (35, 22)),
    ("仙台平野", (140.95, 38.25), (20, 30)),
    ("庄内平野", (139.9, 38.85), (15, 22)),
    ("秋田平野", (140.1, 39.7), (15, 20)),
    ("津軽平野", (140.4, 40.8), (18, 22)),
    ("筑紫平野", (130.4, 33.25), (25, 15)),
    ("熊本平野", (130.75, 32.8), (20, 15)),
    ("宮崎平野", (131.4, 32.05), (15, 22)),
    ("岡山平野", (133.9, 34.65), (25, 12)),
    ("高知平野", (133.55, 33.6), (18, 10)),
    ("讃岐平野", (133.9, 34.25), (25, 10)),
    ("加賀平野", (136.55, 36.5), (15, 18)),
    ("富山平野", (137.2, 36.7), (20, 14)),
    ("甲府盆地", (138.55, 35.65), (14, 10)),
    ("松本盆地", (137.95, 36.25), (10, 16)),
    ("奈良盆地", (135.8, 34.6), (10, 14)),
    ("京都盆地", (135.75, 35.0), (9, 9)),
  ]
] + [
  # 豪雪の土地（冬は雪に埋もれる）
  ("北信濃の雪原", "tundra", japan.to_map(138.15, 36.75), (30, 26)),
  ("越後の雪原", "tundra", japan.to_map(138.6, 37.25), (34, 22)),
  ("南九州の照葉樹林", "jungle", japan.to_map(131.0, 31.6), (45, 35)),
  ("土佐の照葉樹林", "jungle", japan.to_map(133.6, 33.3), (40, 14)),
]
# 川: 山から海・湖へ
RIVERS = [
  L(*r)
  for r in [
    [(139.0, 36.75), (139.4, 36.2), (139.8, 36.05), (140.4, 35.9), (140.85, 35.73)],  # 利根川
    [(138.3, 36.2), (138.25, 36.65), (138.6, 37.1), (138.85, 37.55), (139.05, 37.93)],  # 信濃川
    [(140.1, 37.95), (140.3, 38.4), (140.25, 38.75), (139.85, 38.9)],  # 最上川
    [(141.15, 40.1), (141.1, 39.3), (141.25, 38.6), (141.3, 38.43)],  # 北上川
    [(139.9, 37.5), (139.3, 37.75), (139.2, 37.95)],  # 阿賀野川
    [(137.6, 35.85), (137.1, 35.45), (136.7, 35.05)],  # 木曽川
    [(137.95, 36.0), (137.85, 35.3), (137.8, 34.67)],  # 天竜川
    [(135.9, 34.95), (135.6, 34.85), (135.45, 34.7)],  # 淀川
    [(131.0, 33.3), (130.55, 33.3), (130.35, 33.15)],  # 筑後川
    [(133.6, 33.85), (134.1, 34.05), (134.6, 34.07)],  # 吉野川
    [(133.0, 33.3), (132.85, 33.1), (132.95, 32.93)],  # 四万十川
    [(130.95, 32.25), (130.6, 32.5)],  # 球磨川
  ]
]
# 説明用の名前（地図そのものには描かない）
LABELS = (
  [
    (n, japan.to_map(*c), s)
    for n, c, s in [("本州", (137.5, 36.3), 26), ("四国", (133.5, 33.7), 20), ("九州", (130.9, 32.5), 20)]
  ]
  + [(n, c, 12) for n, _, c, _ in AREAS]
  + [(n, line[len(line) // 2], 12) for n, _, line, _ in RANGES]
)


def seg_dist(px, py, ax, ay, bx, by):
  vx, vy = bx - ax, by - ay
  t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy or 1)))
  return math.hypot(px - ax - t * vx, py - ay - t * vy)


def stamp_lines(grid, lines, width_of, value_of, noise):
  """線のまわり（幅つき）に値を書く。value_of(t) は 0〜1 の近さを受け取る。"""
  for line, width, tag in lines:
    for (ax, ay), (bx, by) in zip(line, line[1:]):
      x0, x1 = min(ax, bx) - width * 1.6, max(ax, bx) + width * 1.6
      y0, y1 = min(ay, by) - width * 1.6, max(ay, by) + width * 1.6
      for j in range(max(0, int(y0) // STEP), min(GH, int(y1) // STEP + 1)):
        y = j * STEP + 1
        for i in range(max(0, int(x0) // STEP), min(GW, int(x1) // STEP + 1)):
          x = i * STEP + 1
          wob = 1 + 0.55 * noise.fbm(x / 110, y / 110, 3) + 0.35 * noise.fbm(x / 28, y / 28 + 5, 2)
          d = seg_dist(x, y, ax, ay, bx, by) / (width * wob)
          if d < 1:
            v = value_of(1 - d)
            if grid[j][i] is None or grid[j][i][0] < v:
              grid[j][i] = (v, tag)


def generate():
  elev, kind = japan.generate()  # 陸と海
  n1, n2, n3 = base.Noise("terrain-1"), base.Noise("terrain-2"), base.Noise("terrain-3")

  # 山（値は高さ 0〜1）
  mount = [[None] * GW for _ in range(GH)]
  stamp_lines(
    mount, [(line, w * (1 if t == "volcano" else WIDEN), t) for _, t, line, w in RANGES], None, lambda c: c**0.8, n1
  )
  # 川（細い線）
  river = [[None] * GW for _ in range(GH)]
  wiggled = []
  for line in RIVERS:
    pts = []
    for (ax, ay), (bx, by) in zip(line, line[1:]):
      for s in range(8):
        t = s / 8
        x, y = ax + (bx - ax) * t, ay + (by - ay) * t
        pts.append((x + n2.fbm(x / 40, y / 40) * 10, y + n2.fbm(x / 40 + 9, y / 40) * 10))
    pts.append(line[-1])
    wiggled.append((pts, 2.2, "river"))
  stamp_lines(river, wiggled, None, lambda c: c, base.Noise("flat"))
  lake = japan.raster(japan.LAKES)

  out = [[None] * GW for _ in range(GH)]
  height = [[0.0] * GW for _ in range(GH)]
  for j in range(GH):
    y = j * STEP + 1
    for i in range(GW):
      x = i * STEP + 1
      k = kind[j][i]
      if k != "land":
        out[j][i] = k  # 海と砂浜
        continue
      # 緯度の気候（北ほど寒い）
      lat = japan.from_map(x, y)[1] + n1.fbm(x / 120, y / 120 + 3, 3) * 0.8
      patch = n3.fbm(x / 80, y / 80, 4)
      if lat > 39.5:
        b = "taiga" if patch > 0.25 else "forest"
      elif lat > 32.0:
        b = "forest" if patch > -0.05 else "plain"
      else:
        b = "jungle" if patch > -0.1 else "forest"
      # 地域
      for _, kind_a, (cx, cy), (rx, ry) in AREAS:
        q = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
        q += n2.fbm(x / 110 + cx, y / 110, 4) * 0.9 + n2.fbm(x / 30, y / 30 + cy, 2) * 0.3
        if q < 1:
          b = kind_a
      # 山
      m = mount[j][i]
      if m:
        h, tag = m
        ridge = 1 - abs(n1.fbm(x / 30, y / 30 + 11, 3))
        h = h * (0.3 + 0.7 * ridge)
        height[j][i] = h
        if tag == "hill":
          if h > 0.3:
            b = "hill"
        elif tag == "volcano":
          if h > 0.15:
            b = "snowcap" if h > 0.85 and lat > 35 else "volcano"
        elif h > 0.16:
          b = "snowcap" if h > (0.6 if lat > 38 else 0.7) else "mount"
      if lake[j][i]:
        b = "shallow"  # 湖
      elif river[j][i] and b != "snowcap":
        b = "river"
      out[j][i] = b
  generate.rivers = [pts for pts, _, _ in wiggled]  # 川の線（ベクター化で使う）
  return out, height


def write_png(path, kind, height):
  palette, rows = {}, []
  for j in range(GH):
    row = bytearray()
    for i in range(GW):
      k = kind[j][i]
      s = 1.0
      if k in ("mount", "snowcap", "hill", "volcano", "lava"):
        g = (height[j][i] - height[max(j - 1, 0)][max(i - 1, 0)]) * 9
        s = (0.84, 0.92, 1.0, 1.08)[max(0, min(3, int(round(2 + g))))]
      c = tuple(max(0, min(255, int(v * s))) for v in COLOR[k])
      idx = palette.setdefault(c, len(palette))
      row += bytes([idx, idx])
    rows.append(bytes(row))
  raw = b"".join(b"\x00" + r + b"\x00" + r for r in rows)

  def chunk(t, data):
    c = struct.pack(">I", len(data)) + t + data
    return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)

  plte = b"".join(bytes(c) for c in sorted(palette, key=palette.get))
  png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 3, 0, 0, 0))
    + chunk(b"PLTE", plte)
    + chunk(b"IDAT", zlib.compress(raw, 9))
    + chunk(b"IEND", b"")
  )
  with open(path, "wb") as f:
    f.write(png)
  return len(png), len(palette)


def write_labels(path, png_name):
  texts = "".join(f'<text x="{x}" y="{y}" font-size="{s}" text-anchor="middle">{n}</text>' for n, (x, y), s in LABELS)
  svg = (
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
    f'<image href="{png_name}" width="{W}" height="{H}"/>'
    f'<g font-family="sans-serif" font-weight="bold" fill="#2b2b2b" stroke="#fff" '
    f'stroke-width="3" paint-order="stroke">{texts}</g></svg>'
  )
  with open(path, "w", encoding="utf-8") as f:
    f.write(svg)


if __name__ == "__main__":
  kind, height = generate()
  size, colors = write_png(sys.argv[1], kind, height)
  print(f"{sys.argv[1]}: {size // 1024}KB {colors} colors")
  if len(sys.argv) > 2:
    import os

    write_labels(sys.argv[2], os.path.basename(sys.argv[1]))
