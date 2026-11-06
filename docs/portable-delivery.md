# Windows 便携版交付

版本：0.1.0；目标：Windows x64；形式：PyInstaller onedir。解压整个目录后运行
`TF-Decoder/TF-Decoder.exe`，不需要安装 Python、MATLAB、MATLAB Runtime 或连接网络。
目录内附中文 `使用说明.txt`、`BUILD-INFO.json`、逐文件 SHA256、第三方说明与许可原文。

## 构建

在 Windows x64 安装 CPython 3.13.9，打开 PowerShell：

```powershell
./scripts/build-portable.ps1 -Python 'C:/Python313/python.exe'
```

脚本创建独立 `.build-venv`，安装 `requirements-build.txt` 的精确版本，检查依赖，
使用仓库中的 spec 打包。输出为 `dist/TF-Decoder-0.1.0-windows-x64.zip` 及
`.zip.sha256`。已有同名输出时停止，先将其移走再重建。可指定 `-OutputDirectory`。
首次获取构建依赖需要网络；应用本身不需要网络。离线构建可事先用
`pip download -r requirements-build.txt -d wheelhouse` 保存 Windows x64／Python 3.13
的 wheel，再设置 `PIP_NO_INDEX=1` 和 `PIP_FIND_LINKS` 指向本地 wheelhouse。

已有完全匹配的隔离环境可用：

```powershell
./scripts/build-portable.ps1 -Python './.venv/Scripts/python.exe' -UseExistingEnvironment
```

脚本验证 Python 与全部构建依赖版本，保留源码提交、未提交变更标记、构建时间和平台。
版本锁定旨在可重复执行流程；时间戳和 PE/ZIP 元数据可能不同，不承诺逐字节相同产物。
交付前应从干净提交构建，检查 `BUILD-INFO.json` 中 `source_dirty=false`。
PyInstaller 从解释器的基础安装复制必要 DLL；构建日志中的 Anaconda 路径只表明
构建来源，不能代替运行时依赖检查。不能把整个 Anaconda 环境当作应用依赖。

## 验证记录与边界

2026-11-06 开发机为 Windows 11 x64（10.0.26200）、Python 3.13.9，依赖详见锁定文件。
已有应用级与 Qt 冒烟回归 42 项全部通过（`QT_QPA_PLATFORM=offscreen`，3.56 秒）。
源程序的真实 RAW、性能、PNG 与 125%／150% 显示缩放记录参见
[实现日志](implementation-run.md)、[数值一致性](numerical-parity.md) 和 README。
本切片不改动界面或信号计算；没有添加仅检验构建脚本文本的测试。

本机装有 Python 和 MATLAB。即使在剥离 PATH、清空 Python/Qt 环境变量、独立目录下
成功启动实际 EXE，并检查所加载 DLL 均在交付目录或 Windows 系统目录，也只能证明
这次启动未使用外部开发库，不能等同“未安装 Python/MATLAB 的干净 Windows”验收。
该验收尚待干净机器完成，不据此勾选全部交付验收项。

干净机器验收步骤（需保留结果和 Windows 版本）：

1. 校验 ZIP 的 SHA256，在中文目录解压，双击 EXE；机器无需开发环境且断网。
2. 导入中文路径的单 RAW 和多级文件夹，含长中文名、空文件和残缺尾块。
3. 查看图形，测试滚轮、平移、框选、恢复、前后切图和逐文件视口记忆。
4. 应用采样率／STFT／真实滤波／独立显示频段；非法参数保留原图并给出原因。
5. 导出当前视图、全时段和整批 PNG；确认目录保留、重名保护、失败继续、取消后保留输出。
6. 在 Windows 设置的 125% 和 150% 缩放检查侧栏、高级区、键盘焦点与文字状态。

打包验收仅在实际 EXE 上运行；源代码测试不能替代上述交付包行为。
首版范围仍为时频图浏览及 PNG 输出，未包含解码。性能数值仅引用已测数据。

## 第三方来源

构建脚本复制所有锁定 wheel 的 metadata 与许可目录，包含 NumPy/SciPy 中的
OpenBLAS 和运行时条款，另附 Python 许可和 Qt LGPL/GPL 许可原文。
Qt/PySide 以动态库分发，保留替换库和调试修改的能力；具体版本的上游源码及构建
说明见随包第三方说明。`parula256.csv` 的 MATLAB 导出来源如实保留，未宣称
MathWorks 数值表获得开源许可。分发者应保留整个 notices/licenses 目录。
