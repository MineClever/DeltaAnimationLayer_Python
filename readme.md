# DeltaAnimationLayer Python

Maya Python version of DeltaAnimationLayer. This is a plain Python tool, not a Maya plugin. Load or run the script in Maya, then create the core class directly or open the PySide UI.

The plugin extracts the practical delta / predelta math used by the CSGO into a small independent script.

## Requirements

- Autodesk Maya 2022 or a compatible Maya version with Python 3
- PySide2 in Maya for the optional UI

## Files

- `delta_anim_layer_pyside2.py`: core OpenMaya API 1.0 implementation plus a PySide UI.
- `inverse_anim_layer_pyside2.py`: OpenMaya API 1.0 + PySide2 tool that compares one animation layer's muted and unmuted channel result, then writes an additive inverse layer.
- `script_tools/ValidateDeltaAnimationLayerPythonRegression.py`: mayapy regression script for the plain Python workflow.

## Usage

In Maya Python:

```python
import sys

sys.path.append("./DeltaAnimationLayer_Python")
import delta_anim_layer_pyside2 as dal

runner = dal.DeltaAnimationLayer(
    mode="subtract",
    reference_layer="baseLayer",
    source_layer="",
    output_layer="deltaOutputLayer",
    start_time=1,
    end_time=24,
    time_step=1,
    replace_output=True,
)
runner.execute()
```

To show the UI:

```python
dal.show_delta_anim_layer_ui()
```

To bake an inverse layer for the selected Maya animation layer:

```python
import sys

sys.path.append("./DeltaAnimationLayer_Python")
import inverse_anim_layer_pyside2 as ial

runner = ial.AnimLayerInverseBaker(
    source_layer="sourceLayer",
    output_layer="sourceLayer_inverse",
    start_time=1,
    end_time=24,
    time_step=1,
    replace_output=True,
)
runner.execute()
```

To show the inverse-layer UI:

```python
ial.show_inverse_anim_layer_ui()
```

## Behavior

The Python version separates the core implementation and UI:

- `DeltaAnimationLayer`: core implementation class.
- `DeltaAnimLayerDialog`: PySide UI class that delegates to `DeltaAnimationLayer`.
- Helper logic such as mode parsing, layer sampling, quaternion math, output key writing, and time range construction is encapsulated by `DeltaAnimationLayer`.

The behavior follows the C++ and C# implementations:

- `reference_layer` is required.
- Input transform nodes are resolved from transform attributes registered on `reference_layer` and non-empty `source_layer`.
- Selection is not used as an input fallback.
- `source_layer` may be empty, which samples the current evaluated scene value.
- Supported modes are `subtract`, `presubtract`, `linearDelta`, and `splineDelta`, with aliases matching the C++ and C# versions.
- Output keys are written to `output_layer`.

The inverse-layer tool is separate from `DeltaAnimationLayer`. It forces the source animation layer on and off for each sampled frame and reads the evaluated scalar plugs registered on that layer through OpenMaya API 1.0. The output layer is always additive and is moved above the source layer. For both additive and override source layers, the tool computes `muted_value - unmuted_value`, then passes `unmuted_value + delta` to Maya's `setKeyframe -animLayer`; Maya then solves the additive layer curve for the current layer stack. Constraints, driven keys, or other complex dependency graph behavior may still only cancel approximately.

## Validation

Run from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\ValidateRegression.ps1
```

The validation script imports `delta_anim_layer_pyside2.py` directly in mayapy and instantiates `DeltaAnimationLayer`; it does not use `loadPlugin`.
