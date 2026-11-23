"""地形のマス目（terrain.pkl）を SVG（ベクター）にする。

- 色ごとの範囲（マスク）の輪郭をたどって閉じた線にし、階段状の角をならして点を間引く
- 重ねて塗る: 海（深い順）→ 陸の下地（砂浜の色）→ 地形 → 山の陰影 → 雪・溶岩 → 川 → 氷
- 穴のある範囲は fill-rule="evenodd" で抜く
使い方: python vectorize.py [OUT.svg]（省くと .cache/map-base.svg）
"""

import math
import pickle
import sys

import paths  # noqa: E402
import terrain as gt  # noqa: E402

STEP = 2
sys.setrecursionlimit(100000)
with open(paths.TERRAIN, "rb") as f:
  data = pickle.load(f)
kind, height, rivers = data["kind"], data["height"], data["rivers"]
GH, GW = len(kind), len(kind[0])
COLOR = gt.COLOR
WATER = ["abyss", "deep", "sea", "shallow"]
MOUNTAINS = {"mount", "snowcap", "hill", "volcano", "lava"}
EPS = 1.3  # 点を間引くときの許容（px）
MIN_AREA = 10  # これより小さい範囲は描かない（px²）


def hexc(rgb, s=1.0):
  r, g, b = (max(0, min(255, int(v * s))) for v in rgb)
  return f"#{r:02x}{g:02x}{b:02x}"


def shade(i, j):
  g = (height[j][i] - height[max(j - 1, 0)][max(i - 1, 0)]) * 9
  return max(0, min(3, int(round(2 + g))))


def loops(mask):
  """マスクの境目を、マスの角をつないだ閉じた線（右回り）の一覧にする。"""
  nxt = {}
  for j in range(GH):
    row = mask[j]
    for i in range(GW):
      if not row[i]:
        continue
      if j == 0 or not mask[j - 1][i]:
        nxt.setdefault((i, j), []).append((i + 1, j))
      if i == GW - 1 or not row[i + 1]:
        nxt.setdefault((i + 1, j), []).append((i + 1, j + 1))
      if j == GH - 1 or not mask[j + 1][i]:
        nxt.setdefault((i + 1, j + 1), []).append((i, j + 1))
      if i == 0 or not row[i - 1]:
        nxt.setdefault((i, j + 1), []).append((i, j))
  out = []
  while nxt:
    start = next(iter(nxt))
    pts, p = [start], start
    while True:
      q = nxt[p].pop()
      if not nxt[p]:
        del nxt[p]
      if q == start:
        break
      pts.append(q)
      p = q
      if p not in nxt:
        break
    out.append(pts)
  return out


def dp(pts, eps):
  if len(pts) < 3:
    return pts
  (ax, ay), (bx, by) = pts[0], pts[-1]
  L = math.hypot(bx - ax, by - ay)
  best, idx = -1, 0
  for k in range(1, len(pts) - 1):
    px, py = pts[k]
    d = (abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L) if L else math.hypot(px - ax, py - ay)
    if d > best:
      best, idx = d, k
  if best <= eps:
    return [pts[0], pts[-1]]
  return dp(pts[: idx + 1], eps)[:-1] + dp(pts[idx:], eps)


def area(pts):
  return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def smooth_loop(corners):
  """マスの角（階段）→ 辺の中点でならし、px に直して点を間引く。"""
  mids = [((a[0] + b[0]) / 2 * STEP, (a[1] + b[1]) / 2 * STEP) for a, b in zip(corners, corners[1:] + corners[:1])]
  if len(mids) < 4:
    return None
  # 閉じた線を、いちばん離れた 2 点で分けてから間引く
  far = max(range(len(mids)), key=lambda k: (mids[k][0] - mids[0][0]) ** 2 + (mids[k][1] - mids[0][1]) ** 2)
  a = dp(mids[: far + 1], EPS)
  b = dp(mids[far:] + mids[:1], EPS)
  pts = a[:-1] + b[:-1]
  if len(pts) < 3 or area(pts) < MIN_AREA:
    return None
  return pts


def path_d(polys):
  """相対座標（小数 1 桁を整数化）の短い path 文字列。"""
  parts = []
  for pts in polys:
    x0, y0 = round(pts[0][0]), round(pts[0][1])
    s = [f"M{x0} {y0}l"]
    px, py = x0, y0
    coords = []
    for x, y in pts[1:]:
      xi, yi = round(x), round(y)
      if (xi, yi) == (px, py):
        continue
      coords.append(f"{xi - px} {yi - py}")
      px, py = xi, yi
    parts.append(s[0] + " ".join(coords) + "z")
  return "".join(parts).replace(" -", "-")


def layer(mask, fill):
  polys = [p for p in (smooth_loop(c) for c in loops(mask)) if p]
  if not polys:
    return ""
  return f'<path fill="{fill}" fill-rule="evenodd" d="{path_d(polys)}"/>'


def mask_of(test):
  return [[test(i, j) for i in range(GW)] for j in range(GH)]


def main(out_path):
  parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GW * STEP} {GH * STEP}" '
    f'width="{GW * STEP}" height="{GH * STEP}">',
    f'<rect width="100%" height="100%" fill="{hexc(COLOR["abyss"])}"/>',
  ]
  # 海: 深い順に「それより浅いところ全部」を塗り重ねる
  for n, w in enumerate(WATER[1:], 1):
    deeper = set(WATER[:n])
    parts.append(layer(mask_of(lambda i, j, deeper=deeper: kind[j][i] not in deeper), hexc(COLOR[w])))
  # 陸の下地（砂浜の色）
  land = mask_of(lambda i, j: kind[j][i] not in WATER and kind[j][i] != "ice")
  parts.append(layer(land, hexc(COLOR["beach"])))
  # 地形（砂浜と山以外）
  for k in (
    "tundra",
    "taiga",
    "forest",
    "plain",
    "steppe",
    "savanna",
    "desert",
    "badland",
    "jungle",
    "wetland",
    "marshwater",
  ):
    parts.append(
      layer(
        mask_of(lambda i, j, k=k: kind[j][i] == k or (k == "wetland" and kind[j][i] == "marshwater")), hexc(COLOR[k])
      )
    )
  # 川の下の地形（川のマスはまわりの色で埋める: 川は線で描く）
  parts.append(layer(mask_of(lambda i, j: kind[j][i] == "river"), hexc(COLOR["plain"])))
  # 山: 下地 → 影 → 光 → 雪・溶岩
  for base in ("hill", "mount", "volcano"):
    group = {"hill": {"hill"}, "mount": {"mount", "snowcap"}, "volcano": {"volcano", "lava"}}[base]
    parts.append(layer(mask_of(lambda i, j, group=group: kind[j][i] in group), hexc(COLOR[base])))
    parts.append(
      layer(mask_of(lambda i, j, group=group: kind[j][i] in group and shade(i, j) <= 1), hexc(COLOR[base], 0.86))
    )
    parts.append(
      layer(mask_of(lambda i, j, group=group: kind[j][i] in group and shade(i, j) == 3), hexc(COLOR[base], 1.08))
    )
  parts.append(layer(mask_of(lambda i, j: kind[j][i] == "snowcap"), hexc(COLOR["snowcap"])))
  parts.append(layer(mask_of(lambda i, j: kind[j][i] == "lava"), hexc(COLOR["lava"])))
  # 川（線）
  for pts in rivers:
    pts = dp([(round(x), round(y)) for x, y in pts], 1.0)
    d = "M" + " L".join(f"{x} {y}" for x, y in pts)
    parts.append(
      f'<path d="{d}" fill="none" stroke="{hexc(COLOR["river"])}" stroke-width="3.2" '
      f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
  # 極地の氷
  parts.append(layer(mask_of(lambda i, j: kind[j][i] == "ice"), hexc(COLOR["ice"])))
  parts.append("</svg>")
  svg = "\n".join(p for p in parts if p)
  with open(out_path, "w", encoding="utf-8") as f:
    f.write(svg)
  print(out_path, len(svg.encode()) // 1024, "KB")


if __name__ == "__main__":
  main(sys.argv[1] if len(sys.argv) > 1 else paths.BASE_SVG)
