#!/usr/bin/env bash
# Vendor the styled-map-package Python library from a pinned GitHub release.
#
#   scripts/vendor-smp.sh            # re-vendor the version in .smp-version
#   scripts/vendor-smp.sh v1.2.0     # update to v1.2.0 and re-pin .smp-version
#
# The library has no third-party dependencies, so vendoring is just extracting
# the package directory from GitHub's auto-generated source tarball for the tag
# (which expands to <repo>-<version>/src/styled_map_package/).
set -euo pipefail

REPO="https://github.com/digidem/styled-map-package-python"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="${ROOT}/.smp-version"
DEST="${ROOT}/styled_map_package"

VERSION="${1:-}"
if [ -z "${VERSION}" ]; then
    if [ ! -f "${VERSION_FILE}" ]; then
        echo "No version given and ${VERSION_FILE} is missing." >&2
        exit 1
    fi
    VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
fi

TARBALL="source-${VERSION}.tar.gz"
URL="${REPO}/archive/refs/tags/${VERSION}.tar.gz"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Fetching ${URL}"
if ! curl -fsSL "${URL}" -o "${TMP}/${TARBALL}"; then
    echo "Failed to download ${URL}" >&2
    echo "Has tag ${VERSION} been pushed to ${REPO}?" >&2
    exit 1
fi

tar -xzf "${TMP}/${TARBALL}" -C "${TMP}"
PKG_DIR="$(find "${TMP}" -type d -name styled_map_package -print -quit)"
if [ -z "${PKG_DIR}" ] || [ ! -f "${PKG_DIR}/__init__.py" ]; then
    echo "Source tarball did not contain a styled_map_package/ package." >&2
    exit 1
fi

rm -rf "${DEST}"
mv "${PKG_DIR}" "${DEST}"
rm -rf "${DEST}/__pycache__"
echo "${VERSION}" > "${VERSION_FILE}"
echo "Vendored styled_map_package ${VERSION} -> ${DEST}"
