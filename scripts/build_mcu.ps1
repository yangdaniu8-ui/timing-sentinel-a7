param(
    [Parameter(Mandatory=$true)][string]$CompilerBin,
    [Parameter(Mandatory=$true)][string]$DeviceInclude,
    [Parameter(Mandatory=$true)][string]$CmsisInclude
)
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot '../mcu/build.ps1') -CompilerBin $CompilerBin -DeviceInclude $DeviceInclude -CmsisInclude $CmsisInclude
if ($LASTEXITCODE -ne 0) {throw 'MCU build failed'}
