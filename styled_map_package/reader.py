"""Reader for Styled Map Package (``.smp``) archives."""

import io
import json
import os
import re
import struct
import unicodedata
import warnings
import zipfile
import zlib
from dataclasses import dataclass
from typing import Optional

from .errors import (
    ResourceNotFoundError,
    ResourceTooLargeError,
    TooManyEntriesError,
    UnsafeEntryError,
    UnsupportedVersionError,
)
from ._templates import (
    STYLE_FILE,
    URI_BASE,
    VERSION_FILE,
    get_content_type,
    get_resource_type,
)

DEFAULT_MAX_ENTRIES = 500_000
DEFAULT_MAX_RESOURCE_SIZE = 20 * 1024 * 1024  # 20 MiB

# Major version(s) supported by this implementation.
SUPPORTED_MAJOR_VERSIONS = (1,)

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass
class Resource:
    """A single resource read from the archive.

    ``data`` holds the resource bytes exactly as stored. For ``.gz`` resources
    these bytes are still gzip-compressed and ``content_encoding`` is ``"gzip"``
    (matching how the data should be served over HTTP).
    """

    resource_type: str  # "style" | "tile" | "sprite" | "glyph"
    content_type: str
    content_length: int
    data: bytes
    content_encoding: Optional[str] = None


def _normalize(name):
    return unicodedata.normalize("NFC", name)


def _is_unsafe(name):
    if name.startswith("/") or name.startswith("\\"):
        return True
    if _DRIVE_LETTER.match(name):
        return True
    # Split on both separators so backslash traversal (..\..) is also rejected.
    return any(segment == ".." for segment in re.split(r"[/\\]", name))


def _to_url(smp_uri, base_url):
    if base_url is None:
        return smp_uri
    if not smp_uri.startswith(URI_BASE):
        # Be lenient: leave non-SMP URLs untouched rather than rejecting.
        return smp_uri
    base = base_url if base_url.endswith("/") else base_url + "/"
    return base + smp_uri[len(URI_BASE):]


class Reader:
    """Read resources and the style document from a ``.smp`` archive.

    :param file: Path to a ``.smp`` file, raw ``bytes``, or a binary file-like
        object opened for reading (and seeking).
    :param max_entries: Maximum number of ZIP entries to accept before raising
        :class:`TooManyEntriesError` (defaults to 500,000, ~a global z9 tileset).
    :param max_resource_size: Maximum uncompressed size, in bytes, of a single
        resource returned by :meth:`get_resource` (defaults to 20 MiB).

    The reader keeps the underlying file open until :meth:`close` is called. It
    can also be used as a context manager::

        with Reader("map.smp") as reader:
            style = reader.get_style()
    """

    def __init__(
        self,
        file,
        *,
        max_entries=DEFAULT_MAX_ENTRIES,
        max_resource_size=DEFAULT_MAX_RESOURCE_SIZE,
    ):
        self._max_resource_size = max_resource_size
        self._owns_fp = False

        if isinstance(file, (bytes, bytearray)):
            self._zip = zipfile.ZipFile(io.BytesIO(bytes(file)))
            self._owns_fp = True
        elif isinstance(file, (str, os.PathLike)):
            self._zip = zipfile.ZipFile(os.fspath(file))
            self._owns_fp = True
        else:
            # Binary file-like object; caller retains ownership.
            self._zip = zipfile.ZipFile(file)

        infos = self._zip.infolist()
        if len(infos) > max_entries:
            self._zip.close()
            raise TooManyEntriesError(
                "Archive exceeds maximum entry count of {0}".format(max_entries)
            )

        self._by_name = {}
        for info in infos:
            name = info.filename
            if _is_unsafe(name):
                self._zip.close()
                raise UnsafeEntryError("Unsafe ZIP entry name: {0}".format(name))
            # Trailing slashes mark directories; index files only.
            if name.endswith("/"):
                continue
            self._by_name[_normalize(name)] = info

    # -- public API -------------------------------------------------------

    def get_version(self):
        """Return the format version string, or ``"1.0"`` if no VERSION file.

        Raises :class:`UnsupportedVersionError` if the major version is not
        supported by this reader (spec §3.1).
        """
        info = self._by_name.get(VERSION_FILE)
        if info is None:
            return "1.0"
        version = self._zip.read(info).decode("utf-8").strip()
        match = re.match(r"^(\d+)\.\d+", version)
        if match:
            major = int(match.group(1))
            if major not in SUPPORTED_MAJOR_VERSIONS:
                raise UnsupportedVersionError(
                    "Unsupported major version: {0} (supported: {1})".format(
                        major, ", ".join(str(v) for v in SUPPORTED_MAJOR_VERSIONS)
                    )
                )
        return version

    def get_style(self, base_url=None):
        """Return the parsed ``style.json`` with SMP URIs resolved.

        :param base_url: If given, every ``smp://maps.v1/...`` URI in the style
            (glyphs, sprite, source ``tiles`` and file-backed GeoJSON ``data``)
            is rewritten to ``{base_url}/...``. If ``None`` the raw SMP URIs are
            returned unchanged.

        Raises :class:`ResourceNotFoundError` if ``style.json`` is missing
        (a fatal error per spec §9.1).
        """
        info = self._by_name.get(STYLE_FILE)
        if info is None:
            raise ResourceNotFoundError(STYLE_FILE)
        style = json.loads(self._zip.read(info))

        if isinstance(style.get("glyphs"), str):
            style["glyphs"] = _to_url(style["glyphs"], base_url)

        sprite = style.get("sprite")
        if isinstance(sprite, str):
            style["sprite"] = _to_url(sprite, base_url)
        elif isinstance(sprite, list):
            style["sprite"] = [
                {"id": item.get("id"), "url": _to_url(item.get("url"), base_url)}
                for item in sprite
            ]

        for source in style.get("sources", {}).values():
            if not isinstance(source, dict):
                continue
            tiles = source.get("tiles")
            if isinstance(tiles, list):
                source["tiles"] = [_to_url(t, base_url) for t in tiles]
            data = source.get("data")
            if isinstance(data, str) and data.startswith(URI_BASE):
                source["data"] = _to_url(data, base_url)

        return style

    def get_resource(self, path):
        """Return the :class:`Resource` at ``path`` (relative to the archive root).

        ``path`` may start with ``/`` (it is stripped). Requesting
        ``style.json`` returns the URI-resolved style as serialized JSON.

        Raises :class:`ResourceNotFoundError` if no such entry exists, or
        :class:`ResourceTooLargeError` if it exceeds ``max_resource_size``.
        """
        if path and path[0] == "/":
            path = path[1:]

        if path == STYLE_FILE:
            data = json.dumps(
                self.get_style(), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            return Resource(
                resource_type="style",
                content_type="application/json; charset=utf-8",
                content_length=len(data),
                data=data,
            )

        info = self._by_name.get(_normalize(path))
        if info is None:
            raise ResourceNotFoundError(path)
        if info.file_size > self._max_resource_size:
            raise ResourceTooLargeError(
                "Resource {0} exceeds maximum size of {1} bytes ({2} bytes)".format(
                    path, self._max_resource_size, info.file_size
                )
            )
        data = self._read_info(info)
        return Resource(
            resource_type=get_resource_type(path),
            content_type=get_content_type(path),
            content_length=len(data),
            data=data,
            content_encoding="gzip" if path.endswith(".gz") else None,
        )

    def read(self, path):
        """Return the raw bytes of the entry at ``path`` (no URI resolution).

        Raises :class:`ResourceNotFoundError` if the entry does not exist.
        """
        if path and path[0] == "/":
            path = path[1:]
        info = self._by_name.get(_normalize(path))
        if info is None:
            raise ResourceNotFoundError(path)
        return self._read_info(info)

    def has(self, path):
        """Return ``True`` if an entry exists at ``path``."""
        if path and path[0] == "/":
            path = path[1:]
        return _normalize(path) in self._by_name

    def namelist(self):
        """Return the list of entry names (NFC-normalized)."""
        return list(self._by_name)

    def _read_info(self, info):
        """Read an entry's bytes, transparently handling deduplicated aliases."""
        try:
            with warnings.catch_warnings():
                # Aliased (deduplicated) entries deliberately overlap; ignore the
                # zip-bomb heuristic warning that this triggers.
                warnings.filterwarnings(
                    "ignore", message="Overlapped entries", category=UserWarning
                )
                return self._zip.read(info)
        except zipfile.BadZipFile:
            # A deduplicated (aliased) entry's local file header name differs
            # from its central-directory name, which stdlib rejects. The central
            # directory is authoritative (spec §3.6), so read the data directly.
            # _read_aliased verifies the CRC, so a genuinely corrupt entry still
            # raises rather than returning bad data.
            return self._read_aliased(info)

    def _read_aliased(self, info):
        fp = self._zip.fp
        fp.seek(info.header_offset)
        header = fp.read(30)
        if len(header) < 30 or header[:4] != b"PK\x03\x04":
            raise zipfile.BadZipFile(
                "Bad local file header for {0}".format(info.filename)
            )
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        fp.seek(info.header_offset + 30 + name_len + extra_len)
        raw = fp.read(info.compress_size)
        if info.compress_type == zipfile.ZIP_STORED:
            data = raw
        elif info.compress_type == zipfile.ZIP_DEFLATED:
            decompressor = zlib.decompressobj(-15)
            data = decompressor.decompress(raw) + decompressor.flush()
        else:
            raise zipfile.BadZipFile(
                "Unsupported compression for {0}".format(info.filename)
            )
        if zipfile.crc32(data) != (info.CRC & 0xFFFFFFFF):
            raise zipfile.BadZipFile("Bad CRC-32 for {0}".format(info.filename))
        return data

    def close(self):
        """Close the underlying ZIP file (and the file, if the reader owns it)."""
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
