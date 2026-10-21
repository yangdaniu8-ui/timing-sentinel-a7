# 源码许可与第三方归属

主体自有源码采用根目录[MIT许可](../LICENSE)。新编写的通用Skill保留同内容的[独立MIT许可](../skill/LICENSE)，方便单独复用。许可只覆盖贡献者有权授权的内容，不把第三方代码、运行库、商标、官方文档或开发板设计重新许可为MIT。

## 第三方内容

- Python、Qt/PySide6、Shiboken、NumPy、pyqtgraph、pyserial等组件沿用各自许可。原始许可及版本资料位于`third_party/host_runtime_licenses/`；运行包中相应文本仍位于`licenses/`。
- Vivado导出器来源与AMD MIT原文见[导出工具归属说明](../third_party/amd_export_tool_notice.md)。厂商工具本身不随源码发布。
- 照片中的开发板标识以及引用的官方文档不代表本队拥有厂商设计或商标；保留原归属。
- 包内任何已有第三方版权头或独立许可均继续有效。根MIT不替代这些条款。

`host/packaging/NOTICES.md`和旧运行ZIP中的“应用尚未指定整体许可”是该构建时的历史说明，文件字节及运行包没有重建；本次自有源码许可以本页和根LICENSE为准。此说明不更改组件版本、动态库替换方式或第三方许可义务。

## 路径对应

仅将一份含空格的第三方许可文件改为自动化友好名称，正文逐字节保留：

```text
third_party/host_runtime_licenses/pyqtgraph/pyqtgraph/colors/maps/
  CC-BY license - applies to CET color map data.txt
    -> cc_by_cet_color_map_data.txt
```

Vivado既有导出的`Sources/`、`Scripts/`等大小写及README/SKILL入口保持原样，不做无验证批量重命名；路径按实际大小写使用。当前复现资格针对Windows，不据此声明已完成Linux构建。
