"""Exception types raised by the styled-map-package reader and writer."""


class SMPError(Exception):
    """Base class for all styled-map-package errors."""


# --- Reader errors -------------------------------------------------------


class ResourceNotFoundError(SMPError, FileNotFoundError):
    """A requested resource does not exist in the archive.

    Subclasses :class:`FileNotFoundError` so existing ``except OSError`` /
    ``except FileNotFoundError`` handlers keep working (mirrors the ``ENOENT``
    behaviour of the JavaScript reference implementation).
    """

    def __init__(self, path):
        self.path = path
        super().__init__(f"Resource not found: {path}")


class UnsafeEntryError(SMPError):
    """A ZIP entry name is unsafe (path traversal or absolute path).

    Per spec §3.4 and §11.1 readers MUST reject archives that contain entries
    with ``..`` path segments or absolute paths.
    """


class TooManyEntriesError(SMPError):
    """The archive contains more entries than ``max_entries`` allows (§11.2)."""


class ResourceTooLargeError(SMPError):
    """A resource's uncompressed size exceeds ``max_resource_size`` (§11.2)."""


class UnsupportedVersionError(SMPError):
    """The archive declares a major version this reader does not support (§3.1)."""


# --- Writer errors -------------------------------------------------------


class SourceNotFoundError(SMPError):
    """A tile was added for a source that is not declared in ``style.json``."""


class UnsupportedSourceTypeError(SMPError):
    """A tile was added for a source whose type is not ``vector`` or ``raster``."""


class TileFormatMismatchError(SMPError):
    """Tiles of differing formats were added to a single source (§5.3)."""


class DuplicateEntryError(SMPError):
    """An entry with the same path was added to the archive twice."""


class MissingSourcesError(SMPError):
    """No sources were added before finishing the archive."""


class MissingFontsError(SMPError):
    """The style references glyphs but no glyph files were added."""


class MissingSpriteError(SMPError):
    """The style references a sprite that was not added."""
