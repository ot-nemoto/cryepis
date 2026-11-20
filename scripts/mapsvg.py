"""世界の MAP を SVG の地図画像にする（標準ライブラリだけで描く）。

world/map.json の layout（大きさとベースの地図）と、場所ごとの pos（座標）、道ごとの path（線）を使う。
layout.background にベースの地図（world/ の中の画像）があれば一番下に敷く。
陸路・航路・ダンジョンへの道を描き、道の途中に移動にかかる行動回数の札を出す。
勇者がいる町（道の途中なら、進んだ分だけ道の上）には、名前とレベルの札を出す。
grid=True にすると、座標の目盛りと場所の ID を重ねた確認用の画像になる（pos を決めるときに使う）。
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

import common

PAD_X, PAD_TOP, PAD_BOTTOM = 24, 76, 56  # 地図の周りの余白（見出し・凡例を置く）
FONT = ("'Hiragino Sans','Hiragino Kaku Gothic ProN','Noto Sans CJK JP','Yu Gothic',Meiryo,sans-serif,"
        "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji'")
HALO = "paint-order:stroke;stroke:#fffdf5;stroke-width:3.5px;stroke-linejoin:round"
MAX_NAMES = 4  # 1 か所に出す勇者の名前の数（超えた分は「ほか n 人」）
ROAD_STYLE = {  # 道の種類ごとの線
    "land": 'stroke="#7a5530" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"',
    "sea": 'stroke="#2f4f6f" stroke-width="2.5" stroke-dasharray="9 6" stroke-linecap="round"',
    "trail": 'stroke="#5a1f1f" stroke-width="2.2" stroke-dasharray="2 5" stroke-linecap="round"',
}
BADGE_COLORS = {"land": ("#fff6e0", "#7a5530"), "sea": ("#e8f0f6", "#2f4f6f"), "trail": ("#fff6e0", "#5a1f1f")}


def text_width(text: str, size: float) -> float:
    """文字列のおおよその表示幅（px）。全角は size、半角は size の 0.6 倍として数える。"""
    return sum(size if common.width(c) == 2 else size * 0.6 for c in text)


def badge(x: float, y: float, actions: int, kind: str = "land", cls: str = "road-cost") -> str:
    """道の上に置く、移動にかかる行動回数の札（凡例の見本は cls を変える）。"""
    fill, stroke = BADGE_COLORS[kind]
    return (f'<g class="{cls}"><circle cx="{x:.0f}" cy="{y:.0f}" r="10" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="2"/><text x="{x:.0f}" y="{y + 4.5:.0f}" text-anchor="middle" font-size="13" '
            f'font-weight="bold" fill="{stroke}">{actions}</text></g>')


def smooth(pts: list) -> str:
    """点を通るなめらかな線（Catmull-Rom を 3 次ベジエに直す）の path の d。"""
    d = f"M{pts[0][0]:.0f},{pts[0][1]:.0f}"
    for i in range(len(pts) - 1):
        p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.0f},{c1[1]:.0f} {c2[0]:.0f},{c2[1]:.0f} {p2[0]:.0f},{p2[1]:.0f}"
    return d


def point_along(pts: list, rate: float) -> tuple[float, float]:
    """線の長さの rate（0〜1）のところの点。"""
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    goal, acc = sum(seg) * rate, 0.0
    for (a, b), length in zip(zip(pts, pts[1:]), seg):
        if acc + length >= goal:
            t = (goal - acc) / (length or 1)
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        acc += length
    return pts[-1][0], pts[-1][1]


def grid_lines(ox: float, oy: float, width: int, height: int) -> list[str]:
    """確認用の座標の目盛り（50 ごとに線、100 ごとに数字）。"""
    out = []
    for v in range(0, max(width, height) + 1, 50):
        strong = v % 100 == 0
        style = f'stroke="#1d4ed8" stroke-opacity="{0.45 if strong else 0.2}" stroke-width="{1 if strong else 0.6}"'
        if v <= width:
            out.append(f'<line x1="{ox + v}" y1="{oy}" x2="{ox + v}" y2="{oy + height}" {style}/>')
            if strong:
                out.append(f'<text x="{ox + v}" y="{oy - 6}" text-anchor="middle" font-size="10" fill="#1d4ed8">{v}</text>')
        if v <= height:
            out.append(f'<line x1="{ox}" y1="{oy + v}" x2="{ox + width}" y2="{oy + v}" {style}/>')
            if strong:
                out.append(f'<text x="{ox + 4}" y="{oy + v - 3}" font-size="10" fill="#1d4ed8">{v}</text>')
    return out


def place_label(p: dict, x: float, y: float) -> list[str]:
    """場所の名前（町は呼び名を小さく添える）。p["label"] で置く向き（up/down/left/right）を変えられる。
    ふだんは、町は上、ダンジョンは下。"""
    town = p["type"] == "town"
    side = p.get("label") or ("up" if town else "down")
    alias = p.get("alias") if town else None
    name_size, color = (15, "#2b2b2b") if town else (13, "#5a1f1f")
    if side == "up":
        nx, ny, anchor = x, y - (30 if alias else 18), "middle"
    elif side == "down":
        nx, ny, anchor = x, y + 31, "middle"
    elif side == "left":
        nx, ny, anchor = x - 18, y + (0 if alias else 5), "end"
    else:
        nx, ny, anchor = x + 18, y + (0 if alias else 5), "start"
    out = [f'<text x="{nx:.0f}" y="{ny:.0f}" text-anchor="{anchor}" font-size="{name_size}" font-weight="bold" '
           f'fill="{color}" style="{HALO}">{escape(p["name"])}</text>']
    if alias:
        out.append(f'<text x="{nx:.0f}" y="{ny + 13:.0f}" text-anchor="{anchor}" font-size="10.5" fill="#4a4a4a" '
                   f'style="{HALO}">{escape(alias)}</text>')
    return out


def road_lines(world: dict) -> list[dict]:
    """描く道（同じ道は 1 本だけ）: {kind, distance, pts}。"""
    places, out, seen = world["places"], [], set()
    for (a, b), info in world["road_info"].items():
        if (b, a) in seen:
            continue
        seen.add((a, b))
        pts = info.get("path") or [places[a]["pos"], places[b]["pos"]]
        out.append({"kind": info["kind"], "distance": world["roads"][(a, b)], "pts": pts})
    for r in world.get("trails", []):
        pts = r.get("path") or [places[r["from"]]["pos"], places[r["to"]]["pos"]]
        out.append({"kind": "trail", "distance": r["distance"], "pts": pts})
    return out


def hero_spot(world: dict, state: dict) -> tuple[float, float] | None:
    """勇者の今の位置（町なら町の座標、道の途中なら進んだ分だけ道の上）。"""
    places, place, road = world["places"], state["place"], state.get("road")
    if "pos" not in places.get(place, {}):
        return None
    if not road or (place, road["to"]) not in world["roads"]:
        return tuple(places[place]["pos"])
    info, total = world["road_info"][(place, road["to"])], world["roads"][(place, road["to"])]
    pts = info.get("path") or [places[place]["pos"], places[road["to"]]["pos"]]
    return point_along(pts, (total - road["left"]) / total)


def render(world: dict, entries: list[tuple[dict, dict]], grid: bool = False) -> str | None:
    """SVG の文字列。map.json に layout がなければ None。entries は (heroes.json の行, 状態)。"""
    layout = world.get("layout")
    if not layout:
        return None
    width, height = layout["size"]
    ox, oy = PAD_X, PAD_TOP
    canvas_w, canvas_h = width + 2 * PAD_X, height + PAD_TOP + PAD_BOTTOM
    places = world["places"]

    def at(x: float, y: float) -> tuple[float, float]:
        return x + ox, y + oy

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{canvas_w}" height="{canvas_h}" viewBox="0 0 {canvas_w} {canvas_h}" font-family="{FONT}">',
           f'<rect width="{canvas_w}" height="{canvas_h}" fill="#f6efdc"/>']

    # ベースの地図（なければ海の色だけ）
    background = world.get("background")
    if background:
        out.append(f'<image x="{ox}" y="{oy}" width="{width}" height="{height}" preserveAspectRatio="none" '
                   f'href="{background}" xlink:href="{background}"/>')
    else:
        out.append(f'<rect x="{ox}" y="{oy}" width="{width}" height="{height}" fill="#7496b0"/>')
    out.append(f'<rect x="{ox}" y="{oy}" width="{width}" height="{height}" fill="none" stroke="#a08b70"/>')

    # 道（陸路は白いふちどりで見やすく）と、真ん中に行動回数の札
    out.append(f'<g transform="translate({ox},{oy})" fill="none">')
    lines = road_lines(world)
    for r in lines:
        if r["kind"] == "land":
            out.append(f'<path d="{smooth(r["pts"])}" stroke="#fff6e0" stroke-width="6" stroke-opacity="0.7" '
                       'stroke-linecap="round" stroke-linejoin="round"/>')
    for r in lines:
        out.append(f'<path class="road-{r["kind"]}" d="{smooth(r["pts"])}" {ROAD_STYLE[r["kind"]]}/>')
    out.append("</g>")
    for r in lines:
        out.append(badge(*at(*point_along(r["pts"], 0.5)), r["distance"], r["kind"]))

    # 場所
    for pid, p in places.items():
        if "pos" not in p:
            continue
        x, y = at(*p["pos"])
        out.append(f'<g id="place-{escape(pid)}">')
        town = p["type"] == "town"
        if town:
            out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="13" fill="#fff3c4" stroke="#6b4a1f" stroke-width="2.5"/>')
            out.append(f'<text x="{x:.0f}" y="{y + 5:.0f}" text-anchor="middle" font-size="13">'
                       f'{escape(p.get("emoji", ""))}</text>')
        else:  # ダンジョン
            out.append(f'<path d="M{x:.0f},{y - 12:.0f} L{x + 11:.0f},{y:.0f} L{x:.0f},{y + 12:.0f} '
                       f'L{x - 11:.0f},{y:.0f} Z" fill="#3a1414" stroke="#e8c070" stroke-width="2.5"/>')
        out += place_label(p, x, y)
        if grid:  # 確認用: 場所の ID と座標
            px, py = p["pos"]
            out.append(f'<text x="{x:.0f}" y="{y + 44:.0f}" text-anchor="middle" font-size="10" fill="#1d4ed8" '
                       f'style="{HALO}">{escape(pid)} ({px:g}, {py:g})</text>')
        out.append("</g>")

    # 勇者（同じ位置ごとにまとめて札を出す）
    here: dict[tuple[float, float], list[str]] = {}
    for hero, state in entries:
        spot = hero_spot(world, state)
        if spot:
            here.setdefault((round(spot[0]), round(spot[1])), []).append(
                f"{hero['name']} Lv.{state['hero']['level']}")
    placed: list[tuple[float, float, float, float]] = []
    for (sx, sy), names in here.items():
        x, y = at(sx, sy)
        shown = names[:MAX_NAMES] + ([f"ほか {len(names) - MAX_NAMES} 人"] if len(names) > MAX_NAMES else [])
        lines_ = [f"🧙 {n}" for n in shown]
        box_w = max(text_width(line, 12) for line in lines_) + 12
        box_h = 16 * len(lines_) + 6
        bx = x + 20 if x + 20 + box_w < canvas_w - 4 else x - 20 - box_w
        by = max(4, y - box_h - 6)
        for _ in range(6):  # 先に置いた札と重なるなら、上へずらす
            if not any(bx < px + pw and px < bx + box_w and by < py + ph and py < by + box_h
                       for px, py, pw, ph in placed):
                break
            by = max(4, by - box_h - 4)
        placed.append((bx, by, box_w, box_h))
        out.append('<g class="hero">')
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="18" fill="none" stroke="#dc2626" stroke-width="3"/>')
        out.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{box_w:.0f}" height="{box_h}" rx="5" fill="#ffffff" '
                   'stroke="#dc2626" stroke-width="1.5" opacity="0.95"/>')
        for i, line in enumerate(lines_):
            out.append(f'<text x="{bx + 6:.0f}" y="{by + 17 + 16 * i:.0f}" font-size="12" fill="#7f1d1d">'
                       f'{escape(line)}</text>')
        out.append("</g>")

    if grid:
        out += grid_lines(ox, oy, width, height)

    # 見出しと凡例
    out.append('<text x="24" y="38" font-size="24" font-weight="bold" fill="#3f2f22">世界の MAP</text>')
    out.append('<text x="24" y="60" font-size="13" fill="#6b5a48">道の上の数字は、移動にかかる行動回数です</text>')
    ly = canvas_h - 22
    legend = [
        (f'<circle cx="34" cy="{ly - 5}" r="9" fill="#fff3c4" stroke="#6b4a1f" stroke-width="2.5"/>', 50, "町"),
        (f'<path d="M100,{ly - 16} L110,{ly - 5} L100,{ly + 6} L90,{ly - 5} Z" fill="#3a1414" stroke="#e8c070" '
         'stroke-width="2"/>', 118, "ダンジョン（まだ入れない）"),
        (f'<path d="M290,{ly - 5} H340" fill="none" {ROAD_STYLE["land"]}/>', 348, "陸路"),
        (f'<path d="M400,{ly - 5} H450" fill="none" {ROAD_STYLE["sea"]}/>', 458, "航路"),
        (f'<path d="M510,{ly - 5} H560" fill="none" {ROAD_STYLE["trail"]}/>', 568, "ダンジョンへの道"),
        (badge(700, ly - 5, 3, "land", "legend"), 716, "移動にかかる行動回数"),
        (f'<circle cx="890" cy="{ly - 5}" r="9" fill="none" stroke="#dc2626" stroke-width="3"/>', 906, "勇者がいる場所"),
    ]
    for shape, tx, label in legend:
        out.append(shape)
        out.append(f'<text x="{tx}" y="{ly}" font-size="13" fill="#3f2f22">{escape(label)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
