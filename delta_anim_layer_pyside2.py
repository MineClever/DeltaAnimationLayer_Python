# -*- coding: utf-8 -*-
# Maya Python / OpenMaya API 1.0
# Paste into Maya Script Editor, Python tab, then run:
# show_delta_anim_layer_ui()

import math
import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMaya as om
import maya.OpenMayaAnim as oma
import maya.OpenMayaUI as omui

try:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
except ImportError:
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance


class DeltaAnimationLayer(object):
    """Core DeltaAnimationLayer implementation, independent from the UI."""

    RAD_TO_DEG = 180.0 / math.pi
    ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
    TRANSFORM_ATTRS = (
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
    )

    def __init__(
        self,
        mode="subtract",
        reference_layer="",
        source_layer="",
        output_layer="DeltaLayer",
        start_time=None,
        end_time=None,
        time_step=1.0,
        reference_time=None,
        use_reference_pose=False,
        use_seconds=False,
        replace_output=False
    ):
        self.mode = self.parse_mode(mode)
        self.reference_layer = reference_layer
        self.source_layer = source_layer
        self.output_layer = output_layer
        self.start_time = start_time
        self.end_time = end_time
        self.time_step = time_step
        self.reference_time = reference_time
        self.use_reference_pose = use_reference_pose
        self.use_seconds = use_seconds
        self.replace_output = replace_output
        self.time_unit = None
        self.times = []
        self.paths = []

    @staticmethod
    def is_empty_layer(layer_name):
        return not layer_name or layer_name in ("None", "none")

    @staticmethod
    def parse_mode(mode):
        mode = (mode or "subtract").lower()
        if mode in ("delta", "subtract"):
            return "subtract"
        if mode in ("predelta", "presubtract", "pre-subtract"):
            return "presubtract"
        if mode in ("lineardelta", "linear"):
            return "linear"
        if mode in ("splinedelta", "spline"):
            return "spline"
        raise RuntimeError("mode must be subtract, presubtract, lineardelta, or splinedelta.")

    @staticmethod
    def spline_weight(t):
        return 3.0 * t * t - 2.0 * t * t * t

    @staticmethod
    def get_dag_path(node):
        selection = om.MSelectionList()
        selection.add(node)
        path = om.MDagPath()
        selection.getDagPath(0, path)
        return path

    @staticmethod
    def get_depend_node(node_name):
        selection = om.MSelectionList()
        selection.add(node_name)
        obj = om.MObject()
        selection.getDependNode(0, obj)
        return obj

    @staticmethod
    def find_plug(node_obj, attr_name):
        node_fn = om.MFnDependencyNode(node_obj)
        return node_fn.findPlug(attr_name, True)

    @staticmethod
    def plug_node_name_and_attr(plug):
        full_name = plug.name()
        node_name, attr_name = full_name.rsplit(".", 1)
        return node_name, attr_name, full_name

    def try_find_layer_curve(self, layer_name, plug):
        if self.is_empty_layer(layer_name) or plug.isNull():
            return None

        command = 'animLayer -q -findCurveForPlug "{0}" "{1}"'.format(
            plug.name(),
            layer_name
        )
        try:
            result = mel.eval(command)
        except Exception:
            return None

        if not result:
            return None

        curve_name = result[0] if isinstance(result, (list, tuple)) else result
        if not curve_name or not cmds.objExists(curve_name):
            return None

        curve_obj = self.get_depend_node(curve_name)
        if curve_obj.hasFn(om.MFn.kAnimCurve):
            return curve_obj
        return None

    def sample_plug_or_layer_curve(self, layer_name, plug, time_value):
        if plug.isNull():
            return 0.0

        if not self.is_empty_layer(layer_name):
            curve_obj = self.try_find_layer_curve(layer_name, plug)
            if curve_obj and not curve_obj.isNull():
                curve_fn = oma.MFnAnimCurve(curve_obj)
                return curve_fn.evaluate(om.MTime(time_value, self.time_unit))
            return 0.0

        _, attr_name, full_plug = self.plug_node_name_and_attr(plug)
        value = cmds.getAttr(full_plug, time=self.format_time_arg(time_value))
        if attr_name in self.ROTATE_ATTRS:
            value = math.radians(value)
        return value

    def sample_transform(self, path, layer_name, time_value):
        node_obj = path.node()
        plugs = [self.find_plug(node_obj, attr_name) for attr_name in self.TRANSFORM_ATTRS]

        translation = om.MVector(
            self.sample_plug_or_layer_curve(layer_name, plugs[0], time_value),
            self.sample_plug_or_layer_curve(layer_name, plugs[1], time_value),
            self.sample_plug_or_layer_curve(layer_name, plugs[2], time_value)
        )

        rot_x = self.sample_plug_or_layer_curve(layer_name, plugs[3], time_value)
        rot_y = self.sample_plug_or_layer_curve(layer_name, plugs[4], time_value)
        rot_z = self.sample_plug_or_layer_curve(layer_name, plugs[5], time_value)

        transform_fn = om.MFnTransform(path)
        current_rot = om.MEulerRotation()
        transform_fn.getRotation(current_rot)

        euler = om.MEulerRotation(rot_x, rot_y, rot_z, current_rot.order)
        return translation, euler.asQuaternion()

    @staticmethod
    def quaternion_inverse(q):
        inverse = om.MQuaternion(q.x, q.y, q.z, q.w)
        inverse.invertIt()
        return inverse

    @staticmethod
    def quaternion_slerp(q1, q2, weight):
        try:
            return om.MQuaternion.slerp(q1, q2, weight)
        except Exception:
            x = q1.x * (1.0 - weight) + q2.x * weight
            y = q1.y * (1.0 - weight) + q2.y * weight
            z = q1.z * (1.0 - weight) + q2.z * weight
            w = q1.w * (1.0 - weight) + q2.w * weight
            q = om.MQuaternion(x, y, z, w)
            q.normalizeIt()
            return q

    def compute_subtract(self, source, reference):
        source_translation, source_rotation = source
        reference_translation, reference_rotation = reference
        return (
            source_translation - reference_translation,
            source_rotation * self.quaternion_inverse(reference_rotation)
        )

    def compute_pre_subtract(self, source, reference):
        source_translation, source_rotation = source
        reference_translation, reference_rotation = reference
        return (
            reference_translation - source_translation,
            self.quaternion_inverse(reference_rotation) * source_rotation
        )

    def ensure_animation_layer(self):
        if not self.output_layer:
            raise RuntimeError("Output layer name is empty.")

        if cmds.objExists(self.output_layer) and self.replace_output:
            cmds.delete(self.output_layer)

        if not cmds.objExists(self.output_layer):
            cmds.animLayer(self.output_layer)

        try:
            cmds.setAttr(self.output_layer + ".override", 0)
        except Exception:
            pass

    def format_time_arg(self, time_value):
        time_arg = str(time_value)
        if self.time_unit == om.MTime.kSeconds:
            time_arg += "sec"
        return time_arg

    def set_layer_key(self, plug, time_value, value):
        node_name, attr_name, full_plug = self.plug_node_name_and_attr(plug)

        try:
            cmds.animLayer(self.output_layer, edit=True, attribute=full_plug)
        except Exception:
            pass

        if attr_name in self.ROTATE_ATTRS:
            value *= self.RAD_TO_DEG

        cmds.setKeyframe(
            node_name,
            attribute=attr_name,
            time=self.format_time_arg(time_value),
            value=value,
            animLayer=self.output_layer
        )

    def write_transform_sample(self, path, time_value, sample):
        node_obj = path.node()
        plugs = [self.find_plug(node_obj, attr_name) for attr_name in self.TRANSFORM_ATTRS]

        transform_fn = om.MFnTransform(path)
        current_rot = om.MEulerRotation()
        transform_fn.getRotation(current_rot)

        translation, rotation = sample
        euler = rotation.asEulerRotation()
        euler.reorderIt(current_rot.order)

        values = [
            translation.x,
            translation.y,
            translation.z,
            euler.x,
            euler.y,
            euler.z,
        ]

        for plug, value in zip(plugs, values):
            self.set_layer_key(plug, time_value, value)

    @staticmethod
    def build_times(start, end, step):
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

    def resolve_node_paths_from_reference_layer(self):
        if self.is_empty_layer(self.reference_layer):
            raise RuntimeError("Reference Layer is required to resolve input transform nodes.")

        attributes = cmds.animLayer(self.reference_layer, query=True, attribute=True) or []
        if isinstance(attributes, str):
            attributes = [attributes]

        paths = []
        seen_paths = set()
        for plug_name in attributes:
            if not plug_name or "." not in plug_name:
                continue

            node_name = plug_name.rsplit(".", 1)[0]
            matches = cmds.ls(node_name, long=True, type="transform") or []
            for match in matches:
                try:
                    path = self.get_dag_path(match)
                except Exception:
                    continue

                full_path = path.fullPathName()
                if full_path not in seen_paths:
                    paths.append(path)
                    seen_paths.add(full_path)

        if not paths:
            raise RuntimeError("Reference Layer must contain one or more transform attributes.")
        return paths

    def execute(self):
        self.prepare()
        self.ensure_animation_layer()

        for path in self.paths:
            self.process_path(path)

        cmds.inViewMessage(
            amg='Created delta animation layer: <hl>{0}</hl>'.format(self.output_layer),
            pos='topCenter',
            fade=True
        )
        return self.output_layer

    def prepare(self):
        if not self.output_layer:
            raise RuntimeError("Output layer is required.")

        self.paths = self.resolve_node_paths_from_reference_layer()
        self.time_unit = om.MTime.kSeconds if self.use_seconds else om.MTime.uiUnit()

        if self.start_time is None:
            self.start_time = oma.MAnimControl.minTime().asUnits(self.time_unit)
        if self.end_time is None:
            self.end_time = oma.MAnimControl.maxTime().asUnits(self.time_unit)
        if self.reference_time is None:
            self.reference_time = self.start_time

        self.times = self.build_times(
            float(self.start_time),
            float(self.end_time),
            float(self.time_step)
        )

    def process_path(self, path):
        source_samples = [
            self.sample_transform(path, self.source_layer, time_value)
            for time_value in self.times
        ]

        reference_pose = None
        if self.use_reference_pose and self.mode in ("subtract", "presubtract"):
            reference_pose = self.sample_transform(
                path,
                self.reference_layer,
                self.reference_time
            )

        for index, time_value in enumerate(self.times):
            output = self.compute_output_sample(
                path,
                source_samples,
                reference_pose,
                index,
                time_value
            )
            self.write_transform_sample(path, time_value, output)

    def compute_output_sample(self, path, source_samples, reference_pose, sample_index, time_value):
        if self.mode in ("linear", "spline"):
            return self.compute_interpolated_output(source_samples, sample_index)

        reference = reference_pose
        if not self.use_reference_pose:
            reference = self.sample_transform(
                path,
                self.reference_layer,
                time_value
            )

        if self.mode == "presubtract":
            return self.compute_pre_subtract(source_samples[sample_index], reference)
        return self.compute_subtract(source_samples[sample_index], reference)

    def compute_interpolated_output(self, source_samples, sample_index):
        if len(self.times) > 1:
            ratio = float(sample_index) / float(len(self.times) - 1)
        else:
            ratio = 1.0

        weight = self.spline_weight(ratio) if self.mode == "spline" else ratio
        first_translation, first_rotation = source_samples[0]
        last_translation, last_rotation = source_samples[-1]

        reference = (
            first_translation * (1.0 - weight) + last_translation * weight,
            self.quaternion_slerp(first_rotation, last_rotation, weight)
        )
        return self.compute_subtract(source_samples[sample_index], reference)


WINDOW_OBJECT_NAME = "deltaAnimLayerPySide2UI"
_delta_anim_layer_dialog = None


class DeltaAnimLayerDialog(QtWidgets.QDialog):
    """Resizable PySide dialog for creating delta animation layers."""

    def __init__(self, parent=None):
        super(DeltaAnimLayerDialog, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Delta Animation Layer - OpenMaya API 1.0")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(420, 440)
        self.resize(560, 520)

        self.mode_combo = None
        self.reference_layer_combo = None
        self.source_layer_combo = None
        self.output_layer_edit = None
        self.start_spin = None
        self.end_spin = None
        self.step_spin = None
        self.reference_time_spin = None
        self.use_reference_pose_check = None
        self.use_seconds_check = None
        self.replace_output_check = None
        self.status_label = None

        self.build_ui()
        self.refresh_layer_menus()
        self.load_default_time_range()

    @staticmethod
    def maya_main_window():
        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)

    @staticmethod
    def list_anim_layers():
        layers = cmds.ls(type="animLayer") or []
        return ["None"] + layers

    @staticmethod
    def selected_layer_name(combo):
        layer_name = combo.currentText()
        if layer_name == "None":
            return ""
        return layer_name

    def build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        intro = QtWidgets.QLabel(
            "Generate a delta animation layer for transform attributes registered on the Reference Layer."
        )
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        settings_group = QtWidgets.QGroupBox("Layer Settings")
        settings_layout = QtWidgets.QFormLayout(settings_group)
        settings_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        settings_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["subtract", "presubtract", "linear", "spline"])
        settings_layout.addRow("Mode", self.mode_combo)

        ref_row = QtWidgets.QHBoxLayout()
        self.reference_layer_combo = QtWidgets.QComboBox()
        self.reference_layer_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        refresh_button = QtWidgets.QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_layer_menus)
        ref_row.addWidget(self.reference_layer_combo, 1)
        ref_row.addWidget(refresh_button)
        settings_layout.addRow("Reference Layer", ref_row)

        self.source_layer_combo = QtWidgets.QComboBox()
        settings_layout.addRow("Source Layer", self.source_layer_combo)

        self.output_layer_edit = QtWidgets.QLineEdit("DeltaLayer")
        settings_layout.addRow("Output Layer", self.output_layer_edit)
        main_layout.addWidget(settings_group)

        time_group = QtWidgets.QGroupBox("Time Range")
        time_layout = QtWidgets.QFormLayout(time_group)
        time_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        self.start_spin = self.new_time_spin()
        self.end_spin = self.new_time_spin()
        self.step_spin = self.new_time_spin(minimum=0.001, default_value=1.0)
        self.reference_time_spin = self.new_time_spin()

        time_layout.addRow("Start Time", self.start_spin)
        time_layout.addRow("End Time", self.end_spin)
        time_layout.addRow("Time Step", self.step_spin)
        time_layout.addRow("Reference Time", self.reference_time_spin)
        main_layout.addWidget(time_group)

        options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        self.use_reference_pose_check = QtWidgets.QCheckBox("Use reference pose at Reference Time")
        self.use_seconds_check = QtWidgets.QCheckBox("Use seconds instead of UI time unit")
        self.replace_output_check = QtWidgets.QCheckBox("Replace output layer if exists")
        options_layout.addWidget(self.use_reference_pose_check)
        options_layout.addWidget(self.use_seconds_check)
        options_layout.addWidget(self.replace_output_check)
        main_layout.addWidget(options_group)

        main_layout.addStretch(1)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        create_button = QtWidgets.QPushButton("Create Delta Animation Layer")
        create_button.setMinimumHeight(34)
        create_button.clicked.connect(self.run_delta)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(create_button)
        button_row.addWidget(close_button)
        main_layout.addLayout(button_row)

    @staticmethod
    def new_time_spin(minimum=-1000000.0, maximum=1000000.0, default_value=0.0):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(4)
        spin.setSingleStep(1.0)
        spin.setValue(default_value)
        spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return spin

    def load_default_time_range(self):
        time_unit = om.MTime.uiUnit()
        start = oma.MAnimControl.minTime().asUnits(time_unit)
        end = oma.MAnimControl.maxTime().asUnits(time_unit)
        self.start_spin.setValue(start)
        self.end_spin.setValue(end)
        self.reference_time_spin.setValue(start)

    def refresh_layer_menus(self):
        layers = self.list_anim_layers()
        old_reference = self.reference_layer_combo.currentText()
        old_source = self.source_layer_combo.currentText()

        for combo, old_value in (
            (self.reference_layer_combo, old_reference),
            (self.source_layer_combo, old_source),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(layers)
            index = combo.findText(old_value)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)

        self.status_label.setText("Animation layer list refreshed.")

    def build_runner(self):
        return DeltaAnimationLayer(
            mode=self.mode_combo.currentText(),
            reference_layer=self.selected_layer_name(self.reference_layer_combo),
            source_layer=self.selected_layer_name(self.source_layer_combo),
            output_layer=self.output_layer_edit.text().strip(),
            start_time=self.start_spin.value(),
            end_time=self.end_spin.value(),
            time_step=self.step_spin.value(),
            reference_time=self.reference_time_spin.value(),
            use_reference_pose=self.use_reference_pose_check.isChecked(),
            use_seconds=self.use_seconds_check.isChecked(),
            replace_output=self.replace_output_check.isChecked()
        )

    def run_delta(self):
        try:
            output_layer = self.build_runner().execute()
            self.status_label.setText("Created delta animation layer: {0}".format(output_layer))
        except Exception as exc:
            message = str(exc)
            self.status_label.setText(message)
            cmds.warning(message)
            QtWidgets.QMessageBox.critical(self, "Delta Animation Layer Error", message)

    @classmethod
    def delete_existing_dialog(cls):
        global _delta_anim_layer_dialog

        if _delta_anim_layer_dialog is not None:
            try:
                _delta_anim_layer_dialog.close()
                _delta_anim_layer_dialog.deleteLater()
            except RuntimeError:
                pass
            _delta_anim_layer_dialog = None

        for widget in QtWidgets.QApplication.topLevelWidgets():
            if widget.objectName() == WINDOW_OBJECT_NAME:
                widget.close()
                widget.deleteLater()

    @classmethod
    def show(cls):
        global _delta_anim_layer_dialog

        cls.delete_existing_dialog()
        _delta_anim_layer_dialog = cls(parent=cls.maya_main_window())
        _delta_anim_layer_dialog.show()
        _delta_anim_layer_dialog.raise_()
        _delta_anim_layer_dialog.activateWindow()
        return _delta_anim_layer_dialog


def show_delta_anim_layer_ui():
    return DeltaAnimLayerDialog.show()


if __name__ == "__main__":
    show_delta_anim_layer_ui()
