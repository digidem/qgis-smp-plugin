---
name: qgis-mcp-test
description: "Verify QGIS plugin code in a live QGIS instance via MCP tools. Test imports, dialog creation, widget visibility, signal wiring, and parameter roundtrips."
---

# QGIS MCP Testing

Verify plugin behavior in a live QGIS instance. Use for any change touching the Processing dialog, parameter wrappers, widget visibility, or plugin loading.

## Workflow

1. `./install-dev.sh` — copy latest code to QGIS plugin dir
2. `mcp_qgis_tool_reload_plugin` with `plugin_name: comapeo_smp`
3. `mcp_qgis_tool_execute_code` — run test snippets (see patterns below)
4. `mcp_qgis_tool_get_message_log` with `level: warning` — check for errors

## Test Patterns

### Import check
```python
from comapeo_smp.comapeo_smp_dialog import SmpAlgorithmDialog, SmpParametersPanel
print("PASS: import")
```

### Algorithm + dialog creation
```python
from comapeo_smp.comapeo_smp_algorithm import ComapeoMapBuilderAlgorithm
from qgis.PyQt.QtWidgets import QWidget
alg = ComapeoMapBuilderAlgorithm()
alg.initAlgorithm(None)
dialog = alg.createCustomParametersWidget(QWidget())
panel = dialog.mainWidget()
print(f"Wrappers: {len(panel.wrappers)}, Params: {len(panel._param_widgets)}")
```

### Visibility toggling
```python
# Use isHidden() not isVisible() — parent is unshown so isVisible() is always False
from qgis.core import QgsProcessingContext
ctx = QgsProcessingContext()
panel.wrappers['INCLUDE_REGION'].setWidgetValue(True, ctx)
panel._on_region_changed()
print(panel._param_widgets['REGION_EXTENT'][0].isHidden())  # expect False
```

### Signal wiring
```python
fired = {'v': False}
panel.wrappers['INCLUDE_WORLD_BASE_ZOOMS'].widgetValueHasChanged.connect(lambda: fired.update(v=True))
panel.wrappers['INCLUDE_WORLD_BASE_ZOOMS'].setWidgetValue(False, QgsProcessingContext())
print(fired['v'])  # expect True
```

### Parameter roundtrip
```python
params = panel.createProcessingParameters()
print(f"Params: {len(params)}")  # expect 12
```

## When to use

- Offline tests (`make test`) cover generator logic
- MCP covers the QGIS runtime path (imports, wrappers, widgets, signals)
- Always use after changes to `comapeo_smp_dialog.py`, `comapeo_smp_algorithm.py` UI code, or plugin loading
