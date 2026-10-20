param(
    [string]$CompilerBin='D:/Keil_v5/ARM/ARMCC/bin',
    [string]$DeviceInclude='D:/Keil_v5/ARM/Pack/Keil/STM32F1xx_DFP/2.4.0/Device/Include',
    [string]$CmsisInclude='D:/Keil_v5/ARM/Pack/ARM/CMSIS/4.5.0/CMSIS/Include'
)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
if ($taskRoot -match '[^\x00-\x7F]') { throw 'ASCII project path required' }
$taskOut=Join-Path $taskRoot ('output/'+(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $taskOut | Out-Null
function Invoke-Compiler([string]$name,[string[]]$arguments) {
    $taskExe=Join-Path $CompilerBin ($name+'.exe')
    & $taskExe @arguments 2>&1 | Tee-Object -FilePath (Join-Path $taskOut ($name+'.log')) -Append
    if ($LASTEXITCODE -ne 0) { throw "$name failed: $LASTEXITCODE" }
}
# The official CMSIS header uses ARM inline assembler extensions, so --strict
# is not compatible with this vendor header. Source remains C99.
$taskOptions=@('--cpu=Cortex-M3','--thumb','--c99','--apcs=interwork','-O2','--library_type=microlib','-DSTM32F10X_MD',"-I$DeviceInclude","-I$CmsisInclude",'-Isrc')
Push-Location $taskRoot
try {
    foreach($taskName in @('main','workload')) {
        Invoke-Compiler 'armcc' ($taskOptions+@('-c',"src/$taskName.c",'-o',"$taskOut/$taskName.o"))
    }
    Invoke-Compiler 'armasm' @('--cpu=Cortex-M3','--pd','__MICROLIB SETA 1','src/startup.s','-o',"$taskOut/startup.o")
    Invoke-Compiler 'armlink' @('--cpu=Cortex-M3','--library_type=microlib','--scatter=src/stm32f103c8.sct','--entry=Reset_Handler','--map','--info=sizes',"--list=$taskOut/stimulus.map","$taskOut/startup.o","$taskOut/main.o","$taskOut/workload.o",'-o',"$taskOut/stimulus.axf")
    Invoke-Compiler 'fromelf' @('--bin',"--output=$taskOut/stimulus.bin","$taskOut/stimulus.axf")
    Invoke-Compiler 'fromelf' @('--i32combined',"--output=$taskOut/stimulus.hex","$taskOut/stimulus.axf")
    $taskSources=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'src'),(Join-Path $taskRoot 'tests') -File
    $taskHashes=@{}
    foreach($taskFile in ($taskSources + (Get-Item -LiteralPath $PSCommandPath))) { $taskHashes[$taskFile.FullName]=(Get-FileHash -LiteralPath $taskFile.FullName -Algorithm SHA256).Hash }
    $taskHeaders=@{}
    foreach($taskHeader in @("$DeviceInclude/stm32f10x.h","$DeviceInclude/system_stm32f10x.h","$CmsisInclude/core_cm3.h","$CmsisInclude/core_cmInstr.h","$CmsisInclude/core_cmFunc.h","$CmsisInclude/cmsis_armcc.h")) { $taskHeaders[$taskHeader]=(Get-FileHash -LiteralPath $taskHeader -Algorithm SHA256).Hash }
    $taskImage=Get-Item -LiteralPath "$taskOut/stimulus.bin"
    if ($taskImage.Length -gt 65536) { throw 'C8 64 KiB limit exceeded' }
    [ordered]@{schema=1;built=$true;programmed=$false;hardware_verified=$false;target='STM32F103C8T6';flash_bytes=65536;ram_bytes=20480;clock='uncalibrated HSI nominal 8 MHz';spi_bus_hz=250000;spi_gpio_output_speed='2MHz';gpioa_crl_spi_mask='F0FF0000';gpioa_crl_spi_expected='A0A20000';sources=$taskHashes;external_headers=$taskHeaders;binary_bytes=$taskImage.Length;binary_sha256=(Get-FileHash -LiteralPath $taskImage.FullName -Algorithm SHA256).Hash} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$taskOut/build_manifest.json" -Encoding UTF8
    Write-Output "STM32_BUILD_PASS=$taskOut"
} finally { Pop-Location }
