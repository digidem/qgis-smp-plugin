# -*- coding: utf-8 -*-

"""
/***************************************************************************
 ComapeoMapBuilder
                                 A QGIS plugin
 Generates SMP files for CoMapeo
                              -------------------
        begin                : 2025-05-01
        copyright            : (C) 2025 by Awana Digital
        email                : luandro@awana.digital
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is released under the MIT License.                       *
 *   Copyright (c) 2026 Awana Digital                                      *
 *   See LICENSE for full license text.                                    *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = 'Awana Digital'
__date__ = '2025-05-01'
__copyright__ = '(C) 2025 by Awana Digital'

from qgis.PyQt.QtWidgets import QCheckBox, QComboBox, QWidget

from processing.gui.AlgorithmDialog import AlgorithmDialog
from processing.gui.ParametersPanel import ParametersPanel
from processing.gui.wrappers import WidgetWrapper


# ---------------------------------------------------------------------------
# Visibility groups: parameter names that should be hidden/shown together
# based on the value of a controlling parameter.
# ---------------------------------------------------------------------------
WORLD_PARAMS = {'WORLD_MAX_ZOOM'}
REGION_PARAMS = {'REGION_EXTENT', 'REGION_MIN_ZOOM', 'REGION_MAX_ZOOM'}
QUALITY_PARAMS = {'JPEG_QUALITY'}

# TILE_FORMAT enum indices that require a quality slider
_QUALITY_FORMATS = {1, 2}  # 1=JPG, 2=WEBP


class SmpAlgorithmDialog(AlgorithmDialog):
    """Custom algorithm dialog that uses :class:`SmpParametersPanel`."""

    def __init__(self, alg, parent=None):
        super().__init__(alg, parent=parent)

    def getParametersPanel(self, alg, parent):
        return SmpParametersPanel(parent, alg)


class SmpParametersPanel(ParametersPanel):
    """Processing parameters panel with conditional visibility.

    The following parameters are hidden by default and shown only when
    their controlling parameter is toggled on:

    * ``WORLD_MAX_ZOOM`` — visible when ``INCLUDE_WORLD_BASE_ZOOMS`` is checked
    * ``REGION_EXTENT``, ``REGION_MIN_ZOOM``, ``REGION_MAX_ZOOM`` — visible
      when ``INCLUDE_REGION`` is checked
    * ``JPEG_QUALITY`` — visible when ``TILE_FORMAT`` is JPG or WEBP
    """

    def __init__(self, parent, alg):
        # Maps parameter name → list of QWidget that form its row
        # (label widget, value widget)
        self._param_widgets = {}
        super().__init__(parent, alg)
        self._connect_visibility_signals()
        self._update_all_visibility()

    # -- Layout interception --------------------------------------------------

    def addParameterLabel(self, param, label):
        """Store label reference before delegating to the base class."""
        self._param_widgets.setdefault(param.name(), []).append(label)
        super().addParameterLabel(param, label)

    def addParameterWidget(self, param, widget, stretch=0):
        """Store widget reference before delegating to the base class."""
        self._param_widgets.setdefault(param.name(), []).append(widget)
        super().addParameterWidget(param, widget, stretch)

    # -- Visibility helpers ---------------------------------------------------

    def _get_wrapper_widget(self, param_name):
        """Return the underlying Qt widget for a parameter wrapper."""
        wrapper = self.wrappers.get(param_name)
        if wrapper is None:
            return None
        if issubclass(wrapper.__class__, WidgetWrapper):
            return wrapper.widget
        return wrapper.wrappedWidget()

    def _set_group_visible(self, param_names, visible):
        """Show or hide all widgets belonging to *param_names*."""
        for name in param_names:
            for widget in self._param_widgets.get(name, []):
                widget.setVisible(visible)

    def _update_all_visibility(self):
        """Apply visibility rules based on current parameter values."""
        self._set_group_visible(WORLD_PARAMS, self._is_world_checked())
        self._set_group_visible(REGION_PARAMS, self._is_region_checked())
        self._set_group_visible(QUALITY_PARAMS, self._is_quality_format())

    def _is_world_checked(self):
        """Return True if the World overview checkbox is checked."""
        wrapper = self.wrappers.get('INCLUDE_WORLD_BASE_ZOOMS')
        if wrapper is None:
            return True  # default
        return bool(wrapper.parameterValue())

    def _is_region_checked(self):
        """Return True if the Region detail checkbox is checked."""
        wrapper = self.wrappers.get('INCLUDE_REGION')
        if wrapper is None:
            return False  # default
        return bool(wrapper.parameterValue())

    def _is_quality_format(self):
        """Return True if the current tile format needs a quality slider."""
        wrapper = self.wrappers.get('TILE_FORMAT')
        if wrapper is None:
            return True  # default is WEBP which needs quality
        value = wrapper.parameterValue()
        try:
            return int(value) in _QUALITY_FORMATS
        except (TypeError, ValueError):
            return True

    # -- Signal wiring --------------------------------------------------------

    def _connect_visibility_signals(self):
        """Connect wrapper value-changed signals to visibility slots."""
        world_wrapper = self.wrappers.get('INCLUDE_WORLD_BASE_ZOOMS')
        if world_wrapper is not None:
            world_wrapper.widgetValueHasChanged.connect(
                self._on_world_changed
            )
            self._connect_widget_fallback(
                'INCLUDE_WORLD_BASE_ZOOMS', self._on_world_changed
            )

        region_wrapper = self.wrappers.get('INCLUDE_REGION')
        if region_wrapper is not None:
            region_wrapper.widgetValueHasChanged.connect(
                self._on_region_changed
            )
            self._connect_widget_fallback(
                'INCLUDE_REGION', self._on_region_changed
            )

        format_wrapper = self.wrappers.get('TILE_FORMAT')
        if format_wrapper is not None:
            format_wrapper.widgetValueHasChanged.connect(
                self._on_format_changed
            )
            self._connect_widget_fallback(
                'TILE_FORMAT', self._on_format_changed
            )

    def _connect_widget_fallback(self, param_name, slot):
        """Connect common change signals on the underlying widget.

        This mirrors the GDAL pattern — ``widgetValueHasChanged`` is not
        always emitted reliably by all wrapper types, so we also listen
        to the concrete widget signals.
        """
        widget = self._get_wrapper_widget(param_name)
        if widget is None:
            return
        self._connect_change_signals(widget, slot)
        for child in widget.findChildren(QWidget):
            self._connect_change_signals(child, slot)

    @staticmethod
    def _connect_change_signals(widget, slot):
        """Connect appropriate change signal for a widget type."""
        if isinstance(widget, QCheckBox):
            widget.stateChanged.connect(lambda _checked: slot())
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _idx: slot())

    # -- Slots ----------------------------------------------------------------

    def _on_world_changed(self):
        self._set_group_visible(WORLD_PARAMS, self._is_world_checked())

    def _on_region_changed(self):
        self._set_group_visible(REGION_PARAMS, self._is_region_checked())

    def _on_format_changed(self):
        self._set_group_visible(QUALITY_PARAMS, self._is_quality_format())
