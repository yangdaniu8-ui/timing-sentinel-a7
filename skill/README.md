# 可复用技能包：验证证据与交付一致性

本目录对应官方指南3.3.5.2。实际提交一项技能`fpga-evidence-check/`，含校验脚本、提示词工作流及排查清单。不必安装到AI工具才能使用。自有技能内容采用本目录MIT许可，工程其余代码与第三方许可不因此改变。

## 适用范围与题目外场景

从本队“文件存在不等于已上板”“旧资格不能替新版本”“冻结正常但串口读取失败”“完整白名单仍漏掉Skill”等观察中提炼，把字节完整性、数值比对、构建、实板资格与提交完备性分开核查。

不依赖本队协议、板卡或电脑路径。可用于其他队伍的计数器/滤波器/接口桥黄金参考比对，以及MCU、FPGA和PC联合交付的选定文件检查。采集故障清单还可用于有缓存和串口控制的数据记录仪，但须适配设备状态与保存命令。已验证通用脚本、合成计数器/FIR夹具及新目录部署，不宣称其他板卡实测。

## 五分钟使用

仅需Python 3.10+标准库，无需板子、Vivado或PySide。在工程根目录执行；`python`替换为本机Python命令。

```text
python -B skill/fpga-evidence-check/scripts/evidence_check.py compare --expected skill/fpga-evidence-check/examples/counter_expected.csv --actual skill/fpga-evidence-check/examples/counter_actual.csv
python -B skill/fpga-evidence-check/scripts/evidence_check.py compare --expected skill/fpga-evidence-check/examples/fir_expected.csv --actual skill/fpga-evidence-check/examples/fir_actual.csv
python -B skill/fpga-evidence-check/scripts/evidence_check.py compare --expected skill/fpga-evidence-check/examples/fir_expected.csv --actual skill/fpga-evidence-check/examples/fir_bad.csv
python -B skill/fpga-evidence-check/scripts/test_evidence_check.py
```

前两条退出0、`passed:true`。第三条为预先标明的故障注入，应退出1并报告`y3`不一致，不是新发现的硬件故障。第四条检查正反例与边界。封存文件：

```text
python -B skill/fpga-evidence-check/scripts/evidence_check.py seal --root skill/fpga-evidence-check/examples --file counter_expected.csv --file fir_expected.csv --out evidence_manifest.json
python -B skill/fpga-evidence-check/scripts/evidence_check.py check --root skill/fpga-evidence-check/examples --manifest evidence_manifest.json
```

`--out`须为不存在的新文件，父目录须存在。接收另一个目录后只改变`--root`即可检查；不要重做清单掩盖改动。脚本输出JSON，不自动发布、烧录、读取串口或扫描整台电脑。

## 输入、输出和失败判据

| 操作 | 输入 | 输出范围 |
| --- | --- | --- |
| seal | 明确文件列表、根目录 | schema=1，字节数及SHA256；不自动发现依赖 |
| check | 可信清单、收到的根目录 | 检查缺失、长度、哈希；未列出文件不在范围内 |
| compare | 两个UTF-8的id,value CSV，可选非负--atol | 按ID配对，输出缺失/多余/差值；默认绝对容差0 |

退出0通过，1有效输入不匹配，2输入/格式/路径/权限错误。CSV列名顺序严格为`id,value`，ID非空唯一，数值为有限十进制数，容差边界含等号。单位、事件端点和任务身份必须先对齐，不能只因数字相近就认为同事务。

## 失效条件

- 错误/不完整输入生成的清单、同时被改的数据和清单：哈希相同不证明可信，本工具不是数字签名。
- 未列出的依赖、环境和文件不检查；提交完备性必须另按官方清单核对。
- 黄金参考、格式转换或容差错误可能产生错误判断，须单独验证。
- 输入正在并发改变或CSV过大：先冻结文件；CSV比对载入内存，不是实时流处理。
- 拒绝链接/重解析输入、绝对路径、重复项和路径穿越；外部依赖须合法取得并显式放入受控目录。
- 文件或数字通过不等于综合、时序或上板通过；合成示例不能冒充RTL/实物测试。

## 提示词工作流

> 请检查我提供的工程交付与黄金参考。输入为根目录、明确文件列表、参考CSV、实际CSV、单位及容差依据。先区分完整性、数值一致、构建和实板资格。使用本目录脚本，保留失败，不修改输入或容差使其通过。缺少设备、日志、身份或转换器证据时列出缺项；不得自动烧录、释放冻结记录或外部上传。输出实际命令、退出码、差异、边界和下一步。

这是本次提炼的模板，不冒充历史逐字提示词。真实交互与自我纠错见工程`report/llm_collaboration.md`。

## 来源、效果和入口

- [真实观察与提炼来源](fpga-evidence-check/references/origins.md)
- [验证及复用结果](fpga-evidence-check/references/validation.md)
- [采集故障分层清单](fpga-evidence-check/references/triage.md)
- [AI助手技能入口](fpga-evidence-check/SKILL.md)

本地旧`timing-sentinel-validation`是项目专用历史草稿，不纳入新公开技能包；公开部分仅此README、新技能目录和许可。技能是否满足提交要求不能只看目录或哈希。
