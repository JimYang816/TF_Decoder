---
status: proposed
---

# 使用 Qt 桌面交互与向量化信号计算

本应用要求离线解压即用、约 100 个 RAW 文件的单图交互浏览，用户已排除 MATLAB 并委托代理选择语言。拟采用 Python、PySide6、PyQtGraph、NumPy/SciPy，将信号计算放在后台、使用编译数值内核，并以有容量限制的图形缓存支持缩放与切图；Windows 下用 PyInstaller onedir 打包。C++/Qt 提供更直接的内存与底层循环控制，但当前没有实测证据证明需要承担相应的信号算法和交互实现成本，因此先采用已有科学计算与绘图组件完成原程序的数值复现，再用实际 RAW 验证性能；本决定不声称 Python 比 C++ 更快。

技术能力依据见 [需求与设计](../batch-raw-viewer-design.md)。整体方案尚待访谈最终确认，确认前不开始应用实现。
