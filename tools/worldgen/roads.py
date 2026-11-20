"""地形の通りにくさから、町どうしの陸路を作る。

1. 町・ダンジョンごとに、地形のマス目でダイクストラ（通りにくさの合計が最小の道）
2. 町どうしは「相対近傍グラフ」でつなぐ: i-j の間に、i と j のどちらからも近い町 k があればつながない
   （近い町を順にたどる、自然な街道網になる）
   ただし KEEP の道は、あとから近くに町を置いても残す
3. ダンジョンは、いちばん近い町とだけつなぐ
使い方: python roads.py  → .cache/roads.json
"""

import heapq
import json
import math

import paths
from places import DUNGEONS, TOWNS

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
}  # 書いていない地形（海・氷）は通れない


def cost(i, j):
  return COST.get(kind[j][i])


def dijkstra(sx, sy, targets):
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
        c = cost(ni, nj)
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


# 近くに町が増えても残す道（依頼や設定で使っている、昔からの街道）
KEEP = {("干潟の港", "南壁の砦")}


def cell(x, y):
  return (x // STEP, y // STEP)


def main():
  nodes = [(n, x, y) for n, x, y in TOWNS]
  cells = {n: cell(x, y) for n, x, y in nodes + [(n, x, y) for n, x, y in DUNGEONS]}
  town_cells = [cells[n] for n, _, _ in nodes]
  res = {}
  for n, x, y in nodes:
    res[n] = dijkstra(x, y, town_cells)

  names = [n for n, _, _ in nodes]
  D = {(a, b): res[a][0].get(cells[b]) for a in names for b in names if a != b}
  roads = []
  for ai, a in enumerate(names):
    for b in names[ai + 1 :]:
      d = D[(a, b)]
      if d is None:
        continue  # 陸でつながっていない（別の大陸・島）
      if (
        (a, b) not in KEEP
        and (b, a) not in KEEP
        and any(
          D[(a, k)] is not None and D[(k, b)] is not None and max(D[(a, k)], D[(k, b)]) < d
          for k in names
          if k not in (a, b)
        )
      ):
        continue
      pts = simplify(path(res[a][1], cells[a], cells[b]))
      roads.append({"from": a, "to": b, "cost": round(d), "path": pts, "kind": "road"})

  # ダンジョンは、いちばん近い町とつなぐ（FORCE は決まった町）
  FORCE = {"始祖竜の頭骨": "天門の町", "氷牙の氷窟": "氷牙の砦"}
  for n, x, y in DUNGEONS:
    dist, prev = dijkstra(x, y, town_cells)
    best = min((dist[cells[t]], t) for t in names if cells[t] in dist and FORCE.get(n, t) == t)
    pts = simplify(path(prev, cells[n], cells[best[1]]))
    roads.append({"from": n, "to": best[1], "cost": round(best[0]), "path": pts, "kind": "trail"})

  json.dump(roads, open(paths.ROADS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
  main()
