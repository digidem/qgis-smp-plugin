.. CoMapeo Map Builder documentation master file, created by
   sphinx-quickstart on Sun Feb 12 17:11:03 2012.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to CoMapeo Map Builder's documentation!
================================================

CoMapeo Map Builder is a QGIS plugin that generates Styled Map Package (SMP)
files for use with CoMapeo.  An SMP file is a ZIP archive containing a
``style.json`` (MapLibre GL style descriptor) and raster XYZ tiles rendered
from your QGIS project.

Quick start
-----------

1. Open QGIS and load the layers you want to include.
2. Style your layers as desired.
3. Open **Processing > Toolbox** and search for "CoMapeo Map Builder".
4. Select **Generate SMP Map**.
5. Draw your local extent and set zoom levels.
6. Click **Run**.

Parameters
----------

**Extent**
  The geographic area for the Local detail source.

**Minimum / Maximum zoom level**
  The zoom range for the Local detail source (0–24).

**Tile image format**
  PNG, JPG, or WebP.

**JPEG/WebP quality**
  Compression quality (1–100) when the format is JPG or WebP.

**Include World overview source**
  When enabled, renders the entire globe at very low zoom levels (0–3 by
  default) on source slot ``s/0``.  This gives users a recognizable world map
  when they zoom out.

**World maximum zoom**
  The highest zoom level for the World overview.  Default is 3.

**Include Region detail source**
  When enabled, adds an optional middle-detail source on slot ``s/1`` that
  covers a larger area than your project at medium zoom levels (4–7 by
  default).

**Region extent**
  The geographic area for the Region detail source.  Must fully contain the
  Local extent.

**Region minimum / maximum zoom level**
  The zoom range for the Region detail source.

**Output SMP file**
  Where to save the generated SMP file.

World / Region / Local sources
------------------------------

The plugin can produce up to three separate tile sources inside a single SMP
file.  Each source covers a different zoom range and geographic area, so
CoMapeo can show the right level of detail as the user zooms in and out::

   Zoom 0 ────────────────────────────────────────────── Zoom 24
    ┌──────────┐┌───────────────┐┌──────────────────────┐
    │   World   ││    Region     ││       Local          │
    │ Overview  ││    Detail     ││      Detail          │
    │  (s/0)    ││    (s/1)      ││      (s/2)           │
    └──────────┘└───────────────┘└──────────────────────┘
     zoom 0–3     zoom 4–7        zoom 8–14
     (whole       (country/       (your project
      world)       province)       area)

**World Overview** (``s/0``) renders the entire globe at very low zoom levels
(0–3 by default).  This gives users a recognizable world map when they zoom
out, so they can orient themselves before diving into higher-detail areas.
Enable it with the **Include World overview source** checkbox.

**Region Detail** (``s/1``) is an optional middle layer.  It covers a larger
area than your project (for example, a province or watershed) at medium zoom
levels (4–7 by default).  This provides a smooth visual transition between the
world overview and your detailed local area.  Enable it with the **Include
Region detail source** checkbox, then draw a Region extent that fully contains
your Local extent and set the Region zoom range.

**Local Detail** (``s/2``) is your main project area — the extent you draw in
the dialog — rendered at the highest zoom levels (8–14 by default).  This is
always generated.  When World and Region are both disabled, Local uses the
legacy single-source slot (``s/0``) for backward compatibility.

The zoom ranges must be strictly ordered so they don't overlap::

   World max zoom < Region min zoom <= Region max zoom < Local min zoom

If your ranges would overlap, the plugin will either automatically adjust the
Local minimum zoom or show a clear error explaining what to change.

Typical setup for a field project:

==========  =======================  ===========  =========================================
Source       Extent                   Zoom range   Purpose
==========  =======================  ===========  =========================================
World       Entire globe             0 – 3        Show continents and oceans when zoomed out
Region      Province or territory    4 – 7        Show roads and rivers at medium zoom
Local       Your project site        8 – 14       Show detailed field data
==========  =======================  ===========  =========================================

Simplest setup (World + Local only)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Leave **Include Region detail source** unchecked.  Set **World maximum zoom**
to 3 and **Minimum zoom level** (Local) to 4 or higher.  The plugin fills the
gap automatically.

Migration from earlier versions
-------------------------------

This release introduces a fixed source configuration with three slots —
World, Region, and Local — and enforces strict, non-overlapping zoom ranges.
Configurations that were valid in earlier versions may now fail validation.

The required ordering is::

   world_max_zoom < region_min_zoom <= region_max_zoom < local_min_zoom

World is **enabled by default** for backward compatibility, but its zoom range
must not overlap Local (or Region, when Region is enabled).  Region remains
optional.

**Example of a config that now fails:** Local **Minimum zoom level** ``0``
with World enabled at **World maximum zoom** ``3``.  Either re-save with
Local **Minimum zoom level** ``4`` or higher (so Local starts above World's
top zoom), or disable **Include World overview source** if you want Local to
start at zoom ``0``.  The same rule applies to any Region range that overlaps
World or Local — adjust **Region minimum/maximum zoom level** so the three
ranges remain strictly ordered.

Tips for better results
------------------------

- Keep the maximum zoom level reasonable (12–16) to avoid generating too many tiles.
- Use a smaller extent for higher zoom levels to reduce processing time and file size.
- Make sure all layers are properly styled before generating the SMP file.
- Test your SMP file with CoMapeo to ensure it displays correctly.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
