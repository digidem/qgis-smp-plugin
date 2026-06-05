"""SMP URI scheme, file-path templates and content-type helpers.

Port of ``lib/utils/templates.js``. These constants and functions define the
on-disk structure of the archive and the ``smp://maps.v1/`` URI scheme, so they
must match the JavaScript reference implementation exactly for cross-language
round-trips to work.
"""

URI_SCHEME = "smp"  # "Styled Map Package"
URI_BASE = URI_SCHEME + "://maps.v1/"

# These constants determine the file format structure.
VERSION_FILE = "VERSION"
FORMAT_VERSION = "1.0"
STYLE_FILE = "style.json"
SOURCES_FOLDER = "s"
SPRITES_FOLDER = "sprites"
FONTS_FOLDER = "fonts"

# Tile paths use just ``s`` to minimise the bytes used for filenames, which are
# stored in the ZIP central directory for every tile.
_TILE_FILE = SOURCES_FOLDER + "/{sourceId}/{z}/{x}/{y}{ext}"
# Pixel-ratio and ext placeholders must be at the end with nothing between them,
# matching the MapLibre sprite spec.
_SPRITE_FILE = SPRITES_FOLDER + "/{id}/sprite{pixelRatio}{ext}"
_GLYPH_FILE = FONTS_FOLDER + "/{fontstack}/{range}.pbf.gz"
GLYPH_URI = URI_BASE + _GLYPH_FILE


def encode_source_id(source_index):
    """Base-36 encode a source index to keep ZIP entry names short."""
    if source_index == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    n = source_index
    while n > 0:
        out = digits[n % 36] + out
        n //= 36
    return out


def _tile_ext(format):
    return "." + format + (".gz" if format == "mvt" else "")


def get_tile_filename(source_id, z, x, y, format):
    return "{folder}/{src}/{z}/{x}/{y}{ext}".format(
        folder=SOURCES_FOLDER, src=source_id, z=z, x=x, y=y, ext=_tile_ext(format)
    )


def _pixel_ratio_string(pixel_ratio):
    return "" if pixel_ratio == 1 else "@{0}x".format(pixel_ratio)


def get_sprite_filename(id, pixel_ratio, ext):
    return "{folder}/{id}/sprite{pr}{ext}".format(
        folder=SPRITES_FOLDER, id=id, pr=_pixel_ratio_string(pixel_ratio), ext=ext
    )


def get_glyph_filename(fontstack, range):
    return "{folder}/{fontstack}/{range}.pbf.gz".format(
        folder=FONTS_FOLDER, fontstack=fontstack, range=range
    )


def get_sprite_uri(id="default"):
    return URI_BASE + "{folder}/{id}/sprite".format(folder=SPRITES_FOLDER, id=id)


def get_tile_uri(source_id, format):
    return URI_BASE + "{folder}/{src}/{{z}}/{{x}}/{{y}}{ext}".format(
        folder=SOURCES_FOLDER, src=source_id, ext=_tile_ext(format)
    )


_RESOURCE_TYPE_PREFIXES = (
    (SOURCES_FOLDER + "/", "tile"),
    (SPRITES_FOLDER + "/", "sprite"),
    (FONTS_FOLDER + "/", "glyph"),
)


def get_resource_type(path):
    """Return ``"style"``, ``"tile"``, ``"sprite"`` or ``"glyph"`` for a path."""
    if path == STYLE_FILE:
        return "style"
    for prefix, resource_type in _RESOURCE_TYPE_PREFIXES:
        if path.startswith(prefix):
            return resource_type
    raise ValueError("Unknown resource type for path: {0}".format(path))


def get_content_type(path):
    """Return the HTTP ``Content-Type`` for a path based on its extension."""
    if path.endswith(".json") or path.endswith(".geojson"):
        return "application/json; charset=utf-8"
    if path.endswith(".pbf.gz") or path.endswith(".pbf"):
        return "application/x-protobuf"
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".jpg"):
        return "image/jpeg"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".mvt.gz") or path.endswith(".mvt"):
        return "application/vnd.mapbox-vector-tile"
    raise ValueError("Unknown content type for path: {0}".format(path))
