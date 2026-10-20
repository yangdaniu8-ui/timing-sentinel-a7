# 源码重建与测试

两板源码及参考镜像沿用已验证的USR43与STM32 FIR/IRQ组合。本次发布只整理源码、说明、照片和界面文字，不编程或擦除任何设备。所有构建脚本均要求显式执行，不自动连接或烧录开发板。

## 上位机

按仓库README建立Python 3.13虚拟环境。完整离线测试和源码清单检查：

```powershell
.venv/Scripts/python.exe scripts/test_host.py --out out/host_tests
.venv/Scripts/python.exe scripts/verify_package.py
```

重建免安装EXE，输出必须是新的ASCII路径：

```powershell
pwsh -NoProfile -File scripts/build_host.ps1 -Python "$PWD/.venv/Scripts/python.exe" -Out D:/fpga_projects/ts_host_build_01
```

builder保留实际冻结EXE自测门禁。构建或自测失败时检查日志，不将未通过产物称作稳定运行包。源码的路径迁移仅调整历史fixture位置和构建helper，不删除测试断言。

## FPGA

已验证工具为Vivado 2026.1，器件xc7a35tfgg484-2，top为mvp5_top。创建新的空英文构建目录，替换下列源码路径为本机实际位置：

```powershell
New-Item -ItemType Directory D:/fpga_projects/ts_fpga_build_01
Set-Location D:/fpga_projects/ts_fpga_build_01
vivado -mode batch -source D:/fpga_projects/timing-sentinel-a7/fpga/Scripts/validate.tcl -tclargs project
```

`project`重建工程，`regression`运行自检，`full`执行自检、综合、实现和bit生成。入口恢复项目专用设置；不能只凭文件存在宣称完成上板验收。无自定义IP/BD，仿真向量位于fpga/Sources/Data。正式镜像不含ILA，片内观察是另行构建的调试实验。

## STM32

需合法安装ARMCC 5.06、STM32F1xx_DFP 2.4.0、CMSIS 4.5.0，并使用本机实际路径：

```powershell
pwsh -NoProfile -File scripts/build_mcu.ps1 -CompilerBin D:/Keil_v5/ARM/ARMCC/bin -DeviceInclude D:/Keil_v5/ARM/Pack/Keil/STM32F1xx_DFP/2.4.0/Device/Include -CmsisInclude D:/Keil_v5/ARM/Pack/ARM/CMSIS/4.5.0/CMSIS/Include
```

取得`STM32_BUILD_PASS`的新目录后，运行`mcu/scripts/qualify.py --build <目录> --gcc <gcc.exe> --fromelf <fromelf.exe>`。历史构建为1844字节，黄金计算校验0x08065D77，本地调度检查4095用例；原ARMASM对齐提示保留。参考bin用于比较，不自动写入芯片。

## 技术文档

`report/technical_report_template_20261017.md`是五部分正文，官方参考模板和照片保存在references内。PDF生成器直接依据模板页边距、字号与结构排版，照片仅作页面旋转，不修改像素。运行环境为Windows，需ReportLab、Pillow及宋体/黑体：

```powershell
python scripts/build_submission_pdf.py --out out/technical_report.pdf
```

输出目录需全新或指定新文件名，生成后仍应逐页检查。仓库提供的PDF已完成逐页视觉检查；视频由作者另行拍摄，不提供虚构演示链接。

## 版本与完整性

原始业务文件、公开文字修改和参考硬件分别核对。界面文字修改保持非字符串AST不变，协议、规则条件、原始样点、记录结构与两板固件不改。历史fixture和原始记录按原字节保留，不用更新文字的方式重写测试结果。清单只证明文件完整性，不代替构建或实物验证。
