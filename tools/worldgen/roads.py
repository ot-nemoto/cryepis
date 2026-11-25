"""地形の通りにくさから、街道（places.LAND_ROADS）とダンジョンへの道（places.TRAILS）の線を作る。

- 町・ダンジョンごとに、地形のマス目でダイクストラ（通りにくさの合計が最小の道）
- 道の長さ（km）は、間引いた線の長さから出す（行動回数は actions.py が km から換算する）
- 浅い海（海峡）は、とても通りにくいが渡れる（関門の海峡など）
使い方: python roads.py  → .cache/roads.json
"""

import heapq
import json
import math

import japan
import paths
from places import DUNGEONS, LAND_ROADS, TOWNS, TRAILS

STEP = 2
kind = paths.kind()
GH, GW = len(kind), len(kind[0])

COST = {
  "plain": 1,
  "steppe": 1,
  "savanna": 1.1,
  "beach": 1.2,
  "meadow": 1,
  "grass": 1,
  "forest": 1.6,
  "taiga": 1.7,
  "hill": 1.8,
  "tundra": 2,
  "badland": 1.8,
  "jungle": 2.3,
  "desert": 2.3,
  "wetland": 2.2,
  "marshwater": 3.5,
  "river": 4,
  "mount": 7,
  "snowcap": 30,
  "volcano": 6,
  "lava": 60,
  "shallow": 40,  # 海峡・湖。渡し舟で渡る
}  # 書いていない地形（沖の海・氷）は通れない


# ダンジョンへの道（山道）は、山を越えて行く（山・雪の通りにくさを下げる）
TRAIL_COST = COST | {"mount": 2.5, "snowcap": 5, "hill": 1.5}


def cost(i, j, table=None):
  return (table or COST).get(kind[j][i])


def dijkstra(sx, sy, targets, table=None):
  si, sj = sx // STEP, sy // STEP
  dist = {(si, sj): 0.0}
  prev = {}
  left = set(targets)
  heap = [(0.0, si, sj)]
  while heap and left:
    d, i, j = heapq.heappop(heap)
    if d > dist.get((i, j), 1e18):
      continue
    left.discard((i, j))
    for di in (-1, 0, 1):
      for dj in (-1, 0, 1):
        if not di and not dj:
          continue
        ni, nj = i + di, j + dj
        if not (0 <= ni < GW and 0 <= nj < GH):
          continue
        c = cost(ni, nj, table)
        if c is None:
          continue
        nd = d + c * (1.4142 if di and dj else 1)
        if nd < dist.get((ni, nj), 1e18):
          dist[(ni, nj)] = nd
          prev[(ni, nj)] = (i, j)
          heapq.heappush(heap, (nd, ni, nj))
  return dist, prev


def path(prev, start, goal):
  pts = [goal]
  while pts[-1] != start:
    pts.append(prev[pts[-1]])
  return [(i * STEP + 1, j * STEP + 1) for i, j in reversed(pts)]


def simplify(pts, eps=2.5):
  """Douglas-Peucker で点を間引く。"""
  if len(pts) < 3:
    return pts
  (ax, ay), (bx, by) = pts[0], pts[-1]
  L = math.hypot(bx - ax, by - ay) or 1
  best, idx = 0, 0
  for k, (px, py) in enumerate(pts[1:-1], 1):
    d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
    if d > best:
      best, idx = d, k
  if best <= eps:
    return [pts[0], pts[-1]]
  return simplify(pts[: idx + 1], eps)[:-1] + simplify(pts[idx:], eps)


def cell(x, y):
  return (x // STEP, y // STEP)


def length_km(pts):
  return round(sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in zip(pts, pts[1:])) * japan.KM_PER_PX)


def main():
  spots = {n: (x, y) for n, x, y in TOWNS + DUNGEONS}
  cells = {n: cell(x, y) for n, (x, y) in spots.items()}
  roads = []
  for routes, kind in ((LAND_ROADS, "road"), (TRAILS, "trail")):
    for a, b in routes:
      dist, prev = dijkstra(*spots[a], [cells[b]], TRAIL_COST if kind == "trail" else None)
      if cells[b] not in dist:
        raise SystemExit(f"{a} から {b} へ陸で行けません")
      pts = simplify(path(prev, cells[a], cells[b]))
      roads.append({"from": a, "to": b, "cost": round(dist[cells[b]]), "km": length_km(pts), "path": pts, "kind": kind})
  with open(paths.ROADS, "w", encoding="utf-8") as f:
    json.dump(roads, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
  main()
