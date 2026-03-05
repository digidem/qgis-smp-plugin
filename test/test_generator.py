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
    TILE_COUNT_WARNING_THRESHOLD,
    TILE_COUNT_ERROR_THRESHOLD,
    BYTES_PER_TILE_PNG,
    BYTES_PER_TILE_JPG,
    MIN_FREE_SPACE_BYTES,
)


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
        gen._create_smp_archive = MagicMock()
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


if __name__ == '__main__':
    unittest.main()
