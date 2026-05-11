# DeltaAnimationLayer Python

Maya Python version of DeltaAnimationLayer. This is a plain Python tool, not a Maya plugin. Load or run the script in Maya, then call the Python function directly or open the PySide UI.

## Requirements

- Autodesk Maya 2022 or a compatible Maya version with Python 3
- PySide2 in Maya for the optional UI

## Files

- `delta_anim_layer_pyside2.py`: core OpenMaya API 1.0 implementation plus a PySide UI.
- `script_tools/ValidateDeltaAnimationLayerPythonRegression.py`: mayapy regression script for the plain Python workflow.

## Usage

In Maya Python:

```python
import sys

sys.path.append(r"D:\_Code_Here\Git\MayaTools\DeltaAnimationLayer\DeltaAnimationLayer_Python")
import delta_anim_layer_pyside2 as dal

dal.delta_animation_layer(
    mode="subtract",
    reference_layer="baseLayer",
    source_layer="",
    output_layer="deltaOutputLayer",
    start_time=1,
    end_time=24,
    time_step=1,
    replace_output=True,
)
```

To show the UI:

```python
dal.show_delta_anim_layer_ui()
```

## Behavior

The Python version follows the C++ and C# implementations:

- `reference_layer` is required.
- Input transform nodes are resolved from transform attributes registered on `reference_layer`.
- Selection is not used as an input fallback.
- `source_layer` may be empty, which samples the current evaluated scene value.
- Supported modes are `subtract`, `presubtract`, `linearDelta`, and `splineDelta`, with aliases matching the C++ and C# versions.
- Output keys are written to `output_layer`.

## Validation

Run from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\ValidateRegression.ps1
```

The validation script imports `delta_anim_layer_pyside2.py` directly in mayapy and calls `delta_animation_layer`; it does not use `loadPlugin`.
