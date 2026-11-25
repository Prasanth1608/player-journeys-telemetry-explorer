"""Generate a demo player_data folder: same format as a real battle-royale telemetry export.

One parquet file per player per match, named {user_id}_{match_id}.nakama-0, in one
folder per day. Columns: user_id, match_id, map_id, x, y, z, ts, event (bytes).
Events: Position, BotPosition, Kill, Killed, BotKill, BotKilled, KilledByStorm, Loot.

How a match is simulated (all on the maps from make_maps.py):
  - humans drop in anywhere and travel across the terrain (pathfinding: open ground,
    a slight preference for roads, water crossed only at bridges, a personal route
    preference per player) to visit 2–4 places (bigger places are more popular),
    loot while there, fight bots on the way, and extract on the safe side;
  - a one-directional storm sweeps across the map; anyone left behind dies to it;
  - tracked bots patrol loosely between places, lingering at each; humans can kill them;
  - humans who meet can fight (Kill / Killed with the same timestamp).
Real-data quirks are kept on purpose, so the pipeline's cleaning has work to do:
ts stored as milliseconds but holding seconds, ~2% exact duplicate rows, occasional
missed snapshots, a few bot-only matches, a few human players with numeric
(bot-style) ids, and a partial last day.

Usage (from the repo root):
    python datagen/make_data.py --maps ../demo_data/player_data/minimaps --output ../demo_data/player_data
"""

import argparse
import heapq
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

# Day folder → (matches per map, hours covered). The last day is partial.
DAYS = {
    "March_03": ({"SaltmarshHollow": 110, "CinderQuarry": 60, "PylonYard": 50}, 24),
    "March_04": ({"SaltmarshHollow": 40, "CinderQuarry": 22, "PylonYard": 18}, 11),
}
YEAR = 2026
STEP = 5.0                    # seconds between position snapshots
HUMAN_POOL = 240              # different human players across both days
BOT_ID_RANGE = (2101, 2299)   # bots have short numeric ids
SCHEMA = pa.schema([("user_id", pa.string()), ("match_id", pa.string()), ("map_id", pa.string()),
                    ("x", pa.float32()), ("y", pa.float32()), ("z", pa.float32()),
                    ("ts", pa.timestamp("ms")), ("event", pa.binary())])


# ---------------------------------------------------------------------------
# Map: road graph, world coordinates, height
# ---------------------------------------------------------------------------
class Map:
    """Terrain grid + pathfinding. Players cross open ground, prefer roads a little,
    and can only cross water where a road (bridge) does."""
    ROUTE_STYLES = 12   # each player gets one of these preference fields, so routes differ

    def __init__(self, layout):
        self.name = layout["map"]
        self.scale, self.ox, self.oz = layout["scale"], layout["ox"], layout["oz"]
        self.lo, self.hi = layout["elevation"]
        self.height = np.array(layout["height"], np.float32)
        self.g = g = layout["grid"]
        self.places = layout["places"]
        water = np.array(layout["water"], bool)
        road = np.array(layout["road"], bool)
        self.walkable = np.array(layout["playable"], bool) & (~water | road)   # water only at bridges
        rng = np.random.default_rng(layout["scale"])
        base = np.where(road, 0.8, 1.0)                                       # roads are slightly quicker
        self.costs = [base * (0.5 + 1.6 * smooth_noise(g, rng)) for _ in range(self.ROUTE_STYLES)]
        self.cache = {}

    # --- grid helpers (row = v, column = u; row 0 = v 0) ---
    def cell(self, u, v):
        """Nearest walkable cell to (u, v)."""
        i = min(self.g - 1, max(0, int(v * self.g)))
        j = min(self.g - 1, max(0, int(u * self.g)))
        if self.walkable[i, j]:
            return i, j
        for r in range(1, self.g):
            ring = [(i + di, j + dj) for di in range(-r, r + 1) for dj in range(-r, r + 1)
                    if max(abs(di), abs(dj)) == r and 0 <= i + di < self.g and 0 <= j + dj < self.g]
            ok = [c for c in ring if self.walkable[c]]
            if ok:
                return min(ok, key=lambda c: (c[0] - i) ** 2 + (c[1] - j) ** 2)
        return i, j

    def random_spot(self, rng):
        """A random walkable point (where a player drops in)."""
        cells = np.argwhere(self.walkable)
        i, j = cells[int(rng.integers(len(cells)))]
        return ((j + rng.random()) / self.g, (i + rng.random()) / self.g)

    def path(self, a, b, style, rng=None):
        """Terrain route from a to b (u, v) as a smooth list of (u, v). With rng, the route
        often bends through a random detour point (flanking, avoiding open ground)."""
        if rng is not None and rng.random() < 0.6 and math.dist(a, b) > 0.12:
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            off = rng.uniform(-0.35, 0.35) * length
            detour = (mid[0] - dy / length * off, mid[1] + dx / length * off)
            first = self.path(a, detour, style)
            return first + self.path(detour, b, style)[1:]
        ca, cb = self.cell(*a), self.cell(*b)
        key = (style, ca, cb)
        if key not in self.cache:
            self.cache[key] = self._astar(ca, cb, self.costs[style])
        cells = self.cache[key]
        pts = [a] + [((j + 0.5) / self.g, (i + 0.5) / self.g) for i, j in cells[2:-2:3]] + [b]
        return smooth(pts)

    def _astar(self, start, goal, cost):
        g, walk = self.g, self.walkable
        best = {start: 0.0}
        prev = {}
        todo = [(0.0, start)]
        cheapest = float(cost.min())
        while todo:
            _, cur = heapq.heappop(todo)
            if cur == goal:
                break
            ci, cj = cur
            for di, dj, step in NEIGHBOURS:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < g and 0 <= nj < g) or not walk[ni, nj]:
                    continue
                d = best[cur] + step * (cost[ci, cj] + cost[ni, nj]) / 2
                if d < best.get((ni, nj), 1e18):
                    best[(ni, nj)] = d
                    prev[(ni, nj)] = cur
                    h = math.hypot(goal[0] - ni, goal[1] - nj) * cheapest
                    heapq.heappush(todo, (d + h, (ni, nj)))
        out, cur = [goal], goal
        while cur != start and cur in prev:
            cur = prev[cur]
            out.append(cur)
        return out[::-1]

    def world(self, u, v, rng):
        gi = min(self.g - 1, max(0, int(v * self.g)))
        gj = min(self.g - 1, max(0, int(u * self.g)))
        y = self.lo + self.height[gi, gj] * (self.hi - self.lo) + rng.normal(0, 0.6)
        return self.ox + u * self.scale, y, self.oz + v * self.scale


NEIGHBOURS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
              (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414)]


def smooth_noise(n, rng, cells=7):
    """Smooth random field (0–1): a coarse random grid scaled up (bicubic)."""
    grid = (rng.random((cells, cells)) * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(grid).resize((n, n), Image.BICUBIC), np.float32) / 255


def smooth(points, steps=4):
    """Catmull-Rom curve through the points, so routes bend naturally instead of in grid steps."""
    if len(points) < 3:
        return points
    pts = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for k in range(steps):
            t = k / steps
            out.append(tuple(0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t * t
                                    + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t ** 3) for c in (0, 1)))
    out.append(points[-1])
    return out


# ---------------------------------------------------------------------------
# Movement
# ---------------------------------------------------------------------------
def walk(points, speed, rng, start_t, end_t, jitter=0.004):
    """Sample a position every ~5 s along a polyline. Returns [(t, u, v)]."""
    out, t, seg, along = [], start_t, 0, 0.0
    while t <= end_t and seg < len(points) - 1:
        a, b = points[seg], points[seg + 1]
        length = math.dist(a, b) or 1e-9
        f = along / length
        out.append((t, a[0] + (b[0] - a[0]) * f + rng.normal(0, jitter), a[1] + (b[1] - a[1]) * f + rng.normal(0, jitter)))
        dt = STEP if rng.random() > 0.15 else STEP * 2          # occasional missed snapshot
        t += dt
        along += speed * dt
        while seg < len(points) - 1 and along > math.dist(points[seg], points[seg + 1]):
            along -= math.dist(points[seg], points[seg + 1])
            seg += 1
    return out, t


def loiter(center, radius, rng, start_t, seconds):
    """Wander around inside a place (looting)."""
    out, t = [], start_t
    u, v = center
    while t < start_t + seconds:
        u += rng.normal(0, radius * 0.35)
        v += rng.normal(0, radius * 0.35)
        u = center[0] + max(-radius, min(radius, u - center[0]))
        v = center[1] + max(-radius, min(radius, v - center[1]))
        out.append((t, u, v))
        t += STEP
    return out, t


class Storm:
    """One-directional storm: a straight front sweeping across the map during the match."""
    def __init__(self, rng, duration):
        self.angle = rng.uniform(0, 2 * math.pi)
        self.dir = (math.cos(self.angle), math.sin(self.angle))
        self.duration = duration

    def front(self, t):        # position of the front along the sweep direction, −0.75 … +0.15
        return -0.75 + 0.9 * min(1.0, t / self.duration)

    def depth(self, u, v):     # where a point lies along the sweep (−0.7 = first hit, +0.7 = safest)
        return (u - 0.5) * self.dir[0] + (v - 0.5) * self.dir[1]

    def caught(self, t, u, v):
        return self.depth(u, v) < self.front(t)


# ---------------------------------------------------------------------------
# One match
# ---------------------------------------------------------------------------
def simulate_match(m: Map, rng, humans, bot_ids, start_unix):
    duration = float(np.clip(rng.normal(450, 170), 200, 900))
    storm = Storm(rng, duration)
    weights = np.array([p["size"] ** 1.6 for p in m.places], float)
    weights /= weights.sum()
    rows = {}   # user → list of (t, u, v, event)

    # --- Bots that have their own files: patrol between 2–3 places ---
    bot_tracks = {}
    for b in bot_ids:
        stops = list(rng.choice(len(m.places), size=int(rng.integers(2, 4)), replace=False, p=weights))
        style, speed = int(rng.integers(Map.ROUTE_STYLES)), rng.uniform(1.6, 2.4) / m.scale
        here = (m.places[stops[0]]["u"], m.places[stops[0]]["v"])
        t, track, k = rng.uniform(0, 30), [], 1
        while t < duration:                                   # patrol: walk to the next stop, linger, repeat
            nxt = m.places[stops[k % len(stops)]]
            there = (nxt["u"] + rng.normal(0, 0.015), nxt["v"] + rng.normal(0, 0.015))
            leg, t = walk(m.path(here, there, style, rng), speed, rng, t, duration, jitter=0.003)
            stay, t = loiter(there, 0.02, rng, t, rng.uniform(10, 45))
            track += leg + stay
            here, k = there, k + 1
        track = [p for p in track if p[0] <= duration]
        bot_tracks[b] = track
        rows[b] = [(t, u, v, "BotPosition") for t, u, v in track]

    # --- Humans ---
    tracks = {}
    for h in humans:
        n_stops = int(rng.integers(2, 5))
        stops = list(rng.choice(len(m.places), size=n_stops, replace=False, p=weights))
        # extract at the place furthest from the storm's start
        safest = max(range(len(m.places)), key=lambda i: storm.depth(m.places[i]["u"], m.places[i]["v"]))
        if stops[-1] != safest:
            stops.append(safest)
        here = m.random_spot(rng)                               # drop in anywhere on the map
        style = int(rng.integers(Map.ROUTE_STYLES))
        t, track, events = rng.uniform(0, 20), [], []
        speed = rng.uniform(2.6, 3.8) / m.scale
        for s in stops:
            place = m.places[s]
            there = (place["u"] + rng.normal(0, 0.01), place["v"] + rng.normal(0, 0.01))
            leg, t = walk(m.path(here, there, style, rng), speed, rng, t, duration)
            track += leg
            stay, t = loiter((place["u"], place["v"]), 0.012 + 0.006 * place["size"], rng, t, rng.uniform(40, 95) * place["size"])
            track += stay
            for tt, u, v in stay:                       # looting while at a place
                if rng.random() < 0.42:
                    events.append((tt + rng.uniform(0.1, 4.9), u, v, "Loot"))
            here = stay[-1][1:] if stay else there
            if t >= duration:
                break
        track = [p for p in track if p[0] <= duration]
        # Fights with bots along the way (more at bigger places), maybe dying to one
        died = None
        for tt, u, v in track:
            if rng.random() < 0.05:
                events.append((tt + rng.uniform(0.1, 4.9), u, v, "BotKill"))
            if died is None and tt > 60 and rng.random() < 0.0045:
                died = (tt + rng.uniform(0.1, 4.9), u, v, "BotKilled")
            # Storm: only players caught well behind the front, late in the match, die to it
            if died is None and tt > duration * 0.6 and storm.depth(u, v) < storm.front(tt) - 0.12 and rng.random() < 0.025:
                died = (tt + rng.uniform(0.1, 4.9), u, v, "KilledByStorm")
        if died:
            track = [p for p in track if p[0] <= died[0]]
            events = [e for e in events if e[0] < died[0]] + [died]
        tracks[h] = track
        rows[h] = [(t, u, v, "Position") for t, u, v in track] + events

    # --- Human vs human: when two humans are close, one may kill the other ---
    alive_until = {h: max(r[0] for r in rows[h]) if rows[h] else 0 for h in humans}
    for i, a in enumerate(humans):
        for b in humans[i + 1:]:
            pos_b = {round(t / STEP): (u, v) for t, u, v in tracks[b]}
            for t, u, v in tracks[a]:
                other = pos_b.get(round(t / STEP))
                if other and math.dist((u, v), other) < 0.03 and rng.random() < 0.1 \
                        and t < min(alive_until[a], alive_until[b]):
                    killer, victim = (a, b) if rng.random() < 0.5 else (b, a)
                    kt = t + rng.uniform(0.1, 4.9)
                    kpos = (u, v) if killer == a else other
                    vpos = other if killer == a else (u, v)
                    rows[killer].append((kt, kpos[0], kpos[1], "Kill"))
                    rows[victim] = [r for r in rows[victim] if r[0] < kt] + [(kt, vpos[0], vpos[1], "Killed")]
                    alive_until[victim] = kt
                    break

    # --- Bots killed by humans: end some tracked bots' files with BotKilled ---
    for b, track in bot_tracks.items():
        if track and rng.random() < 0.7:
            cut = int(rng.integers(len(track) // 5 + 1, len(track) * 3 // 4 + 2)) - 1
            t, u, v = track[cut]
            rows[b] = [r for r in rows[b] if r[0] <= t] + [(t + rng.uniform(0.1, 4.9), u, v, "BotKilled")]
        if rng.random() < 0.25 and rows[b]:
            t, u, v, _ = rows[b][len(rows[b]) // 2]
            rows[b].append((t + 2.0, u, v, "BotKill"))

    # Keep everything on the map, convert to world coordinates and timestamps
    out = {}
    for user, rs in rows.items():
        rs = [(t, min(0.999, max(0.001, u)), min(0.999, max(0.001, v)), e) for t, u, v, e in rs]
        rs.sort(key=lambda r: r[0])
        out[user] = [(start_unix + t, *m.world(u, v, rng), e) for t, u, v, e in rs]
    return out


# ---------------------------------------------------------------------------
# Writing files
# ---------------------------------------------------------------------------
def write_file(path, user, match, map_id, rows, rng):
    if not rows:
        return 0
    if rng.random() < 0.35:   # quirk: exact duplicate rows (a logging bug)
        dups = [rows[k] for k in rng.choice(len(rows), size=max(1, len(rows) // 20), replace=False)]
        rows = sorted(rows + dups, key=lambda r: r[0])
    secs = np.array([r[0] for r in rows], np.float64)
    table = pa.table({
        "user_id": [user] * len(rows), "match_id": [match] * len(rows), "map_id": [map_id] * len(rows),
        "x": np.array([r[1] for r in rows], np.float32), "y": np.array([r[2] for r in rows], np.float32),
        "z": np.array([r[3] for r in rows], np.float32),
        # quirk kept from the real export: stored as milliseconds, but the numbers are seconds
        "ts": pa.array(np.round(secs).astype("int64"), pa.timestamp("ms")),
        "event": [r[4].encode() for r in rows],
    }, schema=SCHEMA)
    pq.write_table(table, path)
    return len(rows)


def main(maps_dir: Path, out_dir: Path, seed: int):
    rng = np.random.default_rng(seed)
    maps = {p.stem.replace("_layout", ""): Map(json.loads(p.read_text())) for p in sorted(maps_dir.glob("*_layout.json"))}
    pool = [str(uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63)), version=4)) for _ in range(HUMAN_POOL)]
    popularity = 1 / np.arange(1, HUMAN_POOL + 1) ** 0.55     # a few very active players
    popularity /= popularity.sum()
    total_files = total_rows = 0
    for day_index, (day, (per_map, hours)) in enumerate(DAYS.items()):
        folder = out_dir / day
        folder.mkdir(parents=True, exist_ok=True)
        month, dom = day.split("_")
        day_start = int(datetime(YEAR, datetime.strptime(month, "%B").month, int(dom), tzinfo=timezone.utc).timestamp())
        for map_id, count in per_map.items():
            m = maps[map_id]
            for _ in range(count):
                match = f"{uuid.UUID(int=int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63)), version=4)}.nakama-0"
                n_humans = 0 if rng.random() < 0.02 else int(rng.choice([1, 2, 3, 4, 5, 6], p=[0.18, 0.24, 0.22, 0.16, 0.12, 0.08]))
                humans = list(rng.choice(pool, size=n_humans, replace=False, p=popularity)) if n_humans else []
                # quirk: ~1% of human files carry a numeric (bot-style) id; they still log Position + Loot
                humans = [str(int(rng.integers(1000, 2000))) if rng.random() < 0.01 else h for h in humans]
                n_bots = 0 if (rng.random() < 0.5 and n_humans) else int(rng.integers(3, 11))
                bots = [str(b) for b in rng.choice(np.arange(*BOT_ID_RANGE), size=n_bots, replace=False)]
                start = day_start + int(rng.uniform(0, hours * 3600 - 900))
                for user, rows in simulate_match(m, rng, humans, bots, start).items():
                    total_rows += write_file(folder / f"{user}_{match}", user, match, map_id, rows, rng)
                    total_files += 1
        print(f"  {day}: {sum(per_map.values())} matches")
    print(f"Wrote {total_files} files, {total_rows:,} rows → {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate demo player_data.")
    parser.add_argument("--maps", required=True, type=Path, help="folder with <Map>_layout.json from make_maps.py")
    parser.add_argument("--output", required=True, type=Path, help="player_data folder to write")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    main(args.maps, args.output, args.seed)
