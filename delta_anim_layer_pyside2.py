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


RAD_TO_DEG = 180.0 / math.pi


def is_empty_layer(layer_name):
    # type: (str) -> bool
    return not layer_name or layer_name in ("None", "none")


def spline_weight(t):
    # type: (float) -> float
    return 3.0 * t * t - 2.0 * t * t * t


def parse_mode(mode):
    # type: (str) -> str
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


def get_dag_path(node):
    # type: (str) -> om.MDagPath
    sel = om.MSelectionList()
    sel.add(node)
    path = om.MDagPath()
    sel.getDagPath(0, path)
    return path


def get_depend_node(node_name):
    # type: (str) -> om.MObject
    sel = om.MSelectionList()
    sel.add(node_name)
    obj = om.MObject()
    sel.getDependNode(0, obj)
    return obj


def find_plug(node_obj, attr_name):
    # type: (om.MObject, str) -> om.MPlug
    fn = om.MFnDependencyNode(node_obj)
    return fn.findPlug(attr_name, True)


def plug_full_name(plug):
    # type: (om.MPlug) -> str
    return plug.name()


def plug_node_name_and_attr(plug):
    # type: (om.MPlug) -> tuple
    full = plug.name()
    node, attr = full.rsplit(".", 1)
    return node, attr, full


def try_find_layer_curve(layer_name, plug):
    # type: (str, om.MPlug) -> object
    if is_empty_layer(layer_name) or plug.isNull():
        return None

    full_plug = plug_full_name(plug)

    # This mirrors the C++ animLayer lookup for the anim curve bound to a plug.
    # animLayer -q -findCurveForPlug "node.attr" "layer"
    cmd = 'animLayer -q -findCurveForPlug "{0}" "{1}"'.format(
        full_plug, layer_name
    )

    try:
        result = mel.eval(cmd)
    except Exception:
        return None

    if not result:
        return None

    if isinstance(result, (list, tuple)):
        curve_name = result[0]
    else:
        curve_name = result

    if not curve_name or not cmds.objExists(curve_name):
        return None

    obj = get_depend_node(curve_name)
    if obj.hasFn(om.MFn.kAnimCurve):
        return obj

    return None


class CurrentTimeGuard(object):
    def __init__(self):
        # type: () -> None
        self.old_time = oma.MAnimControl.currentTime()

    def __enter__(self):
        # type: () -> CurrentTimeGuard
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # type: (object, object, object) -> None
        oma.MAnimControl.setCurrentTime(self.old_time)


def sample_plug_or_layer_curve(layer_name, plug, time_value, time_unit):
    # type: (str, om.MPlug, float, int) -> float
    if plug.isNull():
        return 0.0

    if not is_empty_layer(layer_name):
        curve_obj = try_find_layer_curve(layer_name, plug)
        if curve_obj and not curve_obj.isNull():
            curve_fn = oma.MFnAnimCurve(curve_obj)
            return curve_fn.evaluate(om.MTime(time_value, time_unit))

        # A plug without a curve on the requested layer contributes zero.
        return 0.0

    # Without a layer, sample the final evaluated scene value. In mayapy, direct
    # MPlug.asDouble() does not reliably evaluate keyed values at the requested
    # time after changing MAnimControl.currentTime.
    node_name, attr_name, full_plug = plug_node_name_and_attr(plug)
    time_arg = str(time_value)
    if time_unit == om.MTime.kSeconds:
        time_arg += "sec"
    value = cmds.getAttr(full_plug, time=time_arg)
    if attr_name in ("rotateX", "rotateY", "rotateZ"):
        value = math.radians(value)
    return value


class TransformSample(object):
    def __init__(self):
        # type: () -> None
        self.translation = om.MVector()
        self.rotation = om.MQuaternion()


def sample_transform(path, layer_name, time_value, time_unit):
    # type: (om.MDagPath, str, float, int) -> TransformSample
    node_obj = path.node()

    tx = find_plug(node_obj, "translateX")
    ty = find_plug(node_obj, "translateY")
    tz = find_plug(node_obj, "translateZ")
    rx = find_plug(node_obj, "rotateX")
    ry = find_plug(node_obj, "rotateY")
    rz = find_plug(node_obj, "rotateZ")

    sample = TransformSample()

    sample.translation = om.MVector(
        sample_plug_or_layer_curve(layer_name, tx, time_value, time_unit),
        sample_plug_or_layer_curve(layer_name, ty, time_value, time_unit),
        sample_plug_or_layer_curve(layer_name, tz, time_value, time_unit)
    )

    rot_x = sample_plug_or_layer_curve(layer_name, rx, time_value, time_unit)
    rot_y = sample_plug_or_layer_curve(layer_name, ry, time_value, time_unit)
    rot_z = sample_plug_or_layer_curve(layer_name, rz, time_value, time_unit)

    transform_fn = om.MFnTransform(path)
    current_rot = om.MEulerRotation()
    transform_fn.getRotation(current_rot)

    euler = om.MEulerRotation(rot_x, rot_y, rot_z, current_rot.order)
    sample.rotation = euler.asQuaternion()

    return sample


def quaternion_inverse(q):
    # type: (om.MQuaternion) -> om.MQuaternion
    qi = om.MQuaternion(q.x, q.y, q.z, q.w)
    qi.invertIt()
    return qi


def quaternion_slerp(q1, q2, weight):
    # type: (om.MQuaternion, om.MQuaternion, float) -> om.MQuaternion
    try:
        return om.MQuaternion.slerp(q1, q2, weight)
    except Exception:
        # Fallback to normalized linear interpolation when slerp is unavailable.
        x = q1.x * (1.0 - weight) + q2.x * weight
        y = q1.y * (1.0 - weight) + q2.y * weight
        z = q1.z * (1.0 - weight) + q2.z * weight
        w = q1.w * (1.0 - weight) + q2.w * weight
        q = om.MQuaternion(x, y, z, w)
        q.normalizeIt()
        return q


def compute_subtract(source, reference):
    # type: (TransformSample, TransformSample) -> TransformSample
    result = TransformSample()
    result.translation = source.translation - reference.translation
    result.rotation = source.rotation * quaternion_inverse(reference.rotation)
    return result


def compute_pre_subtract(source, reference):
    # type: (TransformSample, TransformSample) -> TransformSample
    result = TransformSample()
    result.translation = reference.translation - source.translation
    result.rotation = quaternion_inverse(reference.rotation) * source.rotation
    return result


def ensure_animation_layer(layer_name, replace_existing=False):
    # type: (str, bool) -> str
    if not layer_name:
        raise RuntimeError("Output layer name is empty.")

    if cmds.objExists(layer_name) and replace_existing:
        cmds.delete(layer_name)

    if not cmds.objExists(layer_name):
        cmds.animLayer(layer_name)

    try:
        cmds.setAttr(layer_name + ".override", 0)
    except Exception:
        pass

    return layer_name


def set_layer_key(layer_name, plug, time_value, time_unit, value):
    # type: (str, om.MPlug, float, int, float) -> None
    node_name, attr_name, full_plug = plug_node_name_and_attr(plug)

    try:
        cmds.animLayer(layer_name, edit=True, attribute=full_plug)
    except Exception:
        pass

    # Maya setKeyframe expects rotation values in degrees, while OpenMaya samples radians.
    if attr_name in ("rotateX", "rotateY", "rotateZ"):
        value *= RAD_TO_DEG

    time_arg = str(time_value)
    if time_unit == om.MTime.kSeconds:
        time_arg += "sec"

    cmds.setKeyframe(
        node_name,
        attribute=attr_name,
        time=time_arg,
        value=value,
        animLayer=layer_name
    )


def write_transform_sample(path, layer_name, time_value, time_unit, sample):
    # type: (om.MDagPath, str, float, int, TransformSample) -> None
    node_obj = path.node()

    plugs = [
        find_plug(node_obj, "translateX"),
        find_plug(node_obj, "translateY"),
        find_plug(node_obj, "translateZ"),
        find_plug(node_obj, "rotateX"),
        find_plug(node_obj, "rotateY"),
        find_plug(node_obj, "rotateZ"),
    ]

    transform_fn = om.MFnTransform(path)
    current_rot = om.MEulerRotation()
    transform_fn.getRotation(current_rot)

    euler = sample.rotation.asEulerRotation()
    euler.reorderIt(current_rot.order)

    values = [
        sample.translation.x,
        sample.translation.y,
        sample.translation.z,
        euler.x,
        euler.y,
        euler.z,
    ]

    for plug, value in zip(plugs, values):
        set_layer_key(layer_name, plug, time_value, time_unit, value)


def build_times(start, end, step):
    # type: (float, float, float) -> list
    if step <= 0.0:
        step = 1.0

    if end < start:
        start, end = end, start

    times = []
    t = start

    while t <= end + 1.0e-8:
        times.append(t)
        t += step

    if not times or abs(times[-1] - end) > 1.0e-6:
        times.append(end)

    return times


def resolve_node_paths_from_reference_layer(reference_layer):
    # type: (str) -> list
    if is_empty_layer(reference_layer):
        raise RuntimeError("Reference Layer is required to resolve input transform nodes.")

    attributes = cmds.animLayer(reference_layer, query=True, attribute=True) or []
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
                path = get_dag_path(match)
            except Exception:
                continue

            full_path = path.fullPathName()
            if full_path not in seen_paths:
                paths.append(path)
                seen_paths.add(full_path)

    if not paths:
        raise RuntimeError("Reference Layer must contain one or more transform attributes.")

    return paths


def delta_animation_layer(
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
    # type: (str, str, str, str, float, float, float, float, bool, bool, bool) -> str
    mode = parse_mode(mode)

    paths = resolve_node_paths_from_reference_layer(reference_layer)

    if not output_layer:
        raise RuntimeError("Output layer is required.")

    time_unit = om.MTime.kSeconds if use_seconds else om.MTime.uiUnit()

    if start_time is None:
        start_time = oma.MAnimControl.minTime().asUnits(time_unit)
    if end_time is None:
        end_time = oma.MAnimControl.maxTime().asUnits(time_unit)
    if reference_time is None:
        reference_time = start_time

    ensure_animation_layer(output_layer, replace_output)

    times = build_times(float(start_time), float(end_time), float(time_step))

    for path in paths:
        source_samples = []
        for t in times:
            source_samples.append(
                sample_transform(path, source_layer, t, time_unit)
            )

        reference_pose = None
        if use_reference_pose and mode in ("subtract", "presubtract"):
            reference_pose = sample_transform(
                path, reference_layer, reference_time, time_unit
            )

        for i, t in enumerate(times):
            if mode in ("linear", "spline"):
                if len(times) > 1:
                    ratio = float(i) / float(len(times) - 1)
                else:
                    ratio = 1.0

                weight = spline_weight(ratio) if mode == "spline" else ratio

                reference = TransformSample()
                first = source_samples[0]
                last = source_samples[-1]

                reference.translation = (
                    first.translation * (1.0 - weight) +
                    last.translation * weight
                )
                reference.rotation = quaternion_slerp(
                    first.rotation,
                    last.rotation,
                    weight
                )

                output = compute_subtract(source_samples[i], reference)

            else:
                if use_reference_pose:
                    reference = reference_pose
                else:
                    reference = sample_transform(
                        path, reference_layer, t, time_unit
                    )

                if mode == "presubtract":
                    output = compute_pre_subtract(source_samples[i], reference)
                else:
                    output = compute_subtract(source_samples[i], reference)

            write_transform_sample(path, output_layer, t, time_unit, output)

    cmds.inViewMessage(
        amg='Created delta animation layer: <hl>{0}</hl>'.format(output_layer),
        pos='topCenter',
        fade=True
    )

    return output_layer



# -------------------------
# PySide2 UI
# -------------------------

WINDOW_OBJECT_NAME = "deltaAnimLayerPySide2UI"
_delta_anim_layer_dialog = None


def maya_main_window():
    # type: () -> QtWidgets.QWidget
    """Return Maya's main window as a Qt widget."""
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def list_anim_layers():
    # type: () -> list
    """Return all animation layers plus a None entry for final-scene sampling."""
    layers = cmds.ls(type="animLayer") or []
    return ["None"] + layers


class DeltaAnimLayerDialog(QtWidgets.QDialog):
    """Resizable PySide2 dialog for creating delta animation layers."""

    def __init__(self, parent=None):
        # type: (QtWidgets.QWidget) -> None
        super(DeltaAnimLayerDialog, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Delta Animation Layer - OpenMaya API 1.0")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(420, 440)
        self.resize(560, 520)

        self.mode_combo = None  # type: QtWidgets.QComboBox
        self.reference_layer_combo = None  # type: QtWidgets.QComboBox
        self.source_layer_combo = None  # type: QtWidgets.QComboBox
        self.output_layer_edit = None  # type: QtWidgets.QLineEdit
        self.start_spin = None  # type: QtWidgets.QDoubleSpinBox
        self.end_spin = None  # type: QtWidgets.QDoubleSpinBox
        self.step_spin = None  # type: QtWidgets.QDoubleSpinBox
        self.reference_time_spin = None  # type: QtWidgets.QDoubleSpinBox
        self.use_reference_pose_check = None  # type: QtWidgets.QCheckBox
        self.use_seconds_check = None  # type: QtWidgets.QCheckBox
        self.replace_output_check = None  # type: QtWidgets.QCheckBox
        self.status_label = None  # type: QtWidgets.QLabel

        self._build_ui()
        self.refresh_layer_menus()
        self._load_default_time_range()

    def _build_ui(self):
        # type: () -> None
        """Build the resizable UI with Qt layouts."""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        intro = QtWidgets.QLabel("Generate a delta animation layer for transform attributes registered on the Reference Layer.")
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

        self.start_spin = self._new_time_spin()
        self.end_spin = self._new_time_spin()
        self.step_spin = self._new_time_spin(minimum=0.001, default_value=1.0)
        self.reference_time_spin = self._new_time_spin()

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

    def _new_time_spin(self, minimum=-1000000.0, maximum=1000000.0, default_value=0.0):
        # type: (float, float, float) -> QtWidgets.QDoubleSpinBox
        """Create a numeric spin box that expands horizontally."""
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(4)
        spin.setSingleStep(1.0)
        spin.setValue(default_value)
        spin.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        return spin

    def _load_default_time_range(self):
        # type: () -> None
        """Initialize the time fields from Maya's playback range."""
        time_unit = om.MTime.uiUnit()
        start = oma.MAnimControl.minTime().asUnits(time_unit)
        end = oma.MAnimControl.maxTime().asUnits(time_unit)
        self.start_spin.setValue(start)
        self.end_spin.setValue(end)
        self.reference_time_spin.setValue(start)

    def refresh_layer_menus(self):
        # type: () -> None
        """Refresh layer combo boxes while preserving the current choices."""
        layers = list_anim_layers()
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

    def _selected_layer_name(self, combo):
        # type: (QtWidgets.QComboBox) -> str
        """Convert the UI None layer choice to the internal empty string."""
        layer_name = combo.currentText()
        if layer_name == "None":
            return ""
        return layer_name

    def run_delta(self):
        # type: () -> None
        """Read UI values and execute the delta animation layer operation."""
        try:
            output_layer = delta_animation_layer(
                mode=self.mode_combo.currentText(),
                reference_layer=self._selected_layer_name(self.reference_layer_combo),
                source_layer=self._selected_layer_name(self.source_layer_combo),
                output_layer=self.output_layer_edit.text().strip(),
                start_time=self.start_spin.value(),
                end_time=self.end_spin.value(),
                time_step=self.step_spin.value(),
                reference_time=self.reference_time_spin.value(),
                use_reference_pose=self.use_reference_pose_check.isChecked(),
                use_seconds=self.use_seconds_check.isChecked(),
                replace_output=self.replace_output_check.isChecked()
            )
            self.status_label.setText("Created delta animation layer: {0}".format(output_layer))
        except Exception as exc:
            message = str(exc)
            self.status_label.setText(message)
            cmds.warning(message)
            QtWidgets.QMessageBox.critical(self, "Delta Animation Layer Error", message)


def _delete_existing_dialog():
    # type: () -> None
    """Close and delete the previous dialog instance if it exists."""
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


def show_delta_anim_layer_ui():
    # type: () -> DeltaAnimLayerDialog
    """Show the resizable PySide2 UI."""
    global _delta_anim_layer_dialog

    _delete_existing_dialog()
    _delta_anim_layer_dialog = DeltaAnimLayerDialog(parent=maya_main_window())
    _delta_anim_layer_dialog.show()
    _delta_anim_layer_dialog.raise_()
    _delta_anim_layer_dialog.activateWindow()
    return _delta_anim_layer_dialog


if __name__ == "__main__":
    show_delta_anim_layer_ui()
