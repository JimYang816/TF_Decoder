# TF Decoder

中文离线 RAW 时频图桌面应用。目前支持打开一个 RAW 文件，计算未滤波全时段时频图并交互查看局部。

开发启动（Python 3.11+）：

```powershell
python -m pip install -e ".[test]"
python -m tf_decoder
python -m pytest
```

Windows 使用系统中文字体和 Qt 高 DPI 支持。`Ctrl+O` 打开文件；失败原因和残缺尾块警告显示在窗口底部，原文件只读。

图形默认左键拖动平移，滚轮围绕指针缩放。选择“框选放大”后左键拖出矩形放大；“平移”和“框选放大”为互斥工具，当前模式以按钮选中状态显示。`Alt+P` / `Alt+R` 切换工具，`Alt+F` 恢复全图；也可用 Tab 聚焦按钮后按空格。视口操作保留当前文件的 PSD 和全文件色阶，坐标始终显示秒和 kHz。

顶栏“导出 PNG”菜单提供“导出当前视图”和“导出全时段”，再选择输出目录。当前视图保留正在查看的时间／频率范围；全时段使用全部采集时间与已应用显示频段，保留当前浏览视口。PNG 为 1400×900，包含文件身份、坐标、单位与原始 PSD 色阶；来源 `采集.raw` 输出为 `采集.png`，重名自动使用 `_1`、`_2` 后缀。后台导出期间仍可缩放或打开其他文件，底部显示完成路径或失败原因。

## 应用级公开入口

后续功能和行为测试复用 `tf_decoder.Application`，Qt 展示通过这一入口读取与分析，不另设测试专用路径。

```python
from tf_decoder import Application

with Application() as application:
    future = application.open_file("采集.raw")
    state = future.result(timeout=30)  # 仅非 GUI 调用方等待；GUI 通过完成信号收取
    if state.status == "ready":
        result = state.result
        print(result.times, result.frequencies, result.psd, result.levels)
    else:
        print(state.message)
```

- `open_file(str | Path) -> Future[ApplicationState]` 立即将 `state` 更新为 `computing`，后台完成后返回 `ready` 或 `failed`。只有最新请求可更新当前 `state`；每个 Future 仍返回各自请求的快照。
- `state` 是不可变 `ApplicationState(status, path, result, message, viewport)`；初始状态为 `idle`。失败包含完整来源路径与原因，下一次打开可正常恢复。
- `Viewport(time_range, frequency_range)` 是不可变视口，范围为有限、递增的二元组，单位分别为秒和 Hz；Qt 只在展示时将频率换为 kHz。未就绪时 `state.viewport` 为 `None`。首次分析完成后为全时段、0–Nyquist。
- `set_viewport(Viewport)` 选择矩形；`zoom_viewport(factor, *, time, frequency)` 围绕指定坐标缩放（比例小于 1 为放大）；`pan_viewport(time_delta, frequency_delta)` 平移；`restore_full_view()` 恢复全时段及适用频率范围。这些方法同步返回更新后的状态，保留原 `AnalysisResult` 对象与文件身份，不重新计算 PSD 或色阶；无有效结果时抛出 `ValueError`。视口可移至数据区域外，便于平移查看边界。
- `AnalysisResult` 包含绝对 `path`、已应用 `parameters`、`sample_count`、电压最小／最大值 `voltage_range`、`warnings`、只读双精度 `times`（秒）、`frequencies`（Hz）、`psd`（V²/Hz，频率×时间矩阵）、`db` 与文件全时段全频率 `levels`（dB）。频率显示转换为 kHz 仅由 Qt 完成。
- `AnalysisParameters` 是当前默认计算配置的不可变快照：96 kHz、1024 点 hanning、512 点重叠、1024 点 FFT。参数编辑与滤波由后续任务扩展。
- `close()` 关闭后台执行器；支持上下文管理。Future 回调在工作线程运行，Qt 客户端通过 signal 收取，不能直接在回调修改控件。窗口关闭时等待正在计算的任务清理。
- `export_png(directory, *, mode="current" | "full") -> Future[ExportResult]` 立即提交后台 PNG 导出，捕获调用时已应用的分析结果及范围；之后切换文件或改变视口不改变该任务。`export_status` 为最新任务状态（`idle`、`exporting`、`succeeded`、`failed`）；`ExportResult(status, path, viewport, message)` 含成功输出的绝对路径或明确失败原因。尚未就绪、无效模式或不可写目录返回失败结果。每次使用独占文件创建保护重名，失败清理未完成图片。导出独立于浏览状态，不改写 `state`。PNG 使用 `QImage/QPainter` 在后台渲染，不访问窗口控件；调用方须在主线程保持 `QGuiApplication`（桌面窗口已具备），缺少时返回明确失败。
- `ApplicationState.displayed_frequency_range` 保存与缩放视口独立的已应用显示频段（Hz，当前默认为 0–Nyquist），供全时段导出使用；后续显示频段设置沿用此字段。PNG 附带来源、范围、PSD 单位、色阶及已应用参数文本元数据，方便复核。
- `tf_decoder.gui.MainWindow(application=None)` 可注入同一应用实例；`open_file(path)` 是界面的打开文件路径，`current_result` 是当前已显示的结果；图形适配器为 `plot`、`image`、`color_bar`，供后续交互与导出扩展。

## 数值与颜色来源

RAW 每块 32800 字节：32 字节不解析的头、8192 个小端 uint32。低 24 位按补码解释，乘以 `8.196 / 2**24` 得到电压。仅使用完整块和完整时频窗。窗口是 `hanning(1024)`（长度 1026 的对称 Hann 去除两端零值）；不去趋势、不补齐末尾。PSD 单边缩放保留 DC 和 Nyquist 原值，其余频点加倍。显示为 `10*log10(PSD + 2**-52)`；全零色阶上限取下限加 1 dB。

`tf_decoder/parula256.csv` 直接由本机 MathWorks MATLAB 的 `parula(256)` 导出，采用 R2017a 更新后的标准 256 色表，未用旧版 64 色插值或近似配色。首色为 `(0.2422, 0.1504, 0.6603)`。导出命令：`writematrix(parula(256), 'parula256.csv')`。运行应用无需 MATLAB。参考 [MathWorks parula 文档](https://www.mathworks.com/help/matlab/ref/parula.html)。Qt 的 8 位显示查找表按每个原始 RGB 分量乘 255 后四舍五入构建。

## 验证

`tests/test_application.py` 通过唯一应用级入口检验：真实块布局、有符号边界、DC 与已知频率／幅值、完整窗坐标、PSD 与 dB、全零、空文件、残缺尾块、缺失文件、失败后恢复和来源未修改。DC 期望用独立解析和式 `sum(w)=512.5, sum(w²)=384.375` 求出；正弦积分功率采用独立的 `A²/2`，容差覆盖 ADC 量化误差。GUI 冒烟只替换系统文件选择器边界，实际按钮、后台计算、Qt 事件循环与时频图均执行。

开发机真实 RAW 验收采用现有 67,174,400 字节采集文件（2048 完整块，16,777,216 点，174.763 秒）；结果矩阵为 513×32767。2026-11-06 在当前开发机，打开至完成约 0.9–1.3 秒，计算期间 10 ms Qt 定时器触发 67–97 次。离屏渲染在 100%／125%／150% 缩放检验标签、坐标与色条；离屏插件需显式加载本机微软雅黑字体。此结果属于开发验证，未替代后续打包及干净 Windows 验收。
