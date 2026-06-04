#!/usr/bin/env bash
# Fail if the committed styled_map_package/ differs from its pinned release.
# Run in CI so the vendored copy cannot silently drift from the published
# styled-map-package-python release recorded in .smp-version.
set -euo pipefail

REPO="https://github.com/digidem/styled-map-package-python"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="${ROOT}/.smp-version"
DEST="${ROOT}/styled_map_package"

[ -f "${VERSION_FILE}" ] || { echo "Missing ${VERSION_FILE}" >&2; exit 1; }
VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"

TARBALL="source-${VERSION}.tar.gz"
URL="${REPO}/archive/refs/tags/${VERSION}.tar.gz"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

if ! curl -fsSL "${URL}" -o "${TMP}/${TARBALL}"; then
    echo "Failed to download pinned tag ${VERSION} from ${URL}" >&2
    echo "Has it been pushed to ${REPO}?" >&2
    exit 1
fi

tar -xzf "${TMP}/${TARBALL}" -C "${TMP}"
# GitHub's source tarball expands to <repo>-<version>/src/styled_map_package/.
PKG_DIR="$(find "${TMP}" -type d -name styled_map_package -print -quit)"
if [ -z "${PKG_DIR}" ] || [ ! -f "${PKG_DIR}/__init__.py" ]; then
    echo "Source tarball for ${VERSION} did not contain a styled_map_package/ package." >&2
    exit 1
fi
rm -rf "${PKG_DIR}/__pycache__"

if diff -r --exclude=__pycache__ "${PKG_DIR}" "${DEST}" >/dev/null; then
    echo "Vendored styled_map_package/ matches pinned release ${VERSION}."
else
    echo "ERROR: vendored styled_map_package/ has drifted from release ${VERSION}." >&2
    echo "Run 'make vendor' to re-sync, or bump .smp-version to a newer release." >&2
    diff -r --exclude=__pycache__ "${PKG_DIR}" "${DEST}" >&2 || true
    exit 1
fi
