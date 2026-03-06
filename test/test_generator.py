# coding=utf-8
"""Tests for SMPGenerator - does not require a running QGIS instance."""

import math
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Minimal stubs so we can import comapeo_smp_generator without QGIS installed
# ---------------------------------------------------------------------------

class _FakeRectangle:
    """Minimal stub for QgsRectangle used in tests."""

    def __init__(self, xmin, ymin, xmax, ymax):
        self._xmin = xmin
        self._ymin = ymin
        self._xmax = xmax
        self._ymax = ymax

    def xMinimum(self):
        return self._xmin

    def yMinimum(self):
        return self._ymin

    def xMaximum(self):
        return self._xmax

    def yMaximum(self):
        return self._ymax

    def asWktPolygon(self):
        return (f"POLYGON(({self._xmin} {self._ymin}, {self._xmax} {self._ymin}, "
                f"{self._xmax} {self._ymax}, {self._xmin} {self._ymax}, "
                f"{self._xmin} {self._ymin}))")


class _FakeCrs:
    pass


class _FakeProject:
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def crs(self):
        return _FakeCrs()

    def mapLayers(self):
        return {}

    def layerTreeRoot(self):
        root = MagicMock()
        root.findLayer.return_value = MagicMock(isVisible=MagicMock(return_value=True))
        return root


class _FakeTransform:
    def transformBoundingBox(self, rect):
        # Return the same rectangle (pretend project CRS == WGS84 for tests)
        return rect


# Patch the qgis modules before importing the generator
import sys
qgis_mock = MagicMock()
qgis_core_mock = MagicMock()

# Set up specific classes used by the generator
qgis_core_mock.QgsProject = _FakeProject
qgis_core_mock.QgsRectangle = _FakeRectangle
qgis_core_mock.QgsCoordinateReferenceSystem = MagicMock(return_value=MagicMock())
qgis_core_mock.QgsCoordinateTransform = MagicMock(return_value=_FakeTransform())
qgis_core_mock.Qgis = MagicMock()
qgis_core_mock.Qgis.Info = 0
qgis_core_mock.Qgis.Warning = 1
qgis_core_mock.Qgis.Critical = 2


class _FakeQgsTask:
    CanCancel = 1

    def __init__(self, description='', flags=0):
        pass

    def setProgress(self, p):
        pass


qgis_core_mock.QgsTask = _FakeQgsTask
qgis_core_mock.QgsMessageLog = MagicMock()
qgis_core_mock.QgsMapSettings = MagicMock()
qgis_core_mock.QgsMapRendererCustomPainterJob = MagicMock()

pyqt_core_mock = MagicMock()
pyqt_gui_mock = MagicMock()

sys.modules['qgis'] = qgis_mock
sys.modules['qgis.core'] = qgis_core_mock
sys.modules['qgis.PyQt'] = MagicMock()
sys.modules['qgis.PyQt.QtCore'] = pyqt_core_mock
sys.modules['qgis.PyQt.QtGui'] = pyqt_gui_mock

# Now import the generator (QGIS not needed for pure-logic methods)
from comapeo_smp_generator import (  # noqa: E402
    SMPGenerator,
    TileCache,
    TILE_COUNT_WARNING_THRESHOLD,
    BYTES_PER_TILE_PNG,
    BYTES_PER_TILE_JPG,
    MIN_FREE_SPACE_BYTES,
)


class TestTileCache(unittest.TestCase):
    """TileCache fingerprint-based invalidation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = TileCache(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_returns_false_when_no_entry(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.assertFalse(self.cache.is_fresh(0, 0, 0, fp))

    def test_mark_then_fresh(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp)
        self.assertTrue(self.cache.is_fresh(0, 0, 0, fp))

    def test_fingerprint_mismatch_not_fresh(self):
        self.cache.mark(0, 0, 0, TileCache.make_fingerprint('PNG', 85))
        self.assertFalse(self.cache.is_fresh(0, 0, 0, TileCache.make_fingerprint('JPG', 75)))

    def test_invalidate_removes_entry(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp)
        self.cache.invalidate(0, 0, 0)
        self.assertFalse(self.cache.is_fresh(0, 0, 0, fp))

    def test_meta_persists_across_instances(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(1, 2, 3, fp)
        cache2 = TileCache(self.tmp)
        self.assertTrue(cache2.is_fresh(1, 2, 3, fp))

    def test_fingerprint_includes_format_and_quality(self):
        fp1 = TileCache.make_fingerprint('PNG', 85)
        fp2 = TileCache.make_fingerprint('JPG', 85)
        fp3 = TileCache.make_fingerprint('PNG', 75)
        self.assertNotEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3)
        self.assertNotEqual(fp2, fp3)




class TestDeg2Num(unittest.TestCase):
    """Test lat/lon to tile coordinate conversion."""

    def setUp(self):
        self.gen = SMPGenerator()

    def test_zoom0_whole_world(self):
        """At zoom 0 the whole world is tile (0, 0)."""
        x, y = self.gen._deg2num(0.0, 0.0, 0)
        self.assertEqual(x, 0)
        self.assertEqual(y, 0)

    def test_zoom1_nw_quadrant(self):
        """NW quadrant at zoom 1 should be tile (0, 0)."""
        x, y = self.gen._deg2num(45.0, -90.0, 1)
        self.assertEqual(x, 0)
        self.assertEqual(y, 0)

    def test_zoom1_se_quadrant(self):
        """SE quadrant at zoom 1 should be tile (1, 1)."""
        x, y = self.gen._deg2num(-45.0, 90.0, 1)
        self.assertEqual(x, 1)
        self.assertEqual(y, 1)

    def test_roundtrip(self):
        """Converting to tile and back should approximate the original coords."""
        lat, lon = 51.5, -0.1  # London
        zoom = 10
        x, y = self.gen._deg2num(lat, lon, zoom)
        back_lat, back_lon = self.gen._num2deg(x, y, zoom)
        # Should be within one tile width/height
        tile_size_deg = 360.0 / (1 << zoom)
        self.assertAlmostEqual(lon, back_lon, delta=tile_size_deg)


class TestEstimateTileCount(unittest.TestCase):
    """Test tile count estimation."""

    def setUp(self):
        # Small extent around (0,0) in WGS84
        self.gen = SMPGenerator()
        # Patch _get_bounds_wgs84 to return the rect coordinates directly
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def _make_extent(self, west, south, east, north):
        return _FakeRectangle(west, south, east, north)

    def test_zoom0_whole_world(self):
        """Whole world at zoom 0 should produce exactly 1 tile."""
        extent = self._make_extent(-180, -85, 180, 85)
        count = self.gen.estimate_tile_count(extent, 0, 0)
        self.assertEqual(count, 1)

    def test_zoom1_nw_quadrant(self):
        """NW quadrant at zoom 1 should produce 1-4 tiles (boundary tiles may be included)."""
        extent = self._make_extent(-180, 0, 0, 85)
        count = self.gen.estimate_tile_count(extent, 1, 1)
        # When the extent boundary falls exactly on a tile edge, the grid may include
        # the adjacent tile. Acceptable range is 1-4 tiles at zoom 1.
        self.assertGreaterEqual(count, 1)
        self.assertLessEqual(count, 4)

    def test_multiple_zoom_levels(self):
        """Tile count should sum across zoom levels."""
        extent = self._make_extent(-180, -85, 180, 85)
        # zoom 0 → 1, zoom 1 → 4 (whole world)
        count = self.gen.estimate_tile_count(extent, 0, 1)
        self.assertGreaterEqual(count, 5)

    def test_small_extent_high_zoom(self):
        """A tiny extent should produce a small number of tiles even at high zoom."""
        # 1-degree box
        extent = self._make_extent(0, 0, 1, 1)
        count = self.gen.estimate_tile_count(extent, 0, 5)
        # Rough sanity: should be << 1000
        self.assertLess(count, 500)


class TestValidateTileCount(unittest.TestCase):
    """Test validate_tile_count raises/warns correctly."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def _make_extent(self, west, south, east, north):
        return _FakeRectangle(west, south, east, north)

    def test_small_count_no_warning(self):
        extent = self._make_extent(0, 0, 1, 1)
        count, warning = self.gen.validate_tile_count(extent, 0, 5)
        self.assertIsNone(warning)
        self.assertLess(count, TILE_COUNT_WARNING_THRESHOLD)

    def test_large_count_produces_warning(self):
        """Large tile count should return a warning string."""
        # Patch estimate to return something above threshold
        self.gen.estimate_tile_count = MagicMock(return_value=TILE_COUNT_WARNING_THRESHOLD + 1)
        extent = self._make_extent(-180, -85, 180, 85)
        count, warning = self.gen.validate_tile_count(extent, 0, 10)
        self.assertIsNotNone(warning)
        self.assertIn('Warning', warning)

class TestValidateDiskSpace(unittest.TestCase):
    """Test disk space validation."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.tmp = tempfile.mkdtemp()
        self.output_path = os.path.join(self.tmp, 'test.smp')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sufficient_space_passes(self):
        """When plenty of disk space exists, no exception should be raised."""
        # 1 tile should always be fine
        self.gen.validate_disk_space(self.output_path, 1, SMPGenerator.TILE_FORMAT_PNG)

    def test_insufficient_space_raises(self):
        """When estimated size > free space, OSError should be raised."""
        # Mock shutil.disk_usage to return very little free space
        import comapeo_smp_generator as _mod
        original = _mod.shutil.disk_usage

        class _FakeDiskUsage:
            free = 1  # 1 byte only
            total = 100
            used = 99

        try:
            _mod.shutil.disk_usage = MagicMock(return_value=_FakeDiskUsage())
            with self.assertRaises(OSError):
                self.gen.validate_disk_space(self.output_path, 100, SMPGenerator.TILE_FORMAT_PNG)
        finally:
            _mod.shutil.disk_usage = original

    def test_png_uses_larger_estimate(self):
        """PNG byte estimate should be larger than JPG."""
        self.assertGreater(BYTES_PER_TILE_PNG, BYTES_PER_TILE_JPG)


class TestValidateExtentSize(unittest.TestCase):
    """Test extent size validation/warning."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def _make_extent(self, west, south, east, north):
        return _FakeRectangle(west, south, east, north)

    def test_small_extent_no_warning(self):
        """Small extent at any zoom should not produce a warning."""
        extent = self._make_extent(0, 0, 1, 1)
        warning = self.gen.validate_extent_size(extent, 0, 18)
        self.assertIsNone(warning)

    def test_large_extent_high_zoom_warns(self):
        """Large extent at high zoom should produce a warning."""
        extent = self._make_extent(-90, -45, 90, 45)  # 180° wide
        warning = self.gen.validate_extent_size(extent, 0, 15)
        self.assertIsNotNone(warning)
        self.assertIn('Warning', warning)

    def test_large_extent_low_zoom_no_warning(self):
        """Large extent at low zoom should not produce a warning."""
        extent = self._make_extent(-90, -45, 90, 45)  # 180° wide
        warning = self.gen.validate_extent_size(extent, 0, 8)
        self.assertIsNone(warning)


class TestTileGridRects(unittest.TestCase):
    """Test get_tile_grid_rects returns correct tile bounding boxes."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def _make_extent(self, west, south, east, north):
        return _FakeRectangle(west, south, east, north)

    def test_zoom0_returns_one_rect(self):
        extent = self._make_extent(-180, -85, 180, 85)
        rects = self.gen.get_tile_grid_rects(extent, 0, 0)
        self.assertEqual(len(rects), 1)
        r = rects[0]
        self.assertEqual(r['zoom'], 0)
        self.assertEqual(r['x'], 0)
        self.assertEqual(r['y'], 0)

    def test_rect_has_required_keys(self):
        extent = self._make_extent(0, 0, 1, 1)
        rects = self.gen.get_tile_grid_rects(extent, 0, 0)
        self.assertGreater(len(rects), 0)
        for key in ('zoom', 'x', 'y', 'west', 'south', 'east', 'north'):
            self.assertIn(key, rects[0])

    def test_rect_count_matches_estimate(self):
        """get_tile_grid_rects count must equal estimate_tile_count."""
        extent = self._make_extent(-10, -10, 10, 10)
        rects = self.gen.get_tile_grid_rects(extent, 0, 3)
        estimated = self.gen.estimate_tile_count(extent, 0, 3)
        self.assertEqual(len(rects), estimated)

    def test_wgs84_bounds_ordering(self):
        """Each rect must satisfy west < east and south < north."""
        extent = self._make_extent(-20, -20, 20, 20)
        rects = self.gen.get_tile_grid_rects(extent, 1, 2)
        for r in rects:
            self.assertLess(r['west'], r['east'], f"west >= east in {r}")
            self.assertLess(r['south'], r['north'], f"south >= north in {r}")


class TestTileFormatConstants(unittest.TestCase):
    """Test tile format constants and defaults."""

    def test_format_constants(self):
        self.assertEqual(SMPGenerator.TILE_FORMAT_PNG, 'PNG')
        self.assertEqual(SMPGenerator.TILE_FORMAT_JPG, 'JPG')

    def test_generate_smp_validates_format(self):
        """generate_smp_from_canvas should raise ValueError for bad format."""
        gen = SMPGenerator()
        # Stub out validations so we reach the format check
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])

        extent = _FakeRectangle(-1, -1, 1, 1)
        with self.assertRaises(ValueError):
            gen.generate_smp_from_canvas(
                extent, 0, 1, '/tmp/test.smp', tile_format='WEBP'
            )

    def test_jpeg_quality_clamped(self):
        """JPEG quality outside 1-100 should be clamped silently."""
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(0, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._create_style_from_canvas = MagicMock(return_value={})
        gen._generate_tiles_from_canvas = MagicMock()
        gen._build_smp_archive = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])

        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'test.smp')
            extent = _FakeRectangle(-1, -1, 1, 1)
            # quality=0 should be clamped to 1, quality=200 to 100
            with patch('tempfile.mkdtemp', return_value=tmp):
                gen.generate_smp_from_canvas(
                    extent, 0, 1, out,
                    tile_format='JPG', jpeg_quality=0
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCreateStyleJson(unittest.TestCase):
    """Test style.json generation for correct tile format URLs."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-10, -10, 10, 10])

    def _make_extent(self):
        return _FakeRectangle(-10, -10, 10, 10)

    def test_png_style_has_png_url(self):
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 0, 10, 'PNG'
        )
        source = list(style['sources'].values())[0]
        self.assertIn('.png', source['tiles'][0])
        self.assertEqual(source['format'], 'png')

    def test_jpg_style_has_jpg_url(self):
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 0, 10, 'JPG'
        )
        source = list(style['sources'].values())[0]
        self.assertIn('.jpg', source['tiles'][0])
        self.assertEqual(source['format'], 'jpg')

    def test_default_format_is_png(self):
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 0, 10
        )
        source = list(style['sources'].values())[0]
        self.assertIn('.png', source['tiles'][0])

    def test_style_version(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        self.assertEqual(style['version'], 8)

    def test_style_has_bounds(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        source = list(style['sources'].values())[0]
        self.assertIn('bounds', source)
        self.assertEqual(len(source['bounds']), 4)

    def test_style_zoom_levels(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 3, 15)
        source = list(style['sources'].values())[0]
        self.assertEqual(source['minzoom'], 3)
        self.assertEqual(source['maxzoom'], 15)

    def test_root_zoom_respects_min_zoom(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 10, 10)
        self.assertEqual(style['zoom'], 10)


class TestCalculateTilesAtZoom(unittest.TestCase):
    """Test tile range calculations."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def _make_extent(self, west, south, east, north):
        return _FakeRectangle(west, south, east, north)

    def test_whole_world_zoom0(self):
        extent = self._make_extent(-180, -85, 180, 85)
        min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, 0)[0]
        self.assertEqual(min_x, 0)
        self.assertEqual(max_x, 0)
        self.assertEqual(min_y, 0)
        self.assertEqual(max_y, 0)

    def test_tile_range_non_negative(self):
        extent = self._make_extent(0, 0, 10, 10)
        for zoom in range(0, 8):
            min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, zoom)[0]
            self.assertGreaterEqual(min_x, 0)
            self.assertGreaterEqual(min_y, 0)
            self.assertGreaterEqual(max_x, min_x)
            self.assertGreaterEqual(max_y, min_y)

    def test_tile_bounds_within_grid(self):
        """All tile coords should be within [0, 2^zoom - 1]."""
        extent = self._make_extent(-180, -85, 180, 85)
        zoom = 4
        n = 1 << zoom
        min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, zoom)[0]
        self.assertLessEqual(max_x, n - 1)
        self.assertLessEqual(max_y, n - 1)

    def test_normal_extent_returns_single_range(self):
        """Non-antimeridian extent returns exactly one range."""
        extent = self._make_extent(-10, -10, 10, 10)
        ranges = self.gen._calculate_tiles_at_zoom(extent, 4)
        self.assertEqual(len(ranges), 1)

    def test_antimeridian_extent_returns_two_ranges(self):
        """Extent crossing the antimeridian (west > east) returns two ranges."""
        extent = self._make_extent(170, -10, -170, 10)  # west > east
        zoom = 4
        n = 1 << zoom
        ranges = self.gen._calculate_tiles_at_zoom(extent, zoom)
        self.assertEqual(len(ranges), 2)
        min_x1, max_x1, min_y1, max_y1 = ranges[0]
        min_x2, max_x2, min_y2, max_y2 = ranges[1]
        # First range: west side to right edge
        self.assertEqual(max_x1, n - 1)
        # Second range: left edge to east side
        self.assertEqual(min_x2, 0)
        # Y ranges must be identical
        self.assertEqual(min_y1, min_y2)
        self.assertEqual(max_y1, max_y2)
        # All coords within grid
        for r in ranges:
            for v in r:
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, n - 1)

    def test_antimeridian_tile_count_greater_than_zero(self):
        """estimate_tile_count should work correctly for antimeridian extents."""
        extent = self._make_extent(170, -10, -170, 10)
        count = self.gen.estimate_tile_count(extent, 4, 4)
        self.assertGreater(count, 0)


class TestProgressSmoothing(unittest.TestCase):
    """Progress setProgress() should only be called when pct changes."""

    def test_setprogress_not_called_every_tile(self):
        """With many tiles at same pct, setProgress should be called fewer times than tile count."""
        gen = SMPGenerator()
        feedback = MagicMock()
        feedback.isCanceled.return_value = False
        gen.feedback = feedback

        # Patch rendering so no actual QGIS calls happen
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 9, 0, 9)])  # 100 tiles
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()
        fake_img.save = MagicMock()
        fake_painter = MagicMock()
        fake_job = MagicMock()

        with patch.object(_mod, 'QgsMapSettings', MagicMock()), \
             patch.object(_mod, 'QgsProject', _FakeProject), \
             patch('comapeo_smp_generator.QImage', return_value=fake_img), \
             patch('comapeo_smp_generator.QPainter', return_value=fake_painter), \
             patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', return_value=fake_job):

            tmp = tempfile.mkdtemp()
            try:
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp, tile_format='PNG'
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        # 100 tiles but only 101 unique pct values (0..100), so calls <= 101
        call_count = feedback.setProgress.call_count
        self.assertLessEqual(call_count, 101)
        # Should be called at least once
        self.assertGreaterEqual(call_count, 1)

    def test_cancellation_stops_tile_generation(self):
        """When feedback.isCanceled() returns True, the loop breaks early."""
        gen = SMPGenerator()
        feedback = MagicMock()
        feedback.isCanceled.return_value = True
        gen.feedback = feedback

        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 9, 0, 9)])  # 100 tiles
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()
        fake_img.save = MagicMock()

        with patch.object(_mod, 'QgsMapSettings', MagicMock()), \
             patch.object(_mod, 'QgsProject', _FakeProject), \
             patch('comapeo_smp_generator.QImage', return_value=fake_img), \
             patch('comapeo_smp_generator.QPainter', MagicMock()), \
             patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):

            tmp = tempfile.mkdtemp()
            try:
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp, tile_format='PNG',
                    max_workers=1
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        # isCanceled must have been called at least once
        feedback.isCanceled.assert_called()
        # With immediate cancellation, far fewer than 100 setProgress calls expected
        self.assertLess(feedback.setProgress.call_count, 100)


class TestParallelTileRendering(unittest.TestCase):
    """_generate_tiles_from_canvas with max_workers > 1 produces same tile files."""

    def test_parallel_produces_tile_files(self):
        gen = SMPGenerator()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 1, 0, 1)])  # 4 tiles
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()
        fake_img.save = MagicMock()

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()), \
                 patch.object(_mod, 'QgsProject', _FakeProject):
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1), 0, 0, tmp,
                    tile_format='PNG', max_workers=2
                )
            # img.save should have been called 4 times (2x2 tile grid)
            self.assertEqual(fake_img.save.call_count, 4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCacheDirectory(unittest.TestCase):
    """Cache directory is preserved; existing tiles are skipped on resume."""

    def _patched_gen(self):
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()
        gen._build_smp_archive = MagicMock()
        return gen

    def test_cache_dir_passed_as_tiles_dir(self):
        """When cache_dir is provided, _generate_tiles_from_canvas receives it as tiles_dir."""
        gen = self._patched_gen()
        extent = _FakeRectangle(-1, -1, 1, 1)

        cache = tempfile.mkdtemp()
        temp_root = tempfile.mkdtemp()
        try:
            with patch('tempfile.mkdtemp', return_value=temp_root):
                gen.generate_smp_from_canvas(
                    extent, 0, 1, '/tmp/test.smp', cache_dir=cache
                )
        finally:
            shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(temp_root, ignore_errors=True)

        # tiles_dir argument should be cache (not inside a temp dir)
        call_args = gen._generate_tiles_from_canvas.call_args
        tiles_dir_arg = call_args[0][3] if call_args[0] else call_args[1].get('tiles_dir', call_args[0][3])
        self.assertEqual(tiles_dir_arg, cache)

    def test_cache_dir_not_cleaned_up(self):
        """cache_dir must still exist after generate_smp_from_canvas completes."""
        gen = self._patched_gen()
        extent = _FakeRectangle(-1, -1, 1, 1)

        cache = tempfile.mkdtemp()
        try:
            gen.generate_smp_from_canvas(
                extent, 0, 1, '/tmp/test.smp', cache_dir=cache
            )
            self.assertTrue(os.path.isdir(cache), "cache_dir was deleted but should persist")
        finally:
            shutil.rmtree(cache, ignore_errors=True)

    def test_resume_skips_existing_tiles(self):
        """Tiles already on disk are not re-rendered when resume=True."""
        gen = SMPGenerator()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 0, 0, 0)])  # 1 tile
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        tmp = tempfile.mkdtemp()
        try:
            # Pre-create the tile file
            zoom_dir = os.path.join(tmp, '0', '0')
            os.makedirs(zoom_dir, exist_ok=True)
            tile_path = os.path.join(zoom_dir, '0.png')
            with open(tile_path, 'wb') as f:
                f.write(b'FAKE')

            render_mock = MagicMock()
            import comapeo_smp_generator as _mod
            with patch('comapeo_smp_generator.QImage', render_mock), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()), \
                 patch.object(_mod, 'QgsProject', _FakeProject):
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1), 0, 0, tmp,
                    tile_format='PNG', resume=True
                )

            # QImage should NOT have been called because tile already existed
            render_mock.assert_not_called()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_generate_with_cache_dir_rerenders_when_project_fingerprint_changes(self):
        """A changed project render state must invalidate cache-backed resume."""
        import comapeo_smp_generator as _mod

        def build_project(renderer_dump):
            layer = MagicMock()
            layer.id.return_value = 'layer-1'
            layer.name.return_value = 'Layer 1'
            layer.source.return_value = '/tmp/layer-1.gpkg'
            layer.renderer.return_value = MagicMock(dump=MagicMock(return_value=renderer_dump))
            layer.styleManager.return_value = MagicMock(
                currentStyle=MagicMock(return_value='default')
            )
            layer.opacity.return_value = 1.0
            layer.blendMode.return_value = 0

            node = MagicMock()
            node.isVisible.return_value = True
            node.layer.return_value = layer

            root = MagicMock()
            root.findLayers.return_value = [node]
            root.hasCustomLayerOrder.return_value = False

            project = MagicMock()
            project.layerTreeRoot.return_value = root
            project.crs.return_value = MagicMock()
            return project

        def make_save_counter():
            calls = {'count': 0}

            def save(path, *_args):
                calls['count'] += 1
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as fh:
                    fh.write(b'\x89PNG')
                return True

            return calls, save

        cache = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        try:
            gen = SMPGenerator()
            gen.validate_tile_count = MagicMock(return_value=(1, None))
            gen.validate_extent_size = MagicMock(return_value=None)
            gen.validate_disk_space = MagicMock()
            gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
            gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 0, 0, 0)])
            gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

            save_counter_1, save_fn_1 = make_save_counter()
            project_class_mock = MagicMock()
            project_class_mock.instance.return_value = build_project('style-a')

            with patch.object(_mod, 'QgsProject', project_class_mock), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()), \
                 patch('comapeo_smp_generator.QImage',
                       return_value=MagicMock(save=MagicMock(side_effect=save_fn_1))), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen.generate_smp_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1), 0, 0,
                    os.path.join(out_dir, 'first.smp'),
                    cache_dir=cache
                )

            self.assertEqual(save_counter_1['count'], 1)

            save_counter_2, save_fn_2 = make_save_counter()
            project_class_mock.instance.return_value = build_project('style-b')

            with patch.object(_mod, 'QgsProject', project_class_mock), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()), \
                 patch('comapeo_smp_generator.QImage',
                       return_value=MagicMock(save=MagicMock(side_effect=save_fn_2))), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen.generate_smp_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1), 0, 0,
                    os.path.join(out_dir, 'second.smp'),
                    cache_dir=cache
                )

            self.assertEqual(
                save_counter_2['count'], 1,
                "Project fingerprint change should force tile rerender"
            )
        finally:
            shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_generate_with_cache_dir_excludes_stale_tiles_end_to_end(self):
        """Manifest filtering should exclude stale cache tiles in final archives."""
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()

        cache = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        try:
            import json
            current_dir = os.path.join(cache, '0', '0')
            stale_dir = os.path.join(cache, '1', '0')
            os.makedirs(current_dir, exist_ok=True)
            os.makedirs(stale_dir, exist_ok=True)
            with open(os.path.join(current_dir, '0.png'), 'wb') as fh:
                fh.write(b'\x89PNG')
            with open(os.path.join(stale_dir, '0.png'), 'wb') as fh:
                fh.write(b'\x89PNG')
            with open(os.path.join(cache, TileCache.META_FILE), 'w') as fh:
                json.dump({"0/0/0": "PNG:85:any"}, fh)

            out_path = os.path.join(out_dir, 'manifest.smp')
            gen.generate_smp_from_canvas(
                _FakeRectangle(-1, -1, 1, 1), 0, 0, out_path, cache_dir=cache
            )

            import zipfile
            with zipfile.ZipFile(out_path) as zf:
                names = zf.namelist()
            self.assertIn('s/0/0/0/0.png', names)
            self.assertNotIn('s/0/1/0/0.png', names)
            self.assertNotIn('s/0/_cache_meta.json', names)
        finally:
            shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(out_dir, ignore_errors=True)


class TestLowZoomStyleOutput(unittest.TestCase):
    """default_zoom in style.json must never be negative."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-10, -10, 10, 10])

    def _make_extent(self):
        return _FakeRectangle(-10, -10, 10, 10)

    def test_max_zoom_0_default_zoom_non_negative(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 0)
        self.assertGreaterEqual(style['zoom'], 0)

    def test_max_zoom_1_default_zoom_non_negative(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 1)
        self.assertGreaterEqual(style['zoom'], 0)

    def test_max_zoom_2_default_zoom_non_negative(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 2)
        self.assertGreaterEqual(style['zoom'], 0)

    def test_high_max_zoom_default_zoom_capped_at_11(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 20)
        self.assertLessEqual(style['zoom'], 11)


class TestCancelEventInRenderSingleTile(unittest.TestCase):
    """_render_single_tile must skip rendering when cancel_event is set."""

    def test_cancelled_tile_skips_render(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()

        cancel_event = threading.Event()
        cancel_event.set()  # pre-set before calling

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                result = gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, False,
                    cancel_event=cancel_event
                )
            # QImage should never be constructed when cancel_event is set
            fake_img.assert_not_called()
            self.assertFalse(result)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_uncancelled_tile_renders(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()
        cancel_event = threading.Event()  # not set

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, False,
                    cancel_event=cancel_event
                )
            fake_img.save.assert_called_once()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mid_render_cancellation_aborts_job(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()
        fake_job = MagicMock()
        fake_job.isActive.side_effect = [True, False]
        fake_job.cancelWithoutBlocking = MagicMock()

        feedback = MagicMock()
        feedback.isCanceled.side_effect = [False, True]
        gen.feedback = feedback

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob',
                       return_value=fake_job):
                result = gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, False,
                    cancel_event=threading.Event()
                )
            self.assertFalse(result)
            fake_job.cancelWithoutBlocking.assert_called_once()
            fake_img.save.assert_not_called()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestGenerateSmpCancellation(unittest.TestCase):
    """generate_smp_from_canvas returns None when cancelled; no archive built."""

    def _patched_gen(self, cancelled_after_tiles=True):
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()
        gen._build_smp_archive = MagicMock()

        feedback = MagicMock()
        feedback.isCanceled.return_value = cancelled_after_tiles
        gen.feedback = feedback
        return gen

    def test_cancel_returns_none(self):
        gen = self._patched_gen(cancelled_after_tiles=True)
        extent = _FakeRectangle(-1, -1, 1, 1)
        result = gen.generate_smp_from_canvas(extent, 0, 1, '/tmp/test.smp')
        self.assertIsNone(result)

    def test_cancel_skips_archive_build(self):
        gen = self._patched_gen(cancelled_after_tiles=True)
        extent = _FakeRectangle(-1, -1, 1, 1)
        gen.generate_smp_from_canvas(extent, 0, 1, '/tmp/test.smp')
        gen._build_smp_archive.assert_not_called()

    def test_no_cancel_returns_path(self):
        gen = self._patched_gen(cancelled_after_tiles=False)
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'test.smp')
            extent = _FakeRectangle(-1, -1, 1, 1)
            result = gen.generate_smp_from_canvas(extent, 0, 1, out)
            self.assertEqual(result, out)
            gen._build_smp_archive.assert_called_once()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cancel_during_archive_returns_none(self):
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})

        feedback = MagicMock()
        feedback.isCanceled.side_effect = [False, False, True]
        gen.feedback = feedback

        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'test.smp')

            def fake_generate(_extent, _min_zoom, _max_zoom, tiles_dir, **_kwargs):
                tile_dir = os.path.join(tiles_dir, '0', '0')
                os.makedirs(tile_dir, exist_ok=True)
                with open(os.path.join(tile_dir, '0.png'), 'wb') as fh:
                    fh.write(b'\x89PNG')

            gen._generate_tiles_from_canvas = MagicMock(side_effect=fake_generate)

            result = gen.generate_smp_from_canvas(_FakeRectangle(-1, -1, 1, 1), 0, 0, out)
            self.assertIsNone(result)
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSMPArchiveExcludesCacheMetadata(unittest.TestCase):
    """_build_smp_archive must never include _cache_meta.json."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_archive_with_meta(self, tile_paths=None):
        """Build an SMP from a tiles_dir that contains _cache_meta.json."""
        gen = SMPGenerator()

        style_path = os.path.join(self.tmp, 'style.json')
        import json
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        tile_file_dir = os.path.join(tiles_dir, '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)

        # Real tile
        with open(os.path.join(tile_file_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        # Cache metadata sidecar that must NOT appear in the archive
        from comapeo_smp_generator import TileCache
        with open(os.path.join(tiles_dir, TileCache.META_FILE), 'w') as f:
            import json
            json.dump({"0/0/0": "PNG:85"}, f)

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path,
                               tile_paths=tile_paths)
        return out_path

    def test_meta_file_excluded_without_tile_paths(self):
        import zipfile
        smp = self._build_archive_with_meta(tile_paths=None)
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
        from comapeo_smp_generator import TileCache
        for name in names:
            self.assertNotIn(TileCache.META_FILE, name,
                             f"Cache metadata found in archive: {name!r}")

    def test_meta_file_excluded_with_tile_paths(self):
        import zipfile
        tile_paths = {'0/0/0.png'}
        smp = self._build_archive_with_meta(tile_paths=tile_paths)
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
        from comapeo_smp_generator import TileCache
        for name in names:
            self.assertNotIn(TileCache.META_FILE, name)

    def test_stale_tile_excluded_when_tile_paths_provided(self):
        import zipfile
        # tile_paths deliberately does NOT include a stale 1/0/0.png
        gen = SMPGenerator()
        style_path = os.path.join(self.tmp, 's2_style.json')
        import json
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles2')
        for z, x, y in [(0, 0, 0), (1, 0, 0)]:
            d = os.path.join(tiles_dir, str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(b'\x89PNG')

        out_path = os.path.join(self.tmp, 'stale.smp')
        # Only zoom 0 belongs to current export
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path,
                               tile_paths={'0/0/0.png'})

        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
        self.assertIn('s/0/0/0/0.png', names)
        self.assertNotIn('s/0/1/0/0.png', names)

    def test_current_tiles_included_when_tile_paths_provided(self):
        import zipfile
        smp = self._build_archive_with_meta(tile_paths={'0/0/0.png'})
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
        self.assertIn('s/0/0/0/0.png', names)


class TestTileSaveFailure(unittest.TestCase):
    """_render_single_tile reports failure when img.save() fails."""

    def test_save_failure_propagates(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()
        fake_img.save.return_value = False  # Qt save() returns False on failure

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                with self.assertRaises(OSError):
                    gen._render_single_tile(
                        MagicMock(), 0, 0, 0, tmp, 'PNG', 85, False
                    )
            fake_img.save.assert_called_once()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTileCacheThreadSafety(unittest.TestCase):
    """Concurrent TileCache.mark() calls must not corrupt metadata."""

    def test_concurrent_marks_all_recorded(self):
        import threading as _threading
        tmp = tempfile.mkdtemp()
        try:
            cache = TileCache(tmp)
            fp = TileCache.make_fingerprint('PNG', 85)
            n = 50
            errors = []

            def mark_tile(i):
                try:
                    cache.mark(0, i, 0, fp)
                except Exception as exc:
                    errors.append(exc)

            threads = [_threading.Thread(target=mark_tile, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [], f"Errors during concurrent mark: {errors}")

            # Reload from disk and verify all tiles are present
            cache2 = TileCache(tmp)
            for i in range(n):
                self.assertTrue(cache2.is_fresh(0, i, 0, fp),
                                f"Tile (0, {i}, 0) missing after concurrent marks")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_atomic_save_leaves_readable_json(self):
        """After mark(), _cache_meta.json must be valid JSON (not partial write)."""
        tmp = tempfile.mkdtemp()
        try:
            cache = TileCache(tmp)
            fp = TileCache.make_fingerprint('JPG', 75)
            for i in range(10):
                cache.mark(0, i, 0, fp)

            import json
            meta_path = os.path.join(tmp, TileCache.META_FILE)
            with open(meta_path) as fh:
                data = json.load(fh)
            self.assertEqual(len(data), 10)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_multiple_instances_share_lock_for_same_cache_dir(self):
        tmp = tempfile.mkdtemp()
        try:
            cache_a = TileCache(tmp)
            cache_b = TileCache(tmp)
            fp = TileCache.make_fingerprint('PNG', 85)
            errors = []

            def mark(cache, index):
                try:
                    cache.mark(0, index, 0, fp)
                except Exception as exc:
                    errors.append(exc)

            threads = []
            for i in range(20):
                threads.append(threading.Thread(target=mark, args=(cache_a, i)))
                threads.append(threading.Thread(target=mark, args=(cache_b, i + 100)))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            cache_a.flush()
            cache_b.flush()
            self.assertEqual(errors, [], f"Errors during multi-instance mark: {errors}")
            cache_c = TileCache(tmp)
            for i in range(20):
                self.assertTrue(cache_c.is_fresh(0, i, 0, fp))
                self.assertTrue(cache_c.is_fresh(0, i + 100, 0, fp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDeterministicLayerOrder(unittest.TestCase):
    """Layer list is sourced from root.findLayers(), not project.mapLayers()."""

    def test_findlayers_called_not_maplayers(self):
        gen = SMPGenerator()
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[])

        import comapeo_smp_generator as _mod

        # Build a project instance mock where layerTreeRoot().findLayers()
        # returns an empty list so we can assert it was called.
        project_instance = MagicMock()
        root_mock = MagicMock()
        root_mock.findLayers.return_value = []
        project_instance.layerTreeRoot.return_value = root_mock
        project_instance.crs.return_value = MagicMock()

        # QgsProject.instance() must return the instance mock
        project_class_mock = MagicMock()
        project_class_mock.instance.return_value = project_instance

        tmp = tempfile.mkdtemp()
        try:
            with patch.object(_mod, 'QgsProject', project_class_mock), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()):
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # findLayers() must be the source; mapLayers() must not be called
        root_mock.findLayers.assert_called_once()
        project_instance.mapLayers.assert_not_called()

    def test_custom_layer_order_is_used_when_enabled(self):
        gen = SMPGenerator()
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[])

        import comapeo_smp_generator as _mod

        layer_a = MagicMock()
        layer_a.id.return_value = 'a'
        layer_b = MagicMock()
        layer_b.id.return_value = 'b'

        node_a = MagicMock()
        node_a.isVisible.return_value = True
        node_a.layer.return_value = layer_a
        node_b = MagicMock()
        node_b.isVisible.return_value = True
        node_b.layer.return_value = layer_b

        root_mock = MagicMock()
        root_mock.findLayers.return_value = [node_a, node_b]
        root_mock.hasCustomLayerOrder.return_value = True
        root_mock.customLayerOrder.return_value = [layer_b, layer_a]

        project_instance = MagicMock()
        project_instance.layerTreeRoot.return_value = root_mock
        project_instance.crs.return_value = MagicMock()

        project_class_mock = MagicMock()
        project_class_mock.instance.return_value = project_instance

        map_settings_instance = MagicMock()
        map_settings_class = MagicMock(return_value=map_settings_instance)

        tmp = tempfile.mkdtemp()
        try:
            with patch.object(_mod, 'QgsProject', project_class_mock), \
                 patch.object(_mod, 'QgsMapSettings', map_settings_class):
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        map_settings_instance.setLayers.assert_called_once_with([layer_b, layer_a])

    def test_project_fingerprint_changes_when_layer_state_changes(self):
        gen = SMPGenerator()
        project = MagicMock()
        project.crs.return_value = MagicMock(authid=MagicMock(return_value='EPSG:4326'))

        layer_a = MagicMock()
        layer_a.id.return_value = 'a'
        layer_a.name.return_value = 'Layer A'
        layer_a.source.return_value = '/tmp/a.gpkg'
        layer_a.renderer.return_value = MagicMock(dump=MagicMock(return_value='style-a'))
        layer_a.styleManager.return_value = MagicMock(currentStyle=MagicMock(return_value='default'))
        layer_a.opacity.return_value = 1.0
        layer_a.blendMode.return_value = 0

        layer_b = MagicMock()
        layer_b.id.return_value = 'b'
        layer_b.name.return_value = 'Layer B'
        layer_b.source.return_value = '/tmp/b.gpkg'
        layer_b.renderer.return_value = MagicMock(dump=MagicMock(return_value='style-b'))
        layer_b.styleManager.return_value = MagicMock(currentStyle=MagicMock(return_value='default'))
        layer_b.opacity.return_value = 1.0
        layer_b.blendMode.return_value = 0

        fp1 = gen._project_cache_fingerprint(project, [layer_a, layer_b])
        fp2 = gen._project_cache_fingerprint(project, [layer_b, layer_a])
        self.assertNotEqual(fp1, fp2)

    def test_project_fingerprint_changes_when_style_changes(self):
        gen = SMPGenerator()
        project = MagicMock()
        project.crs.return_value = MagicMock(authid=MagicMock(return_value='EPSG:4326'))

        layer = MagicMock()
        layer.id.return_value = 'a'
        layer.name.return_value = 'Layer A'
        layer.source.return_value = '/tmp/a.gpkg'
        layer.opacity.return_value = 1.0
        layer.blendMode.return_value = 0

        renderer_a = MagicMock()
        renderer_a.dump.return_value = 'style-a'
        renderer_b = MagicMock()
        renderer_b.dump.return_value = 'style-b'
        layer.renderer.side_effect = [renderer_a, renderer_b]
        layer.styleManager.return_value = MagicMock(currentStyle=MagicMock(return_value='default'))

        fp1 = gen._project_cache_fingerprint(project, [layer])
        fp2 = gen._project_cache_fingerprint(project, [layer])
        self.assertNotEqual(fp1, fp2)

    def test_project_fingerprint_changes_when_project_crs_changes(self):
        gen = SMPGenerator()

        project_a = MagicMock()
        project_a.crs.return_value = MagicMock(authid=MagicMock(return_value='EPSG:4326'))
        project_b = MagicMock()
        project_b.crs.return_value = MagicMock(authid=MagicMock(return_value='EPSG:3857'))

        layer = MagicMock()
        layer.id.return_value = 'a'
        layer.name.return_value = 'Layer A'
        layer.source.return_value = '/tmp/a.gpkg'
        layer.renderer.return_value = MagicMock(dump=MagicMock(return_value='style-a'))
        layer.styleManager.return_value = MagicMock(currentStyle=MagicMock(return_value='default'))
        layer.opacity.return_value = 1.0
        layer.blendMode.return_value = 0

        fp1 = gen._project_cache_fingerprint(project_a, [layer])
        fp2 = gen._project_cache_fingerprint(project_b, [layer])
        self.assertNotEqual(fp1, fp2)


class TestCheckParameterValues(unittest.TestCase):
    """
    Unit tests for ComapeoMapBuilderAlgorithm.checkParameterValues().

    We stub out the QGIS parameter helpers so the tests run without QGIS.
    """

    def _make_algorithm(self):
        """Return a ComapeoMapBuilderAlgorithm with all QGIS calls stubbed."""
        import sys
        import importlib
        import comapeo_smp_generator as _gen_mod

        qgis_core = sys.modules['qgis.core']

        # Fake base class that satisfies checkParameterValues contract
        class _FakeAlgoBase:
            def checkParameterValues(self, parameters, context):
                return True, ''
            def tr(self, s):
                return s

        qgis_core.QgsProcessingAlgorithm = _FakeAlgoBase
        qgis_core.QgsProcessingParameterExtent = MagicMock()
        qgis_core.QgsProcessingParameterNumber = MagicMock()
        qgis_core.QgsProcessingParameterEnum = MagicMock()
        qgis_core.QgsProcessingParameterFileDestination = MagicMock()
        qgis_core.QgsProcessingException = Exception
        qgis_core.QgsMapRendererCustomPainterJob = MagicMock()

        # The algorithm uses a relative import: "from .comapeo_smp_generator import …"
        # Register the already-imported generator under the package-relative name so
        # importlib.reload() can resolve it.
        sys.modules['comapeo_smp_algorithm'] = None  # clear cached entry if any
        pkg_name = 'comapeo_smp_generator'
        sys.modules[pkg_name] = _gen_mod

        # Build a minimal package shim so the relative import resolves
        import types
        pkg_shim = types.ModuleType('comapeo_smp_plugin')
        pkg_shim.comapeo_smp_generator = _gen_mod
        sys.modules['comapeo_smp_plugin'] = pkg_shim
        sys.modules['comapeo_smp_plugin.comapeo_smp_generator'] = _gen_mod

        # Read and exec the algorithm source with __package__ set
        import os
        algo_path = os.path.join(os.path.dirname(__file__), '..', 'comapeo_smp_algorithm.py')
        with open(os.path.abspath(algo_path)) as fh:
            src = fh.read()

        # Replace relative import with absolute so exec() works standalone
        src = src.replace(
            'from .comapeo_smp_generator import SMPGenerator',
            'from comapeo_smp_generator import SMPGenerator'
        )

        # Make QCoreApplication.translate return the raw string so tr() is transparent
        pyqt_core = sys.modules['qgis.PyQt.QtCore']
        pyqt_core.QCoreApplication = MagicMock()
        pyqt_core.QCoreApplication.translate = MagicMock(side_effect=lambda ctx, s: s)

        ns = {'__name__': 'comapeo_smp_algorithm', '__package__': 'comapeo_smp_plugin'}
        exec(compile(src, algo_path, 'exec'), ns)  # noqa: S102

        cls = ns['ComapeoMapBuilderAlgorithm']
        algo = cls()
        return algo

    def _make_extent(self, west, south, east, north):
        ext = _FakeRectangle(west, south, east, north)
        ext.isEmpty = MagicMock(return_value=False)
        return ext

    def test_valid_params_pass(self):
        """Valid zoom range + small extent should return (True, '')."""
        algo = self._make_algorithm()
        extent = self._make_extent(0, 0, 1, 1)
        algo.parameterAsExtent = MagicMock(return_value=extent)
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)
        algo.parameterAsEnum = MagicMock(return_value=0)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        # Patch generator validations to pass silently
        import comapeo_smp_generator as _gen_mod
        with patch.object(_gen_mod.SMPGenerator, 'validate_tile_count',
                          return_value=(10, None)), \
             patch.object(_gen_mod.SMPGenerator, 'validate_disk_space'):
            ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertTrue(ok)
        self.assertEqual(msg, '')

    def test_non_integer_enum_value_blocked(self):
        algo = self._make_algorithm()
        extent = self._make_extent(0, 0, 1, 1)
        algo.parameterAsExtent = MagicMock(return_value=extent)
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)
        algo.parameterAsEnum = MagicMock(return_value=None)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        ok, msg = algo.checkParameterValues({}, MagicMock())
        self.assertFalse(ok)
        self.assertIn('Invalid tile format value', msg)

    def test_inverted_zoom_range_blocked(self):
        """min_zoom > max_zoom should be caught before touching the generator."""
        algo = self._make_algorithm()
        algo.parameterAsExtent = MagicMock(return_value=self._make_extent(0, 0, 1, 1))
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 10 if k == 'MIN_ZOOM' else 5)
        algo.parameterAsEnum = MagicMock(return_value=0)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertFalse(ok)
        self.assertIn('10', msg)   # min_zoom value in message
        self.assertIn('5', msg)    # max_zoom value in message

    def test_insufficient_disk_space_blocked(self):
        """OSError from validate_disk_space should block execution."""
        algo = self._make_algorithm()
        algo.parameterAsExtent = MagicMock(return_value=self._make_extent(0, 0, 1, 1))
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)
        algo.parameterAsEnum = MagicMock(return_value=0)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        import comapeo_smp_generator as _gen_mod
        with patch.object(_gen_mod.SMPGenerator, 'validate_tile_count',
                          return_value=(100, None)), \
             patch.object(_gen_mod.SMPGenerator, 'validate_disk_space',
                          side_effect=OSError('Insufficient disk space. Estimated 500.0 MB needed')):
            ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertFalse(ok)
        self.assertIn('Insufficient disk space', msg)

    def test_process_algorithm_rejects_non_integer_enum_value(self):
        algo = self._make_algorithm()
        algo.parameterAsExtent = MagicMock(return_value=self._make_extent(0, 0, 1, 1))
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)
        algo.parameterAsEnum = MagicMock(return_value=None)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        with self.assertRaises(Exception) as ctx:
            algo.processAlgorithm({}, MagicMock(), MagicMock())
        self.assertIn('Invalid tile format value', str(ctx.exception))

    def test_empty_extent_skips_generator(self):
        """An empty extent should not call the generator (return True to let processAlgorithm handle it)."""
        algo = self._make_algorithm()
        empty_ext = _FakeRectangle(0, 0, 0, 0)
        empty_ext.isEmpty = MagicMock(return_value=True)
        algo.parameterAsExtent = MagicMock(return_value=empty_ext)
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)

        ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertTrue(ok)


class TestPluginLifecycle(unittest.TestCase):
    """Plugin provider lifecycle guards should handle failed registry ops."""

    def _load_plugin_class(self, add_ok=True, remove_ok=True):
        import os
        import sys

        registry = MagicMock()
        registry.addProvider.return_value = add_ok
        registry.removeProvider.return_value = remove_ok

        qgis_core = sys.modules['qgis.core']
        qgis_core.QgsApplication = MagicMock()
        qgis_core.QgsApplication.processingRegistry = MagicMock(return_value=registry)

        provider_cls = MagicMock()
        plugin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'comapeo_smp.py'))
        with open(plugin_path) as fh:
            src = fh.read()

        src = src.replace(
            'from .comapeo_smp_provider import ComapeoMapBuilderProvider',
            'ComapeoMapBuilderProvider = provider_cls'
        )

        ns = {'__name__': 'comapeo_smp', 'provider_cls': provider_cls}
        exec(compile(src, plugin_path, 'exec'), ns)  # noqa: S102
        return ns['ComapeoMapBuilderPlugin'], registry, provider_cls

    def test_failed_add_provider_does_not_mark_initialized(self):
        cls, registry, provider_cls = self._load_plugin_class(add_ok=False)
        plugin = cls()

        plugin.initProcessing()

        registry.addProvider.assert_called_once()
        provider_cls.assert_called_once()
        self.assertIsNone(plugin.provider)

    def test_retry_after_failed_add_provider(self):
        cls, registry, _provider_cls = self._load_plugin_class(add_ok=False)
        plugin = cls()
        plugin.initProcessing()
        registry.addProvider.return_value = True

        plugin.initProcessing()

        self.assertEqual(registry.addProvider.call_count, 2)
        self.assertIsNotNone(plugin.provider)

    def test_failed_remove_provider_keeps_handle(self):
        cls, registry, _provider_cls = self._load_plugin_class(add_ok=True, remove_ok=False)
        plugin = cls()
        plugin.initProcessing()

        plugin.unload()

        registry.removeProvider.assert_called_once()
        self.assertIsNotNone(plugin.provider)


class TestSMPArchiveStructure(unittest.TestCase):
    """SMP archive must contain style.json and tiles under s/0/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_minimal_smp(self):
        """Build a real SMP zip using _build_smp_archive with synthetic content."""
        gen = SMPGenerator()

        # Create a fake style.json
        style_path = os.path.join(self.tmp, 'style.json')
        import json
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        # Create a fake tile tree: z=0/x=0/y=0.png
        tiles_dir = os.path.join(self.tmp, 'tiles')
        tile_file_dir = os.path.join(tiles_dir, '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)
        tile_path = os.path.join(tile_file_dir, '0.png')
        with open(tile_path, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')  # PNG magic bytes

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path)
        return out_path

    def test_smp_is_valid_zip(self):
        import zipfile
        smp = self._build_minimal_smp()
        self.assertTrue(zipfile.is_zipfile(smp))

    def test_smp_contains_style_json(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
        self.assertIn('style.json', names)

    def test_style_json_is_valid_json(self):
        import zipfile
        import json
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            data = json.loads(zf.read('style.json'))
        self.assertIn('version', data)
        self.assertEqual(data['version'], 8)

    def test_smp_contains_tile_under_s_0(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
        tile_entries = [n for n in names if n.startswith('s/0/')]
        self.assertGreater(len(tile_entries), 0)

    def test_tile_path_follows_z_x_y_convention(self):
        """Tile entry must match s/0/{z}/{x}/{y}.ext pattern."""
        import zipfile
        import re
        smp = self._build_minimal_smp()
        pattern = re.compile(r'^s/0/\d+/\d+/\d+\.\w+$')
        with zipfile.ZipFile(smp) as zf:
            tile_entries = [n for n in zf.namelist() if n.startswith('s/0/') and '.' in n]
        self.assertGreater(len(tile_entries), 0)
        for entry in tile_entries:
            self.assertRegex(entry, pattern, f"Tile path {entry!r} does not match expected pattern")

    def test_smp_archive_no_backslashes(self):
        """ZIP archive entries must use forward slashes on all platforms (POSIX paths)."""
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            for info in zf.infolist():
                self.assertNotIn(
                    '\\', info.filename,
                    f"ZIP entry {info.filename!r} contains a backslash; must use POSIX separators"
                )


class TestErrorHandling(unittest.TestCase):
    """Error conditions are reported clearly and temp dirs are cleaned up."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patched_gen(self):
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()
        return gen

    def test_unwritable_output_raises(self):
        """Passing a path in a non-existent directory raises an error."""
        gen = self._patched_gen()
        gen._build_smp_archive = MagicMock(side_effect=OSError("Permission denied"))
        extent = _FakeRectangle(-1, -1, 1, 1)
        with self.assertRaises(OSError):
            gen.generate_smp_from_canvas(extent, 0, 1, '/no/such/dir/out.smp')

    def test_temp_dir_cleaned_up_on_success(self):
        """After successful generation the internal temp dir must not persist."""
        created_dirs = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created_dirs.append(d)
            return d

        gen = self._patched_gen()
        gen._build_smp_archive = MagicMock()

        extent = _FakeRectangle(-1, -1, 1, 1)
        with patch('tempfile.mkdtemp', side_effect=tracking_mkdtemp):
            gen.generate_smp_from_canvas(extent, 0, 1, os.path.join(self.tmp, 'out.smp'))

        for d in created_dirs:
            self.assertFalse(os.path.exists(d),
                             f"Temp dir {d} was not cleaned up after success")

    def test_temp_dir_cleaned_up_on_error(self):
        """After a generation error the internal temp dir must still be cleaned up."""
        created_dirs = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(*a, **kw):
            d = real_mkdtemp(*a, **kw)
            created_dirs.append(d)
            return d

        gen = self._patched_gen()
        gen._generate_tiles_from_canvas = MagicMock(side_effect=RuntimeError("render failed"))

        extent = _FakeRectangle(-1, -1, 1, 1)
        with patch('tempfile.mkdtemp', side_effect=tracking_mkdtemp):
            with self.assertRaises(RuntimeError):
                gen.generate_smp_from_canvas(extent, 0, 1, os.path.join(self.tmp, 'out.smp'))

        for d in created_dirs:
            self.assertFalse(os.path.exists(d),
                             f"Temp dir {d} was not cleaned up after error")

if __name__ == '__main__':
    unittest.main()
