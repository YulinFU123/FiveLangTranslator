# HANDOFF

FiveLangTranslator v0.4.0-alpha.2 的接手文档。开工前先读本文件，再读
`docs/TRANSLATION.md`（翻译链路）和 `docs/RELEASE_NOTES_0.4.0-alpha.2.md`（本版变更清单）。

## 当前状态

- Python 3.11，PySide6/Qt UI，whisper.cpp Vulkan 常驻 server，Ollama 本地推理
- `python -m compileall app` 通过
- `python -m pytest`：85 通过 / 3 跳过（本机无 PySide6 时跳过 Qt 模块；装齐依赖后 100 通过）
- 任务契约（P0–P6、明确不要做、已拍板决策）保存在 skill：
  `~/.codebuddy/skills/fivelang-translator/references/handoff-prompt-alpha2.md`

## alpha.2 已完成的变更

### 存储与导出（P3 / P4）

- 新增 `app/storage/`：`database.py`（SQLite + 增量补列）、`history.py`（仓储）、
  `session.py`（Qt 会话服务）、`cache_store.py`、`glossary.py`
- 三张业务表：`sessions`、`segments`、`translation_cache`，外加 `glossary`、`schema_meta`
- `segments` 含 provider / model / latency_ms / cache_hit / source_type（`asr` 或 `ocr`，OCR 留给下一阶段）
- 新增 `app/export/exporters.py`：SRT / WebVTT / ASS / TXT / JSON，时间轴排序、去重叠、最短 500ms、最长 7000ms

### 双层缓存

- L1 `TranslationCache`：原内存 LRU 不重写，只加可选 TTL（默认 300 秒）
- L2 `SqliteCacheStore`：SQLite 持久层，支持条目级过期
- 读 L1 → L2 → Provider；写同时落 L1 与 L2；启动从 SQLite 预热 500 条
- UI 区分 内存命中 / SQLite命中 / 未命中

### 术语表

- SQLite `glossary` 表 + 翻译页「术语表」可视化编辑器 + JSON 导入导出
- 术语进缓存键，改术语不会误用旧译法

### 译文行数预算（P0 / P5 的一部分）

- 取消「影院两行裁剪」禁令，改为**显示层不做断行后处理**，长度由字幕框高度驱动
- `OverlayWindow.visible_lines()` 用 `QFontMetrics.lineSpacing()` 换算可视行数 N（夹到 1–8）
- N 进 prompt、进缓存键；N=2 或未赋值时不注入行数句
- 触发：首次 show、resize、move、`ScreenChangeInternal`、字体数据库重载、`refresh()`；100ms 去抖且只在数值变化时推送

### 窗口置顶开关（P5 子项）

- 新增 `app/ui/overlay/topmost.py`：`WindowTopmostManager`（持有置顶状态，200ms 防抖写库，状态变更走事件总线广播，不侵入渲染/布局/坐标）
- 新增 `app/storage/config_store.py` + `subtitle_config` 键值表（key/value/updated_at）；读写失败内存兜底并打印错误日志
- `EventBus` 新增 `topmost_changed(bool)` 信号；设置面板开关、托盘菜单、overlay 三者订阅，状态 100% 双向同步
- overlay 新增 `set_topmost()`：用 `Qt.WindowStaysOnTopHint`（Qt 跨平台映射到 Windows `SetWindowPos`/`HWND_TOPMOST`、macOS `NSWindow`、Linux X11/Wayland `keep_above`），窗口隐藏时不强制 `show()`
- **默认 `false`**：按需求文档置顶默认关闭（此前窗口硬编码常驻置顶，本次改为受开关控制；若希望默认置顶，可在启动 `load()` 后调用 `set_topmost(True)` 或预置配置）
- 双入口：字幕样式页「窗口置顶」勾选 + 托盘右键「窗口置顶」勾选项，实时联动；切换弹 1500ms Toast（字幕已置顶 / 已取消置顶）
- `Ctrl+Alt+T` 仅占位，本期未绑定

### 字幕显隐全局热键 Ctrl+Alt+H（P5 子项）

- 新增 `app/ui/overlay/visibility.py`：`SubtitleVisibilityManager`（显隐状态机，`toggle()`/`show()`/`hide()`/`isVisible()`，100ms 防抖避免误触，200ms 防抖写 `subtitle_visibility` 到 `subtitle_config` 表，读写失败内存兜底+日志）
- `EventBus` 新增 `subtitleVisibilityChanged(bool)` 信号；热键、托盘菜单、控制中心按钮、overlay 四者订阅同步
- 复用既有 `app/windows/hotkeys.py` 的 `GlobalHotkeyManager`（Windows `RegisterHotKey`/`UnregisterHotKey`），新增 `Ctrl+Alt+H`（`MOD_CONTROL|MOD_ALT|MOD_NOREPEAT`，id=107）；该模块仅 Windows 原生，macOS/Linux 走托盘/按钮兜底（与项目既有热键范围一致）
- 三入口双向同步：托盘菜单「隐藏字幕/显示字幕」（带 `Ctrl+Alt+H` 提示）、控制中心「隐藏字幕」按钮、全局热键 —— 全部经 `visibility_manager.toggle()` 单一真源
- 注册失败处理：弹 Toast「全局热键 Ctrl+Alt+H 注册失败，字幕显隐热键暂不可用」+ 托盘项置灰去提示 + 控制台 error 日志，不阻塞主流程（托盘/按钮仍可用）
- 隐藏态语义：置顶/位置/字体/样式全部保留；`update_subtitle` 仍刷新标签但不再强制 `show()`，后台翻译/连接逻辑照常；`set_player_state` 在用户隐藏时让位、不抢显隐
- Toast：隐藏「字幕已隐藏」/ 显示「字幕已显示」，1500ms
- 既有 `Ctrl+Shift+O` 与控制中心按钮也改为走 `visibility_manager`，与热键统一

### 字幕悬浮窗 9 宫格锚点（P5 子项）

- 新增 `app/ui/overlay/anchor.py`：`SubtitleAnchorManager`（锚点状态机，`setAnchor()`/`getAnchor()`/`load()`，200ms 防抖写 `subtitle_anchor_point` 到 `subtitle_config` 表，读写失败内存兜底+日志）
- `EventBus` 新增 `subtitleAnchorChanged(str)` 信号；控制中心 3×3 选择器、overlay 二者订阅同步
- 锚点枚举（持久化原值）：`top-left` / `top-center` / `top-right` / `middle-left` / `center` / `middle-right` / `bottom-left` / `bottom-center` / `bottom-right`，默认 `bottom-center`
- **直接复用** `app/player/layout.py` 已有的 `anchor_rect()`（9 点对齐数学）+ `Rect`/`ANCHOR_KEYS`，不重复造轮子；管理器只持有意图，几何计算由 overlay 完成
- 控制中心「字幕样式」页新增 3×3 可视化选择器（9 个 `QPushButton#anchorButton`，独占 `QButtonGroup`，选中高亮用主题 token，悬停 Tooltip 如「底部居中」），点击即时生效
- overlay 新增 `apply_anchor(anchor)`：基于「窗口中心所在显示器」的 `availableGeometry()`（Qt 已自动排除任务栏/Dock 并处理 DPI），用 `anchor_rect()` 算目标几何，可见时 200ms `QPropertyAnimation` 缓动（`OutCubic`）、隐藏时直接 `setGeometry`；终值写回 profile 分數（`save_profile`）持久化
- 多屏/高 DPI：用 `QApplication.screenAt(center)` 取中心点所在屏，Qt 逻辑像素天然处理缩放；写回的是分數不是绝对像素，换分辨率/显示器也能正确还原
- 持久化：切换锚点防抖 200ms 写库；启动 `load()` 读回并高亮选择器；首次运行（`used_default()`）会调一次 `apply_anchor` 让几何与默认选择器一致
- 与既有 profile 系统融合：锚点只是「算一个目标几何 → 移动 → save_profile 存分數」，所以拖动改位置、重启恢复都仍走原 `apply_profile`/`save_profile` 分數机制，不新增位置模型
- 与「双击边缘吸附」的关系：吸附可直接复用 `anchor_rect()` + 本管理器的枚举/事件，开发量减半（已作为公共底座）

### 字幕悬浮窗 双击边缘吸附（P5 子项）

- 完全复用 9 宫格锚点基建：锚点枚举 / 工作区计算 / `anchor_rect()` / `subtitleAnchorChanged` / SQLite 防抖持久化 / `apply_anchor` 的 200ms 缓动，零新增坐标逻辑
- `app/ui/overlay/anchor.py` 新增纯函数 `nearest_anchor(center_x, center_y, screen, width, height)`：遍历 9 个锚点的标准目标位置，取窗口中心点欧氏距离最小者；距离 ≤0.5px 视为平局，按「中下(3) > 左下(2) > 右下(1) > 其他(0)」兜底
- `OverlayWindow` 新增 `snap_to_nearest_anchor()` + 信号 `anchor_requested(str)`；双击走与手动选点**完全一致**的路径：`anchor_requested → Runtime.set_anchor → anchor_manager.setAnchor → subtitleAnchorChanged → overlay.apply_anchor`（移动/动画）→ `window.set_anchor_state`（高亮）+ 防抖持久化，三态同步零额外代码
- 触发判定（`mouseDoubleClickEvent`）：仅在 `can_edit()`（未锁定、非穿透）且左键双击时触发；点击落在 `source`/`translation` 文本标签几何内**不触发**（标签本就 `WA_TransparentForMouseEvents`，用 `panel.mapFrom + geometry().contains` 判定，避免干扰文本选择/复制）；其余空白/边框/角落区域触发
- 误触防护：记录 `_last_snap_time`，300ms 内重复双击忽略
- 边界修复天然成立：`anchor_rect()` 产出永远在工作区内，窗口部分超出时「最近锚点」必然把它完整拉回；多屏/高 DPI 直接继承锚点模块能力
- 顺手修复了上一期遗留崩溃：`mouseDoubleClickEvent` 原调用不存在的 `self.snap_to_screen_edge()`，现已替换为吸附逻辑

### 字幕悬浮窗 样式配置核心层（P5 子项）

- **统一模型 `OverlayAppearance`**（`app/storage/appearance.py`）：字段名即 SQLite 键，完全对齐规范
  `subtitle_font_family` / `subtitle_font_weight` / `subtitle_font_size` /
  `subtitle_original_color` / `subtitle_translation_color` / `subtitle_bg_color` /
  `subtitle_layout_mode`，外加 `show_source` / `topmost` / `anchor` / `preset` /
  `subtitle_colors_customized`（主题锁标记）。附 `parse_color` / `to_rgba_string` /
  `dim_color`（支持 `#rgb`/`#rrggbb`/`#rrggbbaa`/`rgb()`/`rgba()`，alpha 归一 0..1）
- **`SubtitleStyleManager`**（`app/ui/overlay/style_manager.py`）单例：持有 `OverlayAppearance`，
  所有改动经 `update(**changes)` 走 `subtitleStyleChanged` 事件广播 + 300ms 防抖落库
  （`AppearanceRepository` → `overlay_settings` 表）。与窗口位置/交互彻底解耦，仅暴露 get/set 接口
- **完全接线 `OverlayWindow`**：`refresh()` 不再硬编码「微软雅黑+白字」，改为从模型读取字体族/字重/字号/三色；
  `update_subtitle` 等用原文色渲染稳定/草稿段；所有修改经总线实时预览，不写 `settings.json`
- **两种排列模式**：`dual-line`（上下双行，即原 stacked）与 `single-alternate`（单行交替，3 秒轮换，
  鼠标悬停暂停、移开恢复，由 `QTimer` + `enterEvent`/`leaveEvent` 驱动）。旧 `inline`（并排）仅作内部 placement 兼容，UI 不再暴露
- **字体枚举**（`app/storage/fonts.py`）：经 `QFontDatabase`（Windows 即 DirectWrite 字体集）枚举
  系统字体，按 `isFixedPitch` 分「常用/等宽」两组；缺失字体降级到微软雅黑 / Consolas
- **设置面板 `StylePanel`**（`app/ui/style_panel.py`）：字体族下拉（分组 + 字体预览 + 等宽标识）、
  字重下拉（100–900）、字号 SpinBox、3 个 `ColorField`（RGBA + 透明度，含取色吸管 / 最近 8 色 / 推荐色板）、排列切换；
  每项改动即时预览并自动保存；面板订阅 `subtitleStyleChanged` 在主题切换时同步刷新
- **主题适配**：`Runtime.apply_theme` 在 Windows 亮/暗色切换时调 `style_manager.apply_theme_defaults`；
  用户一改颜色即置 `subtitle_colors_customized=True` 并持久化，此后不再被主题覆盖（重启仍生效）
- **验收对应**：①字体分组/等宽标识/兜底 ②字重 100–900（Qt 原生就近降级）③三色透明度+实时预览
  ④双模式切换+自动换行 ⑤单行交替轮换+悬停暂停 ⑥重启保持（SQLite）⑦高 DPI 走逻辑像素
  ⑧跟随系统主题+手动锁定
- **已知简化（非阻断）**：字重未做「按字体族筛可选档位」——Qt6 不暴露逐字体 weight 列表，
  改为展示标准 100–900 并由 Qt 原生就近降级（规范里的「仅展示支持档位」以原生降级+提示替代）

### 字幕悬浮窗 场景预设（P5 收尾闭环）

- **预设定义 `app/storage/presets.py`**：纯数据 `SUBTITLE_PRESETS`（cinema/meeting/reading）+ `PresetSpec`
  （`style` 字典 + `anchor` + `topmost`），字体族用 Qt 实际可解析名（`Microsoft YaHei UI` / `Consolas`）。
  关键 `subtitle_active_preset` 枚举 `cinema` / `meeting` / `reading` / `custom`
- **`SubtitlePresetManager`**（`app/ui/overlay/preset_manager.py`）：**零侵入**——不碰各管理器核心代码，
  仅经公共接口批量设置：`style_manager.update(**spec.style)` / `anchor_manager.setAnchor` /
  `topmost_manager.set_topmost`。订阅总线 `subtitleStyleChanged` / `subtitleAnchorChanged` / `topmost_changed`，
  当 `active!=custom` 且实时状态与当前预设 spec 不一致时自动 `mark_custom`（当前为 custom 时跳过，避免误标）；
  应用期间用 `_applying` 标志屏蔽自身触发，避免回环。应用失败（异常）用 `_snapshot`/`_restore` 回滚
- **持久化**：`subtitle_active_preset` 走 `ConfigRepository` 键值存储（复用 `app_config` 表，非新表）；
  各分项配置仍走原管理器防抖落库；手动改任意配置即置 `custom` 并立即持久化该键
- **设置面板 `StylePanel`** 顶部新增「场景预设」`QGroupBox`：三张并排卡片（影院/会议/阅读，带场景说明 +
  彩色描边强调色），`setCheckable` + 高亮边框标识当前生效；悬停 Tooltip 说明；点击经 `preset_manager.apply`
  → 字幕窗同步刷新样式/位置/置顶；订阅 `presetApplied` 刷新高亮与「当前预设/自定义方案」提示；
  应用后附带 200ms `windowOpacity` 脉冲（`_pulse`，平滑过渡反馈）。`MainWindow` / `Runtime` 已注入 `preset_manager`
- **验收对应**：①一键生效（字体/三色/锚点/置顶全量同步）②面板各控件 + 9 宫格 + 置顶开关经总线同步
  ③重启保持（SQLite + 重读 `subtitle_active_preset`）④手动改任意配置→高亮取消并标识自定义
  ⑤各管理器核心代码零改动（仅走公共接口）⑥切换走原事件广播，无额外重绘

### Provider 注册表与有序回退链

- 新增 `app/translation/registry.py`：预设 + `TranslationProviderRegistry` + `plan_from_settings`
- **Provider 固定 4 个**：Ollama、OpenAI Compatible、DeepSeek（OpenAI Compatible 预设）、豆包（同前，endpoint id 必填）
- 云厂商扩展口保留（抽象基类 + preset 注册表），**百度 / 阿里 / Google / Azure 已砍掉，当前不做**
- 回退链在这 4 个里排序，`translation_chain[0]` 先应答；缓存按 Provider 独立命名空间查找
- 密钥环境变量：`OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ARK_API_KEY`
- 翻译页 UI：4 个 Provider 下拉、按 Provider 切换地址/模型（豆包显示 Endpoint ID）、
  endpoint id 必填校验（标红 + 禁用「测试连接」+ toast）、回退链列表上移/下移/加入/移除
- 「测试连接」只测当前选中的 Provider：缺密钥直接报「未检测到密钥：请先设置 XXX」，
  有密钥才发真实 `/models`（Ollama 为 `/api/tags`）请求，结果走 toast + 状态栏

### P6 UI 优化

- 翻译页状态区改为数字卡片 + 迷你折线：生效 Provider、实际模型、最近 50 次 P50 / P95、缓存命中率
  （`app/ui/metrics.py` 的 `MetricCard` / `MetricRow` / `Sparkline`，纯 QPainter，无图表依赖）
- `app/translation/stats.py` 的 `LatencyTracker`：50 条滚动窗口，P50/P95/命中率/序列
- 翻译失败走 toast（3 秒自动消失），不用模态框；toast 颜色跟随主题 token
- 「测试连接」有 loading 态（按钮变「测试中…」并禁用），结果以 toast + 状态栏反馈；
  缺密钥直接报「未检测到密钥：请先在当前终端设置 XXX」，不发请求
- 未点「应用翻译设置」时按钮变黄并带 ●，点应用后复位
- 主题 token 化（`app/ui/theme.py`）：读取 Windows `AppsUseLightTheme` 跟随亮/暗色，
  监听 `ApplicationPaletteChange` / `ThemeChange` 实时切换，不再硬编码颜色
- 控制中心的关闭按钮改为收起到托盘（双击托盘图标恢复），托盘菜单「显示控制中心」
- 新增设置项补了 tooltip，并标明「立即生效」

### 端到端延迟实测脚本（P0 质量基建）

- **位置**：`tests/e2e_latency.py`（核心逻辑 + CLI 入口）+ `tests/e2e_latency_config.json`（配置）
  + `tests/test_e2e_latency.py`（10 个 pytest：统计/配置/像素 diff/总线集成）
- **零侵入**：不修改任何业务代码，仅通过公共接口复用 `EventBus` / `SubtitlePipelineController` /
  `TranslationService` / `OverlayWindow`；测试替身 `_FakeTranslationProvider` 返回与真实 Provider
  相同的 `ProviderResult` 形状，纯测试侧，业务无改动
- **采集方案（纯 Windows 原生）**：
  - 计时统一 `QueryPerformanceCounter`（≤ 1ms，非 Windows 退回 `time.perf_counter`）
  - 分段埋点靠**订阅**总线与信号：`bus.recognition`（识别完成，由脚本模拟 ASR 产出注入）、
    `TranslationService.translated`（翻译完成）、`bus.subtitle`（渲染开始，按 `translated_text`
    非空判定最终帧，规避「翻译中…」中间帧，且与信号连接顺序无关）
  - 端到端终点用 **GDI 像素探针**：`PrintWindow` + `GetDIBits` 抓字幕窗客户区，稳定匹配最终帧即判定
    像素渲染完成（用户可感知的真实延迟）；无显示/`--no-pixel` 时退回字幕信号代理模式
- **指标**：端到端总延迟 + 分段（识别/翻译/管线装配/渲染）、均值/最值/P50/P95/P99，线性插值百分位
- **场景覆盖**：`basic`（冷启动 + 稳态）、`style`（影院/会议/阅读预设渲染耗时差异）、
  `state`（置顶开/关、显示/隐藏后台）、`load`（短/长文本）；`vary_text_per_iteration`（默认开）保证
  每次为真实缓存未命中以测真实引擎耗时，关闭则复现缓存主导的稳态
- **报告**：控制台实时进度 + 结构化 JSON（环境/配置/各场景汇总+样本/异常/瓶颈占比）+ 每样本 CSV；
  瓶颈分段占比分析，超 `thresholds` 标记异常；超时/超 `discard_above_ms` 的硬异常值剔除出统计
- **运行**：`python -m tests.e2e_latency [--config ...] [--scenario basic|style|state|load]
  [--iterations N] [--no-pixel] [--output-dir ...]`；自动预热、自动丢弃异常值、可单场景/全量
- **验收对应**：①同场景重复偏差（统计可量化）②覆盖全部指定场景与指标 ③业务代码零改动、不影响翻译流程
  ④相同环境可复现 ⑤JSON/CSV 数据准确、百分位无误 ⑥阈值超界正确标记异常

### 横向性能基准：接入真实 Provider（P0 扩展）

- **零侵入接入真实后端**：复用应用自身 `TranslationProviderRegistry().build_chain(plan)`
  （与 `Runtime` 完全相同的构建路径）生成 Ollama / OpenAI 兼容（LM Studio / vLLM /
  DeepSeek / 豆包 Ark 等）真实 Provider 实例；`E2ELatencyTester._apply_provider_spec`
  按配置 `providers` 列表切换实时翻译链（`set_providers` + `set_language_pair` + `initialize()`），
  业务代码零改动
- **配置（`e2e_latency_config.json`）**：`providers` 列表每项 `{name, provider_id, base_url, model,
  api_key_environment?, api_key?, options?:{timeout,temperature,keep_alive}}`；`providers_example`
  为同结构示例（加载时被忽略）。`language_pair` 默认 `["zh","cinema"]`；`concurrency` 默认 1
- **横向对比**：配置多个 `providers` 时，脚本对每个 Provider 依次跑全部场景，报告新增
  `provider_summary`（各 Provider 端到端 P50/P95/P99 + 翻译均值）与控制台「多 Provider 横向对比」表，
  直接支撑「不同模型规格 / 不同 Provider 默认选型」量化决策
- **失败优雅处理**：Provider 不可用 / 连接失败 → 服务发 `error` 信号（不发 `translated`）→
  测量超时并标记为 `translation_error` 异常（不崩溃）；初始化失败则跳过该 Provider 并计入
  `unavailable_providers`。`run()` 结束调用 `translation.cancel_all()` 清理在途任务，避免事件循环退出告警
- **并发维度**：`concurrency>1`（仅代理模式，像素探针强制串行）时 `load` 场景按批次并发发起识别、
  各自独立分段埋点（按 `segment_id` 隔离 `_pending`，天然并发安全），覆盖「不同并发场景」延迟分布
- **CLI 增强**：`--provider NAME`（仅跑指定 Provider）、`--render-timeout MS`（真实 Provider 建议 ≥5000）、
  `--concurrency N`。配置加载容忍 UTF-8 BOM（`utf-8-sig`）
- **测试覆盖**：`test_real_provider_build_failure_is_graceful`（未知 provider 跳过不崩）、
  `test_real_provider_error_flagged`（真实实例 + 注入异常 → 标记 translation_error 异常），共 12 个测试全绿

## 关键决策与原因

1. **不做影院两行裁剪模块**。纯翻译 API 无状态、装不下上下文；同理，译文长度也不能靠显示层硬切。
   改为框高 → N → prompt 约束，显示层原样输出。框拉小自动精简，拉大自动完整。
2. **不重写现有内存 LRU**，只在外面加 SQLite 层，避免风险，且保留原有行为（`ttl_seconds=0`）。
3. **字幕框设置持久化到 SQLite**，不散在 QSettings（P5 要求，P3 已在建库）。
4. **DeepSeek / 豆包不新写 Provider 类**，它们是 OpenAI 兼容协议，注册为 preset 即可。
5. **Provider 数量收敛到 4 个**。原计划的百度 / 阿里 / Gemini / Azure 适配层整体砍掉，
   注册表保留扩展口，未来需要时再加。

## 遗留问题

- 密钥仍走环境变量，未接 Windows Credential Manager（P2）
- 端到端延迟实测**脚本**已实现（见上方「端到端延迟实测脚本」小节）；真实各家 Provider 延迟对比
  待接入真实 Provider（Ollama/OpenAI 兼容）后跑实测，脚本可直接复用
- P5 主体功能已完整闭环：3 套样式预设（影院/会议/阅读）UI 与一键应用已全部实现并接线
  （见「场景预设」小节）；字体族/字重/字号/原文译文色/译文底色/双行或单行交替、窗口置顶、
  Ctrl+Alt+H 全局热键、9 宫格锚点、双击边缘吸附均已落地
- 翻译页「测试连接」还没做成功/失败图标（当前是文字 + toast）
- 无 git：交付机器上 `git` 不在 PATH，提交需自行执行
