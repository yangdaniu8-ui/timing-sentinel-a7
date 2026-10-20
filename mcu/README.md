# STM32F103C8T6 FIR / IRQ workload

Source: src/main.c, workload.c, workload.h, startup.s and stm32f103c8.sct.
The native C checker is tests/native.c. Hardware sources and build script are unchanged.
Use ../scripts/build_mcu.ps1 with explicit CompilerBin, DeviceInclude and CmsisInclude parameters.
The build writes a new output directory and never programs a board. Then run scripts/qualify.py
with --build, --gcc and --fromelf. See ../README.md for commands and known limitations.
ARMCC 5.06, STM32F1xx_DFP 2.4.0 and CMSIS 4.5.0 must be installed separately under valid licenses.
The build script's D:/Keil_v5 defaults are examples only; the wrapper always requires explicit paths.
