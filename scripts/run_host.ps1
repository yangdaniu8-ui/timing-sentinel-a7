param([Parameter(Mandatory=$true)][string]$Python,[string]$Record)
$ErrorActionPreference='Stop'
$bundleRoot=Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $bundleRoot 'host')
try {
    if ($Record) { & $Python -m mvp5 --open $Record } else { & $Python -m mvp5 }
    if ($LASTEXITCODE -ne 0) {throw 'Host exited with an error; retain diagnostics'}
} finally {Pop-Location}
