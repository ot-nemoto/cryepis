"""e の陸の形に、地形（山・森・平原・砂漠・湿地・川など）を塗る試作。

世界の決まり:
- 赤道は y=500。上下に行くほど寒い（凍土 → 針葉樹 → 温帯 → 熱帯）
- 偏西風が西から雨を運ぶ。大きな山脈の東側は雨が届かず乾く（雨の影）
使い方: python terrain.py OUT.png [OUT_LABELS.svg]（確かめる用の PNG。ふだんは make.py から使う）
"""
import math
import struct
import sys
import zlib

import land as base

W, H, STEP = base.W, base.H, base.STEP
GW, GH = base.GW, base.GH

COLOR = dict(base.COLOR)
COLOR.update({
    "tundra": (198, 202, 190), "taiga": (114, 142, 120), "forest": (118, 152, 104),
    "plain": (180, 198, 142), "steppe": (198, 198, 150), "savanna": (204, 194, 140),
    "desert": (224, 204, 156), "badland": (204, 166, 128), "jungle": (88, 126, 90),
    "wetland": (140, 164, 140), "marshwater": (150, 182, 186), "mount": (158, 148, 130),
    "hill": (176, 176, 140), "snowcap": (236, 238, 240), "volcano": (126, 98, 88),
    "lava": (156, 84, 66), "river": (128, 166, 188),
})

# 山脈: (名前, 種類, 線, 幅)
RANGES = [
    ("竜骨山脈", "mount", [(560, 650), (620, 560), (700, 470), (780, 390), (860, 330),
                           (960, 290), (1060, 260), (1150, 215)], 34),
    ("氷牙山地", "mount", [(1150, 125), (1215, 170), (1265, 240)], 24),
    ("西の背骨山脈", "mount", [(330, 565), (290, 660), (250, 760), (205, 860)], 24),
    ("南壁山脈", "mount", [(1090, 730), (1050, 800), (1000, 880), (965, 955)], 26),
    ("北の丘陵", "hill", [(215, 185), (320, 140), (430, 170)], 42),
    ("炎の列島", "volcano", [(1200, 880), (1260, 820), (1330, 772), (1425, 735)], 18),
    ("炎の列島", "volcano", [(1325, 668), (1335, 678)], 14),
]
# 地域: (名前, 地形, 中心, 半径) 後ろほど上に塗る
AREAS = [
    ("東の草原", "savanna", (1220, 570), (220, 70)),
    ("古の大森林", "forest", (330, 245), (175, 110)),
    ("東の雨林", "jungle", (1470, 430), (110, 170)),
    ("南の森", "forest", (190, 830), (110, 80)),
    ("黄金平原", "plain", (860, 500), (120, 85)),
    ("風の草原", "steppe", (1060, 340), (130, 60)),
    ("灼熱の大砂漠", "desert", (1215, 480), (150, 95)),
    ("霧の大湿原", "wetland", (305, 465), (120, 65)),
    ("大河の三角州", "wetland", (935, 405), (50, 32)),
    ("湖沼地帯", "wetland", (1265, 315), (85, 45)),
    ("南の干潟", "wetland", (880, 720), (75, 35)),
    ("熱帯の半島", "jungle", (600, 600), (110, 80)),
    ("西の密林", "jungle", (110, 520), (120, 70)),
    ("赤い荒野", "badland", (150, 668), (140, 55)),
    ("南の大草原", "steppe", (820, 830), (150, 75)),
]
# 川: 山から海・湖へ
RIVERS = [
    [(965, 300), (990, 350), (968, 398)],
    [(700, 462), (630, 470), (565, 492)],
    [(400, 300), (350, 380), (300, 465), (215, 525)],
    [(1045, 800), (960, 780), (885, 725)],
    [(270, 720), (185, 762), (95, 790)],
    [(1255, 235), (1285, 290), (1290, 330)],
    [(1150, 220), (1200, 300), (1240, 330)],
]
# 説明用の名前（地図そのものには描かない）
LABELS = [
    ("中央大陸", (760, 190), 26), ("西大陸", (90, 600), 22), ("南大陸", (760, 930), 22),
    ("霧氷諸島", (680, 105), 16), ("炎の列島", (1330, 860), 16),
    ("天の頂", (880, 318), 14),
] + [(n, c, 14) for n, _, c, _ in AREAS] + [
    ("竜骨山脈", (700, 420), 14), ("氷牙山地", (1215, 135), 14), ("西の背骨山脈", (300, 760), 13),
    ("南壁山脈", (1045, 885), 13), ("北の丘陵", (320, 165), 13),
    ("凍土", (300, 75), 14), ("針葉樹の森", (610, 222), 14),
]


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
    elev, kind = base.generate("e", 0.45)        # 陸と海（FLAT）
    n1, n2, n3 = base.Noise("terrain-1"), base.Noise("terrain-2"), base.Noise("terrain-3")

    # 山（値は高さ 0〜1）
    mount = [[None] * GW for _ in range(GH)]
    stamp_lines(mount, [(l, w, t) for _, t, l, w in RANGES], None,
                lambda c: c ** 0.8, n1)
    # 川（細い線）
    river = [[None] * GW for _ in range(GH)]
    wiggled = []
    for line in RIVERS:
        pts = []
        for (ax, ay), (bx, by) in zip(line, line[1:]):
            for s in range(8):
                t = s / 8
                x, y = ax + (bx - ax) * t, ay + (by - ay) * t
                pts.append((x + n2.fbm(x / 40, y / 40) * 18, y + n2.fbm(x / 40 + 9, y / 40) * 18))
        pts.append(line[-1])
        wiggled.append((pts, 2.6, "river"))
    stamp_lines(river, wiggled, None, lambda c: c, base.Noise("flat"))

    out = [[None] * GW for _ in range(GH)]
    height = [[0.0] * GW for _ in range(GH)]
    for j in range(GH):
        y = j * STEP + 1
        for i in range(GW):
            x = i * STEP + 1
            k = kind[j][i]
            if k not in ("land", "beach"):
                out[j][i] = k
                continue
            if k == "beach":
                out[j][i] = "beach"
                continue
            # 緯度の気候
            lat = abs(y + n1.fbm(x / 120, y / 120 + 3, 3) * 45 - 500) / 500
            patch = n3.fbm(x / 80, y / 80, 4)
            if lat > 0.8:
                b = "tundra"
            elif lat > 0.6:
                b = "taiga" if patch > -0.25 else "tundra"
            elif lat > 0.28:
                b = "forest" if patch > 0.18 else "plain"
            elif lat > 0.1:
                b = "jungle" if patch > 0.3 else "savanna"
            else:
                b = "jungle" if patch > -0.1 else "savanna"
            # 地域
            for _, kind_a, (cx, cy), (rx, ry) in AREAS:
                q = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
                q += n2.fbm(x / 110 + cx, y / 110, 4) * 1.1 + n2.fbm(x / 30, y / 30 + cy, 2) * 0.3
                if q < 1:
                    b = kind_a
            if b == "wetland" and n3.fbm(x / 14, y / 14 + 7, 2) > 0.25:
                b = "marshwater"
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
                        b = "lava" if h > 0.78 else "volcano"
                elif h > 0.16:
                    cold = lat > 0.55
                    b = "snowcap" if h > (0.62 if cold else 0.8) else "mount"
            if river[j][i] and b not in ("snowcap",):
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
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 3, 0, 0, 0))
           + chunk(b"PLTE", plte) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    open(path, "wb").write(png)
    return len(png), len(palette)


def write_labels(path, png_name):
    texts = "".join(
        f'<text x="{x}" y="{y}" font-size="{s}" text-anchor="middle">{n}</text>'
        for n, (x, y), s in LABELS)
    open(path, "w", encoding="utf-8").write(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
        f'<image href="{png_name}" width="{W}" height="{H}"/>'
        f'<g font-family="sans-serif" font-weight="bold" fill="#2b2b2b" stroke="#fff" '
        f'stroke-width="3" paint-order="stroke">{texts}</g></svg>')


if __name__ == "__main__":
    kind, height = generate()
    size, colors = write_png(sys.argv[1], kind, height)
    print(f"{sys.argv[1]}: {size // 1024}KB {colors} colors")
    if len(sys.argv) > 2:
        import os
        write_labels(sys.argv[2], os.path.basename(sys.argv[1]))
