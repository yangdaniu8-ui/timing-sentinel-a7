# 上位机公开运行版

## 当前技术文档

[最新版技术文档 PDF](technical_report.pdf)为已确认的14页报告，参考文献独立最后一页；对应正文、插图和生成脚本一并保存在仓库中。程序和固件保持冻结。既有免安装ZIP仍内附早期报告，请单独下载最新版；旧附件不覆盖。

## 0.4.9 · 2026年10月20日

[GitHub Releases 下载页](https://github.com/yangdaniu8-ui/timing-sentinel-a7/releases/tag/v0.4.9-public-20261020)

运行包：`TimingSentinelA7-0.4.9-USR43-20261020-win64-portable.zip`，65,958,809字节。SHA256：

```text
d2f3748298c04078cc452d01be1593a56510768959e7d222b309ddbc184f7219
```

Windows x64免安装版，完整解压后双击`START.cmd`，无需Python/Vivado。程序未签名，不建议关闭系统安全措施；不要单独移动EXE或删除配套目录。本包不是安装向导。

## 本次内容

合入当前源码的界面/说明文字，附完整运行环境、第三方许可、技术PDF、丝印接线表和使用说明。六份同事务四规则历史记录位于`demo_records/paired/`，无板也可打开回放。原六场景及三档负载示例保留在应用配套目录。

适配正式USR43 FPGA和STM32 FIR/IRQ任务固件，不需要因下载此包而重新烧录。原3.5/8 ms预设保留声光，同事务四规则预设为“仅波形”。

## 本次检查

- 应用源码来自提交`b449b081b1d2c41bb29efbc9eadfe14600ca1485`；内嵌源码快照逐文件与构建输入相符。
- 435项源码测试已通过。本次重建只改变此前整理的文字，不改变采集、判定、串口协议或两板镜像。
- 实际新EXE构建自测通过，包括六场景、四负载示例、保存回放、CSV/VCD导出、批量统计及安全门禁。
- ZIP在新路径完整解压，470文件完整性检查、新路径EXE自测、六同事务记录JSON/HTML统计均通过。
- 实际窗口核验新文案和历史回放的TIMEOUT/PASS/TIMEOUT/PASS，正常关闭；不连接串口、不新增板上任务。

新EXE SHA256：`fb406e1084f50a9db2679f13c13e06f131be0ea4e71a0f6b694219a9cb628da5`。详细检查摘要与SHA256SUMS随Release提供；校验和只证明完整性，不是代码签名。

此重建包尚未在另一台电脑复验，旧包另机结果不直接移植为此包资格。已有其他历史构建继续保留，不以相同0.4.9标题混同具体EXE；请按Release日期和散列区分。

## 技能与报告补充（应用版本不变）

新增`skill/`可复用检查脚本及计数器/FIR离线示例，`report/llm_collaboration.md`保存真实交互节选和纠错说明。技术PDF增至14页，技能说明单独一页，参考文献仍独立最后一页；原前12页图文像素一致。

该补充仅改变材料与新增通用工具，host、MCU、RTL及参考镜像不变；无需重新烧录。免安装ZIP未重建，其附件文档不会自动变化。含Skill的新源码RAR与旧RAR区分，不能以旧包文件名声称已包含新材料。资格范围和官方条款对应见`docs/skill_submission.md`。

## 最终证据与许可补充

主体自有源码现按MIT发布，第三方许可原文保留，详[许可范围](licensing.md)。新增[evidence](../evidence/README.md)及离线校验脚本；600份历史记录和12组ILA配对已复核，不是再次采集或重新刷写。正式源、镜像、报告哈希及Flash/冷启证据分别绑定，验证结果见[evidence_validation.json](evidence_validation.json)。

本次统一源码包为`TimingSentinelA7-AllSource-USR43-UI049-Evidence-MIT.rar`，包括最新14页PDF、主体MIT、Skill、全部源码和验证附件。独立附件`timing_sentinel_evidence.zip`供只查看验证数据的读者使用；其中的`validation.json`是实际离线复核结果。两者均不包含虚拟环境、厂商工具或个人Git历史。旧运行ZIP和EXE字节不改，根目录许可与独立文档补充适用于本次源码交付。
