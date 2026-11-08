# TF Decoder

**[下载最新版（Windows x64）](https://github.com/JimYang816/TF_Decoder/releases/latest)**

TF Decoder 是一款中文 Windows 桌面工具，用于查看 96 kHz 单通道 RAW 录音的时频图。支持逐文件浏览、频段调整、带通滤波和 PNG 导出。

## 下载与使用

1. 在最新版发布页的 **Assets** 中下载 `TF.Decoder.exe`，双击运行。无需安装 Python 或 MATLAB，窗口可能需要几秒才会出现。
2. 点击「打开 RAW…」选择单个文件，或点击「打开文件夹…」扫描文件夹及其子目录。也可以输入路径后按回车。请自行准备符合下方格式要求的 RAW 文件。
3. 用「上一文件 / 下一文件」或键盘左右方向键切换文件。在「分析参数」中修改显示频率或带通滤波后，点击「应用设置」。
4. 在图上用滚轮缩放、中键拖动平移、左键双击恢复全图。按 F11 切换专注视图，按 Esc 退出。
5. 点击「保存当前图」导出当前缩放视图。等本批处理完成后，点击「保存本批」为每个有效文件导出一张覆盖完整时间范围的 PNG。两种导出都使用已应用的频段和滤波设置。

## 支持的 RAW 格式

| 项目 | 格式要求 |
|---|---|
| 采样率与通道 | 96,000 Hz，单通道 |
| 数据块 | 32 字节块头 + 8192 个四字节采样，共 32,800 字节 |
| 采样编码 | 小端 `uint32`，低 24 位为有符号采样值，高 8 位忽略 |
| 文件长度 | 非空，且为 32,800 字节的整数倍 |

## 项目配置

| 项目 | 默认值或约定 |
|---|---|
| 发布平台 | Windows x64 |
| 显示频率 | 默认 0–48 kHz，可在界面内调整 |
| 带通滤波 | 默认关闭，启用后的默认通带为 20–28 kHz |
| 批处理 | 每批最多 10 个文件，优先计算当前文件 |
| STFT | Hann 窗，窗长 1024 点，重叠 512 点，FFT 长度 1024 点 |

时频图以 dB/Hz 显示功率谱密度。色标上限由每个文件的频谱峰值决定，跨文件比较强度时请读取色标数值。频谱缓存存放在系统临时目录。

### 从源码运行

在项目根目录用 Python 3.13 环境执行以下命令。`requirements.txt` 声明 NumPy、SciPy、Matplotlib 和 PySide6 Essentials 的版本范围。

```powershell
python -m pip install -r requirements.txt
python main.py
```

可以在启动时指定 RAW 文件或数据目录，例如 `python main.py ".\数据"`。

### 重新打包 EXE

在 Windows x64 上，使用 64 位 Python 3.13，在项目根目录运行以下 PowerShell 命令。`requirements-exe.txt` 固定打包所用的依赖版本，`TF Decoder.spec` 定义 PyInstaller 打包配置。完成后，`TF Decoder.exe` 会生成在项目根目录。

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-exe.txt
.\.build-venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --distpath . "TF Decoder.spec"
```

### 测试

`python -m unittest discover -s tests -v` 运行无窗口集成测试。`python tests/test_workspace.py --screenshots` 将界面预览生成至 `artifacts/ui`；预览使用合成测试信号。
