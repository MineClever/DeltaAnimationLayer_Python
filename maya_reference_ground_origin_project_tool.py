# type: ignore
"""
Maya Reference Ground Origin Projection Tool
OpenMaya API 1.0 core implementation.

Usage:
    1. Select one transform reference target.
    2. Run: show_reference_ground_origin_project_tool()
    3. Pick the selected target, then apply current frame or bake a frame range.

Behavior:
    - Projects the target world position vertically onto Maya's ground plane.
    - Computes the delta from that projected ground point to world origin.
    - Optionally applies that delta so the target is moved above world origin.
    - Optionally preserves horizontal-plane rotation correction by keeping only
      the target's twist around Maya's current up axis.
"""

from __future__ import annotations

import math
from typing import Tuple

import maya.cmds as cmds
import maya.OpenMaya as om
import maya.OpenMayaUI as omui

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance


_reference_ground_origin_dialog = None


class ReferenceGroundOriginProjector(object):
    """Moves a transform by its ground projection delta to world origin."""

    def __init__(self, target_transform: str) -> None:
        self.target_transform = target_transform
        self.target_path = self._get_dag_path(target_transform)

    @staticmethod
    def _get_dag_path(node_name: str) -> om.MDagPath:
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
    def get_maya_up_axis_vector() -> om.MVector:
        axis = cmds.upAxis(query=True, axis=True)
        if axis == "x":
            return om.MVector(1.0, 0.0, 0.0)
        if axis == "z":
            return om.MVector(0.0, 0.0, 1.0)
        return om.MVector(0.0, 1.0, 0.0)

    @staticmethod
    def get_maya_up_axis_index() -> int:
        axis = cmds.upAxis(query=True, axis=True)
        if axis == "x":
            return 0
        if axis == "z":
            return 2
        return 1

    @staticmethod
    def _get_world_matrix(dag_path: om.MDagPath) -> om.MMatrix:
        return dag_path.inclusiveMatrix()

    @staticmethod
    def _get_world_translation(dag_path: om.MDagPath) -> om.MVector:
        matrix = dag_path.inclusiveMatrix()
        return om.MVector(matrix(3, 0), matrix(3, 1), matrix(3, 2))

    @staticmethod
    def _get_parent_inverse_matrix(dag_path: om.MDagPath) -> om.MMatrix:
        parent_path = om.MDagPath(dag_path)
        try:
            parent_path.pop()
            return parent_path.inclusiveMatrixInverse()
        except RuntimeError:
            return om.MMatrix()

    @staticmethod
    def _project_point_to_ground(point: om.MVector, ground_normal: om.MVector) -> om.MVector:
        normal = om.MVector(ground_normal)
        normal.normalize()
        return point - normal * (point * normal)

    @staticmethod
    def _set_matrix_row_vector(matrix: om.MMatrix, row: int, vector: om.MVector) -> None:
        om.MScriptUtil.setDoubleArray(matrix[row], 0, vector.x)
        om.MScriptUtil.setDoubleArray(matrix[row], 1, vector.y)
        om.MScriptUtil.setDoubleArray(matrix[row], 2, vector.z)

    @staticmethod
    def _build_matrix_with_new_translation(source_matrix: om.MMatrix, world_translation: om.MVector) -> om.MMatrix:
        result = om.MMatrix(source_matrix)
        om.MScriptUtil.setDoubleArray(result[3], 0, world_translation.x)
        om.MScriptUtil.setDoubleArray(result[3], 1, world_translation.y)
        om.MScriptUtil.setDoubleArray(result[3], 2, world_translation.z)
        return result

    def _keep_only_up_axis_twist(self, matrix: om.MMatrix) -> om.MMatrix:
        up_index = self.get_maya_up_axis_index()
        up_axis = self.get_maya_up_axis_vector()
        up_axis.normalize()

        forward_index = 2 if up_index != 2 else 0
        rows = [
            om.MVector(matrix(0, 0), matrix(0, 1), matrix(0, 2)),
            om.MVector(matrix(1, 0), matrix(1, 1), matrix(1, 2)),
            om.MVector(matrix(2, 0), matrix(2, 1), matrix(2, 2)),
        ]
        row_lengths = [max(row.length(), 1.0e-8) for row in rows]

        forward = om.MVector(rows[forward_index])
        forward = self._project_point_to_ground(forward, up_axis)

        result = om.MMatrix(matrix)
        if forward.length() < 1.0e-8:
            return result

        forward.normalize()
        axes = [om.MVector(), om.MVector(), om.MVector()]
        axes[up_index] = up_axis
        axes[forward_index] = forward
        remaining_index = 3 - up_index - forward_index

        if remaining_index == 0:
            axes[remaining_index] = axes[1] ^ axes[2]
        elif remaining_index == 1:
            axes[remaining_index] = axes[2] ^ axes[0]
        else:
            axes[remaining_index] = axes[0] ^ axes[1]

        if axes[remaining_index].length() < 1.0e-8:
            return result
        axes[remaining_index].normalize()

        for row, axis in enumerate(axes):
            self._set_matrix_row_vector(result, row, axis * row_lengths[row])
        return result

    @staticmethod
    def _matrix_to_translation_rotation_scale(matrix: om.MMatrix) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]:
        transform_matrix = om.MTransformationMatrix(matrix)
        translation_vec = transform_matrix.translation(om.MSpace.kTransform)

        euler_rotation = transform_matrix.eulerRotation()
        rotation = (
            math.degrees(euler_rotation.x),
            math.degrees(euler_rotation.y),
            math.degrees(euler_rotation.z),
        )

        scale_util = om.MScriptUtil()
        scale_util.createFromList([1.0, 1.0, 1.0], 3)
        scale_ptr = scale_util.asDoublePtr()
        transform_matrix.getScale(scale_ptr, om.MSpace.kTransform)
        scale = (
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 0),
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 1),
            om.MScriptUtil.getDoubleArrayItem(scale_ptr, 2),
        )
        return (translation_vec.x, translation_vec.y, translation_vec.z), rotation, scale

    def compute_projected_world_matrix(
        self,
        apply_projection_delta: bool = True,
        preserve_horizontal_rotation_correction: bool = True,
    ) -> om.MMatrix:
        world_matrix = self._get_world_matrix(self.target_path)
        world_pos = self._get_world_translation(self.target_path)
        up_axis = self.get_maya_up_axis_vector()
        ground_projection = self._project_point_to_ground(world_pos, up_axis)

        projected_pos = world_pos
        if apply_projection_delta:
            projected_pos = world_pos - ground_projection

        result = self._build_matrix_with_new_translation(world_matrix, projected_pos)
        if preserve_horizontal_rotation_correction:
            result = self._keep_only_up_axis_twist(result)
        return result

    def apply_current_frame(
        self,
        apply_projection_delta: bool = True,
        preserve_horizontal_rotation_correction: bool = True,
        key_result: bool = False,
    ) -> None:
        world_matrix = self.compute_projected_world_matrix(
            apply_projection_delta,
            preserve_horizontal_rotation_correction,
        )
        local_matrix = world_matrix * self._get_parent_inverse_matrix(self.target_path)
        translation, rotation, scale = self._matrix_to_translation_rotation_scale(local_matrix)

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
        apply_projection_delta: bool = True,
        preserve_horizontal_rotation_correction: bool = True,
    ) -> None:
        if end_frame < start_frame:
            raise RuntimeError("End frame must be greater than or equal to start frame.")

        original_time = cmds.currentTime(query=True)
        cmds.undoInfo(openChunk=True)
        try:
            for frame in range(int(start_frame), int(end_frame) + 1):
                cmds.currentTime(frame, edit=True)
                self.apply_current_frame(
                    apply_projection_delta,
                    preserve_horizontal_rotation_correction,
                    key_result=True,
                )
        finally:
            cmds.currentTime(original_time, edit=True)
            cmds.undoInfo(closeChunk=True)

    def _set_transform_keys(self, frame: float) -> None:
        for attr in (
            "translateX", "translateY", "translateZ",
            "rotateX", "rotateY", "rotateZ",
            "scaleX", "scaleY", "scaleZ",
        ):
            cmds.setKeyframe(self.target_transform, attribute=attr, time=frame)


class ReferenceGroundOriginProjectDialog(QtWidgets.QDialog):
    """PySide2 UI for moving a selected reference target above world origin."""

    WINDOW_OBJECT_NAME = "referenceGroundOriginProjectToolUI"

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super(ReferenceGroundOriginProjectDialog, self).__init__(parent)
        self.setObjectName(self.WINDOW_OBJECT_NAME)
        self.setWindowTitle("Reference Ground Origin Projector")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)

        self.target_field = QtWidgets.QLineEdit()
        self.apply_projection_delta_check = QtWidgets.QCheckBox("Apply projected ground delta to origin")
        self.preserve_horizontal_rotation_check = QtWidgets.QCheckBox("Preserve horizontal-plane rotation correction")
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

        main_layout.addWidget(QtWidgets.QLabel("Move selected reference target above world origin from its ground projection."))

        input_group = QtWidgets.QGroupBox("Input")
        input_layout = QtWidgets.QFormLayout(input_group)
        input_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        input_layout.addRow("Reference Target", self._create_pick_row(self.target_field))
        main_layout.addWidget(input_group)

        options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(options_group)
        self.apply_projection_delta_check.setChecked(True)
        self.preserve_horizontal_rotation_check.setChecked(True)
        options_layout.addWidget(self.apply_projection_delta_check)
        options_layout.addWidget(self.preserve_horizontal_rotation_check)
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
        pick_button.clicked.connect(lambda checked=False, field=text_field: self.pick_from_selection(field))
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
        selection = cmds.ls(selection=True, long=True, type="transform") or []
        if not selection:
            raise RuntimeError("Please select one transform node.")
        return selection[0]

    def pick_from_selection(self, text_field: QtWidgets.QLineEdit) -> None:
        try:
            text_field.setText(self._get_single_selected_transform())
            self.status_label.setText("")
        except Exception as exc:
            self.show_error(str(exc))

    def build_projector(self) -> ReferenceGroundOriginProjector:
        target = self.target_field.text().strip()
        if not target:
            raise RuntimeError("Reference Target is required.")
        return ReferenceGroundOriginProjector(target)

    def show_error(self, message: str) -> None:
        self.status_label.setText(message)
        cmds.warning(message)
        QtWidgets.QMessageBox.critical(self, "Reference Ground Origin Projector Error", message)

    def set_start_from_timeline(self) -> None:
        self.start_field.setValue(int(cmds.playbackOptions(query=True, minTime=True)))
        self.status_label.setText("Start frame refreshed from timeline minimum.")

    def set_end_from_timeline(self) -> None:
        self.end_field.setValue(int(cmds.playbackOptions(query=True, maxTime=True)))
        self.status_label.setText("End frame refreshed from timeline maximum.")

    def apply_current_frame(self) -> None:
        try:
            projector = self.build_projector()
            cmds.undoInfo(openChunk=True)
            try:
                projector.apply_current_frame(
                    self.apply_projection_delta_check.isChecked(),
                    self.preserve_horizontal_rotation_check.isChecked(),
                    key_result=False,
                )
            finally:
                cmds.undoInfo(closeChunk=True)
            self.status_label.setText("Applied current frame.")
        except Exception as exc:
            self.show_error(str(exc))

    def bake_frame_range(self) -> None:
        try:
            projector = self.build_projector()
            projector.bake_frame_range(
                self.start_field.value(),
                self.end_field.value(),
                self.apply_projection_delta_check.isChecked(),
                self.preserve_horizontal_rotation_check.isChecked(),
            )
            self.status_label.setText("Baked frame range {0} to {1}.".format(
                self.start_field.value(),
                self.end_field.value(),
            ))
        except Exception as exc:
            self.show_error(str(exc))


def show_reference_ground_origin_project_tool() -> ReferenceGroundOriginProjectDialog:
    global _reference_ground_origin_dialog

    if _reference_ground_origin_dialog is not None:
        try:
            _reference_ground_origin_dialog.close()
            _reference_ground_origin_dialog.deleteLater()
        except RuntimeError:
            pass
        _reference_ground_origin_dialog = None

    for widget in QtWidgets.QApplication.topLevelWidgets():
        if widget.objectName() == ReferenceGroundOriginProjectDialog.WINDOW_OBJECT_NAME:
            widget.close()
            widget.deleteLater()

    _reference_ground_origin_dialog = ReferenceGroundOriginProjectDialog(
        parent=ReferenceGroundOriginProjectDialog.maya_main_window()
    )
    _reference_ground_origin_dialog.show()
    _reference_ground_origin_dialog.raise_()
    _reference_ground_origin_dialog.activateWindow()
    return _reference_ground_origin_dialog


if __name__ == "__main__":
    show_reference_ground_origin_project_tool()
