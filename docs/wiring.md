# 两板接线（按板上丝印定位）

适用 ATK-DF7A35 V2.1 与 STM32F103C8T6。断电后接线，两板独立供电，只连3.3V数字信号与共地，不并电源。

| STM32 丝印 / 引脚 | FPGA 排针旁丝印 | 排针位置 | 用途 |
| --- | --- | --- | --- |
| A9 / PA9 | R17 | J2-3 | UART TX → CH0 |
| B0 / PB0 | N15 | J2-5 | 响应 GPIO → CH1 |
| A8 / PA8 | P20 | J2-8 | FPGA READY → STM32 |
| A5 / PA5 | R16 | J3-35 | SPI SCK → CH2 |
| A7 / PA7 | P15 | J3-36 | SPI MOSI → CH3 |
| A4 / PA4 | R14 | J3-37 | 低有效 CS → CH4 |
| G / GND | GND | J2-53 或55 | 共地 |

J2-54是3.3V、J2-56是5V，不能当GND。SPI口与LCD复用，本配置不插LCD。无MISO、不接PA10；不按导线颜色猜。其他板版本必须重新核对原理图。

STM32 由自身 Micro-USB 供电，BOOT0/1 跳帽接丝印0一侧。下载时 ST-Link 仅接 PA13/SWDIO、PA14/SWCLK、GND，不接3.3V/5V线。FPGA正常电源供电，电脑读取接板载 USB_UART/CH340，JTAG不是数据串口，COM号会变化。

脱机：KEY0短按启动；按键发起的冻结记录长按KEY1约3秒释放；按住KEY3消音。电脑发起的冻结记录受保护，KEY0/KEY1不应覆盖它。按键默认2ms期限，真实任务超期，异常灯与三声属预期；GUI的3.5ms/8ms是不同期限。勿按RESET代替操作。

新实验先读存原冻结记录，再按程序提示释放。GUI和CLI不能同时占同一个COM口。
