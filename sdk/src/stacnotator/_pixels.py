"""Pixels of a rendered view back to coordinates.

Every map cell of a view is a web mercator map centred on the task at the cell's zoom,
drawn into the rectangle ``rect`` (x, y, width, height) of the packed image. A pixel the
agent points at is therefore an offset from the cell's centre in mercator pixels.
"""

import math
from typing import Any

TILE_PX = 256


def _world(lon: float, lat: float, zoom: float) -> tuple[float, float]:
    size = TILE_PX * 2**zoom
    phi = math.radians(lat)
    x = (lon + 180) / 360 * size
    y = (1 - math.log(math.tan(phi) + 1 / math.cos(phi)) / math.pi) / 2 * size
    return x, y


def _lonlat(x: float, y: float, zoom: float) -> tuple[float, float]:
    size = TILE_PX * 2**zoom
    lon = x / size * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size))))
    return lon, lat


def map_cell_at(meta: dict[str, Any], x: float, y: float) -> dict[str, Any] | None:
    """The map cell of a view's meta whose rectangle holds the image pixel (x, y)."""
    cell: dict[str, Any]
    for cell in meta.get("cells", []):
        rect = cell.get("rect")
        if rect and "zoom" in cell:
            left, top, width, height = rect
            if left <= x <= left + width and top <= y <= top + height:
                return cell
    return None


def pixel_to_lonlat(
    center: tuple[float, float], cell: dict[str, Any], x: float, y: float
) -> tuple[float, float]:
    """Image pixel (x, y) inside ``cell`` -> (lon, lat), for a cell centred on ``center``."""
    left, top, width, height = cell["rect"]
    zoom = cell["zoom"]
    cx, cy = _world(center[0], center[1], zoom)
    return _lonlat(cx + x - (left + width / 2), cy + y - (top + height / 2), zoom)


def polygon_wkt(meta: dict[str, Any], pixels: list[list[float]]) -> str:
    """A polygon given as image pixels of one map cell, as WKT in lon/lat."""
    if len(pixels) < 3:
        raise ValueError("A building needs at least 3 corners")
    cells = {id(map_cell_at(meta, x, y)) for x, y in pixels}
    cell = map_cell_at(meta, *pixels[0])
    if cell is None or len(cells) != 1:
        raise ValueError("Every corner of a building must lie in the same map cell of the image")
    task = meta["task"]
    ring = [pixel_to_lonlat((task["lon"], task["lat"]), cell, x, y) for x, y in pixels]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return "POLYGON((" + ",".join(f"{lon:.9f} {lat:.9f}" for lon, lat in ring) + "))"
