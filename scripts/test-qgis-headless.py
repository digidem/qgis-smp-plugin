#!/usr/bin/python3
"""
Headless QGIS integration test for the CoMapeo SMP plugin.

Runs the generator through real QGIS CRS transforms, style generation,
archive building (with dedup), cancellation, and format support — all
without a GUI. Uses the system Python that ships with QGIS bindings.

Usage:
    ./scripts/test-qgis-headless.py
    # or from repo root:
    scripts/test-qgis-headless.py
"""
import sys
import os

# ---------------------------------------------------------------------------
# Bootstrap: find QGIS Python bindings (system Python, not pyenv/venv)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)

# Ensure we use the system Python's site-packages where QGIS lives
sys.path.insert(0, '/usr/lib/python3/dist-packages')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QGIS_PREFIX_PATH', '/usr')

from qgis.core import (  # noqa: E402
    QgsApplication, QgsProject, QgsRectangle,
    QgsCoordinateReferenceSystem, QgsVectorLayer, QgsFeature,
    QgsGeometry, QgsPointXY, Qgis,
)

app = QgsApplication([], False)
app.initQgis()
print("QGIS {} initialized (headless)".format(Qgis.QGIS_VERSION))

sys.path.insert(0, REPO_DIR)
from comapeo_smp_generator import SMPGenerator, TileCache  # noqa: E402

import tempfile  # noqa: E402
import shutil  # noqa: E402
import json  # noqa: E402
import zipfile  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [PASS] {}".format(label))
    else:
        FAIL += 1
        print("  [FAIL] {} — {}".format(label, detail))


# ---------------------------------------------------------------------------
# Setup: create a memory layer
# ---------------------------------------------------------------------------
layer = QgsVectorLayer(
    "Point?crs=EPSG:4326&field=name:string(20)", "test_points", "memory"
)
pr = layer.dataProvider()
for lon, lat, name in [(-5, 5, "A"), (5, 5, "B"), (0, -5, "C")]:
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
    f.setAttributes([name])
    pr.addFeature(f)
layer.updateExtents()
QgsProject.instance().addMapLayer(layer)
extent = layer.extent()
gen = SMPGenerator()

tmp = tempfile.mkdtemp()

try:
    # ------------------------------------------------------------------
    print("\n--- CRS & Bounds ---")

    bounds = gen._get_bounds_wgs84(extent)
    check("WGS84 bounds has 4 floats", len(bounds) == 4)
    check("West < East", bounds[0] < bounds[2])
    check("South < North", bounds[1] < bounds[3])

    # ------------------------------------------------------------------
    print("\n--- Tile Math ---")

    count = gen.estimate_tile_count(extent, 0, 5)
    check("Tile count > 0", count > 0)
    check("Tile count < 5000", count < 5000)

    rects = gen.get_tile_grid_rects(extent, 0, 2)
    check("Tile grid rects > 0", len(rects) > 0)
    bounds_ok = all(r['west'] < r['east'] and r['south'] < r['north'] for r in rects)
    check("All rect bounds valid (west<east, south<north)", bounds_ok)

    count_val, warn = gen.validate_tile_count(extent, 0, 5)
    check("validate_tile_count returns count", count_val > 0)
    check("No warning for small extent", warn is None)

    # ------------------------------------------------------------------
    print("\n--- Style Generation ---")

    style = gen._create_style_from_canvas(extent, 0, 10, 'PNG')
    src = list(style['sources'].values())[0]
    first_source_id = list(style['sources'].keys())[0]
    check("Style version 8", style['version'] == 8)
    check("PNG tiles URL", '.png' in src['tiles'][0])
    check("minzoom=0, maxzoom=10", src['minzoom'] == 0 and src['maxzoom'] == 10)
    check("Local-only source id = mbtiles-source", first_source_id == 'mbtiles-source')
    check("Local-only source folder = s/0",
          style['metadata']['smp:sourceFolders'][first_source_id] == 's/0')
    check("Local-only tile path uses s/0",
          src['tiles'][0].endswith('s/0/{z}/{x}/{y}.png'))

    style_webp = gen._create_style_from_canvas(extent, 0, 10, 'WEBP')
    src_webp = list(style_webp['sources'].values())[0]
    check("WebP format field", src_webp['format'] == 'webp')
    check("WebP tile extension", '.webp' in src_webp['tiles'][0])

    world_local_plan = gen._build_export_plan(
        extent, 6, 12,
        include_world_base_zooms=True, world_max_zoom=3
    )
    style_world_local = gen._create_style_from_canvas(
        extent, 6, 12, 'PNG',
        include_world_base_zooms=True, world_max_zoom=3,
        source_bounds=world_local_plan['source_bounds'],
        source_plans=world_local_plan['sources']
    )
    world_local_sources = style_world_local['sources']
    check("World+Local style uses two sources",
          set(world_local_sources.keys()) == {'world-overview', 'local-detail'},
          "got {}".format(list(world_local_sources.keys())))
    check("World overview folder = s/0",
          style_world_local['metadata']['smp:sourceFolders'].get('world-overview') == 's/0')
    check("Local detail folder = s/2",
          style_world_local['metadata']['smp:sourceFolders'].get('local-detail') == 's/2')
    check("World overview tile path uses s/0 PNG",
          world_local_sources['world-overview']['tiles'][0].endswith('s/0/{z}/{x}/{y}.png'))
    check("Local detail tile path uses s/2 PNG",
          world_local_sources['local-detail']['tiles'][0].endswith('s/2/{z}/{x}/{y}.png'))
    check("World+Local layer order is world then local",
          [layer['id'] for layer in style_world_local['layers'][1:3]] == ['world-raster', 'local-raster'])
    check("World+Local style bounds follow local extent",
          style_world_local['metadata']['smp:bounds'] == world_local_plan['sources'][1]['source_bounds'])

    full_plan = gen._build_export_plan(
        extent, 6, 12,
        include_world_base_zooms=True,
        world_max_zoom=3,
        include_region=True,
        region_extent=QgsRectangle(extent.xMinimum() - 1, extent.yMinimum() - 1,
                                  extent.xMaximum() + 1, extent.yMaximum() + 1),
        region_min_zoom=4,
        region_max_zoom=5,
    )
    style_full = gen._create_style_from_canvas(
        extent, 6, 12, 'PNG',
        include_world_base_zooms=True,
        world_max_zoom=3,
        source_bounds=full_plan['source_bounds'],
        source_plans=full_plan['sources']
    )
    full_sources = style_full['sources']
    check("Full style uses three sources",
          set(full_sources.keys()) == {'world-overview', 'region-detail', 'local-detail'},
          "got {}".format(list(full_sources.keys())))
    check("Region detail folder = s/1",
          style_full['metadata']['smp:sourceFolders'].get('region-detail') == 's/1')
    check("Full layer order is world then region then local",
          [layer['id'] for layer in style_full['layers'][1:4]] == ['world-raster', 'region-raster', 'local-raster'])
    check("Full style bounds follow local extent",
          style_full['metadata']['smp:bounds'] == full_plan['sources'][2]['source_bounds'])

    # ------------------------------------------------------------------
    print("\n--- Multi-Source Archive Roundtrip ---")

    roundtrip_plan = gen._build_export_plan(
        extent, 6, 7,
        include_world_base_zooms=True,
        world_max_zoom=3,
        include_region=True,
        region_extent=QgsRectangle(extent.xMinimum() - 1, extent.yMinimum() - 1,
                                  extent.xMaximum() + 1, extent.yMaximum() + 1),
        region_min_zoom=4,
        region_max_zoom=5,
    )
    out_roundtrip = os.path.join(tmp, 'world-roundtrip.smp')
    roundtrip_result = gen.generate_smp_from_canvas(
        extent, 6, 7, out_roundtrip,
        tile_format='PNG',
        include_world_base_zooms=True,
        world_max_zoom=3,
        include_region=True,
        region_extent=QgsRectangle(extent.xMinimum() - 1, extent.yMinimum() - 1,
                                  extent.xMaximum() + 1, extent.yMaximum() + 1),
        region_min_zoom=4,
        region_max_zoom=5,
        export_plan=roundtrip_plan
    )
    check("PNG roundtrip archive created",
          roundtrip_result == out_roundtrip and os.path.exists(out_roundtrip))

    with zipfile.ZipFile(out_roundtrip) as zf:
        roundtrip_names = set(zf.namelist())
        roundtrip_style = json.loads(zf.read('style.json'))

    roundtrip_s0 = [
        n for n in roundtrip_names if n.startswith('s/0/') and n.endswith('.png')
    ]
    roundtrip_s1 = [
        n for n in roundtrip_names if n.startswith('s/1/') and n.endswith('.png')
    ]
    roundtrip_s2 = [
        n for n in roundtrip_names if n.startswith('s/2/') and n.endswith('.png')
    ]
    roundtrip_world = roundtrip_style['sources']['world-overview']
    roundtrip_region = roundtrip_style['sources']['region-detail']
    roundtrip_local = roundtrip_style['sources']['local-detail']
    check("Roundtrip style has three sources", len(roundtrip_style['sources']) == 3)
    check("Roundtrip style includes world-overview",
          'world-overview' in roundtrip_style['sources'])
    check("Roundtrip style includes region-detail",
          'region-detail' in roundtrip_style['sources'])
    check("Roundtrip style includes local-detail",
          'local-detail' in roundtrip_style['sources'])
    check("Roundtrip archive has expected world tile count",
          len(roundtrip_s0) == roundtrip_plan['sources'][0]['total_tiles'],
          "found {} expected {}".format(
              len(roundtrip_s0), roundtrip_plan['sources'][0]['total_tiles']))
    check("Roundtrip archive has expected region tile count",
          len(roundtrip_s1) == roundtrip_plan['sources'][1]['total_tiles'],
          "found {} expected {}".format(
              len(roundtrip_s1), roundtrip_plan['sources'][1]['total_tiles']))
    check("Roundtrip archive has expected local tile count",
          len(roundtrip_s2) == roundtrip_plan['sources'][2]['total_tiles'],
          "found {} expected {}".format(
              len(roundtrip_s2), roundtrip_plan['sources'][2]['total_tiles']))
    check("Roundtrip world source zooms match plan",
          roundtrip_world['minzoom'] == roundtrip_plan['sources'][0]['export_zooms'][0] and
          roundtrip_world['maxzoom'] == roundtrip_plan['sources'][0]['export_zooms'][-1])
    check("Roundtrip region source zooms match plan",
          roundtrip_region['minzoom'] == roundtrip_plan['sources'][1]['export_zooms'][0] and
          roundtrip_region['maxzoom'] == roundtrip_plan['sources'][1]['export_zooms'][-1])
    check("Roundtrip local source zooms match plan",
          roundtrip_local['minzoom'] == roundtrip_plan['sources'][2]['export_zooms'][0] and
          roundtrip_local['maxzoom'] == roundtrip_plan['sources'][2]['export_zooms'][-1])
    check("Roundtrip bounds follow local extent",
          roundtrip_style['metadata']['smp:bounds'] == roundtrip_plan['sources'][2]['source_bounds'])
    check("Roundtrip sourceFolders match archive layout",
          roundtrip_style['metadata']['smp:sourceFolders'] == {
              'world-overview': 's/0',
              'region-detail': 's/1',
              'local-detail': 's/2'
          },
          "got {}".format(roundtrip_style['metadata'].get('smp:sourceFolders')))
    check("Roundtrip uses PNG sources only",
          all(src['format'] == 'png' and src['tiles'][0].endswith('.png')
              for src in roundtrip_style['sources'].values()),
          "formats={}".format([
              src['format'] for src in roundtrip_style['sources'].values()
          ]))

    # ------------------------------------------------------------------
    print("\n--- Archive Build (Dedup) ---")

    style_path = os.path.join(tmp, 'style.json')
    with open(style_path, 'w') as f:
        json.dump(style, f, indent=4)

    tiles_dir = os.path.join(tmp, 'tiles')
    identical = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
    for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)]:
        d = os.path.join(tiles_dir, '0', str(z), str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '{}.png'.format(y)), 'wb') as f:
            f.write(identical)

    out_dedup = os.path.join(tmp, 'dedup.smp')
    out_normal = os.path.join(tmp, 'normal.smp')
    gen._build_smp_archive(style_path, tiles_dir, out_dedup, dedup=True)
    gen._build_smp_archive(style_path, tiles_dir, out_normal, dedup=False)

    sz_d = os.path.getsize(out_dedup)
    sz_n = os.path.getsize(out_normal)
    check("Dedup smaller than normal", sz_d < sz_n,
          "dedup={}B normal={}B".format(sz_d, sz_n))

    with zipfile.ZipFile(out_dedup) as zf:
        names = zf.namelist()
        check("VERSION in archive", 'VERSION' in names)
        check("VERSION = 1.0", zf.read('VERSION').decode() == '1.0')
        check("style.json in archive", 'style.json' in names)
        tiles_in = [n for n in names if n.startswith('s/0/')]
        check("5 tile entries", len(tiles_in) == 5, "got {}".format(len(tiles_in)))
        tiles_stored = all(
            zf.getinfo(n).compress_type == zipfile.ZIP_STORED for n in tiles_in
        )
        check("Tiles use ZIP_STORED", tiles_stored)
        check("style.json uses ZIP_DEFLATED",
              zf.getinfo('style.json').compress_type == zipfile.ZIP_DEFLATED)

    # ------------------------------------------------------------------
    print("\n--- Cancellation ---")

    fb1 = MagicMock()
    fb1.isCanceled.return_value = True
    g1 = SMPGenerator(feedback=fb1)
    r1 = g1.generate_smp_from_canvas(extent, 0, 0, os.path.join(tmp, 'c1.smp'))
    check("generate_smp returns None on cancel", r1 is None)

    fb2 = MagicMock()
    # Wrapper os.walk: 1 (before loop) + 5 (one per tile) = 6 False calls
    # Phase 1 hashing: 5 calls (all False, completes)
    # Phase 2 tile-writing: 1 call (False, unique tile written)
    # Phase 2 CD writing: True on first CD entry
    fb2.isCanceled.side_effect = [False]*6 + [False]*5 + [False] + [True]
    g2 = SMPGenerator(feedback=fb2)
    r2 = g2._build_smp_archive(
        style_path, tiles_dir, os.path.join(tmp, 'c2.smp'), dedup=True
    )
    check("dedup archive returns False on cancel", r2 is False)

    # ------------------------------------------------------------------
    print("\n--- Format Constants ---")

    check("TILE_FORMAT_WEBP = 'WEBP'", SMPGenerator.TILE_FORMAT_WEBP == 'WEBP')
    check("_tile_extension(WEBP) = 'webp'", SMPGenerator._tile_extension('WEBP') == 'webp')

    # ------------------------------------------------------------------
    print("\n" + "=" * 50)
    if FAIL == 0:
        print("ALL {} CHECKS PASSED".format(PASS))
    else:
        print("{} PASSED, {} FAILED".format(PASS, FAIL))
    print("=" * 50)

finally:
    shutil.rmtree(tmp, ignore_errors=True)
    app.exitQgis()

sys.exit(1 if FAIL else 0)
