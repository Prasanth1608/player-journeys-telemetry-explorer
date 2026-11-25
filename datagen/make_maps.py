"""Draw the 3 demo minimaps and save the layout the data generator moves players on.

Original artwork, made entirely in code from datagen/maps.py: terrain noise, water,
roads, buildings at each named place, plus map-specific features (pits, rails, pylons).

Usage (from the repo root):
    python datagen/make_maps.py --output ../demo_data/player_data/minimaps
Writes <Map>_Minimap.png (2048 × 2048) and <Map>_layout.json per map.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from maps import MAPS

SIZE = 2048          # output minimap size
SS = 2               # supersampling for smooth edges (draw at 4096, shrink to 2048)
W = SIZE * SS
LAYOUT_GRID = 128    # resolution of the island mask / height grid saved for the generator

PALETTES = {  # ground light/dark, accent patches, roofs
    "marsh":  {"ground": ((58, 82, 48), (34, 52, 32)), "patch": (92, 78, 58), "trees": (24, 40, 26),
               "roofs": [(196, 200, 204), (150, 156, 160), (140, 92, 70)]},
    "quarry": {"ground": ((128, 100, 70), (88, 66, 46)), "patch": (150, 122, 86), "trees": (70, 60, 44),
               "roofs": [(200, 196, 188), (166, 120, 72), (120, 124, 128)]},
    "yard":   {"ground": ((82, 86, 88), (52, 56, 58)), "patch": (70, 84, 60), "trees": (36, 52, 34),
               "roofs": [(176, 182, 188), (126, 132, 140), (162, 98, 60), (74, 104, 140)]},
}
WATER = (40, 112, 120)
ROAD, ROAD_EDGE, ROAD_LINE = (92, 96, 100), (58, 60, 62), (196, 184, 140)
OUTSIDE = (10, 12, 11)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def noise(n, cells, rng):
    """Smooth random field (0–1), n × n: a coarse random grid upscaled with bicubic."""
    grid = (rng.random((cells, cells)) * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(grid).resize((n, n), Image.BICUBIC), np.float32) / 255


def fbm(n, rng, octaves=(4, 9, 20, 48), weights=(0.5, 0.25, 0.15, 0.1)):
    return sum(w * noise(n, c, rng) for c, w in zip(octaves, weights))


def px(u, v):
    """Map fraction → supersampled pixel (v up → image y down)."""
    return u * W, (1 - v) * W


def smooth(points, steps=12):
    """Catmull-Rom curve through the points, so roads and rivers bend naturally."""
    if len(points) < 3:
        return points
    pts = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(steps):
            t = s / steps
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in (0, 1)))
    out.append(points[-1])
    return out


def wiggle(points, rng, amount=0.025):
    """Add in-between points with a sideways offset, so straight links become winding roads."""
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1
        off = rng.uniform(-amount, amount)
        out += [(mid[0] - dy / length * off, mid[1] + dx / length * off), b]
    return out


def island_mask(n, style, rng, places=()):
    """Playable area (True) as an n × n grid, v up. Always includes every place."""
    y, x = np.mgrid[0:n, 0:n] / (n - 1)
    v = 1 - y
    warp = fbm(n, rng, (3, 6, 12), (0.6, 0.3, 0.1)) - 0.5
    if style == "yard":   # rounded-rectangle compound
        d = np.maximum(np.abs(x - 0.5) / 0.46, np.abs(v - 0.5) / 0.46) + warp * 0.08
    else:                 # organic island / plateau
        d = np.hypot((x - 0.5) / 0.47, (v - 0.5) / 0.45) + warp * (0.55 if style == "marsh" else 0.35)
    inside = d < 1.0
    for _, pu, pv, size in places:   # never cut a place off at the edge
        inside |= np.hypot(x - pu, v - pv) < 0.06 + 0.015 * size
    return inside


def place_xy(spec):
    return {name: (u, v) for name, u, v, _ in spec["places"]}


def road_lines(spec, rng):
    """Every road as a smooth polyline of (u, v) points."""
    where = place_xy(spec)
    lines = []
    for chain in spec["roads"]:
        pts = [where[p] if isinstance(p, str) else p for p in chain]
        lines.append(smooth(wiggle(pts, rng)))
    return lines


def grid_mask(lines, g):
    """Rasterise (u, v) polylines with a width (map fraction) onto a g × g grid, row 0 = v 0."""
    img = Image.new("L", (g, g), 0)
    d = ImageDraw.Draw(img)
    for line, width in lines:
        d.line([(u * g, (1 - v) * g) for u, v in line], fill=255, width=max(1, round(width * g * 1.2)))
    return np.flipud(np.asarray(img) > 0).astype(int).tolist()


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def draw_map(name, spec, out_dir):
    rng = np.random.default_rng(spec["seed"])
    pal = PALETTES[spec["style"]]

    # 1. Ground: two-tone noise, patches, darker towards the edge
    base_n = 1024
    mask_small = island_mask(base_n, spec["style"], rng, spec["places"])
    t = fbm(base_n, rng)
    light, dark = np.array(pal["ground"][0], np.float32), np.array(pal["ground"][1], np.float32)
    ground = dark + (light - dark) * t[..., None]
    patches = fbm(base_n, rng, (6, 14, 30), (0.6, 0.3, 0.1)) > 0.58
    ground[patches] = ground[patches] * 0.45 + np.array(pal["patch"], np.float32) * 0.55
    edge = np.asarray(Image.fromarray((mask_small * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(18)), np.float32) / 255
    ground *= (0.55 + 0.45 * edge)[..., None]
    img = Image.fromarray(np.clip(ground, 0, 255).astype(np.uint8)).resize((W, W), Image.BICUBIC).convert("RGBA")
    d = ImageDraw.Draw(img)
    s = SS * SIZE / 2048  # stroke scale

    # 2. Trees / rocks: small dark clusters
    for _ in range(140 if spec["style"] != "yard" else 40):        # clusters
        cu, cv = rng.random(2)
        for _ in range(int(rng.integers(4, 14))):
            u, v = cu + rng.normal(0, 0.012), cv + rng.normal(0, 0.012)
            r = rng.uniform(4, 10) * s
            x, y = px(u, v)
            d.ellipse([x - r, y - r, x + r, y + r], fill=pal["trees"] + (210,))

    # 3. Open pits (quarry): stepped terraces
    for pit, radius in spec.get("pits", []):
        cx, cy = px(*place_xy(spec)[pit])
        for k in range(7):
            rr = radius * W * (1 - k * 0.13)
            shade = int(120 - k * 15)
            d.ellipse([cx - rr, cy - rr * 0.8, cx + rr, cy + rr * 0.8], fill=(shade, int(shade * 0.78), int(shade * 0.55), 255),
                      outline=(shade + 30, int((shade + 30) * 0.8), int((shade + 30) * 0.6), 255), width=int(3 * s))

    # 4. Water with a lighter bank
    water_lines = []   # kept (in u, v) for the terrain grid the generator walks on
    for points, width in spec.get("water", []):
        line_uv = smooth(wiggle(points, rng, 0.015))
        water_lines.append((line_uv, width))
        line = [px(*p) for p in line_uv]
        d.line(line, fill=(96, 86, 64, 255), width=int(width * W * 1.5), joint="curve")
        d.line(line, fill=WATER + (255,), width=int(width * W), joint="curve")
        d.line(line, fill=(70, 150, 156, 255), width=int(width * W * 0.35), joint="curve")

    # 5. Rails and power lines (yard)
    for rail in spec.get("rails", []):
        line = [px(*p) for p in rail]
        horizontal = rail[0][1] == rail[-1][1]
        for off in (-6 * s, 6 * s):   # two parallel tracks
            shifted = [(x, y + off) if horizontal else (x + off, y) for x, y in line]
            d.line(shifted, fill=(120, 110, 96, 255), width=int(4 * s))
        for i in range(60):
            f = i / 59
            x = line[0][0] + (line[-1][0] - line[0][0]) * f
            y = line[0][1] + (line[-1][1] - line[0][1]) * f
            d.rectangle([x - 9 * s, y - 9 * s, x + 9 * s, y + 9 * s], outline=(90, 80, 70, 255), width=int(2 * s))
    for pylon_line in spec.get("pylons", []):
        line = [px(*p) for p in pylon_line]
        d.line(line, fill=(150, 150, 150, 180), width=int(2 * s))
        for x, y in line:
            d.rectangle([x - 12 * s, y - 12 * s, x + 12 * s, y + 12 * s], outline=(210, 210, 210, 255), width=int(3 * s))

    # 6. Roads: dark edge, asphalt, dashed centre line
    roads = road_lines(spec, rng)
    for line_uv in roads:
        line = [px(*p) for p in line_uv]
        d.line(line, fill=ROAD_EDGE + (255,), width=int(26 * s), joint="curve")
    for line_uv in roads:
        line = [px(*p) for p in line_uv]
        d.line(line, fill=ROAD + (255,), width=int(20 * s), joint="curve")
        for i in range(0, len(line) - 1, 2):
            d.line([line[i], line[i + 1]], fill=ROAD_LINE + (255,), width=int(2 * s))

    # 7. Buildings at each place: a paved yard with non-overlapping buildings.
    #    Pit places get the open pit (drawn above) and a couple of sheds on the rim instead.
    pits = dict(spec.get("pits", []))
    for pname, u, v, size in spec["places"]:
        cx, cy = px(u, v)
        if pname in pits:
            rim = pits[pname] * W
            for ang in (0.6, 2.3):
                sx, sy = cx + math.cos(ang) * rim * 1.05, cy - math.sin(ang) * rim * 0.84
                d.rectangle([sx - 26 * s, sy - 18 * s, sx + 26 * s, sy + 18 * s], fill=pal["roofs"][0] + (255,), outline=(60, 62, 64, 255), width=int(2 * s))
            continue
        pad = (28 + size * 18) * s * 2
        d.rounded_rectangle([cx - pad, cy - pad * 0.75, cx + pad, cy + pad * 0.75], radius=int(10 * s),
                            fill=(96, 98, 96, 235), outline=(140, 140, 136, 255), width=int(2 * s))
        placed = []
        for _ in range(200):
            if len(placed) >= 2 + size * 3:
                break
            bw, bh = rng.uniform(14, 34) * s, rng.uniform(12, 26) * s
            bx = cx + rng.uniform(-pad + bw + 8 * s, pad - bw - 8 * s)
            by = cy + rng.uniform(-pad * 0.75 + bh + 8 * s, pad * 0.75 - bh - 8 * s)
            box = (bx - bw - 6 * s, by - bh - 6 * s, bx + bw + 6 * s, by + bh + 6 * s)
            if any(not (box[2] < o[0] or box[0] > o[2] or box[3] < o[1] or box[1] > o[3]) for o in placed):
                continue
            placed.append(box)
            d.rectangle([bx - bw + 5 * s, by - bh + 7 * s, bx + bw + 5 * s, by + bh + 7 * s], fill=(18, 20, 20, 160))
            roof = pal["roofs"][int(rng.integers(len(pal["roofs"])))]
            d.rectangle([bx - bw, by - bh, bx + bw, by + bh], fill=roof + (255,), outline=(56, 58, 60, 255), width=int(2 * s))
            d.line([bx - bw, by, bx + bw, by], fill=tuple(int(c * 0.82) for c in roof) + (255,), width=int(2 * s))

    # 8. Outside the playable area: near-black with a soft edge (like a real game minimap)
    mask = Image.fromarray((mask_small * 255).astype(np.uint8)).resize((W, W), Image.BICUBIC).filter(ImageFilter.GaussianBlur(14 * SS))
    bg = Image.new("RGBA", (W, W), OUTSIDE + (255,))
    img = Image.composite(img, bg, mask)
    img = img.resize((SIZE, SIZE), Image.LANCZOS).convert("RGB")
    img.save(out_dir / f"{name}_Minimap.png", optimize=True)

    # 9. Layout for the data generator: places, roads, playable mask, height field
    g = LAYOUT_GRID
    height = fbm(g, rng, (3, 7), (0.7, 0.3))
    for pit, radius in spec.get("pits", []):   # pits are lower
        pu, pv = place_xy(spec)[pit]
        yy, xx = np.mgrid[0:g, 0:g] / (g - 1)
        height -= 0.6 * np.clip(1 - np.hypot(xx - pu, (1 - yy) - pv) / radius, 0, 1)
    height = (height - height.min()) / (np.ptp(height) or 1)
    layout = {
        "map": name, "label": spec["label"], "scale": spec["scale"], "ox": spec["ox"], "oz": spec["oz"],
        "elevation": spec["elevation"],
        "places": [{"name": n, "u": u, "v": v, "size": sz} for n, u, v, sz in spec["places"]],
        "roads": [[[round(a, 4), round(b, 4)] for a, b in line] for line in roads],
        "grid": g,
        "playable": np.flipud(island_mask(g, spec["style"], np.random.default_rng(spec["seed"]), spec["places"])).astype(int).tolist(),
        "height": np.round(np.flipud(height), 3).tolist(),   # row 0 = v 0 (bottom)
        # terrain for pathfinding (same grid and orientation): water, roads, bridges = both
        "water": grid_mask(water_lines, g),
        "road": grid_mask([(line, 0.012) for line in roads], g),
    }
    (out_dir / f"{name}_layout.json").write_text(json.dumps(layout))
    print(f"  {name}: {SIZE}x{SIZE}, {len(spec['places'])} places, {len(roads)} roads")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Draw the demo minimaps.")
    parser.add_argument("--output", required=True, type=Path, help="folder for minimaps + layouts")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for map_name, map_spec in MAPS.items():
        draw_map(map_name, map_spec, args.output)
