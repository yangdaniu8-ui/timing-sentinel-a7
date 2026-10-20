# 第三方组件与源码

运行时包含 Python、PySide6/Qt、Shiboken、NumPy、pyqtgraph、pyserial、colorama；构建使用 PyInstaller。组件版本及许可证原文位于 licenses/，开发依赖固定在 requirements-lock.txt 和 requirements-build.txt。

Qt/PySide6为动态库形式（_internal内），保留LGPL/GPL等许可证与对应组件元数据；不要移除许可证或阻止用户替换兼容动态库。Qt对应源码及许可见 https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.11.2 和 https://doc.qt.io/qt-6/licensing.html 。Python见 https://www.python.org/，其他组件来源见各自distribution_metadata.txt。

应用自身源码随 host_source.zip 交付便于项目内部调试；本构建没有擅自为用户应用代码指定整体开源许可证。外部公开发布前应另行确认作品与组件的分发许可及义务。本包未签名，无驱动或系统设置变更。
