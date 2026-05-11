# type: ignore
"""
Maya Root Motion Ground Projection Tool
OpenMaya API 1.0 core implementation.

Purpose:
    Input A: transform projection source/reference.
    Input B: transform output target.

    The tool projects A or B onto the world ground plane through origin (0, 0, 0),
    using Maya's current up axis as the ground normal. This is commonly used
    to generate Root Motion where vertical motion is removed while preserving
    horizontal motion and optionally yaw rotation.

Usage:
    1. Run this script in Maya's Script Editor or import it.
    2. Execute: show_root_motion_ground_project_tool()
    3. Pick Source A and Target B.
    4. Apply current frame or bake frame range.

Notes:
    - Uses maya.OpenMaya API 1.0 for MSelectionList, MDagPath, MFnTransform,
      MVector, MTransformationMatrix and matrix operations.
    - UI uses PySide2.
    - The ground plane is always through world origin.
    - Maya up axis can be Y or Z; X is also handled defensively.
"""

from __future__ import annotations

import math
from typing import Tuple

import maya.cmds as cmds
import maya.OpenMaya as om
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance


_root_motion_ground_project_dialog = None


class RootMotionGroundProjector(object):
    """Projects a target transform onto the Maya ground plane using OpenMaya API 1.0."""

    def __init__(self, source_transform: str, target_transform: str) -> None:
        self.source_transform = source_transform
        self.target_transform = target_transform
        self.source_path = self._get_dag_path(source_transform)
        self.target_path = self._get_dag_path(target_transform)

    @staticmethod
    def _get_dag_path(node_name: str) -> om.MDagPath:
        """Return the MDagPath for a transform node."""
        if not node_name or not cmds.objExists(node_name):
            raise RuntimeError("Node does not exist: {0}".format(node_name))

        selection = om.MSelectionList()
        selection.add(node_name)
        dag_path = om.MDagPath()
        selection.getDagPath(0, dag_path)

        if not dag_path.hasFn(om.MFn.kTransform):
            raise RuntimeError("Node is not a transform: {0}".format(node_name))

        return dag_path

    @staticmethod
    def _get_world_translation(dag_path: om.MDagPath) -> om.MVector:
        """Return world-space translation from an inclusive matrix."""
        matrix = dag_path.inclusiveMatrix()
        return om.MVector(matrix(3, 0), matrix(3, 1), matrix(3, 2))

    @staticmethod
    def _get_world_matrix(dag_path: om.MDagPath) -> om.MMatrix:
        """Return the inclusive world matrix for a DAG path."""
        return dag_path.inclusiveMatrix()

    @staticmethod
    def _get_parent_inverse_matrix(dag_path: om.MDagPath) -> om.MMatrix:
        """Return inverse world matrix of the parent transform, or identity for world parent."""
        parent_path = om.MDagPath(dag_path)
        try:
            parent_path.pop()
            return parent_path.inclusiveMatrixInverse()
        except RuntimeError:
            return om.MMatrix()

    @staticmethod
    def get_maya_up_axis_vector() -> om.MVector:
        """Return Maya's current up-axis as a world-space unit vector."""
        axis = cmds.upAxis(query=True, axis=True)
        if axis == "x":
            return om.MVector(1.0, 0.0, 0.0)
        if axis == "z":
            return om.MVector(0.0, 0.0, 1.0)
        return om.MVector(0.0, 1.0, 0.0)

    @staticmethod
    def get_maya_up_axis_index() -> int:
        """Return the local axis index matching Maya's current up-axis."""
        axis = cmds.upAxis(query=True, axis=True)
        if axis == "x":
            return 0
        if axis == "z":
            return 2
        return 1

    @staticmethod
    def _project_point_to_ground(point: om.MVector, ground_normal: om.MVector) -> om.MVector:
        """Project a point to the origin ground plane with the given normal."""
        normal = om.MVector(ground_normal)
        normal.normalize()
        distance = point * normal
        return point - (normal * distance)

    @staticmethod
    def _matrix_to_translation_rotation_scale(matrix: om.MMatrix) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        """Convert an MMatrix to local TRS values."""
        transform_matrix = om.MTransformationMatrix(matrix)

        translation_vec = transform_matrix.translation(om.MSpace.kTransform)
        translation = (translation_vec.x, translation_vec.y, translation_vec.z)

        rotation = [0.0, 0.0, 0.0]
        euler_rotation = transform_matrix.eulerRotation()
        rotation[0] = math.degrees(euler_rotation.x)
        rotation[1] = math.degrees(euler_rotation.y)
        rotation[2] = math.degrees(euler_rotation.z)

        scale_util = om.MScriptUtil()
        scale_util.createFromList([1.0, 1.0, 1.0], 3)
        scale_ptr = scale_util.asDoublePtr()
        transform_matrix.getScale(scale_ptr, om.MSpace.kTransform)
        scale = (
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 0),
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 1),
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 2),
        )

        return translation, (rotation[0], rotation[1], rotation[2]), scale

    @staticmethod
    def _build_matrix_with_new_translation(source_matrix: om.MMatrix, world_translation: om.MVector) -> om.MMatrix:
        """Copy source matrix orientation/scale and replace world-space translation."""
        result = om.MMatrix(source_matrix)
        om.MScriptUtil.setDoubleArray(result[3], 0, world_translation.x)
        om.MScriptUtil.setDoubleArray(result[3], 1, world_translation.y)
        om.MScriptUtil.setDoubleArray(result[3], 2, world_translation.z)
        return result

    @staticmethod
    def _set_matrix_row_vector(matrix: om.MMatrix, row: int, vector: om.MVector) -> None:
        om.MScriptUtil.setDoubleArray(matrix[row], 0, vector.x)
        om.MScriptUtil.setDoubleArray(matrix[row], 1, vector.y)
        om.MScriptUtil.setDoubleArray(matrix[row], 2, vector.z)

    def _align_matrix_up_axis_to_maya_up(self, matrix: om.MMatrix) -> om.MMatrix:
        """Rotate matrix basis so its local Maya up-axis points along Maya world up."""
        up_axis_index = self.get_maya_up_axis_index()
        axes = [
            om.MVector(matrix(0, 0), matrix(0, 1), matrix(0, 2)),
            om.MVector(matrix(1, 0), matrix(1, 1), matrix(1, 2)),
            om.MVector(matrix(2, 0), matrix(2, 1), matrix(2, 2)),
        ]

        current_up = om.MVector(axes[up_axis_index])
        if current_up.length() < 1.0e-8:
            return om.MMatrix(matrix)

        target_up = self.get_maya_up_axis_vector()
        current_up.normalize()
        target_up.normalize()
        rotation = current_up.rotateTo(target_up)

        result = om.MMatrix(matrix)
        for row, axis in enumerate(axes):
            self._set_matrix_row_vector(result, row, axis.rotateBy(rotation))
        return result

    def compute_projected_world_matrix(
        self,
        use_source_offset: bool = True,
        preserve_target_rotation: bool = True,
        align_target_up_to_maya_up: bool = False,
    ) -> om.MMatrix:
        """
        Compute the projected world matrix for target B.

        Args:
            use_source_offset:
                If True, target B is moved to source A's ground-projected world position.
                If False, target B itself is directly projected to the ground plane.

            preserve_target_rotation:
                If True, B's original world rotation/scale is preserved.
                If False, A's world rotation/scale is used.

            align_target_up_to_maya_up:
                If True, B's local Maya up-axis is aligned to Maya's world up-axis.
        """
        up_axis = self.get_maya_up_axis_vector()
        source_pos = self._get_world_translation(self.source_path)
        target_pos = self._get_world_translation(self.target_path)

        if use_source_offset:
            projected_pos = self._project_point_to_ground(source_pos, up_axis)
        else:
            projected_pos = self._project_point_to_ground(target_pos, up_axis)

        basis_matrix = self._get_world_matrix(self.target_path if preserve_target_rotation else self.source_path)
        projected_matrix = self._build_matrix_with_new_translation(basis_matrix, projected_pos)
        if align_target_up_to_maya_up:
            projected_matrix = self._align_matrix_up_axis_to_maya_up(projected_matrix)
        return projected_matrix

    def apply_current_frame(
        self,
        use_source_offset: bool = True,
        preserve_target_rotation: bool = True,
        align_target_up_to_maya_up: bool = False,
        clear_projected_rotation: bool = False,
        key_result: bool = False,
    ) -> None:
        """Apply the projected result to B at the current frame."""
        world_matrix = self.compute_projected_world_matrix(
            use_source_offset,
            preserve_target_rotation,
            align_target_up_to_maya_up,
        )
        parent_inverse = self._get_parent_inverse_matrix(self.target_path)
        local_matrix = world_matrix * parent_inverse
        translation, rotation, scale = self._matrix_to_translation_rotation_scale(local_matrix)
        if clear_projected_rotation:
            rotation = (0.0, 0.0, 0.0)

        cmds.setAttr(self.target_transform + ".translate", translation[0], translation[1], translation[2], type="double3")
        cmds.setAttr(self.target_transform + ".rotate", rotation[0], rotation[1], rotation[2], type="double3")
        cmds.setAttr(self.target_transform + ".scale", scale[0], scale[1], scale[2], type="double3")

        if key_result:
            current_time = cmds.currentTime(query=True)
            self._set_transform_keys(current_time)

    def bake_frame_range(
        self,
        start_frame: int,
        end_frame: int,
        use_source_offset: bool = True,
        preserve_target_rotation: bool = True,
        align_target_up_to_maya_up: bool = False,
        clear_projected_rotation: bool = False,
    ) -> None:
        """Bake projected root motion to B over an inclusive frame range."""
        if end_frame < start_frame:
            raise RuntimeError("End frame must be greater than or equal to start frame.")

        original_time = cmds.currentTime(query=True)
        cmds.undoInfo(openChunk=True)
        try:
            for frame in range(int(start_frame), int(end_frame) + 1):
                cmds.currentTime(frame, edit=True)
                self.apply_current_frame(
                    use_source_offset,
                    preserve_target_rotation,
                    align_target_up_to_maya_up,
                    clear_projected_rotation,
                    key_result=True,
                )
        finally:
            cmds.currentTime(original_time, edit=True)
            cmds.undoInfo(closeChunk=True)

    def _set_transform_keys(self, frame: float) -> None:
        """Set translate/rotate/scale keys on the target transform."""
        attrs = [
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
            "scaleX", "scaleY", "scaleZ",
        ]
        for attr in attrs:
            cmds.setKeyframe(self.target_transform, attribute=attr, time=frame)


class RootMotionGroundProjectDialog(QtWidgets.QDialog):
    """PySide2 UI for the root motion ground projection tool."""

    WINDOW_OBJECT_NAME = "rootMotionGroundProjectToolUI"

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super(RootMotionGroundProjectDialog, self).__init__(parent)
        self.setObjectName(self.WINDOW_OBJECT_NAME)
        self.setWindowTitle("Root Motion Ground Projector")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)

        self.source_field = QtWidgets.QLineEdit()
        self.target_field = QtWidgets.QLineEdit()
        self.use_source_offset_check = QtWidgets.QCheckBox("Use A ground projection as B position")
        self.preserve_target_rotation_check = QtWidgets.QCheckBox("Preserve B rotation and scale")
        self.align_target_up_check = QtWidgets.QCheckBox("Use Maya Up axis as B up axis")
        self.clear_projected_rotation_check = QtWidgets.QCheckBox("Clear B rotation after projection")
        self.start_field = QtWidgets.QSpinBox()
        self.end_field = QtWidgets.QSpinBox()
        self.status_label = QtWidgets.QLabel("")

        self.build_ui()

    @staticmethod
    def maya_main_window():  # type: ignore[no-untyped-def]
        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)

    def build_ui(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        intro = QtWidgets.QLabel("Ground projection for Root Motion")
        main_layout.addWidget(intro)

        input_group = QtWidgets.QGroupBox("Inputs")
        input_layout = QtWidgets.QFormLayout(input_group)
        input_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        input_layout.addRow("Source Joint", self._create_pick_row(self.source_field))
        input_layout.addRow("Motion Root", self._create_pick_row(self.target_field))
        main_layout.addWidget(input_group)

        options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        self.use_source_offset_check.setChecked(True)
        self.use_source_offset_check.setToolTip(
            "When enabled, B moves to A projected onto the ground plane. Disable to directly project B."
        )
        self.preserve_target_rotation_check.setChecked(True)
        self.preserve_target_rotation_check.setToolTip(
            "When enabled, only B translation is ground-projected."
        )
        self.align_target_up_check.setToolTip(
            "When enabled, B's local Maya up-axis is aligned to Maya's world up-axis."
        )
        self.clear_projected_rotation_check.setToolTip(
            "When enabled, B's rotate channels are set to 0 after projection."
        )
        options_layout.addWidget(self.use_source_offset_check)
        options_layout.addWidget(self.preserve_target_rotation_check)
        options_layout.addWidget(self.align_target_up_check)
        options_layout.addWidget(self.clear_projected_rotation_check)
        main_layout.addWidget(options_group)

        frame_group = QtWidgets.QGroupBox("Frame Range")
        frame_layout = QtWidgets.QFormLayout(frame_group)
        for spin in (self.start_field, self.end_field):
            spin.setRange(-1000000, 1000000)
            spin.setSingleStep(1)
        self.start_field.setValue(int(cmds.playbackOptions(query=True, minTime=True)))
        self.end_field.setValue(int(cmds.playbackOptions(query=True, maxTime=True)))
        frame_layout.addRow("Start", self._create_frame_row(self.start_field, self.set_start_from_timeline))
        frame_layout.addRow("End", self._create_frame_row(self.end_field, self.set_end_from_timeline))
        main_layout.addWidget(frame_group)

        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        button_row = QtWidgets.QHBoxLayout()
        apply_button = QtWidgets.QPushButton("Apply Current Frame")
        apply_button.setMinimumHeight(34)
        apply_button.clicked.connect(self.apply_current_frame)
        bake_button = QtWidgets.QPushButton("Bake Frame Range")
        bake_button.setMinimumHeight(34)
        bake_button.clicked.connect(self.bake_frame_range)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(apply_button)
        button_row.addWidget(bake_button)
        button_row.addWidget(close_button)
        main_layout.addLayout(button_row)

    def _create_pick_row(self, text_field: QtWidgets.QLineEdit) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        pick_button = QtWidgets.QPushButton("Pick")
        pick_button.clicked.connect(lambda checked=False, field=text_field: self._pick_from_selection(field))
        layout.addWidget(text_field, 1)
        layout.addWidget(pick_button)
        return row

    def _create_frame_row(self, spin_field: QtWidgets.QSpinBox, command_callback) -> QtWidgets.QWidget:  # type: ignore[no-untyped-def]
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        button = QtWidgets.QPushButton("Get")
        button.clicked.connect(command_callback)
        layout.addWidget(spin_field, 1)
        layout.addWidget(button)
        return row

    @staticmethod
    def _get_single_selected_transform() -> str:
        """Return the first selected transform node."""
        selection = cmds.ls(selection=True, long=True, type="transform") or []
        if not selection:
            raise RuntimeError("Please select one transform node.")
        return selection[0]

    def _pick_from_selection(self, text_field: QtWidgets.QLineEdit) -> None:
        """Fill a text field from selection."""
        try:
            text_field.setText(self._get_single_selected_transform())
            self.status_label.setText("")
        except Exception as exc:
            self._show_error(str(exc))

    def _build_projector(self) -> RootMotionGroundProjector:
        """Build a projector from UI fields."""
        source = self.source_field.text().strip()
        target = self.target_field.text().strip()
        if not source or not target:
            raise RuntimeError("Both Source A and Target B are required.")
        if source == target:
            raise RuntimeError("Source A and Target B must be different transforms.")
        return RootMotionGroundProjector(source, target)

    def _get_options(self) -> Tuple[bool, bool, bool, bool]:
        """Return UI option states."""
        return (
            self.use_source_offset_check.isChecked(),
            self.preserve_target_rotation_check.isChecked(),
            self.align_target_up_check.isChecked(),
            self.clear_projected_rotation_check.isChecked(),
        )

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        cmds.warning(message)
        QtWidgets.QMessageBox.critical(self, "Root Motion Ground Projector Error", message)

    def set_start_from_timeline(self) -> None:
        self.start_field.setValue(int(cmds.playbackOptions(query=True, minTime=True)))
        self.status_label.setText("Start frame refreshed from timeline minimum.")

    def set_end_from_timeline(self) -> None:
        self.end_field.setValue(int(cmds.playbackOptions(query=True, maxTime=True)))
        self.status_label.setText("End frame refreshed from timeline maximum.")

    def apply_current_frame(self) -> None:
        """Apply projection at the current frame from UI."""
        try:
            projector = self._build_projector()
            (
                use_source_offset,
                preserve_target_rotation,
                align_target_up_to_maya_up,
                clear_projected_rotation,
            ) = self._get_options()
            cmds.undoInfo(openChunk=True)
            try:
                projector.apply_current_frame(
                    use_source_offset,
                    preserve_target_rotation,
                    align_target_up_to_maya_up,
                    clear_projected_rotation,
                    key_result=False,
                )
            finally:
                cmds.undoInfo(closeChunk=True)
            self.status_label.setText("Applied current frame.")
        except Exception as exc:
            self._show_error(str(exc))

    def bake_frame_range(self) -> None:
        """Bake projection over UI frame range."""
        try:
            projector = self._build_projector()
            (
                use_source_offset,
                preserve_target_rotation,
                align_target_up_to_maya_up,
                clear_projected_rotation,
            ) = self._get_options()
            start_frame = self.start_field.value()
            end_frame = self.end_field.value()
            projector.bake_frame_range(
                start_frame,
                end_frame,
                use_source_offset,
                preserve_target_rotation,
                align_target_up_to_maya_up,
                clear_projected_rotation,
            )
            self.status_label.setText("Baked frame range {0} to {1}.".format(start_frame, end_frame))
        except Exception as exc:
            self._show_error(str(exc))


def show_root_motion_ground_project_tool() -> RootMotionGroundProjectDialog:
    """Show the Root Motion Ground Projector UI."""
    global _root_motion_ground_project_dialog

    if _root_motion_ground_project_dialog is not None:
        try:
            _root_motion_ground_project_dialog.close()
            _root_motion_ground_project_dialog.deleteLater()
        except RuntimeError:
            pass
        _root_motion_ground_project_dialog = None

    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == RootMotionGroundProjectDialog.WINDOW_OBJECT_NAME:
            widget.close()
            widget.deleteLater()

    _root_motion_ground_project_dialog = RootMotionGroundProjectDialog(
        parent=RootMotionGroundProjectDialog.maya_main_window()
    )
    _root_motion_ground_project_dialog.show()
    _root_motion_ground_project_dialog.raise_()
    _root_motion_ground_project_dialog.activateWindow()
    return _root_motion_ground_project_dialog


if __name__ == "__main__":
    show_root_motion_ground_project_tool()
