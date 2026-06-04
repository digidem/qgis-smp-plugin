"""Geographic / tile-coordinate helpers.

Ported from the JavaScript reference implementation (``lib/utils/geo.js``) and
``@mapbox/sphericalmercator`` so that bounds, ``smp:bounds`` and
``smp:bufferTiles`` are computed identically and survive round-trips between the
JS and Python implementations.
"""

import math

R2D = 180 / math.pi
D2R = math.pi / 180

# Spherical Mercator max bounds, rounded to 6 decimal places (geo.js MAX_BOUNDS).
MAX_BOUNDS = (-180.0, -85.051129, 180.0, 85.051129)


def _js_round(value):
    """Replicate JavaScript ``Math.round`` (round half up, not banker's)."""
    return math.floor(value + 0.5)


def _tile2lon(x, z):
    return x / (2 ** z) * 360 - 180


def _tile2lat(y, z):
    n = math.pi - (2 * math.pi * y) / (2 ** z)
    return R2D * math.atan(0.5 * (math.exp(n) - math.exp(-n)))


def tile_to_bbox(x, y, z):
    """Return the WGS84 bounding box ``[w, s, e, n]`` for an XYZ tile."""
    w = _tile2lon(x, z)
    e = _tile2lon(x + 1, z)
    n = _tile2lat(y, z)
    s = _tile2lat(y + 1, z)
    return [w, s, e, n]


def union_bbox(bboxes):
    """Smallest bounding box ``[w, s, e, n]`` containing all input bboxes."""
    w, s, e, n = bboxes[0]
    for bb in bboxes[1:]:
        w = min(w, bb[0])
        s = min(s, bb[1])
        e = max(e, bb[2])
        n = max(n, bb[3])
    return [w, s, e, n]


def tms_to_xyz_y(y, z):
    """Convert a TMS Y coordinate to an XYZ Y coordinate (``2**z - y - 1``)."""
    return 2 ** z - y - 1


class SphericalMercator:
    """Minimal port of ``@mapbox/sphericalmercator`` (integer-zoom code path).

    Only :meth:`xyz` is needed by the writer (to infer ``smp:bufferTiles``), and
    it is only ever called with integer zoom levels, so the floating-point zoom
    branch from the original is omitted.
    """

    def __init__(self, size=256):
        self.size = size
        self.Bc = []
        self.Cc = []
        self.zc = []
        self.Ac = []
        s = size
        for _ in range(30):
            self.Bc.append(s / 360.0)
            self.Cc.append(s / (2 * math.pi))
            self.zc.append(s / 2.0)
            self.Ac.append(s)
            s *= 2

    def px(self, ll, zoom):
        d = self.zc[zoom]
        f = min(max(math.sin(D2R * ll[1]), -0.9999), 0.9999)
        x = _js_round(d + ll[0] * self.Bc[zoom])
        y = _js_round(d + 0.5 * math.log((1 + f) / (1 - f)) * (-self.Cc[zoom]))
        ac = self.Ac[zoom]
        if x > ac:
            x = ac
        if y > ac:
            y = ac
        return [x, y]

    def xyz(self, bbox, zoom):
        """Return tile bounds ``{minX, minY, maxX, maxY}`` covering ``bbox``."""
        px_ll = self.px([bbox[0], bbox[1]], zoom)
        px_ur = self.px([bbox[2], bbox[3]], zoom)
        xs = [
            math.floor(px_ll[0] / self.size),
            math.floor((px_ur[0] - 1) / self.size),
        ]
        ys = [
            math.floor(px_ur[1] / self.size),
            math.floor((px_ll[1] - 1) / self.size),
        ]
        min_x = min(xs)
        min_y = min(ys)
        return {
            "minX": 0 if min_x < 0 else min_x,
            "minY": 0 if min_y < 0 else min_y,
            "maxX": max(xs),
            "maxY": max(ys),
        }


def _walk_positions(coords, cb):
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        cb(coords)
    else:
        for c in coords:
            _walk_positions(c, cb)


def _geometry_positions(geometry, cb):
    if not geometry:
        return
    if geometry.get("type") == "GeometryCollection":
        for g in geometry.get("geometries", []):
            _geometry_positions(g, cb)
    else:
        _walk_positions(geometry.get("coordinates", []), cb)


def geojson_bbox(geojson):
    """Compute the 2D bounding box ``[w, s, e, n]`` of any GeoJSON object.

    Equivalent to ``@turf/bbox``: visits every position in the geometry.
    """
    result = [math.inf, math.inf, -math.inf, -math.inf]

    def update(position):
        x, y = position[0], position[1]
        if result[0] > x:
            result[0] = x
        if result[1] > y:
            result[1] = y
        if result[2] < x:
            result[2] = x
        if result[3] < y:
            result[3] = y

    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        for feature in geojson.get("features", []):
            _geometry_positions(feature.get("geometry"), update)
    elif gtype == "Feature":
        _geometry_positions(geojson.get("geometry"), update)
    else:
        _geometry_positions(geojson, update)
    if result[0] == math.inf:
        # No coordinates were visited (e.g. null geometries): no bbox exists.
        return None
    return result


def bbox_2d(bbox):
    """Return a 2D ``[w, s, e, n]`` bbox from a (possibly 3D) GeoJSON bbox."""
    if len(bbox) == 4:
        return list(bbox)
    return [bbox[0], bbox[1], bbox[3], bbox[4]]
