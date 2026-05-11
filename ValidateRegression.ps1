param(
    [string]$MayapyPath = "C:\Program Files\Autodesk\Maya2022\bin\mayapy.exe"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$validationScript = Join-Path $scriptRoot "script_tools\ValidateDeltaAnimationLayerPythonRegression.py"

if (-not (Test-Path -LiteralPath $MayapyPath)) {
    throw "mayapy executable not found: $MayapyPath"
}

$env:MAYA_SKIP_USERSETUP_PY = "1"
$env:MAYA_SKIP_USERSETUP = "1"
& $MayapyPath $validationScript "--repo" $scriptRoot
exit $LASTEXITCODE
