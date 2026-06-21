# coding=utf-8
"""Tests for SMP merge functionality - does not require a running QGIS instance."""

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock

from comapeo_smp_merger import (
    merge_smp_files, _parse_smp_uri, _make_smp_uri, _is_safe_tile_path,
    _bounds_match, _find_mergeable_sources,
)


def _build_smp(path, sources, tiles):
    """Build a minimal SMP ZIP file for testing.

    :param path: Output file path
    :param sources: Dict of source_id -> source_def for style.json sources.
        Special key '__metadata__' can provide extra metadata fields.
    :param tiles: Dict of archive_path (e.g. 's/0/0/0/0.png') -> bytes content
    """
    source_folders = {}
    for source_id, source_def in sources.items():
        if source_id == '__metadata__':
            continue
        tiles_uri = source_def.get('tiles', [])
        if tiles_uri:
            try:
                src_idx, _ = _parse_smp_uri(tiles_uri[0])
                source_folders[source_id] = "s/{}".format(src_idx)
            except ValueError:
                pass

    extra_meta = sources.get('__metadata__', {})
    style = {
        "version": 8,
        "name": "Test SMP",
        "sources": {k: v for k, v in sources.items()
                    if k != '__metadata__'},
        "layers": [
            {"id": "background", "type": "background",
             "paint": {"background-color": "white"}},
        ],
        "metadata": {
            "smp:sourceFolders": source_folders,
        },
    }
    style["metadata"].update(extra_meta)

    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('style.json', json.dumps(style, indent=2))
        zf.writestr('VERSION', '1.0')
        for arcname, data in tiles.items():
            zf.writestr(arcname, data)


def _make_source(source_index, ext='png', minzoom=0, maxzoom=14,
                 bounds=None):
    """Create a source definition dict for an SMP style.json."""
    source = {
        "type": "raster",
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "tiles": [_make_smp_uri(source_index, ext)],
    }
    if bounds is not None:
        source["bounds"] = bounds
    return source


class TestParseSmpUri(unittest.TestCase):
    """Tests for _parse_smp_uri helper."""

    def test_valid_png_uri(self):
        src_idx, ext = _parse_smp_uri(
            "smp://maps.v1/s/0/{z}/{x}/{y}.png"
        )
        self.assertEqual(src_idx, 0)
        self.assertEqual(ext, 'png')

    def test_valid_jpg_uri(self):
        src_idx, ext = _parse_smp_uri(
            "smp://maps.v1/s/3/{z}/{x}/{y}.jpg"
        )
        self.assertEqual(src_idx, 3)
        self.assertEqual(ext, 'jpg')

    def test_valid_webp_uri(self):
        src_idx, ext = _parse_smp_uri(
            "smp://maps.v1/s/10/{z}/{x}/{y}.webp"
        )
        self.assertEqual(src_idx, 10)
        self.assertEqual(ext, 'webp')

    def test_invalid_uri_raises(self):
        with self.assertRaises(ValueError):
            _parse_smp_uri("https://example.com/{z}/{x}/{y}.png")


class TestMakeSmpUri(unittest.TestCase):
    """Tests for _make_smp_uri helper."""

    def test_png_uri(self):
        uri = _make_smp_uri(0, 'png')
        self.assertEqual(uri, "smp://maps.v1/s/0/{z}/{x}/{y}.png")

    def test_high_index(self):
        uri = _make_smp_uri(42, 'jpg')
        self.assertEqual(uri, "smp://maps.v1/s/42/{z}/{x}/{y}.jpg")


class TestSafeTilePath(unittest.TestCase):
    """Tests for _is_safe_tile_path security helper."""

    def test_valid_tile_path(self):
        self.assertTrue(_is_safe_tile_path("s/0/0/0/0.png"))

    def test_valid_deep_tile_path(self):
        self.assertTrue(_is_safe_tile_path("s/5/14/8192/4096.webp"))

    def test_rejects_traversal(self):
        self.assertFalse(_is_safe_tile_path("s/0/../../evil.png"))

    def test_rejects_absolute_path(self):
        self.assertFalse(_is_safe_tile_path("/etc/passwd"))

    def test_rejects_backslash(self):
        self.assertFalse(_is_safe_tile_path("s\\0\\0\\0\\0.png"))

    def test_rejects_non_numeric_source(self):
        self.assertFalse(_is_safe_tile_path("s/abc/0/0/0.png"))


class TestBoundsMatch(unittest.TestCase):
    """Tests for _bounds_match helper."""

    def test_identical_bounds(self):
        self.assertTrue(
            _bounds_match([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        )

    def test_near_identical_bounds(self):
        self.assertTrue(
            _bounds_match([1.0, 2.0, 3.0, 4.0],
                          [1.0 + 1e-9, 2.0, 3.0, 4.0])
        )

    def test_different_bounds(self):
        self.assertFalse(
            _bounds_match([1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0])
        )

    def test_both_none(self):
        self.assertTrue(_bounds_match(None, None))

    def test_one_none(self):
        self.assertFalse(_bounds_match([1.0, 2.0, 3.0, 4.0], None))

    def test_caru_local_detail_bounds(self):
        """Exact Caru bounds should match."""
        bounds_a = [-46.69035754171865, -3.930368950984813,
                     -45.98482735612111, -3.54189559736741]
        bounds_b = [-46.69035754171865, -3.930368950984813,
                     -45.98482735612111, -3.54189559736741]
        self.assertTrue(_bounds_match(bounds_a, bounds_b))


class TestFindMergeableSources(unittest.TestCase):
    """Tests for _find_mergeable_sources logic."""

    def _make_candidate(self, input_idx, source_id, ext, bounds,
                        minzoom, maxzoom):
        return {
            'input_idx': input_idx,
            'source_id': source_id,
            'source_def': {'type': 'raster', 'tiles': []},
            'ext': ext,
            'bounds': bounds,
            'minzoom': minzoom,
            'maxzoom': maxzoom,
        }

    def test_adjacent_zooms_same_bounds_merge(self):
        """z12-16 and z17-18 with same bounds should merge."""
        bounds = [-46.69, -3.93, -45.98, -3.54]
        candidates = [
            self._make_candidate(0, 'local-detail', 'webp', bounds, 12, 16),
            self._make_candidate(1, 'mbtiles-source', 'webp', bounds, 17, 18),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_overlapping_zooms_do_not_merge(self):
        """z0-5 and z3-8 with same bounds should NOT merge."""
        bounds = [0, 0, 10, 10]
        candidates = [
            self._make_candidate(0, 'src_a', 'png', bounds, 0, 5),
            self._make_candidate(1, 'src_b', 'png', bounds, 3, 8),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0]), 1)
        self.assertEqual(len(groups[1]), 1)

    def test_different_bounds_do_not_merge(self):
        """Same zooms but different bounds should NOT merge."""
        candidates = [
            self._make_candidate(0, 'src_a', 'png',
                                 [0, 0, 10, 10], 0, 5),
            self._make_candidate(1, 'src_b', 'png',
                                 [20, 20, 30, 30], 6, 10),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 2)

    def test_different_ext_do_not_merge(self):
        """Same bounds, adjacent zooms, but different ext should NOT merge."""
        bounds = [0, 0, 10, 10]
        candidates = [
            self._make_candidate(0, 'src_a', 'png', bounds, 0, 5),
            self._make_candidate(1, 'src_b', 'jpg', bounds, 6, 10),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 2)

    def test_same_input_never_merges(self):
        """Sources from the same input should never merge."""
        bounds = [0, 0, 10, 10]
        candidates = [
            self._make_candidate(0, 'src_a', 'png', bounds, 0, 5),
            self._make_candidate(0, 'src_b', 'png', bounds, 6, 10),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 2)

    def test_three_way_merge(self):
        """Three sources with same bounds, z0-5, z6-11, z12-18 merge."""
        bounds = [0, 0, 10, 10]
        candidates = [
            self._make_candidate(0, 'world', 'webp', bounds, 0, 5),
            self._make_candidate(1, 'region', 'webp', bounds, 6, 11),
            self._make_candidate(2, 'local', 'webp', bounds, 12, 18),
        ]
        groups = _find_mergeable_sources(candidates)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)

    def test_caru_scenario(self):
        """Exact Caru scenario: z16 has 3 sources, z18 has 1 source.
        Only local-detail and mbtiles-source should merge."""
        candidates = [
            # z16 input sources
            self._make_candidate(
                0, 'world-overview', 'webp',
                [-180.0, -85.0511, 180.0, 85.0511], 0, 5),
            self._make_candidate(
                0, 'region-detail', 'webp',
                [-46.72847953787366, -3.967504056635188,
                 -45.81265340919218, -2.392460524550727], 6, 11),
            self._make_candidate(
                0, 'local-detail', 'webp',
                [-46.69035754171865, -3.930368950984813,
                 -45.98482735612111, -3.54189559736741], 12, 16),
            # z18 input source
            self._make_candidate(
                1, 'mbtiles-source', 'webp',
                [-46.69035754171865, -3.930368950984813,
                 -45.98482735612111, -3.54189559736741], 17, 18),
        ]
        groups = _find_mergeable_sources(candidates)
        # world-overview and region-detail are standalone
        # local-detail + mbtiles-source merge
        self.assertEqual(len(groups), 3)
        group_sizes = sorted([len(g) for g in groups])
        self.assertEqual(group_sizes, [1, 1, 2])

        # Find the merged group
        for group in groups:
            if len(group) == 2:
                ids = [candidates[i]['source_id'] for i in group]
                self.assertIn('local-detail', ids)
                self.assertIn('mbtiles-source', ids)


class TestMergeTwoSmps(unittest.TestCase):
    """Test merging two simple SMP files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _smp_a_path(self):
        return os.path.join(self.tmpdir, 'a.smp')

    def _smp_b_path(self):
        return os.path.join(self.tmpdir, 'b.smp')

    def _output_path(self):
        return os.path.join(self.tmpdir, 'merged.smp')

    def test_merge_two_single_source_smps(self):
        """Merge two SMPs each with one source."""
        a_path = self._smp_a_path()
        _build_smp(a_path,
                   sources={
                       "world": _make_source(0, 'png', 0, 3,
                                             [-10, -10, 10, 10]),
                   },
                   tiles={
                       "s/0/0/0/0.png": b"tile_a_0",
                       "s/0/1/0/0.png": b"tile_a_1",
                   })

        b_path = self._smp_b_path()
        _build_smp(b_path,
                   sources={
                       "detail": _make_source(0, 'png', 4, 8,
                                              [20, 20, 30, 30]),
                   },
                   tiles={
                       "s/0/4/0/0.png": b"tile_b_4",
                       "s/0/5/0/0.png": b"tile_b_5",
                   })

        output = self._output_path()
        result = merge_smp_files([a_path, b_path], output)
        self.assertEqual(result, output)
        self.assertTrue(os.path.isfile(output))

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertEqual(style['version'], 8)
            self.assertIn('world', style['sources'])
            self.assertIn('detail', style['sources'])
            self.assertEqual(len(style['sources']), 2)

            meta = style['metadata']
            self.assertIn('smp:sourceFolders', meta)
            folders = meta['smp:sourceFolders']
            self.assertEqual(len(folders), 2)
            self.assertEqual(meta['smp:maxzoom'], 8)

            bounds = meta['smp:bounds']
            self.assertEqual(bounds[0], -10)
            self.assertEqual(bounds[1], -10)
            self.assertEqual(bounds[2], 30)
            self.assertEqual(bounds[3], 30)

            world_tiles = style['sources']['world']['tiles'][0]
            detail_tiles = style['sources']['detail']['tiles'][0]
            world_idx, _ = _parse_smp_uri(world_tiles)
            detail_idx, _ = _parse_smp_uri(detail_tiles)
            self.assertNotEqual(world_idx, detail_idx)

            self.assertIn(
                's/{}/0/0/0.png'.format(world_idx), zf.namelist())
            self.assertIn(
                's/{}/4/0/0.png'.format(detail_idx), zf.namelist())

    def test_merge_preserves_tile_content(self):
        """Merged archive should contain exact tile data from inputs."""
        a_path = self._smp_a_path()
        tile_a_data = b"\x89PNG\r\n\x1a\n" + b"a" * 100
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": tile_a_data})

        b_path = self._smp_b_path()
        tile_b_data = b"\x89PNG\r\n\x1a\n" + b"b" * 100
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": tile_b_data})

        output = self._output_path()
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            src_a_idx, _ = _parse_smp_uri(
                style['sources']['src_a']['tiles'][0])
            src_b_idx, _ = _parse_smp_uri(
                style['sources']['src_b']['tiles'][0])
            a_tile = zf.read('s/{}/0/0/0.png'.format(src_a_idx))
            b_tile = zf.read('s/{}/0/0/0.png'.format(src_b_idx))
            self.assertEqual(a_tile, tile_a_data)
            self.assertEqual(b_tile, tile_b_data)

    def test_merge_rejects_single_input(self):
        """Must raise ValueError with fewer than 2 inputs."""
        with self.assertRaises(ValueError):
            merge_smp_files([self._smp_a_path()], self._output_path())

    def test_merge_rejects_zero_inputs(self):
        with self.assertRaises(ValueError):
            merge_smp_files([], self._output_path())

    def test_merge_rejects_nonexistent_file(self):
        a_path = self._smp_a_path()
        _build_smp(a_path,
                   sources={"src": _make_source(0, 'png')},
                   tiles={})
        with self.assertRaises(ValueError):
            merge_smp_files(
                [a_path, '/nonexistent/file.smp'],
                self._output_path()
            )

    def test_merge_rejects_corrupt_zip(self):
        """BadZipFile should be converted to ValueError."""
        a_path = self._smp_a_path()
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png')},
                   tiles={})
        # Create a corrupt "SMP" file
        corrupt_path = os.path.join(self.tmpdir, 'corrupt.smp')
        with open(corrupt_path, 'w') as fh:
            fh.write("not a zip file")
        with self.assertRaises(ValueError) as ctx:
            merge_smp_files([a_path, corrupt_path], self._output_path())
        self.assertIn('not a valid ZIP', str(ctx.exception))

    def test_merge_rejects_traversal_paths(self):
        """Tile paths with '..' should be silently skipped."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png')},
                   tiles={})
        # Manually inject a traversal path into the archive
        with zipfile.ZipFile(a_path, 'a') as zf:
            zf.writestr('s/0/../../evil.png', b'evil_data')

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={})

        output = self._output_path()
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            names = zf.namelist()
            for name in names:
                self.assertNotIn('..', name)
            self.assertNotIn('evil.png', names)

    def test_merge_rejects_output_overwriting_input(self):
        """Output path must not match any input path."""
        a_path = self._smp_a_path()
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png')},
                   tiles={})
        b_path = self._smp_b_path()
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={})
        with self.assertRaises(ValueError) as ctx:
            merge_smp_files([a_path, b_path], a_path)
        self.assertIn(
            'different from every input', str(ctx.exception))

    def test_merge_rejects_non_smp_uri(self):
        """Source with non-SMP tile URI should fail fast."""
        a_path = self._smp_a_path()
        # Build an SMP with an external URL source
        style = {
            "version": 8,
            "name": "A",
            "sources": {
                "ext_src": {
                    "type": "raster",
                    "tiles": ["https://example.com/{z}/{x}/{y}.png"],
                },
            },
            "layers": [],
            "metadata": {"smp:sourceFolders": {}},
        }
        with zipfile.ZipFile(a_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style))
            zf.writestr('VERSION', '1.0')

        b_path = self._smp_b_path()
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={})

        with self.assertRaises(ValueError) as ctx:
            merge_smp_files([a_path, b_path], self._output_path())
        self.assertIn('Unsupported non-SMP tile URI', str(ctx.exception))

    def test_merge_rejects_missing_tiles_uri(self):
        """Source with no tiles URI should fail fast."""
        a_path = self._smp_a_path()
        style = {
            "version": 8,
            "name": "A",
            "sources": {
                "no_tiles": {
                    "type": "raster",
                },
            },
            "layers": [],
            "metadata": {"smp:sourceFolders": {}},
        }
        with zipfile.ZipFile(a_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style))
            zf.writestr('VERSION', '1.0')

        b_path = self._smp_b_path()
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={})

        with self.assertRaises(ValueError) as ctx:
            merge_smp_files([a_path, b_path], self._output_path())
        self.assertIn('missing an SMP tile URI', str(ctx.exception))


class TestMergeThreeSmps(unittest.TestCase):
    """Test merging three or more SMP files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_merge_three_smps(self):
        """Merge three SMPs - all sources should be present."""
        paths = []
        for i in range(3):
            p = os.path.join(self.tmpdir, 'smp_{}.smp'.format(i))
            _build_smp(p,
                       sources={
                           "src_{}".format(i): _make_source(
                               0, 'png', i * 3, i * 3 + 3,
                               [i * 10, 0, i * 10 + 5, 5]
                           ),
                       },
                       tiles={
                           "s/0/{}/0/0.png".format(i * 3):
                               "tile_{}".format(i).encode(),
                       })
            paths.append(p)

        output = os.path.join(self.tmpdir, 'merged.smp')
        result = merge_smp_files(paths, output)
        self.assertEqual(result, output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertEqual(len(style['sources']), 3)
            for i in range(3):
                self.assertIn("src_{}".format(i), style['sources'])
            meta = style['metadata']
            # maxzoom = max(3, 6, 9) = 9
            self.assertEqual(meta['smp:maxzoom'], 9)

    def test_merge_four_smps(self):
        """Merge four SMPs to exercise larger merges."""
        paths = []
        for i in range(4):
            p = os.path.join(self.tmpdir, 'smp_{}.smp'.format(i))
            _build_smp(p,
                       sources={
                           "source_{}".format(i): _make_source(0, 'png'),
                       },
                       tiles={
                           "s/0/0/{}/0.png".format(i):
                               "t{}".format(i).encode(),
                       })
            paths.append(p)

        output = os.path.join(self.tmpdir, 'merged.smp')
        result = merge_smp_files(paths, output)
        self.assertEqual(result, output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertEqual(len(style['sources']), 4)


class TestMergeSourceIdConflicts(unittest.TestCase):
    """Test that source ID conflicts are resolved with suffixes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_duplicate_source_ids_get_suffixed(self):
        """Two inputs with same source ID should get _2 suffix."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "my-source": _make_source(0, 'png', 0, 5,
                                                 [0, 0, 10, 10]),
                   },
                   tiles={"s/0/0/0/0.png": b"tile_a"})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "my-source": _make_source(0, 'png', 5, 10,
                                                 [10, 10, 20, 20]),
                   },
                   tiles={"s/0/5/0/0.png": b"tile_b"})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertIn("my-source", style['sources'])
            self.assertIn("my-source_2", style['sources'])

    def test_three_duplicate_source_ids(self):
        """Three inputs with same source ID should get _2, _3 suffixes."""
        paths = []
        for i in range(3):
            p = os.path.join(self.tmpdir, 'smp_{}.smp'.format(i))
            _build_smp(p,
                       sources={"shared": _make_source(0, 'png')},
                       tiles={
                           "s/0/0/{}/0.png".format(i):
                               "t{}".format(i).encode(),
                       })
            paths.append(p)

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files(paths, output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertIn("shared", style['sources'])
            self.assertIn("shared_2", style['sources'])
            self.assertIn("shared_3", style['sources'])


class TestMergeBoundsUnion(unittest.TestCase):
    """Test that merged bounds are the union of all input bounds."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_disjoint_bounds(self):
        """Merged bounds should cover both inputs."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "src_a": _make_source(0, 'png', 0, 5,
                                             [-50, -50, -40, -40]),
                   },
                   tiles={})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "src_b": _make_source(0, 'png', 0, 5,
                                             [40, 40, 50, 50]),
                   },
                   tiles={})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            bounds = style['metadata']['smp:bounds']
            self.assertEqual(bounds, [-50, -50, 50, 50])

    def test_overlapping_bounds(self):
        """Overlapping bounds should produce union."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "src_a": _make_source(0, 'png', 0, 5,
                                             [-10, -10, 10, 10]),
                   },
                   tiles={})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "src_b": _make_source(0, 'png', 0, 5,
                                             [-5, -5, 15, 15]),
                   },
                   tiles={})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            bounds = style['metadata']['smp:bounds']
            self.assertEqual(bounds, [-10, -10, 15, 15])

    def test_maxzoom_is_maximum(self):
        """smp:maxzoom should be the max across all inputs."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "src_a": _make_source(0, 'png', 0, 5,
                                             [0, 0, 1, 1]),
                   },
                   tiles={})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "src_b": _make_source(0, 'png', 0, 14,
                                             [0, 0, 1, 1]),
                   },
                   tiles={})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertEqual(style['metadata']['smp:maxzoom'], 14)


class TestMergeIdenticalTileContent(unittest.TestCase):
    """Test that identical tile content remains readable after merging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_identical_tiles_preserved_at_each_source_path(self):
        """Identical input tiles should be readable from both output paths."""
        shared_tile = b"\x89PNG" + b"x" * 50

        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": shared_tile})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": shared_tile})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            src_a_idx, _ = _parse_smp_uri(
                style['sources']['src_a']['tiles'][0])
            src_b_idx, _ = _parse_smp_uri(
                style['sources']['src_b']['tiles'][0])
            tile_a = zf.read('s/{}/0/0/0.png'.format(src_a_idx))
            tile_b = zf.read('s/{}/0/0/0.png'.format(src_b_idx))
            self.assertEqual(tile_a, shared_tile)
            self.assertEqual(tile_b, shared_tile)

    def test_different_tiles_preserved(self):
        """Non-identical tiles should both be stored."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": b"unique_a"})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png', 0, 1)},
                   tiles={"s/0/0/0/0.png": b"unique_b"})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            src_a_idx, _ = _parse_smp_uri(
                style['sources']['src_a']['tiles'][0])
            src_b_idx, _ = _parse_smp_uri(
                style['sources']['src_b']['tiles'][0])
            tile_a = zf.read('s/{}/0/0/0.png'.format(src_a_idx))
            tile_b = zf.read('s/{}/0/0/0.png'.format(src_b_idx))
            self.assertEqual(tile_a, b"unique_a")
            self.assertEqual(tile_b, b"unique_b")


class TestMergeMultiSourceSmp(unittest.TestCase):
    """Test merging SMPs that have multiple sources each."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_merge_multi_source_smps(self):
        """Each input has 2 sources - all 4 should be in the merged output."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "world": _make_source(0, 'png', 0, 3,
                                             [-180, -85, 180, 85]),
                       "local": _make_source(1, 'jpg', 8, 14,
                                             [0, 0, 1, 1]),
                   },
                   tiles={
                       "s/0/0/0/0.png": b"world_tile",
                       "s/1/8/0/0.jpg": b"local_tile",
                   })

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "world": _make_source(0, 'png', 0, 3,
                                             [-180, -85, 180, 85]),
                       "region": _make_source(1, 'webp', 5, 10,
                                              [10, 10, 20, 20]),
                   },
                   tiles={
                       "s/0/0/0/0.png": b"b_world_tile",
                       "s/1/5/0/0.webp": b"region_tile",
                   })

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            self.assertEqual(len(style['sources']), 4)
            self.assertIn("world", style['sources'])
            self.assertIn("local", style['sources'])
            self.assertIn("world_2", style['sources'])
            self.assertIn("region", style['sources'])

            folders = style['metadata']['smp:sourceFolders']
            self.assertEqual(len(folders), 4)
            folder_set = set(folders.values())
            self.assertEqual(len(folder_set), 4)
            self.assertEqual(style['metadata']['smp:maxzoom'], 14)


class TestMergeLayerHandling(unittest.TestCase):
    """Test that layers are properly remapped in the merged style."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_layers_remap_source_refs(self):
        """Layer source references should point to the new source IDs."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        style_a = {
            "version": 8,
            "name": "A",
            "sources": {
                "satellite": _make_source(0, 'png', 0, 10,
                                          [0, 0, 10, 10]),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "sat-layer", "type": "raster",
                 "source": "satellite"},
            ],
            "metadata": {
                "smp:sourceFolders": {"satellite": "s/0"},
            },
        }
        with zipfile.ZipFile(a_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style_a))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/0/0/0.png', b'tile')

        b_path = os.path.join(self.tmpdir, 'b.smp')
        style_b = {
            "version": 8,
            "name": "B",
            "sources": {
                "satellite": _make_source(0, 'png', 0, 10,
                                          [10, 10, 20, 20]),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "sat-layer", "type": "raster",
                 "source": "satellite"},
            ],
            "metadata": {
                "smp:sourceFolders": {"satellite": "s/0"},
            },
        }
        with zipfile.ZipFile(b_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style_b))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/0/0/0.png', b'tile_b')

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            non_bg = [lyr for lyr in style['layers']
                      if lyr.get('type') != 'background']
            self.assertEqual(len(non_bg), 2)
            self.assertEqual(non_bg[0]['source'], 'satellite')
            self.assertEqual(non_bg[1]['source'], 'satellite_2')


class TestMergeCancellation(unittest.TestCase):
    """Test that cancellation is handled correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cancelled_returns_none(self):
        """If feedback is cancelled, should return None."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png')},
                   tiles={"s/0/0/0/0.png": b"tile"})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={"s/0/0/0/0.png": b"tile"})

        feedback = MagicMock()
        feedback.isCanceled.return_value = True

        output = os.path.join(self.tmpdir, 'merged.smp')
        result = merge_smp_files([a_path, b_path], output,
                                 feedback=feedback)
        self.assertIsNone(result)


class TestMergeVersionFile(unittest.TestCase):
    """Test that VERSION file is written correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_version_file_present(self):
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={"src_a": _make_source(0, 'png')},
                   tiles={})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={"src_b": _make_source(0, 'png')},
                   tiles={})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            version = zf.read('VERSION').decode('utf-8')
            self.assertEqual(version, '1.0')


class TestMergeMixedFormats(unittest.TestCase):
    """Test merging SMPs with different tile formats."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_png_and_jpg_sources(self):
        """PNG and JPG sources should coexist in the merged archive."""
        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "png_src": _make_source(0, 'png', 0, 5,
                                               [0, 0, 10, 10]),
                   },
                   tiles={"s/0/0/0/0.png": b"png_data"})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "jpg_src": _make_source(0, 'jpg', 5, 10,
                                               [10, 10, 20, 20]),
                   },
                   tiles={"s/0/5/0/0.jpg": b"jpg_data"})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            png_uri = style['sources']['png_src']['tiles'][0]
            jpg_uri = style['sources']['jpg_src']['tiles'][0]
            self.assertTrue(png_uri.endswith('.png'))
            self.assertTrue(jpg_uri.endswith('.jpg'))


class TestSourceMerging(unittest.TestCase):
    """Test that sources with same bounds and adjacent zooms are merged."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_adjacent_zooms_merge_into_one_source(self):
        """z12-16 and z17-18 with same bounds should become one source."""
        shared_bounds = [-46.69, -3.93, -45.98, -3.54]

        a_path = os.path.join(self.tmpdir, 'z16.smp')
        _build_smp(a_path,
                   sources={
                       "local-detail": _make_source(
                           0, 'webp', 12, 16, shared_bounds),
                   },
                   tiles={
                       "s/0/12/0/0.webp": b"tile_z12",
                       "s/0/14/100/200.webp": b"tile_z14",
                   })

        b_path = os.path.join(self.tmpdir, 'z18.smp')
        _build_smp(b_path,
                   sources={
                       "mbtiles-source": _make_source(
                           0, 'webp', 17, 18, shared_bounds),
                   },
                   tiles={
                       "s/0/17/0/0.webp": b"tile_z17",
                       "s/0/18/0/0.webp": b"tile_z18",
                   })

        output = os.path.join(self.tmpdir, 'merged.smp')
        result = merge_smp_files([a_path, b_path], output)
        self.assertEqual(result, output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))

            # Should have ONE source, not two
            self.assertEqual(len(style['sources']), 1)

            # The merged source should keep the first source's ID
            self.assertIn('local-detail', style['sources'])
            src = style['sources']['local-detail']

            # Zoom range should be combined
            self.assertEqual(src['minzoom'], 12)
            self.assertEqual(src['maxzoom'], 18)

            # Bounds should be preserved
            self.assertEqual(src['bounds'], shared_bounds)

            # All 4 tiles should be under the same source folder
            src_idx, ext = _parse_smp_uri(src['tiles'][0])
            self.assertEqual(ext, 'webp')
            self.assertIn(
                's/{}/12/0/0.webp'.format(src_idx), zf.namelist())
            self.assertIn(
                's/{}/14/100/200.webp'.format(src_idx), zf.namelist())
            self.assertIn(
                's/{}/17/0/0.webp'.format(src_idx), zf.namelist())
            self.assertIn(
                's/{}/18/0/0.webp'.format(src_idx), zf.namelist())

            # Metadata
            self.assertEqual(style['metadata']['smp:maxzoom'], 18)
            self.assertEqual(
                style['metadata']['smp:bounds'], shared_bounds)

    def test_caru_like_scenario(self):
        """Full Caru-like scenario: z16 with 3 sources + z18 with 1 source.
        local-detail (z12-16) + mbtiles-source (z17-18) should merge."""
        world_bounds = [-180.0, -85.0511, 180.0, 85.0511]
        region_bounds = [-46.728, -3.967, -45.812, -2.392]
        local_bounds = [-46.690, -3.930, -45.984, -3.541]

        z16_path = os.path.join(self.tmpdir, 'z16.smp')
        z16_style = {
            "version": 8,
            "name": "z16",
            "sources": {
                "world-overview": _make_source(0, 'webp', 0, 5,
                                               world_bounds),
                "region-detail": _make_source(1, 'webp', 6, 11,
                                              region_bounds),
                "local-detail": _make_source(2, 'webp', 12, 16,
                                             local_bounds),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "world-raster", "type": "raster",
                 "source": "world-overview"},
                {"id": "region-raster", "type": "raster",
                 "source": "region-detail"},
                {"id": "local-raster", "type": "raster",
                 "source": "local-detail"},
            ],
            "metadata": {
                "smp:sourceFolders": {
                    "world-overview": "s/0",
                    "region-detail": "s/1",
                    "local-detail": "s/2",
                },
                "smp:bounds": local_bounds,
                "smp:maxzoom": 16,
            },
        }
        with zipfile.ZipFile(z16_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(z16_style))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/0/0/0.webp', b'world_tile')
            zf.writestr('s/1/6/0/0.webp', b'region_tile')
            zf.writestr('s/2/12/0/0.webp', b'local_z12')
            zf.writestr('s/2/16/0/0.webp', b'local_z16')

        z18_path = os.path.join(self.tmpdir, 'z18.smp')
        z18_style = {
            "version": 8,
            "name": "z18",
            "sources": {
                "mbtiles-source": _make_source(0, 'webp', 17, 18,
                                               local_bounds),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "raster", "type": "raster",
                 "source": "mbtiles-source"},
            ],
            "metadata": {
                "smp:sourceFolders": {
                    "mbtiles-source": "s/0",
                },
                "smp:bounds": local_bounds,
                "smp:maxzoom": 18,
            },
        }
        with zipfile.ZipFile(z18_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(z18_style))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/17/0/0.webp', b'z17_tile')
            zf.writestr('s/0/18/0/0.webp', b'z18_tile')

        output = os.path.join(self.tmpdir, 'merged.smp')
        result = merge_smp_files([z16_path, z18_path], output)
        self.assertEqual(result, output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))

            # Should have 3 sources: world-overview, region-detail,
            # local-detail (merged with mbtiles-source)
            self.assertEqual(len(style['sources']), 3)
            self.assertIn('world-overview', style['sources'])
            self.assertIn('region-detail', style['sources'])
            self.assertIn('local-detail', style['sources'])

            # local-detail should now cover z12-18
            local = style['sources']['local-detail']
            self.assertEqual(local['minzoom'], 12)
            self.assertEqual(local['maxzoom'], 18)
            self.assertEqual(local['bounds'], local_bounds)

            # world and region unchanged
            self.assertEqual(
                style['sources']['world-overview']['minzoom'], 0)
            self.assertEqual(
                style['sources']['world-overview']['maxzoom'], 5)
            self.assertEqual(
                style['sources']['region-detail']['minzoom'], 6)
            self.assertEqual(
                style['sources']['region-detail']['maxzoom'], 11)

            # All tiles from both inputs should be present
            local_idx, _ = _parse_smp_uri(local['tiles'][0])
            world_idx, _ = _parse_smp_uri(
                style['sources']['world-overview']['tiles'][0])
            region_idx, _ = _parse_smp_uri(
                style['sources']['region-detail']['tiles'][0])

            self.assertIn(
                's/{}/0/0/0.webp'.format(world_idx), zf.namelist())
            self.assertIn(
                's/{}/6/0/0.webp'.format(region_idx), zf.namelist())
            self.assertIn(
                's/{}/12/0/0.webp'.format(local_idx), zf.namelist())
            self.assertIn(
                's/{}/16/0/0.webp'.format(local_idx), zf.namelist())
            self.assertIn(
                's/{}/17/0/0.webp'.format(local_idx), zf.namelist())
            self.assertIn(
                's/{}/18/0/0.webp'.format(local_idx), zf.namelist())

            # Layers: 3 non-background layers (world, region, local)
            # The mbtiles-source layer is consolidated with local
            non_bg = [lyr for lyr in style['layers']
                      if lyr.get('type') != 'background']
            self.assertEqual(len(non_bg), 3)

            # Metadata
            self.assertEqual(style['metadata']['smp:maxzoom'], 18)

    def test_merged_source_tiles_under_single_folder(self):
        """When sources merge, all tiles go under one source folder."""
        bounds = [0, 0, 10, 10]

        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "low-zoom": _make_source(0, 'png', 0, 5, bounds),
                   },
                   tiles={
                       "s/0/0/0/0.png": b"low_tile",
                       "s/0/3/1/1.png": b"low_z3",
                   })

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "high-zoom": _make_source(0, 'png', 6, 10, bounds),
                   },
                   tiles={
                       "s/0/6/0/0.png": b"high_tile",
                       "s/0/10/0/0.png": b"high_z10",
                   })

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))

            # One merged source
            self.assertEqual(len(style['sources']), 1)
            src_id = list(style['sources'].keys())[0]
            src = style['sources'][src_id]
            self.assertEqual(src['minzoom'], 0)
            self.assertEqual(src['maxzoom'], 10)

            # All tiles under one folder
            src_idx, _ = _parse_smp_uri(src['tiles'][0])
            tile_names = [n for n in zf.namelist()
                          if n.startswith('s/')]
            for name in tile_names:
                self.assertTrue(
                    name.startswith('s/{}/'.format(src_idx)),
                    "Tile {} not under merged source folder s/{}".format(
                        name, src_idx))

    def test_merged_layer_consolidation(self):
        """When sources merge, their layers should consolidate to one."""
        bounds = [0, 0, 10, 10]

        a_path = os.path.join(self.tmpdir, 'a.smp')
        style_a = {
            "version": 8,
            "name": "A",
            "sources": {
                "satellite": _make_source(0, 'png', 0, 10, bounds),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "sat-layer", "type": "raster",
                 "source": "satellite"},
            ],
            "metadata": {
                "smp:sourceFolders": {"satellite": "s/0"},
            },
        }
        with zipfile.ZipFile(a_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style_a))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/0/0/0.png', b'tile_low')

        b_path = os.path.join(self.tmpdir, 'b.smp')
        style_b = {
            "version": 8,
            "name": "B",
            "sources": {
                "detail": _make_source(0, 'png', 11, 14, bounds),
            },
            "layers": [
                {"id": "background", "type": "background",
                 "paint": {"background-color": "white"}},
                {"id": "detail-layer", "type": "raster",
                 "source": "detail"},
            ],
            "metadata": {
                "smp:sourceFolders": {"detail": "s/0"},
            },
        }
        with zipfile.ZipFile(b_path, 'w') as zf:
            zf.writestr('style.json', json.dumps(style_b))
            zf.writestr('VERSION', '1.0')
            zf.writestr('s/0/11/0/0.png', b'tile_high')

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))

            # One merged source
            self.assertEqual(len(style['sources']), 1)

            # One non-background layer (consolidated)
            non_bg = [lyr for lyr in style['layers']
                      if lyr.get('type') != 'background']
            self.assertEqual(len(non_bg), 1)
            # The first layer encountered is kept
            self.assertEqual(non_bg[0]['id'], 'sat-layer')

    def test_identical_merged_tiles_remain_readable(self):
        """Identical tile content should remain readable at each tile path."""
        bounds = [0, 0, 10, 10]
        shared_data = b"x" * 1000  # 1KB of identical data

        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "low": _make_source(0, 'png', 0, 5, bounds),
                   },
                   tiles={
                       "s/0/0/0/0.png": shared_data,
                   })

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "high": _make_source(0, 'png', 6, 10, bounds),
                   },
                   tiles={
                       "s/0/6/0/0.png": shared_data,
                   })

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        # Both tiles should be readable
        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            src = list(style['sources'].values())[0]
            src_idx, _ = _parse_smp_uri(src['tiles'][0])
            tile_a = zf.read('s/{}/0/0/0.png'.format(src_idx))
            tile_b = zf.read('s/{}/6/0/0.png'.format(src_idx))
            self.assertEqual(tile_a, shared_data)
            self.assertEqual(tile_b, shared_data)

        # The archive should be valid and non-empty.
        output_size = os.path.getsize(output)
        self.assertGreater(output_size, 0)

    def test_no_merge_when_zooms_overlap(self):
        """Sources with overlapping zooms should NOT merge even if bounds
        match."""
        bounds = [0, 0, 10, 10]

        a_path = os.path.join(self.tmpdir, 'a.smp')
        _build_smp(a_path,
                   sources={
                       "src_a": _make_source(0, 'png', 0, 10, bounds),
                   },
                   tiles={"s/0/0/0/0.png": b"tile_a"})

        b_path = os.path.join(self.tmpdir, 'b.smp')
        _build_smp(b_path,
                   sources={
                       "src_b": _make_source(0, 'png', 5, 15, bounds),
                   },
                   tiles={"s/0/5/0/0.png": b"tile_b"})

        output = os.path.join(self.tmpdir, 'merged.smp')
        merge_smp_files([a_path, b_path], output)

        with zipfile.ZipFile(output, 'r') as zf:
            style = json.loads(zf.read('style.json'))
            # Should NOT merge — zooms overlap (5-10)
            self.assertEqual(len(style['sources']), 2)
            self.assertIn('src_a', style['sources'])
            self.assertIn('src_b', style['sources'])


if __name__ == '__main__':
    unittest.main()
