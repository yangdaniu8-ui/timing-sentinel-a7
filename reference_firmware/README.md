# 固件仅供负责人恢复参考

当前板子已固化同一 USR43＋STM32任务固件，无需重烧。本目录不含自动烧录入口，普通用户不要用它覆盖现有板子。

fpga_usr43.bit 是 JTAG 配置参考；fpga_usr43_spi_x1.mcs 是外部 SPI Flash 参考；stm32_fir_irq.bin 是 STM32 Flash 参考。各文件字节数/SHA见根[source_manifest.json](../source_manifest.json)，执行`python -B scripts/verify_package.py`检查完整性。正式FPGA为USR_ACCESS 0x4D565043，bit SHA为`b18e94908531ebf2f0218c375f78d815290fbebb554a1c51848f50e4c12a3636`，与[evidence/index.json](../evidence/index.json)绑定；Flash载荷和冷启资格见[evidence/flash/](../evidence/flash/)。Flash 的最终交付策略为整片清空后重写并全片读回；擦写仅由负责人确认型号、身份、已有记录保护和授权后操作，不由启动 EXE 触发。

16 MiB 是 FPGA 板上配置 Flash，不是 STM32 内部 Flash 容量。当前正式镜像没有 ILA；临时 ILA 证据不应替代或覆盖此镜像。
