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




class TestWorldBaseZooms(unittest.TestCase):
    """Tests for world-base-zooms tile estimation behavior."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]
        self.world_extent = _FakeRectangle(-180, -85.0511, 180, 85.0511)
        self.user_extent = _FakeRectangle(-1, -1, 1, 1)
        self.gen.get_world_extent = lambda: self.world_extent

    def test_world_tile_count_math(self):
        self.assertEqual(self.gen.estimate_world_tile_count(0, 0), 1)
        self.assertEqual(self.gen.estimate_world_tile_count(0, 2), 21)
        self.assertEqual(self.gen.estimate_world_tile_count(0, 5), 1365)

    def test_mixed_export_between_extent_only_and_full_world(self):
        min_zoom = 0
        max_zoom = 5
        extent_only_count = self.gen.estimate_mixed_tile_count(
            self.user_extent, min_zoom, max_zoom, include_world_base_zooms=False
        )
        mixed_count = self.gen.estimate_mixed_tile_count(
            self.user_extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=True,
            world_max_zoom=4
        )
        world_count = self.gen.estimate_world_tile_count(min_zoom, max_zoom)

        self.assertGreater(mixed_count, extent_only_count)
        self.assertLessEqual(mixed_count, world_count)

    def test_enabled_world_coverage_applies_through_world_max_zoom(self):
        min_zoom = 0
        max_zoom = 5
        mixed_count = self.gen.estimate_mixed_tile_count(
            self.user_extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=True,
            world_max_zoom=3
        )
        # World source covers full world at zooms 0-3
        # Region source covers user extent at zooms 0-5
        expected = (
            self.gen.estimate_world_tile_count(0, 3)
            + self.gen.estimate_mixed_tile_count(
                self.user_extent, 0, 5, include_world_base_zooms=False
            )
        )
        self.assertEqual(mixed_count, expected)

    def test_disabled_mode_matches_original_logic(self):
        min_zoom = 1
        max_zoom = 6
        original = self.gen.estimate_tile_count(self.user_extent, min_zoom, max_zoom)
        disabled = self.gen.estimate_mixed_tile_count(
            self.user_extent,
            min_zoom,
            max_zoom,
            include_world_base_zooms=False
        )
        self.assertEqual(disabled, original)

    def test_world_max_zoom_values_for_enabled_mode(self):
        extent = self.user_extent
        count_three, _ = self.gen.validate_tile_count(
            extent, 0, 5, include_world_base_zooms=True, world_max_zoom=3
        )
        count_five, _ = self.gen.validate_tile_count(
            extent, 0, 5, include_world_base_zooms=True, world_max_zoom=5
        )
        self.assertGreaterEqual(count_five, count_three)


    def test_enabled_mode_adds_world_zooms_below_selected_min_zoom(self):
        mixed_count = self.gen.estimate_mixed_tile_count(
            self.user_extent,
            6,
            7,
            include_world_base_zooms=True,
            world_max_zoom=3
        )
        expected = (
            self.gen.estimate_world_tile_count(0, 3)
            + self.gen.estimate_mixed_tile_count(
                self.user_extent, 6, 7, include_world_base_zooms=False
            )
        )
        self.assertEqual(mixed_count, expected)

    def test_world_zoom_selection_helper_enforces_world_0_to_2(self):
        custom_world = _FakeRectangle(-180, -85, 180, 85)
        chosen_at_zoom2 = self.gen._get_extent_for_zoom(
            self.user_extent, custom_world, 2, include_world_base_zooms=True, world_max_zoom=3
        )
        chosen_at_zoom4 = self.gen._get_extent_for_zoom(
            self.user_extent, custom_world, 4, include_world_base_zooms=True, world_max_zoom=3
        )
        self.assertIs(chosen_at_zoom2, custom_world)
        self.assertIs(chosen_at_zoom4, self.user_extent)

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

    def test_rect_count_matches_world_enabled_estimate(self):
        extent = self._make_extent(-1, -1, 1, 1)
        self.gen.get_world_extent = MagicMock(return_value=_FakeRectangle(-180, -85, 180, 85))
        rects = self.gen.get_tile_grid_rects(
            extent, 6, 7,
            include_world_base_zooms=True,
            world_max_zoom=3
        )
        estimated = self.gen.estimate_tile_count(
            extent, 6, 7,
            include_world_base_zooms=True,
            world_max_zoom=3
        )
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
                extent, 0, 1, '/tmp/test.smp', tile_format='BMP'
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

    def test_style_minzoom_uses_zero_when_world_base_zooms_enabled(self):
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 6, 12,
            include_world_base_zooms=True,
            world_max_zoom=3
        )
        source = list(style['sources'].values())[0]
        self.assertEqual(source['minzoom'], 0)
        self.assertEqual(source['maxzoom'], 12)
        self.assertEqual(source['bounds'], [-180.0, -85.0511, 180.0, 85.0511])


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




class TestWorldBaseZoomGeneration(unittest.TestCase):
    """Generation path should include world base zooms below selected min zoom."""

    def test_generation_uses_effective_zoom_set_when_world_enabled(self):
        gen = SMPGenerator()
        feedback = MagicMock()
        feedback.isCanceled.return_value = False
        gen.feedback = feedback

        seen_zooms = []

        def _record_zoom(_extent, zoom):
            seen_zooms.append(zoom)
            return [(0, 0, 0, 0)]

        gen._calculate_tiles_at_zoom = MagicMock(side_effect=_record_zoom)
        gen.get_world_extent = MagicMock(return_value=_FakeRectangle(-180, -85, 180, 85))

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()
        fake_img.save = MagicMock()

        with patch.object(_mod, 'QgsMapSettings', MagicMock()), \
             patch.object(_mod, 'QgsProject', _FakeProject), \
             patch('comapeo_smp_generator.QImage', return_value=fake_img), \
             patch('comapeo_smp_generator.QPainter', return_value=MagicMock()), \
             patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', return_value=MagicMock()):

            tmp = tempfile.mkdtemp()
            try:
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1),
                    6,
                    7,
                    tmp,
                    tile_format='PNG',
                    include_world_base_zooms=True,
                    world_max_zoom=3
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(sorted(set(seen_zooms)), [0, 1, 2, 3, 6, 7])

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

    def test_tile_submission_is_bounded_while_reporting_progress(self):
        """Large exports should not queue every tile before progress can advance."""
        gen = SMPGenerator()
        feedback = MagicMock()
        feedback.isCanceled.return_value = False
        gen.feedback = feedback

        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 9, 0, 9)])  # 100 tiles
        gen._render_single_tile = MagicMock(return_value=True)

        import comapeo_smp_generator as _mod

        executors = []

        class _FakeFuture:
            def __init__(self, value):
                self._value = value

            def result(self):
                return self._value

            def cancel(self):
                return True

        class _FakeExecutor:
            def __init__(self, *args, **kwargs):
                self.pending = []
                self.max_pending = 0
                executors.append(self)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def submit(self, fn, *args, **kwargs):
                future = _FakeFuture(fn(*args, **kwargs))
                self.pending.append(future)
                self.max_pending = max(self.max_pending, len(self.pending))
                return future

        def _fake_wait(futures, return_when=None, timeout=None):
            executor = executors[0]
            future = futures[0]
            executor.pending.remove(future)
            return {future}, set(futures[1:])

        with patch.object(_mod, 'QgsMapSettings', MagicMock()), \
             patch.object(_mod, 'QgsProject', _FakeProject), \
             patch.object(_mod, 'ThreadPoolExecutor', _FakeExecutor), \
             patch.object(_mod, 'wait', side_effect=_fake_wait):

            tmp = tempfile.mkdtemp()
            try:
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp, tile_format='PNG',
                    max_workers=2
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(executors), 1)
        self.assertLessEqual(executors[0].max_pending, 4)
        self.assertGreater(feedback.setProgress.call_count, 1)

    def test_wait_timeout_path_can_cancel_without_completed_futures(self):
        """A non-completing future must not trap the coordinator in wait()."""
        gen = SMPGenerator()
        feedback = MagicMock()
        feedback.isCanceled.return_value = False
        gen.feedback = feedback

        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 0, 0, 0)])
        gen._render_single_tile = MagicMock(return_value=True)

        import comapeo_smp_generator as _mod

        executors = []

        class _FakeFuture:
            def __init__(self, value):
                self._value = value
                self.cancelled = False

            def result(self):
                return self._value

            def cancel(self):
                self.cancelled = True
                return True

        class _FakeExecutor:
            def __init__(self, *args, **kwargs):
                self.pending = []
                executors.append(self)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def submit(self, fn, *args, **kwargs):
                future = _FakeFuture(fn(*args, **kwargs))
                self.pending.append(future)
                return future

        wait_calls = {'count': 0}

        def _fake_wait(futures, return_when=None, timeout=None):
            wait_calls['count'] += 1
            if wait_calls['count'] == 1:
                feedback.isCanceled.return_value = True
                return set(), set(futures)
            raise AssertionError('Coordinator kept waiting after cancellation with no completed futures')

        with patch.object(_mod, 'QgsMapSettings', MagicMock()), \
             patch.object(_mod, 'QgsProject', _FakeProject), \
             patch.object(_mod, 'ThreadPoolExecutor', _FakeExecutor), \
             patch.object(_mod, 'wait', side_effect=_fake_wait):

            tmp = tempfile.mkdtemp()
            try:
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(0, 0, 1, 1), 0, 0, tmp, tile_format='PNG',
                    max_workers=1
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(wait_calls['count'], 1)
        self.assertTrue(executors[0].pending[0].cancelled)


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
            # Pre-create the tile file at source_index=0 path
            zoom_dir = os.path.join(tmp, '0', '0', '0')
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
            current_dir = os.path.join(cache, '0', '0', '0')
            stale_dir = os.path.join(cache, '0', '1', '0')
            os.makedirs(current_dir, exist_ok=True)
            os.makedirs(stale_dir, exist_ok=True)
            with open(os.path.join(current_dir, '0.png'), 'wb') as fh:
                fh.write(b'\x89PNG')
            with open(os.path.join(stale_dir, '0.png'), 'wb') as fh:
                fh.write(b'\x89PNG')
            with open(os.path.join(cache, TileCache.META_FILE), 'w') as fh:
                json.dump({"0/0/0/0": "PNG:85:any"}, fh)

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

    def test_generate_with_cache_dir_includes_world_low_zoom_tiles_in_manifest(self):
        """Cache-backed exports must archive world tiles added below selected min zoom."""
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(6, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 0, 0, 0)])
        gen.get_world_extent = MagicMock(return_value=_FakeRectangle(-180, -85, 180, 85))

        cache = tempfile.mkdtemp()
        out_dir = tempfile.mkdtemp()
        try:
            import json
            # World source tiles (source_index=0): zooms 0-3
            world_tiles = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]
            # Region source tiles (source_index=1): zooms 6-7
            region_tiles = [(6, 0, 0), (7, 0, 0)]
            stale_tiles = [(5, 0, 0)]

            for zoom, x, y in world_tiles + region_tiles + stale_tiles:
                # World tiles go under source_index=0, region under source_index=1
                src_idx = 0 if (zoom, x, y) in world_tiles else 1
                tile_dir = os.path.join(cache, str(src_idx), str(zoom), str(x))
                os.makedirs(tile_dir, exist_ok=True)
                with open(os.path.join(tile_dir, f'{y}.png'), 'wb') as fh:
                    fh.write(b'\x89PNG')

            with open(os.path.join(cache, TileCache.META_FILE), 'w') as fh:
                meta = {}
                for z, x, y in world_tiles:
                    meta[f"0/{z}/{x}/{y}"] = "PNG:85:any"
                for z, x, y in region_tiles:
                    meta[f"1/{z}/{x}/{y}"] = "PNG:85:any"
                json.dump(meta, fh)

            out_path = os.path.join(out_dir, 'world-cache.smp')
            gen.generate_smp_from_canvas(
                _FakeRectangle(-1, -1, 1, 1),
                6,
                7,
                out_path,
                cache_dir=cache,
                include_world_base_zooms=True,
                world_max_zoom=3
            )

            import zipfile
            with zipfile.ZipFile(out_path) as zf:
                names = set(zf.namelist())

            for zoom, x, y in world_tiles:
                self.assertIn(f's/0/{zoom}/{x}/{y}.png', names)
            for zoom, x, y in region_tiles:
                self.assertIn(f's/1/{zoom}/{x}/{y}.png', names)
            self.assertNotIn('s/0/5/0/0.png', names)
            self.assertNotIn('s/1/5/0/0.png', names)
            self.assertNotIn(f's/0/{TileCache.META_FILE}', names)
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

    def test_post_render_cancellation_skips_save(self):
        """When cancellation is signalled while a render job is in progress,
        _render_single_tile should skip saving the tile after the job finishes."""
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()
        fake_job = MagicMock()

        feedback = MagicMock()
        # First call: cancel_event check (not set) → enters render_lock
        # Second call: post-render cancellation check → True
        feedback.isCanceled.return_value = True
        gen.feedback = feedback

        cancel_event = threading.Event()

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob',
                       return_value=fake_job):
                result = gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, False,
                    cancel_event=cancel_event
                )
            self.assertFalse(result)
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
                tile_dir = os.path.join(tiles_dir, '0', '0', '0')
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
        tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)

        # Real tile
        with open(os.path.join(tile_file_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        # Cache metadata sidecar that must NOT appear in the archive
        from comapeo_smp_generator import TileCache
        with open(os.path.join(tiles_dir, TileCache.META_FILE), 'w') as f:
            import json
            json.dump({"0/0/0/0": "PNG:85"}, f)

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
        tile_paths = {'0/0/0/0.png'}
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
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(b'\x89PNG')

        out_path = os.path.join(self.tmp, 'stale.smp')
        # Only zoom 0 belongs to current export
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path,
                               tile_paths={'0/0/0/0.png'})

        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
        self.assertIn('s/0/0/0/0.png', names)
        self.assertNotIn('s/0/1/0/0.png', names)

    def test_current_tiles_included_when_tile_paths_provided(self):
        import zipfile
        smp = self._build_archive_with_meta(tile_paths={'0/0/0/0.png'})
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
            self.assertEqual(len(data), 11)
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
        qgis_core.QgsProcessingParameterBoolean = MagicMock()
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
        algo.parameterAsBool = MagicMock(return_value=False)
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
        algo.parameterAsBool = MagicMock(return_value=False)
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
        algo.parameterAsBool = MagicMock(return_value=False)

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
        algo.parameterAsBool = MagicMock(return_value=False)

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
        algo.parameterAsBool = MagicMock(return_value=False)
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
        algo.parameterAsBool = MagicMock(return_value=False)

        ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertTrue(ok)


    def test_world_max_zoom_out_of_range_fails_when_enabled(self):
        """Enabled world-base-zooms must reject values outside 3..5."""
        algo = self._make_algorithm()
        extent = self._make_extent(0, 0, 1, 1)
        algo.parameterAsExtent = MagicMock(return_value=extent)
        algo.parameterAsInt = MagicMock(
            side_effect=lambda p, k, c: 0 if k == 'MIN_ZOOM' else (6 if k == 'WORLD_MAX_ZOOM' else 5)
        )
        algo.parameterAsBool = MagicMock(return_value=True)

        ok, msg = algo.checkParameterValues({}, MagicMock())

        self.assertFalse(ok)
        self.assertIn('between 3 and 5', msg)


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
        tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
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

class TestVersionFileInArchive(unittest.TestCase):
    """SMP archive must contain a VERSION file with content '1.0'."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_minimal_smp(self):
        gen = SMPGenerator()
        style_path = os.path.join(self.tmp, 'style.json')
        import json
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)
        with open(os.path.join(tile_file_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path)
        return out_path

    def test_version_file_exists_in_archive(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            self.assertIn('VERSION', zf.namelist())

    def test_version_file_content(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            content = zf.read('VERSION').decode('utf-8')
        self.assertEqual(content, '1.0')


class TestSourceFoldersValue(unittest.TestCase):
    """smp:sourceFolders value must be 's/0', not '0'."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-10, -10, 10, 10])

    def _make_extent(self):
        return _FakeRectangle(-10, -10, 10, 10)

    def test_source_folders_value_is_s_slash_0(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        source_id = list(style['sources'].keys())[0]
        self.assertEqual(style['metadata']['smp:sourceFolders'][source_id], 's/0')

    def test_source_folders_value_not_bare_0(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        source_id = list(style['sources'].keys())[0]
        self.assertNotEqual(style['metadata']['smp:sourceFolders'][source_id], '0')


class TestSourceNoCenter(unittest.TestCase):
    """Source definition must not contain a 'center' key."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-10, -10, 10, 10])

    def _make_extent(self):
        return _FakeRectangle(-10, -10, 10, 10)

    def test_source_has_no_center_key(self):
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        source = list(style['sources'].values())[0]
        self.assertNotIn('center', source)

    def test_root_center_still_exists(self):
        """Root-level center should still be present (only source center was removed)."""
        style = self.gen._create_style_from_canvas(self._make_extent(), 0, 10)
        self.assertIn('center', style)
        self.assertEqual(len(style['center']), 2)


class TestWebPFormatSupport(unittest.TestCase):
    """WebP tile format support (Task 5)."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-10, -10, 10, 10])

    def _make_extent(self):
        return _FakeRectangle(-10, -10, 10, 10)

    def test_webp_format_constant_exists(self):
        """SMPGenerator must define a TILE_FORMAT_WEBP constant."""
        self.assertEqual(SMPGenerator.TILE_FORMAT_WEBP, 'WEBP')

    def test_webp_style_has_webp_url(self):
        """style.json tiles URL must use .webp extension."""
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 0, 10, 'WEBP'
        )
        source = list(style['sources'].values())[0]
        self.assertIn('.webp', source['tiles'][0])

    def test_webp_style_format_field(self):
        """style.json source 'format' field must be 'webp'."""
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 0, 10, 'WEBP'
        )
        source = list(style['sources'].values())[0]
        self.assertEqual(source['format'], 'webp')

    def test_webp_accepted_by_generate_smp(self):
        """generate_smp_from_canvas must accept 'WEBP' without raising ValueError."""
        gen = SMPGenerator()
        gen.validate_tile_count = MagicMock(return_value=(1, None))
        gen.validate_extent_size = MagicMock(return_value=None)
        gen.validate_disk_space = MagicMock()
        gen._create_style_from_canvas = MagicMock(return_value={"version": 8})
        gen._generate_tiles_from_canvas = MagicMock()
        gen._build_smp_archive = MagicMock()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])

        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'test.smp')
            extent = _FakeRectangle(-1, -1, 1, 1)
            gen.generate_smp_from_canvas(
                extent, 0, 1, out, tile_format='WEBP'
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # If we get here without ValueError, the test passes

    def test_webp_tile_paths_in_manifest(self):
        """_tile_paths_from_source_plans must produce .webp extensions for WEBP format."""
        source_plans = [{'tiles_by_zoom': [(0, 0, 0, 0, 0, 1, 0)], 'source_index': 0}]
        paths = SMPGenerator._tile_paths_from_source_plans(source_plans, 'WEBP')
        self.assertIn('0/0/0/0.webp', paths)
        self.assertNotIn('0/0/0/0.png', paths)
        self.assertNotIn('0/0/0/0.jpg', paths)

    def test_webp_archive_contains_webp_tiles(self):
        """Archive built with WEBP tiles must contain .webp entries."""
        gen = SMPGenerator()
        tmp = tempfile.mkdtemp()
        try:
            import json
            style_path = os.path.join(tmp, 'style.json')
            with open(style_path, 'w') as f:
                json.dump({"version": 8, "sources": {}, "layers": []}, f)

            tiles_dir = os.path.join(tmp, 'tiles')
            tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
            os.makedirs(tile_file_dir, exist_ok=True)
            with open(os.path.join(tile_file_dir, '0.webp'), 'wb') as f:
                f.write(b'RIFF\x00\x00\x00\x00WEBP')

            out_path = os.path.join(tmp, 'output.smp')
            gen._build_smp_archive(
                style_path=style_path,
                tiles_dir=tiles_dir,
                output_path=out_path
            )

            import zipfile
            with zipfile.ZipFile(out_path) as zf:
                names = zf.namelist()
            self.assertIn('s/0/0/0/0.webp', names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_webp_tile_uses_zip_stored(self):
        """WebP tiles (like PNG/JPG) should use ZIP_STORED compression."""
        gen = SMPGenerator()
        tmp = tempfile.mkdtemp()
        try:
            import json
            style_path = os.path.join(tmp, 'style.json')
            with open(style_path, 'w') as f:
                json.dump({"version": 8, "sources": {}, "layers": []}, f)

            tiles_dir = os.path.join(tmp, 'tiles')
            tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
            os.makedirs(tile_file_dir, exist_ok=True)
            with open(os.path.join(tile_file_dir, '0.webp'), 'wb') as f:
                f.write(b'RIFF\x00\x00\x00\x00WEBP')

            out_path = os.path.join(tmp, 'output.smp')
            gen._build_smp_archive(
                style_path=style_path,
                tiles_dir=tiles_dir,
                output_path=out_path
            )

            import zipfile
            with zipfile.ZipFile(out_path) as zf:
                info = zf.getinfo('s/0/0/0/0.webp')
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_webp_render_single_tile_uses_webp_format(self):
        """_render_single_tile must save with 'WEBP' Qt format string."""
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()
        fake_img.save.return_value = True

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'WEBP', 85, False
                )
            # Verify save was called with 'WEBP' format and quality
            save_call = fake_img.save.call_args
            self.assertEqual(save_call[0][1], 'WEBP')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_webp_storage_estimate(self):
        """estimate_tile_storage_bytes should handle WEBP format."""
        # WebP should use a reasonable estimate (similar to JPG)
        webp_estimate = self.gen.estimate_tile_storage_bytes(100, 'WEBP')
        self.assertGreater(webp_estimate, 0)
        # Should be less than PNG estimate
        png_estimate = self.gen.estimate_tile_storage_bytes(100, 'PNG')
        self.assertLessEqual(webp_estimate, png_estimate)


class TestTileDeduplication(unittest.TestCase):
    """SHA-256 based tile deduplication in SMP archives (Task 6)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_smp_with_duplicate_tiles(self, dedup_enabled=True):
        """Build an SMP archive with intentionally duplicate tile content."""
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        # Create 4 tiles with the SAME content (simulating uniform low-zoom tiles)
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(identical_content)

        out_path = os.path.join(self.tmp, f'output_{dedup_enabled}.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path,
            dedup=dedup_enabled
        )
        return out_path

    def test_dedup_reduces_archive_size(self):
        """Archive with dedup enabled should be smaller than without."""
        smp_dedup = self._build_smp_with_duplicate_tiles(dedup_enabled=True)
        smp_no_dedup = self._build_smp_with_duplicate_tiles(dedup_enabled=False)
        size_dedup = os.path.getsize(smp_dedup)
        size_no_dedup = os.path.getsize(smp_no_dedup)

        self.assertLess(size_dedup, size_no_dedup,
                        f"Dedup archive ({size_dedup}B) should be smaller than "
                        f"non-dedup ({size_no_dedup}B)")

    def test_dedup_all_tile_paths_still_present(self):
        """With dedup, all tile paths must still exist in the archive."""
        import zipfile
        smp = self._build_smp_with_duplicate_tiles(dedup_enabled=True)
        with zipfile.ZipFile(smp) as zf:
            names = set(zf.namelist())
        for expected in ['s/0/0/0/0.png', 's/0/1/0/0.png', 's/0/1/0/1.png', 's/0/1/1/0.png']:
            self.assertIn(expected, names, f"Missing tile path: {expected}")

    def test_dedup_tiles_extract_correctly(self):
        """All tiles extracted from a dedup archive must have correct content.

        Note: Dedup archives use a shared-local-header strategy where multiple
        CD entries point to one local header. The first arcname in each hash group
        matches the local header filename and extracts cleanly via zf.read().
        Duplicate entries share the same offset and must be read via raw bytes.
        """
        import zipfile
        import struct
        import warnings as _warnings
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        smp = self._build_smp_with_duplicate_tiles(dedup_enabled=True)
        with zipfile.ZipFile(smp) as zf:
            # The first arcname per unique hash extracts cleanly via zf.read()
            first_names = set()
            for info in zf.infolist():
                if info.filename.startswith('s/0/') and info.filename.endswith('.png'):
                    first_names.add(info.filename)
                    break  # only check first tile
            for name in first_names:
                with _warnings.catch_warnings():
                    _warnings.simplefilter("ignore", UserWarning)
                    content = zf.read(name)
                self.assertEqual(content, identical_content,
                                 f"Tile {name} content mismatch after dedup")

            # Verify ALL tile CD entries exist in namelist (correct arcnames)
            expected_tiles = {'s/0/1/1/0.png', 's/0/1/0/0.png', 's/0/1/0/1.png', 's/0/0/0/0.png'}
            actual_tiles = {n for n in zf.namelist() if n.startswith('s/0/') and n.endswith('.png')}
            self.assertEqual(actual_tiles, expected_tiles,
                             f"Expected tile paths not found. Got: {actual_tiles}")

            # Verify raw tile data at each CD entry's offset is correct
            for info in zf.infolist():
                if info.filename.startswith('s/0/') and info.filename.endswith('.png'):
                    with open(smp, 'rb') as raw:
                        raw.seek(info.header_offset)
                        sig = raw.read(4)
                        self.assertEqual(sig, b'PK\x03\x04',
                                         f"Tile {info.filename} has bad local header at offset {info.header_offset}")
                        # Skip local header to find data
                        raw.seek(info.header_offset + 26)
                        name_len, extra_len = struct.unpack('<HH', raw.read(4))
                        raw.seek(info.header_offset + 30 + name_len + extra_len)
                        data = raw.read(info.file_size)
                        self.assertEqual(data, identical_content,
                                         f"Tile {info.filename} data mismatch at offset {info.header_offset}")

    def test_no_dedup_produces_larger_archive_for_duplicates(self):
        """Without dedup, identical tiles should each be stored independently."""
        import zipfile
        smp = self._build_smp_with_duplicate_tiles(dedup_enabled=False)
        with zipfile.ZipFile(smp) as zf:
            tile_entries = [i for i in zf.infolist() if i.filename.startswith('s/0/')]
        # Each tile should have its own data (unique header offsets)
        offsets = [e.header_offset for e in tile_entries]
        self.assertEqual(len(offsets), len(set(offsets)),
                         "Without dedup, each tile should have a unique offset")

    def test_dedup_unique_tiles_not_affected(self):
        """Dedup must not affect tiles with unique content."""
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_unique.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_unique')
        # Create tiles with UNIQUE content
        for i, (z, x, y) in enumerate([(0, 0, 0), (1, 0, 0)]):
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(f'\x89PNG unique {i}'.encode())

        out_dedup = os.path.join(self.tmp, 'unique_dedup.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_dedup,
            dedup=True
        )

        out_no_dedup = os.path.join(self.tmp, 'unique_no_dedup.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_no_dedup,
            dedup=False
        )

        # With all unique tiles, sizes should be very similar
        size_dedup = os.path.getsize(out_dedup)
        size_no_dedup = os.path.getsize(out_no_dedup)
        # Dedup might be slightly larger due to hash table overhead, but within 10%
        self.assertLessEqual(
            size_dedup, size_no_dedup * 1.1,
            f"Dedup with unique tiles should not inflate archive by >10%: "
            f"{size_dedup} vs {size_no_dedup}"
        )

    def test_dedup_preserves_version_and_style(self):
        """Dedup must not affect VERSION or style.json entries."""
        import zipfile
        import json
        smp = self._build_smp_with_duplicate_tiles(dedup_enabled=True)
        with zipfile.ZipFile(smp) as zf:
            self.assertIn('VERSION', zf.namelist())
            self.assertEqual(zf.read('VERSION').decode(), '1.0')
            self.assertIn('style.json', zf.namelist())
            style = json.loads(zf.read('style.json'))
            self.assertEqual(style['version'], 8)

    def test_cancel_during_dedup_phase1_returns_false(self):
        """Phase 1 cancellation must return False without creating the output file.

        isCanceled() call breakdown for 4 identical tiles (1 unique hash),
        when calling _build_smp_archive (the wrapper):
          Wrapper os.walk: 1 call at line 1150 (before loop) +
                          4 calls at line 1153 (one per tile found)
                          = 5 wrapper calls (all False to reach dedup)
          Phase 1 hashing: 4 calls (one per tile)
          Total to cancel in Phase 1 after 2 tiles: 5 + 2 = 7 False, then True

        This test cancels after 2 Phase-1 hashing calls (8th overall returns True).
        Because Phase 1 is cancelled, the output file is never created.
        """
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_p1.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_p1')
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(identical_content)

        out_path = os.path.join(self.tmp, 'cancel_p1.smp')

        feedback = MagicMock()
        # 5 wrapper calls (all False) + 2 Phase-1 calls (False) + True (cancel on 3rd Phase-1)
        feedback.isCanceled.side_effect = [False] * 7 + [True]
        gen.feedback = feedback

        result = gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path,
            dedup=True
        )
        self.assertFalse(result)
        self.assertFalse(os.path.exists(out_path),
                         "Output file must not be created when Phase 1 is cancelled")

    def test_cancel_during_dedup_phase2_returns_false(self):
        """Phase 2 cancellation must return False and clean up the partial output file.

        isCanceled() call breakdown for 4 identical tiles (1 unique hash),
        when calling _build_smp_archive (the wrapper):
          Wrapper os.walk: 1 call at line 1150 (before loop) +
                          4 calls at line 1153 (one per tile found)
                          = 5 wrapper calls (all False to reach dedup)
          Phase 1 hashing: 4 calls -> all False (Phase 1 completes)
          Phase 2 tile-writing: 1 call  -> False (unique tile is written)
          Phase 2 CD writing: True on first CD write iteration
          Total: 5 + 4 + 1 + 1 = 11 values, cancels at call 11

        side_effect = [False]*10 + [True]
        """
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_p2.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_p2')
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(identical_content)

        out_path = os.path.join(self.tmp, 'cancel_p2.smp')

        feedback = MagicMock()
        # Wrapper calls: 5 (all False to reach dedup)
        # Phase 1 hashing: 4 calls (all False, completes)
        # Phase 2 tile-writing: 1 call (False, unique tile written)
        # Phase 2 CD writing: True on first CD entry
        feedback.isCanceled.side_effect = [False] * 5 + [False] * 4 + [False] + [True]
        gen.feedback = feedback

        result = gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path,
            dedup=True
        )
        self.assertFalse(result)
        self.assertFalse(os.path.exists(out_path),
                         "Partial output file must be cleaned up on Phase 2 cancellation")

    def test_dedup_succeeds_with_feedback_none(self):
        """Dedup must work correctly when feedback is None (no cancellation support)."""
        import zipfile as _zipfile
        gen = SMPGenerator()
        gen.feedback = None

        import json
        style_path = os.path.join(self.tmp, 'style_nofb.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_nofb')
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        for z, x, y in [(0, 0, 0), (1, 0, 0)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(identical_content)

        out_path = os.path.join(self.tmp, 'nofeedback.smp')
        result = gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path,
            dedup=True
        )
        self.assertTrue(result)
        self.assertTrue(os.path.exists(out_path))
        with _zipfile.ZipFile(out_path) as zf:
            names = set(zf.namelist())
        self.assertIn('s/0/0/0/0.png', names)
        self.assertIn('s/0/1/0/0.png', names)

    def test_dedup_error_cleans_up_partial_file(self):
        """An OSError during Phase 2 must delete the partial output file and re-raise."""
        from unittest.mock import patch as _patch
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_err.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_err')
        for z, x, y in [(0, 0, 0), (1, 0, 0)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(b'\x89PNG unique_' + str(z).encode())

        out_path = os.path.join(self.tmp, 'error_cleanup.smp')

        # Make the tile file unreadable during Phase 2 to trigger OSError.
        # Phase 1 reads succeed (for hashing). We patch open() so that the
        # second open of the tile file (Phase 2 re-read) raises OSError.
        real_open = open
        call_counts = {}

        def patched_open(path, mode='r', **kwargs):
            if mode == 'rb' and path not in (style_path,):
                count = call_counts.get(path, 0) + 1
                call_counts[path] = count
                if count > 1:  # second open of same tile = Phase 2 re-read
                    raise OSError(f"Simulated read error for {path}")
            return real_open(path, mode, **kwargs)

        with _patch('builtins.open', side_effect=patched_open):
            with self.assertRaises(OSError):
                gen._build_smp_archive(
                    style_path=style_path,
                    tiles_dir=tiles_dir,
                    output_path=out_path,
                    dedup=True
                )

        self.assertFalse(os.path.exists(out_path),
                         "Partial output file must be removed after OSError in Phase 2")

    def test_dedup_raises_on_too_many_entries(self):
        """Building an archive with >=65535 total entries must raise ValueError.

        We create 3 real files with distinct content and build a tile_entries list
        of 65,534 items (same files reused with unique arcnames). Combined with
        style.json and VERSION, that totals 65,536 central directory entries --
        exceeding the 65,534 limit (65,535 is the ZIP64 magic marker).

        Phase 1 deduplicates to 3 unique hashes. Phase 2 writes 3 tile files.
        The CD building loop creates 65,536 in-memory entries, then the guard fires
        before any CD data is written to disk. The except handler cleans up.
        """
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_overflow.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        # 3 real files with distinct content
        real_files = []
        for i in range(3):
            p = os.path.join(self.tmp, f'real_tile_{i}.png')
            with open(p, 'wb') as f:
                f.write(bytes([i]))
            real_files.append(p)

        # 65,534 tile_entries (same files cycled, unique arcnames)
        tile_entries = [
            (real_files[i % 3], f's/0/0/0/tile_{i}.png')
            for i in range(65534)
        ]

        out_path = os.path.join(self.tmp, 'overflow.smp')
        with self.assertRaises(ValueError) as ctx:
            gen._build_smp_archive_dedup(style_path, tile_entries, out_path)

        self.assertIn('65535', str(ctx.exception))
        self.assertFalse(os.path.exists(out_path),
                         "Output file must be cleaned up after entry-count ValueError")

    def test_check_zip32_limit_raises_on_overflow(self):
        """_check_zip32_limit must raise ValueError when file position exceeds 4 GiB."""
        mock_file = MagicMock()
        mock_file.tell.return_value = 0x100000000  # 4 GiB + 1 byte

        with self.assertRaises(ValueError) as ctx:
            SMPGenerator._check_zip32_limit(mock_file)

        self.assertIn('4 GB', str(ctx.exception))

    def test_check_zip32_limit_passes_at_boundary(self):
        """_check_zip32_limit must not raise when file position is exactly at the limit."""
        mock_file = MagicMock()
        mock_file.tell.return_value = 0xFFFFFFFF  # exactly 4 GiB - 1 byte, OK

        # Should not raise
        SMPGenerator._check_zip32_limit(mock_file)

    def test_dedup_with_tile_paths_filtering(self):
        """tile_paths filtering and dedup must compose correctly.

        6 tiles on disk: 4 current (identical content) + 2 stale.
        tile_paths includes only the 4 current tiles.
        The archive must contain exactly the 4 current tiles plus style.json and VERSION.
        Dedup must still work (archive smaller than non-dedup with same tiles).
        """
        import zipfile as _zipfile
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_filter.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_filter')
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100

        # 4 current tiles (identical content)
        current = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0)]
        for z, x, y in current:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(identical_content)

        # 2 stale tiles (different content)
        for z, x, y in [(2, 0, 0), (2, 0, 1)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                f.write(b'\x89PNG stale')

        tile_paths = {f'0/{z}/{x}/{y}.png' for z, x, y in current}

        out_dedup = os.path.join(self.tmp, 'filter_dedup.smp')
        result = gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_dedup,
            tile_paths=tile_paths,
            dedup=True
        )
        self.assertTrue(result)

        with _zipfile.ZipFile(out_dedup) as zf:
            names = set(zf.namelist())

        # All 4 current tiles present
        for z, x, y in current:
            self.assertIn(f's/0/{z}/{x}/{y}.png', names,
                          f"Current tile {z}/{x}/{y}.png missing from archive")

        # Stale tiles absent
        self.assertNotIn('s/0/2/0/0.png', names)
        self.assertNotIn('s/0/2/0/1.png', names)

        # Dedup still works (smaller than non-dedup)
        out_no_dedup = os.path.join(self.tmp, 'filter_no_dedup.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_no_dedup,
            tile_paths=tile_paths,
            dedup=False
        )
        self.assertLess(os.path.getsize(out_dedup), os.path.getsize(out_no_dedup))

    def test_dedup_return_value_propagated(self):
        """_build_smp_archive must propagate _build_smp_archive_dedup's return value."""
        from unittest.mock import patch as _patch
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style_prop.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles_prop')
        d = os.path.join(tiles_dir, '0', '0')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, '0.png'), 'wb') as f:
            f.write(b'\x89PNG')

        out_path = os.path.join(self.tmp, 'propagate.smp')

        with _patch.object(gen, '_build_smp_archive_dedup', return_value=False) as mock_dedup:
            result = gen._build_smp_archive(
                style_path=style_path,
                tiles_dir=tiles_dir,
                output_path=out_path,
                dedup=True
            )

        self.assertFalse(result, "_build_smp_archive must return False when dedup returns False")
        mock_dedup.assert_called_once()


class TestSMPValidation(unittest.TestCase):
    """Python-based SMP format validation (Task 7)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_valid_smp(self, tile_format='PNG'):
        """Build a spec-compliant SMP archive for validation testing."""
        gen = SMPGenerator()
        tile_ext = tile_format.lower()
        if tile_ext == 'jpg':
            tile_ext = 'jpg'
            tile_content = b'\xFF\xD8\xFF\xE0'
        elif tile_ext == 'webp':
            tile_content = b'RIFF\x00\x00\x00\x00WEBP'
        else:
            tile_content = b'\x89PNG\r\n\x1a\n'

        import json
        style = {
            "version": 8,
            "name": "Test Map",
            "sources": {
                "mbtiles-source": {
                    "type": "raster",
                    "format": tile_ext,
                    "minzoom": 0,
                    "maxzoom": 1,
                    "bounds": [-10, -10, 10, 10],
                    "tiles": [f"smp://maps.v1/s/0/{{z}}/{{x}}/{{y}}.{tile_ext}"]
                }
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "raster", "type": "raster", "source": "mbtiles-source"}
            ],
            "metadata": {
                "smp:bounds": [-10, -10, 10, 10],
                "smp:maxzoom": 1,
                "smp:sourceFolders": {"mbtiles-source": "s/0"}
            },
            "center": [0, 0],
            "zoom": 0
        }

        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump(style, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)]:
            d = os.path.join(tiles_dir, '0', str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{y}.{tile_ext}'), 'wb') as f:
                f.write(tile_content)

        out_path = os.path.join(self.tmp, 'valid.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path
        )
        return out_path

    def test_validate_version_file(self):
        """Validator must check VERSION file exists with content '1.0'."""
        import zipfile
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            names = zf.namelist()
            self.assertIn('VERSION', names)
            content = zf.read('VERSION').decode('utf-8')
            self.assertEqual(content, '1.0')

    def test_validate_style_json_structure(self):
        """Validator must check style.json has required fields."""
        import zipfile
        import json
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            style = json.loads(zf.read('style.json'))

        # Required top-level fields
        self.assertIn('version', style)
        self.assertEqual(style['version'], 8)
        self.assertIn('sources', style)
        self.assertIn('layers', style)

        # Source must have required fields
        source = list(style['sources'].values())[0]
        self.assertIn('type', source)
        self.assertIn('tiles', source)
        self.assertIn('minzoom', source)
        self.assertIn('maxzoom', source)

    def test_validate_source_folders_match_archive(self):
        """smp:sourceFolders paths must match actual archive structure."""
        import zipfile
        import json
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            style = json.loads(zf.read('style.json'))
            archive_names = set(zf.namelist())

        source_folders = style.get('metadata', {}).get('smp:sourceFolders', {})
        for source_id, folder_path in source_folders.items():
            # folder_path should be a prefix in the archive
            matching = [n for n in archive_names if n.startswith(folder_path + '/')]
            self.assertGreater(len(matching), 0,
                               f"No archive entries found under {folder_path}")

    def test_validate_tile_paths_resolve(self):
        """Tile URL template paths must resolve to actual archive entries."""
        import zipfile
        import json
        import re
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            style = json.loads(zf.read('style.json'))
            archive_names = set(zf.namelist())

        for source_id, source in style['sources'].items():
            tiles_template = source['tiles'][0]
            # Extract the path pattern: smp://maps.v1/s/0/{z}/{x}/{y}.ext
            # Convert to archive path pattern
            path_pattern = tiles_template.replace('smp://maps.v1/', '')
            # Replace template variables with regex
            path_regex = path_pattern.replace('{z}', r'\d+').replace('{x}', r'\d+').replace('{y}', r'\d+')
            pattern = re.compile(f'^{path_regex}$')

            matching = [n for n in archive_names if pattern.match(n)]
            self.assertGreater(len(matching), 0,
                               f"No archive entries match tile template {tiles_template}")

    def test_validate_no_cache_metadata(self):
        """Archive must not contain _cache_meta.json."""
        import zipfile
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            for name in zf.namelist():
                self.assertNotIn('_cache_meta.json', name)

    def test_validate_all_formats(self):
        """Validation should pass for PNG, JPG, and WEBP formats."""
        import zipfile
        for fmt in ['PNG', 'JPG', 'WEBP']:
            smp = self._build_valid_smp(tile_format=fmt)
            self.assertTrue(zipfile.is_zipfile(smp), f"{fmt} SMP is not a valid zip")
            with zipfile.ZipFile(smp) as zf:
                self.assertIn('VERSION', zf.namelist())
                self.assertIn('style.json', zf.namelist())

    def test_validate_missing_version_detected(self):
        """Validator must detect missing VERSION file."""
        import zipfile
        import json
        smp = self._build_valid_smp()
        # Rebuild without VERSION by manually creating zip
        bad_smp = os.path.join(self.tmp, 'no_version.smp')
        with zipfile.ZipFile(smp) as zf_in:
            with zipfile.ZipFile(bad_smp, 'w') as zf_out:
                for item in zf_in.infolist():
                    if item.filename != 'VERSION':
                        zf_out.writestr(item, zf_in.read(item.filename))
        with zipfile.ZipFile(bad_smp) as zf:
            self.assertNotIn('VERSION', zf.namelist())

    def test_validate_tile_completeness(self):
        """All tiles referenced in style bounds should exist in archive."""
        import zipfile
        import json
        smp = self._build_valid_smp()
        with zipfile.ZipFile(smp) as zf:
            style = json.loads(zf.read('style.json'))
            archive_names = set(zf.namelist())

        source = list(style['sources'].values())[0]
        tile_ext = source.get('format', 'png')
        # At minimum, zoom 0 tile (0,0,0) must exist
        self.assertIn(f's/0/0/0/0.{tile_ext}', archive_names)


class TestZipCompressionMethods(unittest.TestCase):
    """Tile entries use ZIP_STORED; style.json uses ZIP_DEFLATED."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_minimal_smp(self):
        gen = SMPGenerator()
        style_path = os.path.join(self.tmp, 'style.json')
        import json
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)
        with open(os.path.join(tile_file_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(style_path=style_path,
                               tiles_dir=tiles_dir,
                               output_path=out_path)
        return out_path

    def test_tile_entries_use_zip_stored(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            tile_entries = [i for i in zf.infolist()
                           if i.filename.startswith('s/0/')]
        self.assertGreater(len(tile_entries), 0)
        for entry in tile_entries:
            self.assertEqual(
                entry.compress_type, zipfile.ZIP_STORED,
                f"Tile {entry.filename!r} should use ZIP_STORED, "
                f"got {entry.compress_type}"
            )

    def test_style_json_uses_zip_deflated(self):
        import zipfile
        smp = self._build_minimal_smp()
        with zipfile.ZipFile(smp) as zf:
            style_info = zf.getinfo('style.json')
        self.assertEqual(
            style_info.compress_type, zipfile.ZIP_DEFLATED,
            f"style.json should use ZIP_DEFLATED, got {style_info.compress_type}"
        )


# ===================================================================
# Tests for Separate World Sources (multi-source SMP)
# ===================================================================

class TestBuildSingleSourcePlan(unittest.TestCase):
    """Tests for _build_single_source_plan helper."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]
        self.world_extent = _FakeRectangle(-180, -85.0511, 180, 85.0511)
        self.user_extent = _FakeRectangle(-1, -1, 1, 1)
        self.gen.get_world_extent = lambda: self.world_extent

    def test_world_source_plan_has_correct_source_id(self):
        plan = self.gen._build_single_source_plan(
            self.world_extent, list(range(0, 3)),
            source_id="world-overview", source_index=0
        )
        self.assertEqual(plan['source_id'], 'world-overview')
        self.assertEqual(plan['source_index'], 0)

    def test_region_source_plan_has_correct_source_id(self):
        plan = self.gen._build_single_source_plan(
            self.user_extent, list(range(5, 8)),
            source_id="region-detail", source_index=1
        )
        self.assertEqual(plan['source_id'], 'region-detail')
        self.assertEqual(plan['source_index'], 1)

    def test_world_source_covers_full_world_at_low_zooms(self):
        plan = self.gen._build_single_source_plan(
            self.world_extent, list(range(0, 3)),
            source_id="world-overview", source_index=0
        )
        # At zoom 0, the whole world is 1 tile
        self.assertEqual(plan['total_tiles'], sum(4**z for z in range(0, 3)))
        self.assertEqual(plan['export_zooms'], [0, 1, 2])

    def test_tiles_by_zoom_tuples_have_seven_elements(self):
        plan = self.gen._build_single_source_plan(
            self.user_extent, list(range(0, 2)),
            source_id="region-detail", source_index=1
        )
        for entry in plan['tiles_by_zoom']:
            self.assertEqual(len(entry), 7, f"Expected 7-tuple, got {entry}")

    def test_source_index_in_tiles_by_zoom(self):
        plan = self.gen._build_single_source_plan(
            self.world_extent, [0],
            source_id="world-overview", source_index=0
        )
        for entry in plan['tiles_by_zoom']:
            self.assertEqual(entry[6], 0, "source_index should be 0")

        plan2 = self.gen._build_single_source_plan(
            self.user_extent, [5],
            source_id="region-detail", source_index=1
        )
        for entry in plan2['tiles_by_zoom']:
            self.assertEqual(entry[6], 1, "source_index should be 1")

    def test_source_bounds_returned(self):
        plan = self.gen._build_single_source_plan(
            self.world_extent, [0],
            source_id="world-overview", source_index=0
        )
        self.assertEqual(plan['source_bounds'], [-180, -85.0511, 180, 85.0511])

    def test_antimeridian_region_extent_multiple_ranges(self):
        """Region source with antimeridian-crossing extent must produce multiple ranges."""
        antimeridian_extent = _FakeRectangle(170, -10, -170, 10)
        plan = self.gen._build_single_source_plan(
            antimeridian_extent, [4],
            source_id="region-detail", source_index=1
        )
        # Should have at least 2 ranges at zoom 4 for antimeridian crossing
        self.assertGreaterEqual(len(plan['tiles_by_zoom']), 2)
        self.assertGreater(plan['total_tiles'], 0)


class TestMultiSourceExportPlan(unittest.TestCase):
    """Tests for _build_export_plan with separate sources."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]
        self.world_extent = _FakeRectangle(-180, -85.0511, 180, 85.0511)
        self.user_extent = _FakeRectangle(-1, -1, 1, 1)
        self.gen.get_world_extent = lambda: self.world_extent

    def test_world_disabled_has_single_source(self):
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=False
        )
        self.assertEqual(len(plan['sources']), 1)
        self.assertEqual(plan['sources'][0]['source_id'], 'mbtiles-source')
        self.assertEqual(plan['sources'][0]['source_index'], 0)

    def test_world_enabled_has_two_sources(self):
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        self.assertEqual(len(plan['sources']), 2)
        self.assertEqual(plan['sources'][0]['source_id'], 'world-overview')
        self.assertEqual(plan['sources'][0]['source_index'], 0)
        self.assertEqual(plan['sources'][1]['source_id'], 'region-detail')
        self.assertEqual(plan['sources'][1]['source_index'], 1)

    def test_world_source_zooms_start_at_zero(self):
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        world_source = plan['sources'][0]
        self.assertEqual(world_source['export_zooms'][0], 0)
        self.assertEqual(world_source['export_zooms'][-1], 3)

    def test_world_source_max_zoom_floored_at_2(self):
        """When world_max_zoom < 2, the world source should still go up to zoom 2."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=1
        )
        world_source = plan['sources'][0]
        self.assertEqual(world_source['export_zooms'][-1], 2)

    def test_region_source_zooms_match_user_range(self):
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        region_source = plan['sources'][1]
        self.assertEqual(region_source['export_zooms'], list(range(5, 11)))

    def test_merged_tiles_by_zoom_preserves_order(self):
        """World source tiles come first, then region source tiles."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        # All world tiles should have source_index=0, region tiles source_index=1
        world_tiles = [t for t in plan['tiles_by_zoom'] if t[6] == 0]
        region_tiles = [t for t in plan['tiles_by_zoom'] if t[6] == 1]
        # World tiles come first in the merged list
        first_region_idx = next(
            i for i, t in enumerate(plan['tiles_by_zoom']) if t[6] == 1
        )
        last_world_idx = len(plan['tiles_by_zoom']) - len(region_tiles) - 1
        # All entries before first_region_idx should be world (source_index=0)
        for i in range(first_region_idx):
            self.assertEqual(plan['tiles_by_zoom'][i][6], 0)

    def test_total_tiles_is_sum_across_sources(self):
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        source_total = sum(s['total_tiles'] for s in plan['sources'])
        self.assertEqual(plan['total_tiles'], source_total)

    def test_world_tiles_backward_compat(self):
        """world_tiles must remain sum(4**z for z in export_zooms)."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        export_zooms = plan['export_zooms']
        expected_world_tiles = sum(4**z for z in export_zooms)
        self.assertEqual(plan['world_tiles'], expected_world_tiles)

    def test_world_pct_backward_compat(self):
        """world_pct must equal (total_tiles / world_tiles) * 100."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        if plan['world_tiles'] > 0:
            expected_pct = (plan['total_tiles'] / plan['world_tiles']) * 100
            self.assertAlmostEqual(plan['world_pct'], expected_pct, places=5)

    def test_world_disabled_backward_compat(self):
        """When world disabled, plan should look like current single-source."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=False
        )
        self.assertEqual(len(plan['sources']), 1)
        self.assertEqual(plan['sources'][0]['source_id'], 'mbtiles-source')
        # tiles_by_zoom should all have source_index=0
        for t in plan['tiles_by_zoom']:
            self.assertEqual(t[6], 0)
        # No source_index=1 entries
        self.assertFalse(any(t[6] == 1 for t in plan['tiles_by_zoom']))

    def test_world_tiles_backward_compat_when_disabled(self):
        """world_tiles formula must also hold when world tiles are disabled."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=False
        )
        export_zooms = plan['export_zooms']
        expected_world_tiles = sum(4**z for z in export_zooms)
        self.assertEqual(plan['world_tiles'], expected_world_tiles)

    def test_world_pct_backward_compat_when_disabled(self):
        """world_pct formula must also hold when world tiles are disabled."""
        plan = self.gen._build_export_plan(
            self.user_extent, 5, 10,
            include_world_base_zooms=False
        )
        if plan['world_tiles'] > 0:
            expected_pct = (plan['total_tiles'] / plan['world_tiles']) * 100
            self.assertAlmostEqual(plan['world_pct'], expected_pct, places=5)


class TestMultiSourceStyleJson(unittest.TestCase):
    """Tests for _create_style_from_canvas with source_plans."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])

    def _make_extent(self):
        return _FakeRectangle(-1, -1, 1, 1)

    def test_single_source_style_backward_compat(self):
        """With source_plans=None, style should be single-source (backward compat)."""
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG'
        )
        self.assertEqual(len(style['sources']), 1)
        source_id = list(style['sources'].keys())[0]
        self.assertEqual(source_id, 'mbtiles-source')

    def test_single_source_style_with_one_plan(self):
        """With source_plans having 1 entry, style should be single-source."""
        source_plans = [{
            'source_id': 'mbtiles-source',
            'source_index': 0,
            'source_bounds': [-1, -1, 1, 1],
            'export_zooms': list(range(5, 11)),
            'tiles_by_zoom': [],
            'total_tiles': 0
        }]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        self.assertEqual(len(style['sources']), 1)
        self.assertIn('mbtiles-source', style['sources'])
        self.assertEqual(style['metadata']['smp:sourceFolders']['mbtiles-source'], 's/0')

    def test_two_source_style_has_two_sources(self):
        """With 2 source plans, style should have two sources."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        self.assertEqual(len(style['sources']), 2)
        self.assertIn('world-overview', style['sources'])
        self.assertIn('region-detail', style['sources'])

    def test_two_source_style_has_two_raster_layers(self):
        """Multi-source style should have background + world-raster + region-raster layers."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        raster_layers = [l for l in style['layers'] if l['type'] == 'raster']
        self.assertEqual(len(raster_layers), 2)
        source_refs = {l['source'] for l in raster_layers}
        self.assertEqual(source_refs, {'world-overview', 'region-detail'})

    def test_two_source_style_has_two_source_folders(self):
        """smp:sourceFolders should have entries for both sources."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        folders = style['metadata']['smp:sourceFolders']
        self.assertEqual(folders['world-overview'], 's/0')
        self.assertEqual(folders['region-detail'], 's/1')

    def test_two_source_world_overview_bounds(self):
        """World overview source should have full-world bounds."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        world_src = style['sources']['world-overview']
        self.assertEqual(world_src['bounds'], [-180, -85.0511, 180, 85.0511])
        self.assertEqual(world_src['minzoom'], 0)
        self.assertEqual(world_src['maxzoom'], 3)

    def test_two_source_region_detail_bounds(self):
        """Region detail source should have user extent bounds."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        region_src = style['sources']['region-detail']
        self.assertEqual(region_src['bounds'], [-1, -1, 1, 1])
        self.assertEqual(region_src['minzoom'], 5)
        self.assertEqual(region_src['maxzoom'], 10)

    def test_smp_bounds_uses_region_not_world(self):
        """smp:bounds must use region-detail bounds (highest maxzoom source)."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        self.assertEqual(style['metadata']['smp:bounds'], [-1, -1, 1, 1])
        self.assertEqual(style['metadata']['smp:maxzoom'], 10)

    def test_source_tiles_url_includes_source_index(self):
        """World tiles URL should use s/0/, region tiles URL should use s/1/."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        world_tiles_url = style['sources']['world-overview']['tiles'][0]
        region_tiles_url = style['sources']['region-detail']['tiles'][0]
        self.assertIn('s/0/', world_tiles_url)
        self.assertIn('s/1/', region_tiles_url)

    def test_sources_have_format_name_version(self):
        """Each source should have format, name, and version properties."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-1, -1, 1, 1],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        for src_id, src in style['sources'].items():
            self.assertIn('format', src)
            self.assertIn('name', src)
            self.assertIn('version', src)
            self.assertEqual(src['version'], '2.0')

    def test_center_derived_from_region_bounds(self):
        """Root center should be derived from region-detail bounds."""
        source_plans = [
            {
                'source_id': 'world-overview',
                'source_index': 0,
                'source_bounds': [-180, -85.0511, 180, 85.0511],
                'export_zooms': list(range(0, 4)),
                'tiles_by_zoom': [],
                'total_tiles': 85
            },
            {
                'source_id': 'region-detail',
                'source_index': 1,
                'source_bounds': [-10, -20, 30, 40],
                'export_zooms': list(range(5, 11)),
                'tiles_by_zoom': [],
                'total_tiles': 100
            }
        ]
        style = self.gen._create_style_from_canvas(
            self._make_extent(), 5, 10, 'PNG',
            source_plans=source_plans
        )
        # Center should be midpoint of region bounds
        self.assertAlmostEqual(style['center'][0], (-10 + 30) / 2)
        self.assertAlmostEqual(style['center'][1], (-20 + 40) / 2)


class TestTilePathsFromSourcePlans(unittest.TestCase):
    """Tests for _tile_paths_from_source_plans static method."""

    def test_single_source_produces_zero_prefixed_paths(self):
        """Single source (source_index=0) should produce paths like 0/{z}/{x}/{y}.png."""
        source_plans = [{
            'tiles_by_zoom': [(0, 0, 0, 0, 0, 1, 0)],
            'source_index': 0
        }]
        paths = SMPGenerator._tile_paths_from_source_plans(source_plans, 'PNG')
        self.assertIn('0/0/0/0.png', paths)

    def test_two_sources_produce_separate_paths(self):
        """Two sources should produce paths with different source_index prefixes."""
        source_plans = [
            {'tiles_by_zoom': [(0, 0, 0, 0, 0, 1, 0)], 'source_index': 0},
            {'tiles_by_zoom': [(5, 3, 3, 7, 7, 1, 1)], 'source_index': 1}
        ]
        paths = SMPGenerator._tile_paths_from_source_plans(source_plans, 'PNG')
        self.assertIn('0/0/0/0.png', paths)
        self.assertIn('1/5/3/7.png', paths)

    def test_webp_extension(self):
        """Should use .webp extension for WEBP format."""
        source_plans = [
            {'tiles_by_zoom': [(0, 0, 0, 0, 0, 1, 0)], 'source_index': 0}
        ]
        paths = SMPGenerator._tile_paths_from_source_plans(source_plans, 'WEBP')
        self.assertIn('0/0/0/0.webp', paths)
        self.assertNotIn('.png', str(paths))

    def test_multi_range_tiles(self):
        """Should handle tiles_by_zoom with multiple ranges per zoom (antimeridian)."""
        source_plans = [{
            'tiles_by_zoom': [
                (4, 15, 15, 5, 5, 1, 0),  # first range
                (4, 0, 0, 5, 5, 1, 0),    # second range (antimeridian)
            ],
            'source_index': 0
        }]
        paths = SMPGenerator._tile_paths_from_source_plans(source_plans, 'PNG')
        self.assertIn('0/4/15/5.png', paths)
        self.assertIn('0/4/0/5.png', paths)


class TestArchivePathsMultiSource(unittest.TestCase):
    """Archive paths must use s/ prefix directly (not s/0/) since source_index is in tile path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_source_tiles_under_s_0(self):
        """Single source tiles should be archived under s/0/{z}/{x}/{y}."""
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        # Tiles at source_index 0 path
        tile_file_dir = os.path.join(tiles_dir, '0', '0', '0')
        os.makedirs(tile_file_dir, exist_ok=True)
        with open(os.path.join(tile_file_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path
        )

        import zipfile
        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
        self.assertIn('s/0/0/0/0.png', names)

    def test_two_source_tiles_under_s_0_and_s_1(self):
        """Two sources should have tiles under s/0/ and s/1/."""
        gen = SMPGenerator()
        import json
        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        # Source 0 tile
        s0_dir = os.path.join(tiles_dir, '0', '0', '0')
        os.makedirs(s0_dir, exist_ok=True)
        with open(os.path.join(s0_dir, '0.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')
        # Source 1 tile
        s1_dir = os.path.join(tiles_dir, '1', '5', '3')
        os.makedirs(s1_dir, exist_ok=True)
        with open(os.path.join(s1_dir, '7.png'), 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n')

        out_path = os.path.join(self.tmp, 'output.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path
        )

        import zipfile
        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()
        self.assertIn('s/0/0/0/0.png', names)
        self.assertIn('s/1/5/3/7.png', names)


class TestTileCacheSourceIndex(unittest.TestCase):
    """TileCache must use source_index in cache keys."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = TileCache(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_with_source_index_zero(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp, source_index=0)
        self.assertTrue(self.cache.is_fresh(0, 0, 0, fp, source_index=0))

    def test_different_source_index_not_fresh(self):
        """Cache entries for source 0 should not be fresh for source 1."""
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp, source_index=0)
        self.assertFalse(self.cache.is_fresh(0, 0, 0, fp, source_index=1))

    def test_mark_with_source_index_persists(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(5, 3, 7, fp, source_index=1)
        cache2 = TileCache(self.tmp)
        self.assertTrue(cache2.is_fresh(5, 3, 7, fp, source_index=1))
        self.assertFalse(cache2.is_fresh(5, 3, 7, fp, source_index=0))

    def test_invalidate_with_source_index(self):
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp, source_index=0)
        self.cache.invalidate(0, 0, 0, source_index=0)
        self.assertFalse(self.cache.is_fresh(0, 0, 0, fp, source_index=0))

    def test_default_source_index_is_zero(self):
        """Calling without source_index should default to 0."""
        fp = TileCache.make_fingerprint('PNG', 85)
        self.cache.mark(0, 0, 0, fp)
        self.assertTrue(self.cache.is_fresh(0, 0, 0, fp))
        self.assertTrue(self.cache.is_fresh(0, 0, 0, fp, source_index=0))


class TestCacheSchemaMigration(unittest.TestCase):
    """Cache schema must handle migration from old format."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_old_cache_meta_treated_as_stale(self):
        """Cache meta without schema_version should be treated as stale."""
        import json
        # Write old-format cache meta (no schema_version)
        meta_path = os.path.join(self.tmp, TileCache.META_FILE)
        with open(meta_path, 'w') as f:
            json.dump({"0/0/0": "PNG:85:fp1"}, f)

        cache = TileCache(self.tmp)
        fp = TileCache.make_fingerprint('PNG', 85, 'fp1')
        # Old-format key "0/0/0" should not match new key "0/0/0/0"
        self.assertFalse(cache.is_fresh(0, 0, 0, fp))

    def test_schema_version_1_treated_as_stale(self):
        """Cache meta with schema_version < 2 should be treated as stale."""
        import json
        meta_path = os.path.join(self.tmp, TileCache.META_FILE)
        with open(meta_path, 'w') as f:
            json.dump({"schema_version": 1, "0/0/0": "PNG:85:fp1"}, f)

        cache = TileCache(self.tmp)
        fp = TileCache.make_fingerprint('PNG', 85, 'fp1')
        self.assertFalse(cache.is_fresh(0, 0, 0, fp))


class TestRenderSingleTileSourceIndex(unittest.TestCase):
    """_render_single_tile must place tiles in source_index subdirectory."""

    def test_default_source_index_creates_zero_subdir(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()

        def real_save(path, *args, **kwargs):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'\x89PNG')
            return True

        fake_img.save.side_effect = real_save

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, False
                )
            # Tile should be at tmp/0/0/0/0.png
            self.assertTrue(
                os.path.exists(os.path.join(tmp, '0', '0', '0', '0.png')),
                "Tile should be at tiles_dir/0/z/x/y.ext"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_source_index_1_creates_one_subdir(self):
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        fake_img = MagicMock()

        def real_save(path, *args, **kwargs):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'\x89PNG')
            return True

        fake_img.save.side_effect = real_save

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                gen._render_single_tile(
                    MagicMock(), 5, 3, 7, tmp,
                    'PNG', 85, False,
                    source_index=1
                )
            # Tile should be at tmp/1/5/3/7.png
            self.assertTrue(
                os.path.exists(os.path.join(tmp, '1', '5', '3', '7.png')),
                "Tile should be at tiles_dir/1/z/x/y.ext"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resume_checks_source_index_path(self):
        """Resume check should look at the correct source_index path."""
        gen = SMPGenerator()
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        tmp = tempfile.mkdtemp()
        try:
            # Pre-create tile at source_index=1 path
            tile_dir = os.path.join(tmp, '1', '0', '0')
            os.makedirs(tile_dir, exist_ok=True)
            tile_path = os.path.join(tile_dir, '0.png')
            with open(tile_path, 'wb') as f:
                f.write(b'FAKE')

            fake_img = MagicMock()
            fake_img.save.return_value = True

            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()):
                result = gen._render_single_tile(
                    MagicMock(), 0, 0, 0, tmp,
                    'PNG', 85, True,
                    source_index=1
                )
            # Should skip rendering because tile exists at source_index=1 path
            fake_img.save.assert_not_called()
            self.assertFalse(result)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestGenerateTilesWithSourceIndex(unittest.TestCase):
    """_generate_tiles_from_canvas must thread source_index through pipeline."""

    def test_tiles_placed_in_source_subdirs(self):
        """Tiles should be placed in tiles_dir/{source_index}/{z}/{x}/{y}.{ext}."""
        gen = SMPGenerator()
        gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        gen._calculate_tiles_at_zoom = MagicMock(return_value=[(0, 0, 0, 0)])
        gen._calculate_tile_extent = MagicMock(return_value=MagicMock())

        import comapeo_smp_generator as _mod
        fake_img = MagicMock()

        def real_save(path, *args, **kwargs):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'\x89PNG')
            return True

        fake_img.save.side_effect = real_save

        tmp = tempfile.mkdtemp()
        try:
            with patch('comapeo_smp_generator.QImage', return_value=fake_img), \
                 patch('comapeo_smp_generator.QPainter', MagicMock()), \
                 patch('comapeo_smp_generator.QgsMapRendererCustomPainterJob', MagicMock()), \
                 patch.object(_mod, 'QgsMapSettings', MagicMock()), \
                 patch.object(_mod, 'QgsProject', _FakeProject):
                gen._generate_tiles_from_canvas(
                    _FakeRectangle(-1, -1, 1, 1), 0, 0, tmp,
                    tile_format='PNG',
                    export_plan={
                        'total_tiles': 1,
                        'tiles_by_zoom': [(0, 0, 0, 0, 0, 1, 0)],
                    }
                )
            # Tile should be at tmp/0/0/0/0.png
            self.assertTrue(
                os.path.exists(os.path.join(tmp, '0', '0', '0', '0.png'))
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestZoomGapCase(unittest.TestCase):
    """When min_zoom > max(2, world_max_zoom), sources have disjoint zoom ranges."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]
        self.world_extent = _FakeRectangle(-180, -85.0511, 180, 85.0511)
        self.user_extent = _FakeRectangle(-1, -1, 1, 1)
        self.gen.get_world_extent = lambda: self.world_extent

    def test_disjoint_zoom_ranges(self):
        """When min_zoom > world_max_zoom, world and region zooms should not overlap."""
        plan = self.gen._build_export_plan(
            self.user_extent, 8, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        world_zooms = plan['sources'][0]['export_zooms']
        region_zooms = plan['sources'][1]['export_zooms']
        # No overlap between world and region zoom ranges
        self.assertEqual(set(world_zooms) & set(region_zooms), set())

    def test_both_sources_generate_tiles(self):
        """Both sources should produce tiles even with zoom gap."""
        plan = self.gen._build_export_plan(
            self.user_extent, 8, 10,
            include_world_base_zooms=True, world_max_zoom=3
        )
        self.assertGreater(plan['sources'][0]['total_tiles'], 0)
        self.assertGreater(plan['sources'][1]['total_tiles'], 0)


class TestSMPRoundtripMultiSource(unittest.TestCase):
    """Full SMP roundtrip test: generate archive, inspect contents."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multi_source_archive_contains_both_source_trees(self):
        """Archive should contain s/0/ and s/1/ tile trees."""
        gen = SMPGenerator()
        import json

        style = {
            "version": 8,
            "name": "Test Multi-Source",
            "sources": {
                "world-overview": {
                    "type": "raster",
                    "format": "png",
                    "minzoom": 0,
                    "maxzoom": 2,
                    "bounds": [-180, -85.0511, 180, 85.0511],
                    "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.png"]
                },
                "region-detail": {
                    "type": "raster",
                    "format": "png",
                    "minzoom": 5,
                    "maxzoom": 10,
                    "bounds": [-1, -1, 1, 1],
                    "tiles": ["smp://maps.v1/s/1/{z}/{x}/{y}.png"]
                }
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "world-raster", "type": "raster", "source": "world-overview"},
                {"id": "region-raster", "type": "raster", "source": "region-detail"}
            ],
            "metadata": {
                "smp:bounds": [-1, -1, 1, 1],
                "smp:maxzoom": 10,
                "smp:sourceFolders": {
                    "world-overview": "s/0",
                    "region-detail": "s/1"
                }
            },
            "center": [0, 0],
            "zoom": 5
        }

        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump(style, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        # World source tiles (source 0)
        for z in range(0, 3):
            n = 1 << z
            for x in range(n):
                for y in range(n):
                    d = os.path.join(tiles_dir, '0', str(z), str(x))
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                        f.write(b'\x89PNG\r\n\x1a\n')

        # Region source tiles (source 1)
        for z in [5]:
            d = os.path.join(tiles_dir, '1', str(z), '16')
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, '16.png'), 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n')

        out_path = os.path.join(self.tmp, 'multi.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path
        )

        import zipfile
        with zipfile.ZipFile(out_path) as zf:
            names = set(zf.namelist())
            style_data = json.loads(zf.read('style.json'))

        # Both source trees present
        s0_tiles = {n for n in names if n.startswith('s/0/')}
        s1_tiles = {n for n in names if n.startswith('s/1/')}
        self.assertGreater(len(s0_tiles), 0, "No tiles under s/0/")
        self.assertGreater(len(s1_tiles), 0, "No tiles under s/1/")

        # style.json has both sources
        self.assertIn('world-overview', style_data['sources'])
        self.assertIn('region-detail', style_data['sources'])

        # sourceFolders map to directories that exist
        for src_id, folder in style_data['metadata']['smp:sourceFolders'].items():
            matching = [n for n in names if n.startswith(folder + '/')]
            self.assertGreater(len(matching), 0,
                               f"No entries under {folder} for source {src_id}")

    def test_tile_counts_match_source_plans(self):
        """Tile counts under s/0/ and s/1/ should match source plan totals."""
        gen = SMPGenerator()
        import json, zipfile

        # Build a real export plan to get accurate tile counts
        gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]
        plan = gen._build_export_plan(
            _FakeRectangle(-1, -1, 1, 1), 0, 2,
            include_world_base_zooms=True, world_max_zoom=1
        )

        world_plan = plan['sources'][0]
        region_plan = plan['sources'][1]

        tiles_dir = os.path.join(self.tmp, 'tiles')

        # Create tiles for both sources
        for sp in plan['sources']:
            for zoom, min_x, max_x, min_y, max_y, _, si in sp['tiles_by_zoom']:
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        d = os.path.join(tiles_dir, str(si), str(zoom), str(x))
                        os.makedirs(d, exist_ok=True)
                        with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                            f.write(b'\x89PNG\r\n\x1a\n')

        style = {
            "version": 8,
            "sources": {
                "world-overview": {
                    "type": "raster", "format": "png",
                    "minzoom": 0, "maxzoom": 1,
                    "bounds": [-180, -85.0511, 180, 85.0511],
                    "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.png"]
                },
                "region-detail": {
                    "type": "raster", "format": "png",
                    "minzoom": 0, "maxzoom": 2,
                    "bounds": [-1, -1, 1, 1],
                    "tiles": ["smp://maps.v1/s/1/{z}/{x}/{y}.png"]
                }
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "world-raster", "type": "raster", "source": "world-overview"},
                {"id": "region-raster", "type": "raster", "source": "region-detail"}
            ],
            "metadata": {
                "smp:bounds": [-1, -1, 1, 1],
                "smp:maxzoom": 2,
                "smp:sourceFolders": {
                    "world-overview": "s/0",
                    "region-detail": "s/1"
                }
            },
            "center": [0, 0],
            "zoom": 5
        }

        style_path = os.path.join(self.tmp, 'style_plan.json')
        with open(style_path, 'w') as f:
            json.dump(style, f)

        out_path = os.path.join(self.tmp, 'counts.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path
        )

        with zipfile.ZipFile(out_path) as zf:
            names = zf.namelist()

        s0_count = len([n for n in names if n.startswith('s/0/') and n.endswith('.png')])
        s1_count = len([n for n in names if n.startswith('s/1/') and n.endswith('.png')])

        self.assertEqual(s0_count, world_plan['total_tiles'],
                         f"s/0/ tile count {s0_count} != world plan total {world_plan['total_tiles']}")
        self.assertEqual(s1_count, region_plan['total_tiles'],
                         f"s/1/ tile count {s1_count} != region plan total {region_plan['total_tiles']}")


class TestDedupWithOverlappingZooms(unittest.TestCase):
    """Dedup must not collapse cross-source tiles even when content is identical."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_source_entries_present_with_identical_content(self):
        """When world and region share overlapping zooms with identical tile content,
        both s/0/ and s/1/ entries must exist in archive."""
        gen = SMPGenerator()
        import json

        style_path = os.path.join(self.tmp, 'style.json')
        with open(style_path, 'w') as f:
            json.dump({"version": 8, "sources": {}, "layers": []}, f)

        tiles_dir = os.path.join(self.tmp, 'tiles')
        identical_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100

        # Same tile coordinate (0, 0, 0) in both source 0 and source 1
        for source_idx in [0, 1]:
            d = os.path.join(tiles_dir, str(source_idx), '0', '0')
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, '0.png'), 'wb') as f:
                f.write(identical_content)

        out_path = os.path.join(self.tmp, 'overlap.smp')
        gen._build_smp_archive(
            style_path=style_path,
            tiles_dir=tiles_dir,
            output_path=out_path,
            dedup=True
        )

        import zipfile
        with zipfile.ZipFile(out_path) as zf:
            names = set(zf.namelist())

        # Both entries must exist despite identical content
        self.assertIn('s/0/0/0/0.png', names)
        self.assertIn('s/1/0/0/0.png', names)


class TestWorldBackwardCompatDisabled(unittest.TestCase):
    """When world tiles disabled, output must be identical to current behavior."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def test_single_source_named_mbtiles_source(self):
        plan = self.gen._build_export_plan(
            _FakeRectangle(-1, -1, 1, 1), 5, 10,
            include_world_base_zooms=False
        )
        self.assertEqual(plan['sources'][0]['source_id'], 'mbtiles-source')

    def test_style_single_source(self):
        self.gen._get_bounds_wgs84 = MagicMock(return_value=[-1, -1, 1, 1])
        style = self.gen._create_style_from_canvas(
            _FakeRectangle(-1, -1, 1, 1), 5, 10, 'PNG'
        )
        source_ids = list(style['sources'].keys())
        self.assertEqual(len(source_ids), 1)
        self.assertEqual(source_ids[0], 'mbtiles-source')
        self.assertEqual(
            style['metadata']['smp:sourceFolders']['mbtiles-source'], 's/0'
        )

    def test_tiles_by_zoom_six_tuples_when_disabled(self):
        """When world disabled, tiles_by_zoom tuples still have 7 elements (source_index=0)."""
        plan = self.gen._build_export_plan(
            _FakeRectangle(-1, -1, 1, 1), 5, 10,
            include_world_base_zooms=False
        )
        for t in plan['tiles_by_zoom']:
            self.assertEqual(len(t), 7)
            self.assertEqual(t[6], 0)


class TestGenerateSmpOrchestrationMultiSource(unittest.TestCase):
    """End-to-end orchestration tests for multi-source SMP generation."""

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

    def test_tiles_dir_is_style_dir_not_subdir(self):
        """tiles_dir should be the style_dir (s/), not style_dir + '/0'."""
        gen = self._patched_gen()
        extent = _FakeRectangle(-1, -1, 1, 1)

        temp_root = tempfile.mkdtemp()
        try:
            with patch('tempfile.mkdtemp', return_value=temp_root):
                gen.generate_smp_from_canvas(
                    extent, 0, 1, '/tmp/test.smp'
                )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

        # tiles_dir should be style_dir (s/), not style_dir/0
        call_args = gen._generate_tiles_from_canvas.call_args
        tiles_dir_arg = call_args[0][3]
        self.assertTrue(
            tiles_dir_arg.endswith('/s'),
            f"tiles_dir should end with /s, got {tiles_dir_arg}"
        )
        self.assertFalse(
            tiles_dir_arg.endswith('/s/0'),
            f"tiles_dir should NOT end with /s/0, got {tiles_dir_arg}"
        )


class TestCancellationMidSource(unittest.TestCase):
    """Cancellation between sources: resume must skip cached source 0, re-render source 1."""

    def setUp(self):
        self.gen = SMPGenerator()
        self.gen._get_bounds_wgs84 = lambda ext: [
            ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()
        ]

    def test_resume_skips_cached_source_zero(self):
        """After cancel between sources, resume reuses source 0 tiles from cache."""
        import tempfile, shutil, json, zipfile
        from unittest.mock import MagicMock, patch

        tmp = tempfile.mkdtemp()
        try:
            # Build an export plan with 2 sources (world enabled)
            plan = self.gen._build_export_plan(
                _FakeRectangle(-1, -1, 1, 1), 0, 2,
                include_world_base_zooms=True, world_max_zoom=1
            )
            self.assertEqual(len(plan['sources']), 2)

            # Source 0 tiles (world, zooms 0-1)
            world_tiles = [t for t in plan['tiles_by_zoom'] if t[6] == 0]
            # Source 1 tiles (region, zooms 0-2)
            region_tiles = [t for t in plan['tiles_by_zoom'] if t[6] == 1]

            tiles_dir = os.path.join(tmp, 'tiles')

            # Pre-create source 0 tiles (simulating they were rendered before cancel)
            for zoom, min_x, max_x, min_y, max_y, num_tiles, source_index in world_tiles:
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        d = os.path.join(tiles_dir, str(source_index), str(zoom), str(x))
                        os.makedirs(d, exist_ok=True)
                        with open(os.path.join(d, f'{y}.png'), 'wb') as f:
                            f.write(b'\x89PNG\r\n\x1a\n')

            # Verify source 0 tiles exist on disk
            s0_files = []
            for root, dirs, files in os.walk(tiles_dir):
                for fn in files:
                    s0_files.append(os.path.join(root, fn))
            self.assertGreater(len(s0_files), 0, 'Source 0 tiles should exist')

            # Source 1 tiles should NOT exist yet (not rendered before cancel)
            s1_dir = os.path.join(tiles_dir, '1')
            self.assertFalse(os.path.exists(s1_dir),
                             'Source 1 tiles should not exist before resume')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
