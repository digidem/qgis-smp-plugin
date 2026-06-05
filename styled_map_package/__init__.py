"""Pure-Python reader and writer for the Styled Map Package (``.smp``) format.

A Styled Map Package is a ZIP archive containing everything needed to render a
styled MapLibre map offline: a ``style.json`` document, vector and/or raster
tiles, glyphs (fonts) and sprites.

Public API:

- :class:`Reader` — open a ``.smp`` archive and read its style and resources.
- :class:`Writer` — assemble a ``.smp`` archive from a style and resources.
- :class:`Resource` — a resource returned by :meth:`Reader.get_resource`.
- :func:`tms_to_xyz_y`, :func:`tile_to_bbox` — tile-coordinate helpers.
- Exception types in :mod:`styled_map_package.errors`.

This implementation deliberately performs **no** MapLibre style migration or
validation (see the README); the caller is responsible for supplying a valid
MapLibre v8 style to the :class:`Writer`.
"""

from . import errors
from ._geo import MAX_BOUNDS, tile_to_bbox, tms_to_xyz_y
from ._templates import (
    FORMAT_VERSION,
    GLYPH_URI,
    URI_BASE,
    URI_SCHEME,
    get_content_type,
    get_resource_type,
)
from .errors import (
    DuplicateEntryError,
    MissingFontsError,
    MissingSourcesError,
    MissingSpriteError,
    ResourceNotFoundError,
    ResourceTooLargeError,
    SMPError,
    SourceNotFoundError,
    TileFormatMismatchError,
    TooManyEntriesError,
    UnsafeEntryError,
    UnsupportedSourceTypeError,
    UnsupportedVersionError,
)
from .reader import Reader, Resource
from .writer import ArchiveEntry, Writer, write_smp_archive

__version__ = "1.0.0"

__all__ = [
    "Reader",
    "Resource",
    "Writer",
    "ArchiveEntry",
    "write_smp_archive",
    "errors",
    "SMPError",
    "ResourceNotFoundError",
    "ResourceTooLargeError",
    "TooManyEntriesError",
    "UnsafeEntryError",
    "UnsupportedVersionError",
    "SourceNotFoundError",
    "UnsupportedSourceTypeError",
    "TileFormatMismatchError",
    "DuplicateEntryError",
    "MissingSourcesError",
    "MissingFontsError",
    "MissingSpriteError",
    "tms_to_xyz_y",
    "tile_to_bbox",
    "MAX_BOUNDS",
    "URI_BASE",
    "URI_SCHEME",
    "GLYPH_URI",
    "FORMAT_VERSION",
    "get_content_type",
    "get_resource_type",
]
