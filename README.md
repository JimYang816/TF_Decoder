# TF Decoder

中文离线 RAW 时频图桌面应用。支持打开单个 RAW 文件或递归导入文件夹，统一应用计算与滤波参数，按需计算时频图并交互查看局部。

Windows 便携版：解压完整 ZIP 后双击 `TF-Decoder/TF-Decoder.exe`，保留 `_internal`
目录。运行不需要 Python、MATLAB、MATLAB Runtime 或网络。中文说明、版本来源和第三方
许可随包附带；[构建方式与交付验收记录](docs/portable-delivery.md) 说明当前验证范围。

开发启动（Python 3.11+）：

```powershell
python -m pip install -e ".[test]"
python -m tf_decoder
python -m pytest
```

Windows 使用系统中文字体和 Qt 高 DPI 支持。`Ctrl+O` 打开文件；失败原因和残缺尾块警告显示在窗口底部，原文件只读。

“打开文件夹”递归识别 `.raw` 的大小写变体，按相对路径自然排序。左侧可收起文件列表显示文件名、相对目录及成功／警告／失败状态，底部显示序号与汇总；尚未打开的文件标记“尚未分析”。点选列表、上一张／下一张或左右键切换；首尾停留，不循环。输入控件聚焦时左右键保留编辑行为。每个文件首次显示全时段，在当前导入批次内切回时恢复自己的视口；重新导入开始新批次。空目录、扫描错误与各文件读取失败均以文字说明，不阻断其他文件。

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
- `open_folder(str | Path) -> Future[ApplicationState]` 立即进入 `scanning`，后台递归扫描后仅分析首项；返回该项的结果。空目录返回 `empty`，不可访问目录返回 `failed`。被新批次替代的扫描返回 `idle` 快照且不影响当前批次。
- `browser` 是不可变 `BrowserState(root, entries, current_index, message)` 快照；`entries` 为 `BrowserEntry(path, relative_path, status, message)` 元组，状态为 `pending / computing / success / warning / failed`，只保留身份与状态，不持有整批时频矩阵。`current_index` 从 0 起，无文件为 `None`。`browser.summary` 给出各状态数量；扫描错误保留在 `browser.message`。
- `select_file(index)`、`previous_file()`、`next_file()` 返回当前目标的 `Future[ApplicationState]`；无前项／后项时返回当前状态的已完成 Future。越界选项抛出 `IndexError`。单文件入口建立只含该项的新批次。文件按需重算，当前批次的各文件视口独立保存，过期结果不能替换当前图或写入新批次。
- `state` 是不可变 `ApplicationState(status, path, result, message, viewport)`；初始状态为 `idle`。失败包含完整来源路径与原因，下一次打开可正常恢复。
- `Viewport(time_range, frequency_range)` 是不可变视口，范围为有限、递增的二元组，单位分别为秒和 Hz；Qt 只在展示时将频率换为 kHz。未就绪时 `state.viewport` 为 `None`。首次分析完成后为全时段、0–Nyquist。
- `set_viewport(Viewport)` 选择矩形；`zoom_viewport(factor, *, time, frequency)` 围绕指定坐标缩放（比例小于 1 为放大）；`pan_viewport(time_delta, frequency_delta)` 平移；`restore_full_view()` 恢复全时段及适用频率范围。这些方法同步返回更新后的状态，保留原 `AnalysisResult` 对象与文件身份，不重新计算 PSD 或色阶；无有效结果时抛出 `ValueError`。视口可移至数据区域外，便于平移查看边界。
- `AnalysisResult` 包含绝对 `path`、已应用 `parameters`、`sample_count`、电压最小／最大值 `voltage_range`、`warnings`、只读双精度 `times`（秒）、`frequencies`（Hz）、`psd`（V²/Hz，频率×时间矩阵）、`db` 与文件全时段全频率 `levels`（dB）。频率显示转换为 kHz 仅由 Qt 完成。
- `AnalysisParameters` 是不可变计算配置：默认 `sample_rate=96000`、`window_length=1024`、`overlap=512`、`fft_length=1024`、`filter_enabled=False`、`passband_low=20000`、`passband_high=28000`、`transition_width=1000`，频率单位为 Hz。启用滤波使用 Chebyshev I，通带纹波 0.5 dB、阻带衰减 10 dB，零初始状态因果滤波，RAW 块间保留滤波状态，STFT 窗跨块连续。
- `apply_parameters(AnalysisParameters, *, displayed_frequency_range=None) -> Future[ApplicationState]` 校验后统一更新当前整批配置；只有计算参数变化才清除旧配置视口并重新计算当前文件。完全相同的应用保留每个文件的视口；仅改变显示频段时保留各文件的时间范围，将已记忆频率范围与新频段取交集，无交集时使用新频段。后续切图和导入沿用同一份配置。`applied_parameters` 返回当前已应用计算配置。采样率必须有限且为正；窗长至少 2、重叠非负且小于窗长、FFT 长度不小于窗长，三者为整数。启用滤波须满足 `0 < low-transition < low < high < high+transition < sample_rate/2`，否则同步抛出中文 `ValueError` 并保留原配置与结果。
- `displayed_frequency_range=(low, high)` 使用 Hz，须在 0 至 Nyquist 内有限递增，省略时为完整范围。它独立于滤波，写入 `state.displayed_frequency_range`；仅改变显示频段时保留同一 `AnalysisResult`、PSD 和全文件色阶，当前视口频率更新，恢复全图及全时段导出使用此频段。计算中的旧请求不能覆盖新配置。
- 右侧参数栏可收起，常用滤波和显示频段直接可见；高级参数默认折叠。输入值只在点击“应用到整批”后生效，无效输入保留原图并在参数区提示；图上方标识已应用配置。计算期间可继续编辑、应用及切图。
- `close()` 关闭后台执行器；支持上下文管理。Future 回调在工作线程运行，Qt 客户端通过 signal 收取，不能直接在回调修改控件。窗口关闭时等待正在计算的任务清理。
- `Application(cache_budget_bytes=384 * 1024**2)` 设置分析结果缓存预算（非负整数字节，0 禁用缓存和预取）。缓存按 PSD、dB、时间与频率数组总字节计费，使用 LRU 淘汰；当前显示、计算工作区及导出快照独立占用内存，因此预算不是进程 RSS 上限。缓存键包含绝对文件路径、文件系统身份、大小、纳秒修改／变更时间和已应用计算参数；文件变化会重新读取，计算期间变化返回明确失败。视口单独保存在当前批次中，不占用矩阵缓存。
- 当前文件优先分析，完成后低优先级预取前后相邻文件，不遍历计算整批。等待中的前台请求按最新优先调度，正在执行的任务不强制中断；缓存命中返回已完成 Future，即使后台正在预取。旧请求仍有自己的结果快照，但不能替换当前图或污染新批次／新配置缓存。批量导出逐个计算并及时释放，不向浏览缓存写入整批矩阵。
- `export_png(directory, *, mode="current" | "full") -> Future[ExportResult]` 立即提交后台 PNG 导出，捕获调用时已应用的分析结果及范围；之后切换文件或改变视口不改变该任务。`export_status` 为最新任务状态（`idle`、`exporting`、`succeeded`、`failed`）；`ExportResult(status, path, viewport, message)` 含成功输出的绝对路径或明确失败原因。尚未就绪、无效模式或不可写目录返回失败结果。每次使用独占文件创建保护重名，失败清理未完成图片。导出独立于浏览状态，不改写 `state`。PNG 使用 `QImage/QPainter` 在后台渲染，不访问窗口控件；调用方须在主线程保持 `QGuiApplication`（桌面窗口已具备），缺少时返回明确失败。
- `ApplicationState.displayed_frequency_range` 保存与缩放视口独立的已应用显示频段（Hz，当前默认为 0–Nyquist），供全时段导出使用；后续显示频段设置沿用此字段。PNG 附带来源、范围、PSD 单位、色阶及已应用参数文本元数据，方便复核。
- `export_batch_png(directory, *, progress=None) -> Future[BatchExportState]` 捕获启动时的文件列表、已应用参数及显示频段，后台逐个分析并导出全时段 PNG。自动创建输出相对目录，沿用单图渲染、元数据及 `_1` 等重名保护；失败后继续，不保留整批时频矩阵。浏览、导入新批次或应用新参数不改变已启动任务。已有批量任务时再次启动会抛出 `ValueError`；无文件返回失败快照。
- `batch_export_status` 是不可变任务快照，状态为 `idle / exporting / completed / cancelled / failed`。快照包含 `parameters`、`displayed_frequency_range`、`entries`、已处理数 `completed`、`summary` 和中文 `message`；每项 `BatchExportEntry(path, relative_path, status, output, message)` 标明来源、相对路径、成功输出或原因。条目状态为 `pending / processing / success / warning / failed / uncompleted`，`summary` 分别统计成功、警告、失败、未完成；警告表示图片已成功输出但来源有残缺尾块。
- `cancel_batch_export()` 在安全边界取消；已开始写入的图片完整写完，已完成图片保留，后续项和分析中未写入的项标为 `uncompleted`。可选 `progress(snapshot)` 在工作线程发布每个文件的开始／完成及最终状态，下一项开始前调用完毕，可在回调中确定地取消；回调应尽快返回，异常不会中断导出，Qt 客户端应通过 signal 更新控件。窗口底部提供批量文件进度、取消及可展开的逐文件明细，不弹出逐个错误窗口。
- `tf_decoder.gui.MainWindow(application=None)` 可注入同一应用实例；`open_file(path)` 和 `open_folder(path)` 是界面的导入路径，`current_result` 是当前已显示的结果；图形适配器为 `plot`、`image`、`color_bar`，供后续交互与导出扩展。

## 数值与颜色来源

RAW 每块 32800 字节：32 字节不解析的头、8192 个小端 uint32。低 24 位按补码解释，乘以 `8.196 / 2**24` 得到电压。仅使用完整块和完整时频窗。窗口是 `hanning(1024)`（长度 1026 的对称 Hann 去除两端零值）；不去趋势、不补齐末尾。PSD 单边缩放保留 DC 和 Nyquist 原值，其余频点加倍。显示为 `10*log10(PSD + 2**-52)`；全零色阶上限取下限加 1 dB。

`tf_decoder/parula256.csv` 直接由本机 MathWorks MATLAB 的 `parula(256)` 导出，采用 R2017a 更新后的标准 256 色表，未用旧版 64 色插值或近似配色。首色为 `(0.2422, 0.1504, 0.6603)`。导出命令：`writematrix(parula(256), 'parula256.csv')`。运行应用无需 MATLAB。参考 [MathWorks parula 文档](https://www.mathworks.com/help/matlab/ref/parula.html)。Qt 的 8 位显示查找表按每个原始 RGB 分量乘 255 后四舍五入构建。

## 验证

`tests/test_application.py` 通过唯一应用级入口检验：真实块布局、有符号边界、DC 与已知频率／幅值、完整窗坐标、PSD 与 dB、全零、空文件、残缺尾块、缺失文件、失败后恢复和来源未修改。DC 期望用独立解析和式 `sum(w)=512.5, sum(w²)=384.375` 求出；正弦积分功率采用独立的 `A²/2`，容差覆盖 ADC 量化误差。GUI 冒烟只替换系统文件选择器边界，实际按钮、后台计算、Qt 事件循环与时频图均执行。

开发机真实 RAW 验收采用现有 67,174,400 字节采集文件（2048 完整块，16,777,216 点，174.763 秒）；结果矩阵为 513×32767。2026-11-06 在当前开发机，打开至完成约 0.9–1.3 秒，计算期间 10 ms Qt 定时器触发 67–97 次。离屏渲染在 100%／125%／150% 缩放检验标签、坐标与色条；离屏插件需显式加载本机微软雅黑字体。此结果属于开发验证，未替代后续打包及干净 Windows 验收。
