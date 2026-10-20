param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Out
)
$ErrorActionPreference='Stop'
$bundleRoot=Split-Path -Parent $PSScriptRoot
$fixtures=Join-Path $bundleRoot 'fixtures'
& $Python (Join-Path $bundleRoot 'host/scripts/build_spi_release.py') --out $Out --experiment (Join-Path $fixtures 'hardware_six/experiment.json') --workload-records (Join-Path $fixtures 'usr43/narrow_load0_job0012.tscap') (Join-Path $fixtures 'usr43/narrow_load1_job0011.tscap') (Join-Path $fixtures 'usr43/narrow_load2_job0009.tscap') (Join-Path $fixtures 'usr43/wide_load2_job0309.tscap')
if ($LASTEXITCODE -ne 0) {throw 'Host build/self-test failed; retain logs, do not publish'}
