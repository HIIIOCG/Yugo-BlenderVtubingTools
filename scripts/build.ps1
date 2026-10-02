param(
    [string]$BlenderPath,
    [string]$PythonPath,
    [string]$OutputDirectory = "dist"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$AddonRoot = Join-Path $RepositoryRoot 'addons\yugo'

if (-not $BlenderPath) {
    $Command = Get-Command blender -ErrorAction SilentlyContinue
    if ($Command) {
        $BlenderPath = $Command.Source
    }
}

if (-not $BlenderPath) {
    $StandardPath = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
    if (Test-Path -LiteralPath $StandardPath) {
        $BlenderPath = $StandardPath
    }
}

if (-not $BlenderPath -or -not (Test-Path -LiteralPath $BlenderPath)) {
    throw "Blender was not found. Pass -BlenderPath with the full blender executable path."
}

if (-not $PythonPath) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $PythonPath = $PythonCommand.Source
    }
}

if (-not $PythonPath) {
    $BlenderDirectory = Split-Path -Parent $BlenderPath
    $BlenderVersion = (& $BlenderPath --version | Select-Object -First 1) -replace '^Blender\s+', ''
    $BlenderSeries = ($BlenderVersion -split '\.')[0..1] -join '.'
    $BundledPython = Join-Path $BlenderDirectory "$BlenderSeries\python\bin\python.exe"
    if (Test-Path -LiteralPath $BundledPython) {
        $PythonPath = $BundledPython
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python was not found. Pass -PythonPath or install Python on PATH."
}

$OutputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory
} else {
    Join-Path $RepositoryRoot $OutputDirectory
}
New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null

Push-Location $RepositoryRoot
try {
    $ProductVersion = & $PythonPath scripts/check_version.py --print-version
    if ($LASTEXITCODE -ne 0) { throw "Release metadata validation failed." }
    & $BlenderPath --command extension validate $AddonRoot
    if ($LASTEXITCODE -ne 0) { throw "Blender source validation failed." }

    & $BlenderPath --command extension build --source-dir $AddonRoot --output-dir $OutputPath
    if ($LASTEXITCODE -ne 0) { throw "Blender extension build failed." }

    $PackagePath = Join-Path $OutputPath "yugo-$ProductVersion.zip"
    if (-not (Test-Path -LiteralPath $PackagePath)) { throw "Blender did not create the expected Yugo package." }
    $Package = Get-Item -LiteralPath $PackagePath
    if ($Package.Length -ge 50MB) {
        throw "The extension package is unexpectedly larger than 50 MB."
    }

    & $BlenderPath --command extension validate $Package.FullName
    if ($LASTEXITCODE -ne 0) { throw "Blender package validation failed." }

    Write-Output "Validated package: $($Package.FullName)"
}
finally {
    Pop-Location
}
