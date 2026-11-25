"""The 3 demo maps: places, roads, water and world settings.

Everything is in map fractions (u across, v up, 0–1), like the pipeline uses.
make_maps.py draws the minimaps from this; make_data.py moves players along
these roads and between these places.
"""

# scale / ox / oz: world (x, z) → minimap, same meaning as pipeline/config.py
# elevation: range of the y (height) column for this map
MAPS = {
    "SaltmarshHollow": {
        "label": "Saltmarsh Hollow", "scale": 900, "ox": -450, "oz": -450, "elevation": (100, 160),
        "style": "marsh", "seed": 11,
        # (name, u, v, size 1–3)
        "places": [
            ("Harbor Works", 0.22, 0.20, 3), ("Old Cannery", 0.40, 0.30, 2), ("Reed Village", 0.62, 0.25, 2),
            ("Lighthouse Point", 0.82, 0.16, 1), ("Pump Station", 0.50, 0.52, 2), ("Salt Flats Depot", 0.76, 0.50, 3),
            ("Ferry Landing", 0.28, 0.58, 1), ("Hilltop Chapel", 0.42, 0.78, 1), ("North Farm", 0.70, 0.80, 2),
            ("Radio Mast", 0.20, 0.80, 1),
        ],
        # roads: chains of places (by name) or extra points (u, v)
        "roads": [
            ["Harbor Works", "Old Cannery", "Reed Village", "Lighthouse Point"],
            ["Harbor Works", "Ferry Landing", "Radio Mast", "Hilltop Chapel", "North Farm"],
            ["Old Cannery", "Pump Station", "Salt Flats Depot", "Reed Village"],
            ["Ferry Landing", "Pump Station", "Hilltop Chapel"],
            ["Salt Flats Depot", "North Farm"],
        ],
        "water": [  # rivers: list of points, then width (map fraction)
            ([(0.55, 0.98), (0.52, 0.80), (0.58, 0.65), (0.46, 0.45), (0.33, 0.40), (0.14, 0.30), (0.02, 0.24)], 0.022),
            ([(0.46, 0.45), (0.56, 0.36), (0.70, 0.38), (0.90, 0.30), (0.99, 0.32)], 0.012),
        ],
    },
    "CinderQuarry": {
        "label": "Cinder Quarry", "scale": 640, "ox": -300, "oz": -340, "elevation": (10, 55),
        "style": "quarry", "seed": 23,
        "places": [
            ("Crusher Plant", 0.50, 0.50, 3), ("North Pit", 0.48, 0.78, 3), ("West Pit", 0.20, 0.45, 2),
            ("Miners' Camp", 0.78, 0.72, 2), ("Conveyor Ridge", 0.72, 0.42, 1), ("Slag Heaps", 0.75, 0.18, 2),
            ("Control Tower", 0.30, 0.20, 1), ("Fuel Depot", 0.18, 0.75, 1),
        ],
        "roads": [
            ["Control Tower", "Crusher Plant", "Conveyor Ridge", "Miners' Camp"],
            ["West Pit", "Crusher Plant", "North Pit", "Miners' Camp"],
            ["Fuel Depot", "West Pit", "Control Tower", "Slag Heaps", "Conveyor Ridge"],
            ["Fuel Depot", "North Pit"],
            ["Crusher Plant", "Slag Heaps"],
        ],
        "pits": [("North Pit", 0.13), ("West Pit", 0.09)],   # open pits: radius (map fraction)
        "water": [([(0.05, 0.05), (0.18, 0.10), (0.30, 0.06)], 0.018)],  # settling pond channel
    },
    "PylonYard": {
        "label": "Pylon Yard", "scale": 520, "ox": -260, "oz": -250, "elevation": (30, 42),
        "style": "yard", "seed": 37,
        "places": [
            ("Substation", 0.50, 0.55, 3), ("Container Stacks", 0.25, 0.70, 3), ("Rail Depot", 0.72, 0.75, 2),
            ("Cooling Towers", 0.78, 0.35, 2), ("Admin Block", 0.30, 0.30, 2), ("Loading Docks", 0.52, 0.20, 2),
            ("Scrap Lot", 0.18, 0.48, 1),
        ],
        "roads": [
            ["Admin Block", "Substation", "Rail Depot"],
            ["Container Stacks", "Substation", "Cooling Towers"],
            ["Scrap Lot", "Admin Block", "Loading Docks", "Cooling Towers", "Rail Depot"],
            ["Scrap Lot", "Container Stacks", "Rail Depot"],
            ["Loading Docks", "Substation"],
        ],
        "rails": [[(0.05, 0.88), (0.95, 0.88)], [(0.88, 0.05), (0.88, 0.95)]],
        "pylons": [[(0.10, 0.10), (0.50, 0.55), (0.95, 0.60)]],
        "water": [([(0.02, 0.06), (0.40, 0.04), (0.98, 0.08)], 0.02)],  # canal along the south edge
    },
}
