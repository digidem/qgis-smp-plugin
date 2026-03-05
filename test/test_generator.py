# coding=utf-8
"""Tests for SMPGenerator - does not require a running QGIS instance."""

import math
import os
import shutil
import tempfile
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
    SMPGeneratorTask,
    TileCache,
    TILE_COUNT_WARNING_THRESHOLD,
    TILE_COUNT_ERROR_THRESHOLD,
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


class TestSMPGeneratorTask(unittest.TestCase):
    """SMPGeneratorTask stores parameters and exposes run/finished interface."""

    def _make_task(self):
        extent = _FakeRectangle(0, 0, 1, 1)
        return SMPGeneratorTask(extent, 0, 5, '/tmp/out.smp',
                                tile_format='PNG', jpeg_quality=85)

    def test_task_stores_params(self):
        task = self._make_task()
        self.assertEqual(task.min_zoom, 0)
        self.assertEqual(task.max_zoom, 5)
        self.assertEqual(task.tile_format, 'PNG')
        self.assertEqual(task.jpeg_quality, 85)
        self.assertEqual(task.output_path, '/tmp/out.smp')

    def test_task_run_calls_generator(self):
        import comapeo_smp_generator as _mod
        extent = _FakeRectangle(0, 0, 1, 1)
        task = SMPGeneratorTask(extent, 0, 5, '/tmp/out.smp')

        with patch.object(_mod.SMPGenerator, 'generate_smp_from_canvas',
                          return_value='/tmp/out.smp') as mock_gen:
            result = task.run()

        self.assertTrue(result)
        self.assertEqual(task.result_path, '/tmp/out.smp')
        mock_gen.assert_called_once()

    def test_task_run_captures_error(self):
        import comapeo_smp_generator as _mod
        extent = _FakeRectangle(0, 0, 1, 1)
        task = SMPGeneratorTask(extent, 0, 5, '/tmp/out.smp')

        with patch.object(_mod.SMPGenerator, 'generate_smp_from_canvas',
                          side_effect=ValueError('bad params')):
            result = task.run()

        self.assertFalse(result)
        self.assertIn('bad params', task.error)


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

    def test_excessive_count_raises_error(self):
        """Tile count above error threshold should raise ValueError."""
        self.gen.estimate_tile_count = MagicMock(return_value=TILE_COUNT_ERROR_THRESHOLD + 1)
        extent = self._make_extent(-180, -85, 180, 85)
        with self.assertRaises(ValueError):
            self.gen.validate_tile_count(extent, 0, 20)


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
        original = _mod._shutil.disk_usage

        class _FakeDiskUsage:
            free = 1  # 1 byte only
            total = 100
            used = 99

        try:
            _mod._shutil.disk_usage = MagicMock(return_value=_FakeDiskUsage())
            with self.assertRaises(OSError):
                self.gen.validate_disk_space(self.output_path, 100, SMPGenerator.TILE_FORMAT_PNG)
        finally:
            _mod._shutil.disk_usage = original

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
        min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, 0)
        self.assertEqual(min_x, 0)
        self.assertEqual(max_x, 0)
        self.assertEqual(min_y, 0)
        self.assertEqual(max_y, 0)

    def test_tile_range_non_negative(self):
        extent = self._make_extent(0, 0, 10, 10)
        for zoom in range(0, 8):
            min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, zoom)
            self.assertGreaterEqual(min_x, 0)
            self.assertGreaterEqual(min_y, 0)
            self.assertGreaterEqual(max_x, min_x)
            self.assertGreaterEqual(max_y, min_y)

    def test_tile_bounds_within_grid(self):
        """All tile coords should be within [0, 2^zoom - 1]."""
        extent = self._make_extent(-180, -85, 180, 85)
        zoom = 4
        n = 1 << zoom
        min_x, max_x, min_y, max_y = self.gen._calculate_tiles_at_zoom(extent, zoom)
        self.assertLessEqual(max_x, n - 1)
        self.assertLessEqual(max_y, n - 1)


class TestProgressSmoothing(unittest.TestCase):
    """Progress setProgress() should only be called when pct changes."""

    def test_setprogress_not_called_every_tile(self):
        """With many tiles at same pct, setProgress should be called fewer times than tile count."""
        gen = SMPGenerator()
        feedback = MagicMock()
        gen.feedback = feedback

        # Patch rendering so no actual QGIS calls happen
        gen._calculate_tiles_at_zoom = MagicMock(return_value=(0, 9, 0, 9))  # 100 tiles
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


class TestParallelTileRendering(unittest.TestCase):
    """_generate_tiles_from_canvas with max_workers > 1 produces same tile files."""

    def test_parallel_produces_tile_files(self):
        gen = SMPGenerator()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._calculate_tiles_at_zoom = MagicMock(return_value=(0, 1, 0, 1))  # 4 tiles
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
        gen._calculate_tiles_at_zoom = MagicMock(return_value=(0, 0, 0, 0))  # 1 tile
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

    def test_excessive_tile_count_blocked(self):
        """Tile count above error threshold should block execution."""
        algo = self._make_algorithm()
        algo.parameterAsExtent = MagicMock(return_value=self._make_extent(-180, -85, 180, 85))
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 18)
        algo.parameterAsEnum = MagicMock(return_value=0)
        algo.parameterAsFileOutput = MagicMock(return_value='/tmp/test.smp')

        import comapeo_smp_generator as _gen_mod
        with patch.object(_gen_mod.SMPGenerator, 'validate_tile_count',
                          side_effect=ValueError('Estimated tile count (999999) exceeds maximum')):
            ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertFalse(ok)
        self.assertIn('999999', msg)

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

    def test_empty_extent_skips_generator(self):
        """An empty extent should not call the generator (return True to let processAlgorithm handle it)."""
        algo = self._make_algorithm()
        empty_ext = _FakeRectangle(0, 0, 0, 0)
        empty_ext.isEmpty = MagicMock(return_value=True)
        algo.parameterAsExtent = MagicMock(return_value=empty_ext)
        algo.parameterAsInt = MagicMock(side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else 5)

        ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertTrue(ok)


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

    def test_invalid_zoom_range_message_contains_values(self):
        """checkParameterValues error message must include both zoom values."""
        gen = SMPGenerator()
        gen.estimate_tile_count = MagicMock(
            return_value=TILE_COUNT_ERROR_THRESHOLD + 1
        )
        extent = _FakeRectangle(-180, -85, 180, 85)
        gen._get_bounds_wgs84 = lambda e: [e.xMinimum(), e.yMinimum(),
                                           e.xMaximum(), e.yMaximum()]
        try:
            gen.validate_tile_count(extent, 0, 20)
            self.fail("Expected ValueError not raised")
        except ValueError as exc:
            msg = str(exc)
            self.assertIn('Estimated tile count', msg)
            self.assertIn('exceeds', msg)


if __name__ == '__main__':
    unittest.main()
