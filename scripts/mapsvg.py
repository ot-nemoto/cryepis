"""世界の MAP を SVG の地図画像にする（標準ライブラリだけで描く）。

world/map.json の layout（輪・方角の描き方）と、場所ごとの pos（座標）を使う。
pos のない場所は描かない。勇者がいる場所には、名前とレベルの札を出す。
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

import common

PAD_X, PAD_TOP, PAD_BOTTOM = 100, 150, 100  # 地図の周りの余白（方角の名前・見出し・凡例を置く）
FONT = ("'Hiragino Sans','Hiragino Kaku Gothic ProN','Noto Sans CJK JP','Yu Gothic',Meiryo,sans-serif,"
        "'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji'")
HALO = "paint-order:stroke;stroke:#fffdf5;stroke-width:3px;stroke-linejoin:round"
MAX_NAMES = 4  # 1 か所に出す勇者の名前の数（超えた分は「ほか n 人」）


def text_width(text: str, size: float) -> float:
    """文字列のおおよその表示幅（px）。全角は size、半角は size の 0.6 倍として数える。"""
    return sum(size if common.width(c) == 2 else size * 0.6 for c in text)


def badge(x: float, y: float, actions: int, cls: str = "road-cost") -> str:
    """道の上に置く、移動にかかる行動回数の札（凡例の見本は cls を変える）。"""
    return (f'<g class="{cls}"><circle cx="{x:.0f}" cy="{y:.0f}" r="7.5" fill="#fffdf5" stroke="#8a7357" '
            f'stroke-width="1.2"/><text x="{x:.0f}" y="{y + 3.5:.0f}" text-anchor="middle" font-size="9.5" '
            f'font-weight="bold" fill="#5b4636">{actions}</text></g>')


def render(world: dict, entries: list[tuple[dict, dict]]) -> str | None:
    """SVG の文字列。map.json に layout がなければ None。entries は (heroes.json の行, 状態)。"""
    layout = world.get("layout")
    if not layout:
        return None
    width, height = layout["size"]
    cx, cy = layout["center"]
    ox, oy = PAD_X, PAD_TOP
    canvas_w, canvas_h = width + 2 * PAD_X, height + PAD_TOP + PAD_BOTTOM
    places = world["places"]

    def at(pid: str) -> tuple[float, float]:
        x, y = places[pid]["pos"]
        return x + ox, y + oy

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w}" height="{canvas_h}" '
           f'viewBox="0 0 {canvas_w} {canvas_h}" font-family="{FONT}">',
           f'<rect width="{canvas_w}" height="{canvas_h}" fill="#f6efdc"/>']

    # 輪（外側から順に塗る）と方角の区切り
    rings = sorted(layout.get("rings", []), key=lambda r: -r["radius"])
    for ring in rings:
        out.append(f'<circle cx="{cx + ox}" cy="{cy + oy}" r="{ring["radius"]}" fill="{escape(ring["color"])}" '
                   'stroke="#fffdf5" stroke-width="3"/>')
    outer = rings[0]["radius"] if rings else min(width, height) / 2
    inner = rings[-1]["radius"] if rings else 0
    for sector in layout.get("sectors", []):
        edge = sector["angle"] + 30
        x1, y1 = cx + ox + inner * math.cos(math.radians(edge)), cy + oy + inner * math.sin(math.radians(edge))
        x2, y2 = cx + ox + outer * math.cos(math.radians(edge)), cy + oy + outer * math.sin(math.radians(edge))
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#fffdf5" '
                   'stroke-width="2" stroke-dasharray="4 6"/>')
        lx = cx + ox + (outer + 38) * math.cos(math.radians(sector["angle"]))
        ly = cy + oy + (outer + 38) * math.sin(math.radians(sector["angle"]))
        out.append(f'<text x="{lx:.0f}" y="{ly - 4:.0f}" text-anchor="middle" font-size="17" font-weight="bold" '
                   f'fill="#5b4636">{escape(sector["name"])}</text>')
        out.append(f'<text x="{lx:.0f}" y="{ly + 14:.0f}" text-anchor="middle" font-size="11" fill="#7c6a55">'
                   f'{escape(sector.get("theme", ""))}</text>')

    # 道（真ん中に、移動にかかる行動回数の札を出す）
    drawn, badges = set(), []
    for (a, b), dist in world["roads"].items():
        if (b, a) in drawn or "pos" not in places[a] or "pos" not in places[b]:
            continue
        drawn.add((a, b))
        (x1, y1), (x2, y2) = at(a), at(b)
        dash = ' stroke-dasharray="7 5"' if dist >= 2 else ""
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#8a7357" '
                   f'stroke-width="2.2" stroke-linecap="round"{dash}/>')
        badges.append(badge((x1 + x2) / 2, (y1 + y2) / 2, dist))
    out += badges

    # 場所
    for pid, p in places.items():
        if "pos" not in p:
            continue
        x, y = at(pid)
        town = p["type"] == "town"
        r = 15 if town else 12
        fill, stroke = ("#fff3c4", "#a16207") if town else ("#ffffff", "#7c6a55")
        out.append(f'<g id="place-{escape(pid)}">')
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        out.append(f'<text x="{x:.0f}" y="{y + 5:.0f}" text-anchor="middle" font-size="{15 if town else 12}">'
                   f'{escape(p.get("emoji", ""))}</text>')
        weight = ' font-weight="bold"' if town else ""
        out.append(f'<text x="{x:.0f}" y="{y + r + 13:.0f}" text-anchor="middle" font-size="11"{weight} '
                   f'fill="#2f2419" style="{HALO}">{escape(p["name"])}</text>')
        if not town:
            lo, hi = p["level"]
            out.append(f'<text x="{x:.0f}" y="{y + r + 24:.0f}" text-anchor="middle" font-size="9.5" '
                       f'fill="#6b5a48" style="{HALO}">Lv{lo}〜{hi}</text>')
        out.append("</g>")

    # 勇者（場所ごとにまとめて札を出す）
    here: dict[str, list[str]] = {}
    for hero, state in entries:
        if "pos" in places.get(state["place"], {}):
            here.setdefault(state["place"], []).append(f"{hero['name']} Lv.{state['hero']['level']}")
    for pid, names in here.items():
        x, y = at(pid)
        shown = names[:MAX_NAMES] + ([f"ほか {len(names) - MAX_NAMES} 人"] if len(names) > MAX_NAMES else [])
        lines = [f"🧙 {n}" for n in shown]
        box_w = max(text_width(line, 11) for line in lines) + 12
        box_h = 15 * len(lines) + 6
        bx = x + 20 if x + 20 + box_w < canvas_w - 4 else x - 20 - box_w
        by = y - box_h - 6
        out.append(f'<g class="hero">')
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="19" fill="none" stroke="#dc2626" stroke-width="3"/>')
        out.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{box_w:.0f}" height="{box_h}" rx="5" fill="#ffffff" '
                   'stroke="#dc2626" stroke-width="1.5" opacity="0.95"/>')
        for i, line in enumerate(lines):
            out.append(f'<text x="{bx + 6:.0f}" y="{by + 16 + 15 * i:.0f}" font-size="11" fill="#7f1d1d">'
                       f'{escape(line)}</text>')
        out.append("</g>")

    # 見出しと凡例
    out.append('<text x="24" y="40" font-size="24" font-weight="bold" fill="#3f2f22">世界の MAP</text>')
    out.append('<text x="24" y="62" font-size="12" fill="#6b5a48">外側ほど易しく、中心ほど難しい</text>')
    for i, ring in enumerate(sorted(rings, key=lambda r: -r["radius"])):
        ly = 92 + 18 * i
        out.append(f'<rect x="{canvas_w - 190}" y="{ly - 11}" width="14" height="14" rx="3" '
                   f'fill="{escape(ring["color"])}" stroke="#a08b70"/>')
        out.append(f'<text x="{canvas_w - 170}" y="{ly}" font-size="12" fill="#3f2f22">'
                   f'{escape(ring.get("label", ring["name"]))}</text>')
    ly = canvas_h - 24
    legend = [
        (f'<circle cx="34" cy="{ly - 4}" r="9" fill="#fff3c4" stroke="#a16207" stroke-width="2"/>', 50, "町"),
        (f'<circle cx="104" cy="{ly - 4}" r="8" fill="#ffffff" stroke="#7c6a55" stroke-width="2"/>', 118, "野外"),
        (f'<line x1="166" y1="{ly - 4}" x2="206" y2="{ly - 4}" stroke="#8a7357" stroke-width="2.2"/>'
         + badge(186, ly - 4, 1, "legend"), 214, "道（数字は移動にかかる行動回数）"),
        (f'<line x1="420" y1="{ly - 4}" x2="460" y2="{ly - 4}" stroke="#8a7357" stroke-width="2.2" '
         'stroke-dasharray="7 5"/>', 468, "遠い道（2 行動以上）"),
        (f'<circle cx="620" cy="{ly - 4}" r="9" fill="none" stroke="#dc2626" stroke-width="3"/>', 636, "勇者がいる場所"),
    ]
    for shape, tx, label in legend:
        out.append(shape)
        out.append(f'<text x="{tx}" y="{ly}" font-size="12" fill="#3f2f22">{escape(label)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
