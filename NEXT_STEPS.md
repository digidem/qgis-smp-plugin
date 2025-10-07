# Next Steps - QGIS SMP Plugin v0.2.0

## Immediate Actions

### 1. Review the Changes
Review all commits and documentation:
```bash
git log --oneline -3
git show b8cf612  # Main implementation commit
git show 23f5afc  # Version bump commit
```

Key files to review:
- `comapeo_smp_generator.py` - Core implementation changes
- `metadata.txt` - Version and changelog
- `CLAUDE.md` - Architecture documentation
- `XYZ_SMP.md` - Comprehensive SMP format guide
- `IMPLEMENTATION_SUMMARY.md` - Complete change summary

### 2. Test in QGIS

**Basic Test**:
1. Install/reload the plugin in QGIS
2. Create a simple project with 1-2 vector or raster layers
3. Run "Generate SMP Map" from Processing Toolbox
4. Set extent, zoom 0-2, output path
5. Verify:
   - Tiles are generated
   - No errors in log
   - Temp directory is cleaned up

**Verification**:
```bash
# Extract and check structure
unzip -l output.smp
# Should show: style.json, s/0/0/0/0.png, s/0/1/*, etc.

# Validate style.json
unzip -p output.smp style.json | python3 -m json.tool
```

**Expected Results**:
- Zoom 0: 1 tile (if extent is small) or subset of 1x1
- Zoom 1: 1-4 tiles depending on extent
- Zoom 2: 1-16 tiles depending on extent

### 3. Test with CoMapeo

1. Transfer generated .smp file to device
2. Open in CoMapeo application
3. Verify:
   - Map loads without errors
   - Tiles display correctly
   - Tiles align properly (no gaps/overlaps)
   - Zoom in/out works smoothly

### 4. Push to GitHub

```bash
git push origin main
```

This will:
- Push the 3 commits to GitHub
- Trigger GitHub Actions release workflow (if configured)
- Create v0.2.0 release with plugin ZIP

## Testing Checklist

### Functional Tests
- [ ] Plugin loads in QGIS without errors
- [ ] Processing algorithm appears in toolbox
- [ ] Can select extent interactively
- [ ] Can set zoom range (0-24)
- [ ] Can specify output path
- [ ] Progress bar shows correctly
- [ ] SMP file is created
- [ ] SMP file structure is correct
- [ ] style.json is valid JSON

### Tile Validation Tests
- [ ] Zoom 0 produces 1 tile (or correct subset)
- [ ] Zoom 1 produces correct number of tiles (1-4)
- [ ] Tiles are 256x256 pixels
- [ ] Tiles are PNG format
- [ ] Tiles cover the specified extent
- [ ] No extra tiles generated outside extent

### Error Handling Tests
- [ ] Invalid extent shows error
- [ ] Invalid zoom range shows error
- [ ] Unwritable output path shows error
- [ ] Temp directory cleaned up on success
- [ ] Temp directory cleaned up on error
- [ ] Error messages are clear and helpful

### CoMapeo Integration Tests
- [ ] SMP loads in CoMapeo
- [ ] Tiles display at correct locations
- [ ] Tiles align with other map sources
- [ ] No gaps between tiles
- [ ] No overlaps between tiles
- [ ] Zoom levels work correctly

## Known Limitations

1. **Tile Format**: Currently hardcoded to PNG
   - Issue #6 tracks making this configurable
   - PNG works but creates larger files than JPG
   - For now, users must accept PNG output

2. **No Format Validation**: Plugin doesn't validate that extent is reasonable
   - Very large extents at high zoom can generate millions of tiles
   - Users should be cautious with zoom >14

3. **No Disk Space Check**: Plugin doesn't check available disk space
   - Large tile generations could fill disk
   - Consider adding a warning for large operations

## Future Enhancements

### Short Term (v0.3.0)
- [ ] Add tile format parameter (PNG/JPG) - Issue #6
- [ ] Add JPEG quality setting
- [ ] Add tile count estimate before generation
- [ ] Add disk space validation
- [ ] Add extent size validation/warning

### Medium Term
- [ ] Add support for vector tiles (MVT)
- [ ] Add support for multiple sources in one SMP
- [ ] Add preview of tile grid before generation
- [ ] Add resume capability for interrupted generations

### Long Term
- [ ] Background processing for large tile generations
- [ ] Parallel tile rendering
- [ ] Incremental updates (only regenerate changed tiles)
- [ ] Direct integration with CoMapeo API

## Troubleshooting

### Plugin Won't Load
```bash
# Check Python syntax
python3 -m py_compile comapeo_smp_generator.py

# Check QGIS logs
# Help > Debugging > Show Debug Messages
# Look for plugin load errors
```

### Tiles Look Wrong
```bash
# Validate tile structure
unzip -l output.smp | grep "s/0"
# Should show: s/0/{z}/{x}/{y}.png

# Check tile coordinates match extent
python3 -c "
import math
def deg2num(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

# Replace with your extent coordinates
north, west = 40.0, -120.0
south, east = 35.0, -115.0
zoom = 5

min_x, min_y = deg2num(north, west, zoom)
max_x, max_y = deg2num(south, east, zoom)
print(f'Expected tiles: X:[{min_x}-{max_x}] Y:[{min_y}-{max_y}]')
"
```

### CoMapeo Won't Load SMP
1. Validate style.json structure:
   ```bash
   unzip -p output.smp style.json | jq .
   ```
2. Check for required fields (see XYZ_SMP.md)
3. Verify tiles exist at paths specified in style.json
4. Check file permissions on the .smp file

## Documentation References

- **XYZ_SMP.md** - Complete guide to SMP format and bash script
- **CLAUDE.md** - Plugin architecture and development guide
- **IMPLEMENTATION_SUMMARY.md** - Detailed change log
- **GitHub Issues** - https://github.com/digidem/qgis-smp-plugin/issues

## Support

For issues or questions:
1. Check existing GitHub issues
2. Review documentation files
3. Create new issue with:
   - QGIS version
   - Plugin version (0.2.0)
   - Steps to reproduce
   - Expected vs actual behavior
   - Relevant log messages

## Success Criteria

The implementation is successful if:
- ✅ Plugin loads without errors
- ✅ Generates valid SMP files
- ✅ Tiles align with XYZ grid
- ✅ Files work in CoMapeo
- ✅ No temp file leaks
- ✅ Progress tracking works
- ✅ All 6 GitHub issues are addressed (5 fixed, 1 documented for future)

Current Status: **READY FOR TESTING** 🎉
