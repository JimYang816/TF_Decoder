param(
    [string]$Python = 'python',
    [string]$OutputDirectory = '',
    [switch]$UseExistingEnvironment
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repo 'dist' }
if (-not $UseExistingEnvironment) {
    $buildEnv = Join-Path $repo '.build-venv'
    & $Python -m venv $buildEnv
    if ($LASTEXITCODE) { throw 'Cannot create build environment' }
    $Python = Join-Path $buildEnv 'Scripts/python.exe'
    & $Python -m pip install -r (Join-Path $repo 'requirements-build.txt')
    if ($LASTEXITCODE) { throw 'Cannot install pinned build dependencies' }
}
& $Python (Join-Path $PSScriptRoot 'build_portable.py') --output $OutputDirectory
if ($LASTEXITCODE) { throw 'Portable build failed' }
