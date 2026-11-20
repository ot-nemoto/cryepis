"""ベースの地図（地形の絵）を機械的に作る試作 その2（リアルな世界地図ふう）。

- 座標をゆがめたノイズで大陸・島を作り、陸の割合を LAND_RATIO に合わせる
- 地図の端ほど海になりやすく、中央の大陸は陸になりやすい
- 地形の色は、方角（6 地域）と難易度の帯、高さで決める
使い方: python land.py OUT.png [SEED] [LAND_RATIO]（陸と海だけの PNG。地形を選び直すとき用）
"""

import math
import random
import struct
import sys
import zlib

W, H = 1600, 1000
FLAT = True  # 地形の色分け・陰影をせず、陸は一色にする
STEP = 2
GW, GH = W // STEP, H // STEP
CX, CY, SX = 800, 500, 1.6
EDGES = [490, 380, 285, 200, 115]


class Noise:
  def __init__(self, seed, size=64):
    rng = random.Random(seed)
    self.size = size
    self.v = [[rng.uniform(-1, 1) for _ in range(size)] for _ in range(size)]

  def at(self, x, y):
    n = self.size
    xi, yi = math.floor(x), math.floor(y)
    fx, fy = x - xi, y - yi
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    x0, y0, x1, y1 = xi % n, yi % n, (xi + 1) % n, (yi + 1) % n
    v = self.v
    a = v[y0][x0] + (v[y0][x1] - v[y0][x0]) * fx
    b = v[y1][x0] + (v[y1][x1] - v[y1][x0]) * fx
    return a + (b - a) * fy

  def fbm(self, x, y, octaves=4, gain=0.5):
    total, amp, norm = 0.0, 1.0, 0.0
    for i in range(octaves):
      total += self.at(x + i * 17.3, y + i * 9.1) * amp
      norm += amp
      amp *= gain
      x, y = (x * 0.8 - y * 0.6) * 2.03, (x * 0.6 + y * 0.8) * 2.03
    return total / norm


SECTORS = {"北": -90, "北東": -30, "南東": 30, "南": 90, "南西": 150, "北西": 210}
BIOME = {
  "北": ["meadow", "grass", "mountain", "sky"],
  "北東": ["hill", "hill", "snow", "glacier"],
  "南東": ["coast", "coast", "jungle", "reef"],
  "南": ["marsh", "marsh", "miasma", "miasma"],
  "南西": ["waste", "desert", "desert", "volcano"],
  "北西": ["forest", "forest", "jungle", "dragon"],
}
COLOR = {
  "meadow": (178, 198, 142),
  "grass": (160, 186, 126),
  "mountain": (152, 142, 122),
  "sky": (206, 208, 222),
  "hill": (170, 178, 130),
  "snow": (224, 230, 234),
  "glacier": (200, 216, 226),
  "coast": (186, 196, 148),
  "reef": (170, 186, 150),
  "marsh": (136, 160, 132),
  "miasma": (126, 110, 132),
  "waste": (190, 170, 128),
  "desert": (218, 198, 150),
  "volcano": (118, 86, 76),
  "forest": (116, 152, 104),
  "jungle": (86, 124, 88),
  "dragon": (110, 92, 88),
  "dark": (80, 74, 92),
  "peak": (128, 120, 116),
  "beach": (222, 212, 172),
  "ice": (234, 240, 244),
  "shallow": (152, 186, 200),
  "sea": (116, 154, 176),
  "deep": (92, 128, 156),
  "abyss": (78, 108, 138),
  "land": (184, 194, 150),
}
SHADES = [0.86, 0.94, 1.0, 1.06]


def sector_of(angle):
  best, name = 999, None
  for s, a in SECTORS.items():
    diff = abs((angle - a + 180) % 360 - 180)
    if diff < best:
      best, name = diff, s
  return name


def band_of(d):
  for i, edge in enumerate(EDGES[1:]):
    if d > edge:
      return i
  return 4


def generate(seed, land_ratio):
  n_elev, n_wx, n_wy, n_ang, n_d, n_ridge = (Noise(f"{seed}-{k}") for k in "exyadr")
  elev = [[0.0] * GW for _ in range(GH)]
  info = [[None] * GW for _ in range(GH)]
  for j in range(GH):
    y = j * STEP + 1
    for i in range(GW):
      x = i * STEP + 1
      # 座標をゆがめてから高さを取ると、大陸の形が自然になる
      wx = x + n_wx.fbm(x / 340, y / 340, 4) * 170
      wy = y + n_wy.fbm(x / 340, y / 340, 4) * 120
      e = n_elev.fbm(wx / 300, wy / 300, 6, 0.52)

      dx, dy = (x - CX) / SX, y - CY
      r = math.hypot(dx, dy)
      d = (
        r * (1 + n_d.fbm(x / 260, y / 260, 4) * 0.22)
        + n_d.fbm(x / 90, y / 90 + 9, 4) * 32
        + n_d.fbm(x / 22, y / 22 + 30, 2) * 7
      )
      ang = (
        math.degrees(math.atan2(dy, dx))
        + n_ang.fbm(x / 190 + 5, y / 190, 5) * 50
        + n_ang.fbm(x / 25, y / 25 + 40, 2) * 6
      )
      band, sec = band_of(d), sector_of(ang)

      # 端は海に、中央は陸になりやすくする
      ex, ey = min(x, W - x) / 260, min(y, H - y) / 150
      e -= 0.45 * max(0, 1 - ex) ** 2 + 0.3 * max(0, 1 - ey) ** 2
      e += 0.18 * max(0, 1 - r / 330)
      if band == 4:
        e += 0.25
      # 南東の内側は深い海になりやすい
      if sec == "南東" and band in (2, 3):
        e -= 0.18
      # 中央を囲む山脈（ところどころ切れる）
      ring = abs(d - 132)
      ridge = 1 - abs(n_ridge.fbm(x / 60, y / 60, 3))
      width = 16 + 10 * n_ridge.fbm(x / 50 + 7, y / 50, 3)
      mount = 0.0
      if ring < width and n_ridge.fbm(ang / 14, 3.3, 2) > -0.25:
        mount = (1 - ring / width) * (0.6 + 0.4 * ridge)
      elev[j][i] = e
      pole = ey + n_wy.fbm(x / 220, y / 90 + 20, 3) * 0.45 + n_wy.fbm(x / 35, y / 35 + 50, 2) * 0.08
      # 海の深さは細かい起伏を除いた高さで決める
      sea_e = n_elev.fbm(wx / 300, wy / 300, 3, 0.52)
      info[j][i] = (band, sec, ridge, mount, pole, sea_e - e)

  # 陸の割合を合わせる
  flat = sorted(v for row in elev for v in row)
  thr = flat[int(len(flat) * (1 - land_ratio))]

  kind = [[None] * GW for _ in range(GH)]
  for j in range(GH):
    for i in range(GW):
      h = elev[j][i] - thr
      band, sec, ridge, mount, pole, smooth = info[j][i]
      if h < 0 and pole < 0.12:
        k = "ice"  # 極地の海は凍る
      elif h < 0:
        hs = min(h, (h + smooth) * 0.8)  # 岸の近くは本当の高さ、沖はなめらかな高さ
        k = "shallow" if h > -0.03 else "sea" if hs > -0.13 else "deep" if hs > -0.26 else "abyss"
      elif FLAT:
        k = "ice" if pole < 0.3 else "beach" if h < 0.012 else "land"
      elif mount > 0.35 and h > 0.02:
        k = "peak"
      elif pole < 0.3:
        k = "ice"  # 上下の端は極地
      elif h < 0.012:
        k = "beach"
      elif band == 4:
        k = "dark"
      else:
        k = BIOME[sec][band]
        if ridge > 0.94 and h > 0.2:  # 高いところは岩・雪
          k = "snow" if k in ("snow", "glacier", "sky") else "peak"
      kind[j][i] = k
      elev[j][i] = h + mount * 0.15 + (ridge * 0.05 if h > 0 else 0)
  return elev, kind


def shade_level(elev, kind, i, j):
  h = elev[j][i]
  if h < 0 or FLAT:
    return 2
  lo = elev[max(j - 1, 0)][max(i - 1, 0)]
  g = (h - lo) * 14
  return max(0, min(3, int(round(2 + g))))


def write_png(path, elev, kind):
  palette, rows = {}, []
  for j in range(GH):
    row = bytearray()
    for i in range(GW):
      s = SHADES[shade_level(elev, kind, i, j)]
      c = tuple(max(0, min(255, int(v * s))) for v in COLOR[kind[j][i]])
      idx = palette.setdefault(c, len(palette))
      row += bytes([idx, idx])
    rows.append(bytes(row))
  assert len(palette) <= 256, len(palette)
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


def main():
  out = sys.argv[1]
  seed = sys.argv[2] if len(sys.argv) > 2 else "cryepis"
  ratio = float(sys.argv[3]) if len(sys.argv) > 3 else 0.45
  elev, kind = generate(seed, ratio)
  size, colors = write_png(out, elev, kind)
  print(f"{out}: seed={seed} land={ratio} {size // 1024}KB {colors} colors")


if __name__ == "__main__":
  main()
