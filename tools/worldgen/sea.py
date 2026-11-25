"""船の道を、海のマス目だけを通る最短の道にする。

- 港から一番近い海のマスまでは、まっすぐ出る
- 浅瀬は少し通りにくくして、岸に沿いすぎないようにする
使い方: python sea.py  → .cache/sea.json
"""

import heapq
import json
import math

import paths
from places import SEA_ROUTES, TOWNS
from roads import length_km, simplify

STEP = 2
kind = paths.kind()
GH, GW = len(kind), len(kind[0])
COST = {"shallow": 2.0, "sea": 1.0, "deep": 1.0, "abyss": 1.0}
SANDBAR = 25  # 細い砂州（海岸の砂浜のマス）は、船で越えられるが通りにくい
pos = {n: (x, y) for n, x, y in TOWNS}


def ocean_cells():
  """広い水域（湾や外海）の海のマス。小さな池は含めない。"""
  seen, best = set(), set()
  for j0 in range(GH):
    for i0 in range(GW):
      if (i0, j0) in seen or kind[j0][i0] not in COST:
        continue
      comp, stack = set(), [(i0, j0)]
      seen.add((i0, j0))
      while stack:
        i, j = stack.pop()
        comp.add((i, j))
        for n in ((i + a, j + b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b):
          if 0 <= n[0] < GW and 0 <= n[1] < GH and n not in seen and kind[n[1]][n[0]] in COST:
            seen.add(n)
            stack.append(n)
      if len(comp) > 2000:
        best |= comp
  return best


OCEAN = ocean_cells()


def nearest_water(x, y):
  ci, cj = x // STEP, y // STEP
  cand = [
    (math.hypot(a - ci, b - cj), (a, b))
    for a in range(max(0, ci - 60), min(GW, ci + 61))
    for b in range(max(0, cj - 60), min(GH, cj + 61))
    if (a, b) in OCEAN
  ]
  if not cand:
    raise SystemExit(f"海が見つからない: {x},{y}")
  return min(cand)[1]


def sea_path(a, b):
  s, g = nearest_water(*pos[a]), nearest_water(*pos[b])
  dist, prev, heap = {s: 0.0}, {}, [(0.0, s)]
  while heap:
    d, c = heapq.heappop(heap)
    if c == g:
      break
    if d > dist[c]:
      continue
    i, j = c
    for di in (-1, 0, 1):
      for dj in (-1, 0, 1):
        n = (i + di, j + dj)
        if n == c or not (0 <= n[0] < GW and 0 <= n[1] < GH):
          continue
        k = kind[n[1]][n[0]]
        c_ = COST.get(k, SANDBAR if k == "beach" else None)
        if c_ is None:
          continue
        nd = d + c_ * (1.4142 if di and dj else 1)
        if nd < dist.get(n, 1e18):
          dist[n], prev[n] = nd, c
          heapq.heappush(heap, (nd, n))
  cells = [g]
  while cells[-1] != s:
    cells.append(prev[cells[-1]])
  pts = [(i * STEP + 1, j * STEP + 1) for i, j in reversed(cells)]
  return [pos[a]] + simplify(pts, 1.2) + [pos[b]], round(dist[g])


def check(pts):
  """港の出入り（最初と最後の 1 区間）以外で陸を通っていないか。"""
  bad = 0
  for (ax, ay), (bx, by) in list(zip(pts, pts[1:]))[1:-1]:
    n = max(2, int(math.hypot(bx - ax, by - ay) // 2))
    for t in range(n + 1):
      x, y = ax + (bx - ax) * t / n, ay + (by - ay) * t / n
      if kind[int(y) // STEP][int(x) // STEP] not in COST and kind[int(y) // STEP][int(x) // STEP] != "beach":
        bad += 1
  return bad


def main():
  routes = []
  for a, b in SEA_ROUTES:
    pts, cost = sea_path(a, b)
    routes.append({"from": a, "to": b, "cost": cost, "km": length_km(pts), "path": pts})
    assert check(pts) == 0, f"航路 {a}-{b} が陸の上を通っています"
  with open(paths.SEA, "w", encoding="utf-8") as f:
    json.dump(routes, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
  main()
