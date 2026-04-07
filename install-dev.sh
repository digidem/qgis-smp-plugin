#!/usr/bin/env bash
# Install the current working tree into the local QGIS plugins directory.
# Useful for quick iteration without committing or zipping.
#
# Usage:
#   ./install-dev.sh           # copy files
#   ./install-dev.sh --watch   # copy and watch for changes (requires inotifywait)
#
# After installing, reload in QGIS via:
#   • Plugin Reloader plugin: Plugins → Plugin Reloader → Reload comapeo_smp
#   • Or restart QGIS

set -e

PLUGIN_NAME="comapeo_smp"
QGIS_PLUGIN_DIR="${HOME}/.local/share/QGIS/QGIS3/profiles/default/python/plugins"
DEST="${QGIS_PLUGIN_DIR}/${PLUGIN_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

FILES=(
    __init__.py
    comapeo_smp.py
    comapeo_smp_provider.py
    comapeo_smp_algorithm.py
    comapeo_smp_generator.py
    metadata.txt
)

echo "Installing ${PLUGIN_NAME} → ${DEST}"
mkdir -p "${DEST}"

for f in "${FILES[@]}"; do
    src="${SCRIPT_DIR}/${f}"
    if [ -f "${src}" ]; then
        cp -v "${src}" "${DEST}/"
    else
        echo "  WARNING: ${f} not found, skipping"
    fi
done

echo ""
echo "Done. To activate changes in QGIS:"
echo "  • Plugin Reloader: Plugins → Plugin Reloader → Reload ${PLUGIN_NAME}"
echo "  • Or restart QGIS"

# Optional watch mode
if [ "${1}" = "--watch" ]; then
    if ! command -v inotifywait &>/dev/null; then
        echo "ERROR: --watch requires inotifywait (install: sudo apt install inotify-tools)"
        exit 1
    fi
    echo ""
    echo "Watching for changes... (Ctrl+C to stop)"
    while inotifywait -q -e modify,close_write "${SCRIPT_DIR}"/*.py "${SCRIPT_DIR}/metadata.txt" 2>/dev/null; do
        echo "Change detected, reinstalling..."
        for f in "${FILES[@]}"; do
            src="${SCRIPT_DIR}/${f}"
            [ -f "${src}" ] && cp -v "${src}" "${DEST}/"
        done
        echo "Done. Reload in QGIS."
    done
fi
