#!/usr/bin/env bash
# Install the current working tree into the local QGIS plugins directory.
# Useful for quick iteration without committing or zipping.
#
# Usage:
#   ./install-dev.sh   # copy files and print reload instructions

set -e

PLUGIN_NAME="comapeo_smp"
DEST="$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins/$PLUGIN_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing $PLUGIN_NAME → $DEST"
mkdir -p "$DEST"

cp -v "$SCRIPT_DIR/__init__.py" \
      "$SCRIPT_DIR/comapeo_smp.py" \
      "$SCRIPT_DIR/comapeo_smp_algorithm.py" \
      "$SCRIPT_DIR/comapeo_smp_generator.py" \
      "$SCRIPT_DIR/comapeo_smp_provider.py" \
      "$SCRIPT_DIR/metadata.txt" \
      "$DEST/"

echo ""
echo "Done. To activate changes in QGIS:"
echo "  • If Plugin Reloader is installed: Plugins → Plugin Reloader → Reload $PLUGIN_NAME"
echo "  • Otherwise: restart QGIS"
