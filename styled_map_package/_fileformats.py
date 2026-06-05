"""Tile format detection from magic bytes (port of ``lib/utils/file-formats.js``)."""

# ``None`` entries are wildcard bytes that are not checked.
_MAGIC_BYTES = {
    "png": [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A],
    "jpg": [0xFF, 0xD8, 0xFF],
    "webp": [0x52, 0x49, 0x46, 0x46, None, None, None, None, 0x57, 0x45, 0x42, 0x50],
    # Include the compression-type byte, always 0x08 (DEFLATE) for gzip.
    "gz": [0x1F, 0x8B, 0x08],
}

_FIRST_BYTE = {sig[0]: ext for ext, sig in _MAGIC_BYTES.items()}


def detect_tile_format(buf):
    """Determine the tile format of ``buf`` (bytes) from its magic bytes.

    Returns one of ``"png"``, ``"jpg"``, ``"webp"`` or ``"mvt"`` (gzip data is
    assumed to be a vector tile). Raises :class:`ValueError` for unknown data.
    """
    if not buf:
        raise ValueError("Unknown file type")
    ext = _FIRST_BYTE.get(buf[0])
    if ext is None:
        raise ValueError("Unknown file type")
    sig = _MAGIC_BYTES[ext]
    for i in range(1, len(sig)):
        if sig[i] is not None:
            if i >= len(buf) or sig[i] != buf[i]:
                raise ValueError("Unknown file type")
    if ext == "gz":
        # Gzipped tiles are always MVT.
        return "mvt"
    return ext
