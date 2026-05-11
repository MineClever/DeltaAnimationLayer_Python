# -*- coding: utf-8 -*-
# Maya Python / OpenMaya API 1.0
# Paste into Maya Script Editor, Python tab, then run:
# show_inverse_anim_layer_ui()

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import maya.cmds as cmds
import maya.OpenMaya as om
import maya.OpenMayaAnim as oma
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance


_inverse_anim_layer_dialog = None  # type: Optional[Any]


class AnimLayerInverseBaker(object):
    """Bakes an additive inverse layer by comparing muted and unmuted layer results."""

    ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
    RAD_TO_DEG = 57.29577951308232

    def __init__(
        self,
        source_layer: str = "",
        output_layer: str = "InverseLayer",
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        time_step: float = 1.0,
        replace_output: bool = True,
        use_seconds: bool = False,
    ) -> None:
        self.source_layer = source_layer
        self.output_layer = output_layer
        self.start_time = start_time
        self.end_time = end_time
        self.time_step = time_step
        self.replace_output = replace_output
        self.use_seconds = use_seconds
        self.time_unit = om.MTime.uiUnit()
        self.times = []  # type: List[float]
        self.plugs = []  # type: List[Tuple[str, str, str, om.MPlug]]

    @staticmethod
    def selected_anim_layer() -> str:
        try:
            selected = cmds.animLayer(query=True, selected=True) or []
            if isinstance(selected, str):
                selected = [selected]
            selected = [layer for layer in selected if layer and cmds.objExists(layer)]
            if selected:
                return selected[0]
        except Exception:
            pass

        try:
            best = cmds.animLayer(query=True, bestAnimLayer=True) or []
            if isinstance(best, str):
                best = [best]
            for layer in best:
                if layer and cmds.objExists(layer):
                    return layer
        except Exception:
            pass

        layers = cmds.ls(type="animLayer") or []
        if layers:
            return layers[0]
        return ""

    @staticmethod
    def get_layer_mute(layer_name: str) -> bool:
        mute_attr = layer_name + ".mute"
        if cmds.objExists(mute_attr):
            return bool(cmds.getAttr(mute_attr))
        return bool(cmds.animLayer(layer_name, query=True, mute=True))

    @staticmethod
    def set_layer_mute(layer_name: str, muted: bool) -> None:
        try:
            cmds.animLayer(layer_name, edit=True, mute=muted)
            return
        except Exception:
            pass

        mute_attr = layer_name + ".mute"
        if not cmds.objExists(mute_attr):
            raise RuntimeError("Animation layer has no mute attribute: {0}".format(layer_name))
        cmds.setAttr(mute_attr, muted)

    @staticmethod
    def build_times(start: float, end: float, step: float) -> List[float]:
        if step <= 0.0:
            step = 1.0
        if end < start:
            start, end = end, start

        times = []
        time_value = start
        while time_value <= end + 1.0e-8:
            times.append(time_value)
            time_value += step

        if not times or abs(times[-1] - end) > 1.0e-6:
            times.append(end)
        return times

    @staticmethod
    def split_plug_name(plug_name: str) -> Tuple[str, str]:
        if "." not in plug_name:
            raise RuntimeError("Invalid plug name: {0}".format(plug_name))
        return plug_name.rsplit(".", 1)

    @staticmethod
    def find_plug(plug_name: str) -> om.MPlug:
        selection = om.MSelectionList()
        selection.add(plug_name)
        plug = om.MPlug()
        selection.getPlug(0, plug)
        return plug

    @staticmethod
    def is_supported_plug(plug: om.MPlug) -> bool:
        if plug.isNull() or plug.isCompound() or plug.isArray():
            return False
        attribute = plug.attribute()
        if attribute.hasFn(om.MFn.kNumericAttribute):
            return True
        if attribute.hasFn(om.MFn.kUnitAttribute):
            return True
        return False

    def resolve_layer_plugs(self) -> List[Tuple[str, str, str, om.MPlug]]:
        attributes = cmds.animLayer(self.source_layer, query=True, attribute=True) or []
        if isinstance(attributes, str):
            attributes = [attributes]

        plugs = []
        seen = set()
        for plug_name in attributes:
            if not plug_name or plug_name in seen or "." not in plug_name:
                continue
            if not cmds.objExists(plug_name):
                continue
            try:
                plug = self.find_plug(plug_name)
            except Exception:
                continue
            if not self.is_supported_plug(plug):
                continue

            node_name, attr_name = self.split_plug_name(plug_name)
            plugs.append((node_name, attr_name, plug_name, plug))
            seen.add(plug_name)

        if not plugs:
            raise RuntimeError("Source layer has no supported scalar animation attributes.")
        return plugs

    def prepare(self) -> None:
        if not self.source_layer:
            self.source_layer = self.selected_anim_layer()
        if not self.source_layer or not cmds.objExists(self.source_layer):
            raise RuntimeError("Source animation layer is required.")
        if not self.output_layer:
            raise RuntimeError("Output animation layer name is required.")
        if self.output_layer == self.source_layer:
            raise RuntimeError("Output layer must be different from source layer.")

        self.time_unit = om.MTime.kSeconds if self.use_seconds else om.MTime.uiUnit()
        if self.start_time is None:
            self.start_time = oma.MAnimControl.minTime().asUnits(self.time_unit)
        if self.end_time is None:
            self.end_time = oma.MAnimControl.maxTime().asUnits(self.time_unit)

        self.times = self.build_times(float(self.start_time), float(self.end_time), float(self.time_step))
        self.plugs = self.resolve_layer_plugs()

    def format_time_arg(self, time_value: float) -> str:
        time_arg = str(time_value)
        if self.time_unit == om.MTime.kSeconds:
            time_arg += "sec"
        return time_arg

    @staticmethod
    def read_plug_value(plug: om.MPlug) -> float:
        return float(plug.asDouble())

    def sample_values(self, muted: bool) -> Dict[str, List[float]]:
        self.set_layer_mute(self.source_layer, muted)
        samples = dict((plug_name, []) for _, _, plug_name, _ in self.plugs)

        for time_value in self.times:
            cmds.currentTime(self.format_time_arg(time_value), edit=True)
            for _, _, plug_name, plug in self.plugs:
                samples[plug_name].append(self.read_plug_value(plug))
        return samples

    def ensure_output_layer(self) -> None:
        if cmds.objExists(self.output_layer) and self.replace_output:
            cmds.delete(self.output_layer)
        if not cmds.objExists(self.output_layer):
            cmds.animLayer(self.output_layer)

        try:
            cmds.setAttr(self.output_layer + ".override", 0)
        except Exception:
            pass
        self.set_layer_mute(self.output_layer, False)

    def move_output_layer_after_source(self) -> None:
        try:
            cmds.animLayer(self.output_layer, edit=True, moveLayerAfter=self.source_layer)
        except Exception:
            pass

    def set_output_key(self, node_name: str, attr_name: str, plug_name: str, time_value: float, value: float) -> None:
        try:
            cmds.animLayer(self.output_layer, edit=True, attribute=plug_name)
        except Exception:
            pass

        if attr_name in self.ROTATE_ATTRS:
            value *= self.RAD_TO_DEG

        cmds.setKeyframe(
            node_name,
            attribute=attr_name,
            time=self.format_time_arg(time_value),
            value=value,
            animLayer=self.output_layer,
        )

    def write_inverse_keys(self, muted_samples: Dict[str, List[float]], unmuted_samples: Dict[str, List[float]]) -> None:
        self.ensure_output_layer()
        self.move_output_layer_after_source()

        for node_name, attr_name, plug_name, _ in self.plugs:
            muted_values = muted_samples[plug_name]
            unmuted_values = unmuted_samples[plug_name]
            for index, time_value in enumerate(self.times):
                inverse_delta = muted_values[index] - unmuted_values[index]
                inverse_value = unmuted_values[index] + inverse_delta
                self.set_output_key(node_name, attr_name, plug_name, time_value, inverse_value)

        self.move_output_layer_after_source()

    def execute(self) -> str:
        self.prepare()

        original_time = cmds.currentTime(query=True)
        original_source_mute = self.get_layer_mute(self.source_layer)
        output_existed = cmds.objExists(self.output_layer)
        original_output_mute = self.get_layer_mute(self.output_layer) if output_existed else False

        cmds.undoInfo(openChunk=True)
        try:
            if output_existed:
                self.set_layer_mute(self.output_layer, True)
            unmuted_samples = self.sample_values(False)
            muted_samples = self.sample_values(True)
            self.set_layer_mute(self.source_layer, False)
            self.write_inverse_keys(muted_samples, unmuted_samples)
        finally:
            self.set_layer_mute(self.source_layer, original_source_mute)
            if output_existed and cmds.objExists(self.output_layer) and not self.replace_output:
                self.set_layer_mute(self.output_layer, original_output_mute)
            cmds.currentTime(original_time, edit=True)
            cmds.undoInfo(closeChunk=True)

        cmds.inViewMessage(
            amg='Created inverse animation layer: <hl>{0}</hl>'.format(self.output_layer),
            pos='topCenter',
            fade=True,
        )
        return self.output_layer


class InverseAnimLayerDialog(QtWidgets.QDialog):
    """PySide2 UI for baking an inverse animation layer."""

    WINDOW_OBJECT_NAME = "inverseAnimLayerPySide2UI"

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super(InverseAnimLayerDialog, self).__init__(parent)
        self.setObjectName(self.WINDOW_OBJECT_NAME)
        self.setWindowTitle("Inverse Animation Layer - OpenMaya API 1.0")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(460, 340)

        self.source_layer_combo = QtWidgets.QComboBox()
        self.output_layer_edit = QtWidgets.QLineEdit("InverseLayer")
        self.start_spin = self.new_time_spin()
        self.end_spin = self.new_time_spin()
        self.step_spin = self.new_time_spin(minimum=0.001, default_value=1.0)
        self.replace_output_check = QtWidgets.QCheckBox("Replace output layer if exists")
        self.use_seconds_check = QtWidgets.QCheckBox("Use seconds instead of UI time unit")
        self.status_label = QtWidgets.QLabel("")

        self.build_ui()
        self.refresh_layer_menus()
        self.load_default_time_range()

    @staticmethod
    def maya_main_window():  # type: ignore[no-untyped-def]
        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)

    @staticmethod
    def new_time_spin(minimum: float = -1000000.0, maximum: float = 1000000.0, default_value: float = 0.0) -> Any:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(4)
        spin.setSingleStep(1.0)
        spin.setValue(default_value)
        spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return spin

    def build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        main_layout.addWidget(QtWidgets.QLabel("Bake an inverse layer from the selected animation layer's muted/unmuted channel result."))

        layer_group = QtWidgets.QGroupBox("Layers")
        layer_layout = QtWidgets.QFormLayout(layer_group)
        layer_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        layer_layout.addRow("Source Layer", self.source_layer_combo)
        layer_layout.addRow("Output Layer", self.output_layer_edit)
        refresh_button = QtWidgets.QPushButton("Refresh layers")
        refresh_button.clicked.connect(self.refresh_layer_menus)
        layer_layout.addWidget(refresh_button)
        main_layout.addWidget(layer_group)

        time_group = QtWidgets.QGroupBox("Time Range")
        time_layout = QtWidgets.QFormLayout(time_group)
        time_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        refresh_time_button = QtWidgets.QPushButton("Refresh from Timeline")
        refresh_time_button.clicked.connect(lambda checked=False: self.load_default_time_range(True))
        time_layout.addRow("Start Time", self.start_spin)
        time_layout.addRow("End Time", self.end_spin)
        time_layout.addRow("Timeline", refresh_time_button)
        time_layout.addRow("Time Step", self.step_spin)
        main_layout.addWidget(time_group)

        options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        self.replace_output_check.setChecked(True)
        options_layout.addWidget(self.replace_output_check)
        options_layout.addWidget(self.use_seconds_check)
        main_layout.addWidget(options_group)

        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        create_button = QtWidgets.QPushButton("Create Inverse Layer")
        create_button.setMinimumHeight(34)
        create_button.clicked.connect(self.run_bake)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(create_button)
        button_row.addWidget(close_button)
        main_layout.addLayout(button_row)

    def load_default_time_range(self, update_status: bool = False) -> None:
        time_unit = om.MTime.uiUnit()
        self.start_spin.setValue(oma.MAnimControl.minTime().asUnits(time_unit))
        self.end_spin.setValue(oma.MAnimControl.maxTime().asUnits(time_unit))
        if update_status:
            self.status_label.setText("Time range refreshed from current timeline.")

    def refresh_layer_menus(self) -> None:
        layers = cmds.ls(type="animLayer") or []
        selected_layer = AnimLayerInverseBaker.selected_anim_layer()
        old_source = self.source_layer_combo.currentText() or selected_layer

        self.source_layer_combo.blockSignals(True)
        self.source_layer_combo.clear()
        self.source_layer_combo.addItems(layers)
        index = self.source_layer_combo.findText(old_source)
        if index < 0 and selected_layer:
            index = self.source_layer_combo.findText(selected_layer)
        if index >= 0:
            self.source_layer_combo.setCurrentIndex(index)
        self.source_layer_combo.blockSignals(False)

        self.status_label.setText("Animation layer list refreshed.")

    def show_error(self, message: str) -> None:
        self.status_label.setText(message)
        cmds.warning(message)
        QtWidgets.QMessageBox.critical(self, "Inverse Animation Layer Error", message)

    def run_bake(self) -> None:
        try:
            output_layer = AnimLayerInverseBaker(
                source_layer=self.source_layer_combo.currentText(),
                output_layer=self.output_layer_edit.text().strip(),
                start_time=self.start_spin.value(),
                end_time=self.end_spin.value(),
                time_step=self.step_spin.value(),
                replace_output=self.replace_output_check.isChecked(),
                use_seconds=self.use_seconds_check.isChecked(),
            ).execute()
            self.status_label.setText("Created inverse animation layer: {0}".format(output_layer))
        except Exception as exc:
            self.show_error(str(exc))


def show_inverse_anim_layer_ui() -> InverseAnimLayerDialog:
    global _inverse_anim_layer_dialog

    if _inverse_anim_layer_dialog is not None:
        try:
            _inverse_anim_layer_dialog.close()
            _inverse_anim_layer_dialog.deleteLater()
        except RuntimeError:
            pass
        _inverse_anim_layer_dialog = None

    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == InverseAnimLayerDialog.WINDOW_OBJECT_NAME:
            widget.close()
            widget.deleteLater()

    _inverse_anim_layer_dialog = InverseAnimLayerDialog(parent=InverseAnimLayerDialog.maya_main_window())
    _inverse_anim_layer_dialog.show()
    _inverse_anim_layer_dialog.raise_()
    _inverse_anim_layer_dialog.activateWindow()
    return _inverse_anim_layer_dialog


if __name__ == "__main__":
    show_inverse_anim_layer_ui()
