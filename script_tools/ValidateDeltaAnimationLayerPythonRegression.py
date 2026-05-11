import argparse
import importlib
import math
import os
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DeltaAnimationLayer Python regression tests.")
    parser.add_argument("--repo", required=True)
    return parser.parse_args()


def exit_without_maya_shutdown() -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
    os._exit(0)


def assert_close(actual: float, expected: float, label: str, tolerance: float = 1.0e-4) -> None:
    if not math.isfinite(actual):
        raise RuntimeError("{0}: value is not finite: {1!r}".format(label, actual))
    if abs(actual - expected) > tolerance:
        raise RuntimeError("{0}: expected {1}, got {2}".format(label, expected, actual))


def find_layer_curve(cmds, layer: str, node: str, attribute: str) -> str:
    plug = "{0}.{1}".format(node, attribute)
    curves = cmds.animLayer(layer, query=True, findCurveForPlug=plug)
    if isinstance(curves, str):
        curves = [curves]
    if not curves:
        raise RuntimeError("No animation curve found for {0} on layer {1}.".format(plug, layer))
    return curves[0]


def has_layer_curve(cmds, layer: str, node: str, attribute: str) -> bool:
    plug = "{0}.{1}".format(node, attribute)
    curves = cmds.animLayer(layer, query=True, findCurveForPlug=plug)
    if isinstance(curves, str):
        curves = [curves]
    return bool(curves)


def evaluate_layer_value(cmds, layer: str, node: str, attribute: str, time: float) -> float:
    curve = find_layer_curve(cmds, layer, node, attribute)
    values = cmds.keyframe(curve, query=True, eval=True, time=(time, time))
    if not values:
        raise RuntimeError("Could not evaluate {0} at time {1}.".format(curve, time))
    return float(values[0])


def create_keyed_transform(cmds, name: str) -> str:
    node = cmds.createNode("transform", name=name)
    samples = {
        1.0: 1.0,
        2.0: 2.0,
        3.0: 6.0,
        4.0: 9.0,
    }
    for time, value in samples.items():
        cmds.setKeyframe(node, attribute="translateX", time=time, value=value)
        cmds.setKeyframe(node, attribute="translateY", time=time, value=value * 0.5)
        cmds.setKeyframe(node, attribute="translateZ", time=time, value=-value)
        cmds.setKeyframe(node, attribute="rotateX", time=time, value=value * 2.0)
        cmds.setKeyframe(node, attribute="rotateY", time=time, value=-value)
        cmds.setKeyframe(node, attribute="rotateZ", time=time, value=value * 0.25)
    return node


def create_reference_layer(cmds, node: str, layer: str) -> str:
    reference_layer = cmds.animLayer(layer)
    cmds.select(node, replace=True)
    cmds.animLayer(reference_layer, edit=True, addSelectedObjects=True)
    return reference_layer


def run_python_tool(
    dal,
    mode: str,
    reference_layer: str,
    output_layer: str,
    start_time: float = 1.0,
    end_time: float = 4.0,
    time_step: float = 1.0,
    **kwargs
) -> None:
    runner = dal.DeltaAnimationLayer(
        mode=mode,
        reference_layer=reference_layer,
        output_layer=output_layer,
        start_time=start_time,
        end_time=end_time,
        time_step=time_step,
        replace_output=True,
        **kwargs
    )
    result = runner.execute()
    if result != output_layer:
        raise RuntimeError("Unexpected function result for {0}: {1!r}".format(mode, result))


def validate_reference_pose_modes(cmds, dal) -> None:
    for mode in ("subtract", "presubtract"):
        node = create_keyed_transform(cmds, "deltaPyRegression_{0}".format(mode))
        reference_layer = create_reference_layer(cmds, node, "deltaPyRegression_{0}_reference".format(mode))
        layer = "deltaPyRegression_{0}_layer".format(mode)
        run_python_tool(dal, mode, reference_layer, layer, use_reference_pose=True, reference_time=1)
        evaluate_layer_value(cmds, layer, node, "translateX", 1.0)


def validate_interpolated_modes(cmds, dal) -> None:
    cases = (
        ("linearDelta", -1.6666666667),
        ("splineDelta", -1.0740740741),
    )
    for mode, expected_at_sample_1 in cases:
        node = create_keyed_transform(cmds, "deltaPyRegression_{0}".format(mode))
        reference_layer = create_reference_layer(cmds, node, "deltaPyRegression_{0}_reference".format(mode))
        layer = "deltaPyRegression_{0}_layer".format(mode)
        run_python_tool(dal, mode, reference_layer, layer)
        evaluate_layer_value(cmds, layer, node, "translateX", 1.0)

        runner = dal.DeltaAnimationLayer(mode=mode)
        identity_rotation = dal.om.MQuaternion()
        source_samples = [
            (dal.om.MVector(1.0, 0.0, 0.0), identity_rotation),
            (dal.om.MVector(2.0, 0.0, 0.0), identity_rotation),
            (dal.om.MVector(6.0, 0.0, 0.0), identity_rotation),
            (dal.om.MVector(9.0, 0.0, 0.0), identity_rotation),
        ]
        output = runner.compute_interpolated_output(source_samples, 1)
        assert_close(
            output[0].x,
            expected_at_sample_1,
            "{0} translateX at sample-index interpolation".format(mode),
        )


def validate_source_and_reference_layer_nodes(cmds, dal) -> None:
    reference_node = create_keyed_transform(cmds, "deltaPyRegression_referenceOnlyNode")
    source_node = create_keyed_transform(cmds, "deltaPyRegression_sourceOnlyNode")
    reference_layer = create_reference_layer(cmds, reference_node, "deltaPyRegression_combined_reference")
    source_layer = create_reference_layer(cmds, source_node, "deltaPyRegression_combined_source")
    output_layer = "deltaPyRegression_combined_output"

    run_python_tool(
        dal,
        "subtract",
        reference_layer,
        output_layer,
        source_layer=source_layer,
    )

    evaluate_layer_value(cmds, output_layer, reference_node, "translateX", 1.0)
    evaluate_layer_value(cmds, output_layer, source_node, "translateX", 1.0)


def validate_ignored_channel_groups(cmds, dal) -> None:
    node = create_keyed_transform(cmds, "deltaPyRegression_ignoreChannels")
    reference_layer = create_reference_layer(cmds, node, "deltaPyRegression_ignoreChannels_reference")

    translate_output = "deltaPyRegression_ignoreTranslate_output"
    run_python_tool(dal, "subtract", reference_layer, translate_output, ignore_translate=True)
    if has_layer_curve(cmds, translate_output, node, "translateX"):
        raise RuntimeError("ignore_translate should not write translateX.")
    evaluate_layer_value(cmds, translate_output, node, "rotateX", 1.0)

    rotation_output = "deltaPyRegression_ignoreRotation_output"
    run_python_tool(dal, "subtract", reference_layer, rotation_output, ignore_rotation=True)
    evaluate_layer_value(cmds, rotation_output, node, "translateX", 1.0)
    if has_layer_curve(cmds, rotation_output, node, "rotateX"):
        raise RuntimeError("ignore_rotation should not write rotateX.")

    try:
        run_python_tool(
            dal,
            "subtract",
            reference_layer,
            "deltaPyRegression_ignoreAll_output",
            ignore_translate=True,
            ignore_rotation=True,
        )
    except RuntimeError:
        return

    raise RuntimeError("Ignoring translate and rotation together should fail.")


def main() -> int:
    args = parse_args()

    import maya.standalone

    maya.standalone.initialize(name="python")

    import maya.cmds as cmds

    sys.path.insert(0, args.repo)
    dal = importlib.import_module("delta_anim_layer_pyside2")

    cmds.file(new=True, force=True)
    validate_reference_pose_modes(cmds, dal)
    validate_interpolated_modes(cmds, dal)
    validate_source_and_reference_layer_nodes(cmds, dal)
    validate_ignored_channel_groups(cmds, dal)

    print("DeltaAnimationLayer Python regression validation passed.")
    exit_without_maya_shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
