# 发布前全量回归清单（Regression Checklist）

> 配合 `tests/run_regression.sh` 使用：`quick` 高频门禁 / `full` 发布全量 / `benchmark` 仅延迟基准。
> 所有条目需在目标发布平台（Windows 主用）走一遍；标注「自动化」的由 pytest 覆盖。

## 一、P5 主功能回归

### 识别（音频 → 文本）
- [ ] 系统音频采集可启动 / 停止，无设备占用冲突
- [ ] 稳定稿（stable）与草稿稿（draft）分离，草稿随说话实时刷新
- [ ] 静音 / 无语音时不产生空字幕
- [ ] 中英文混说识别语言标记正确
- [ ] 识别延迟稳定性：queue_wait_ms 指标可观测；单 lane 下 stale DRAFT（>1500ms）被跳过，不拖累 FINAL
- [ ] FINAL 中断式抢占：在途/排队 DRAFT 被 FINAL 抢占丢弃；preemption_count / draft_discarded_count / preemption_save_ms 准确；FINAL 在途不被抢占；抢占后 DRAFT 流程无残留

### 翻译（Provider 链）
- [ ] Ollama 本地模型翻译正常（`keep_alive` 生效，连续翻译不重复冷加载）
- [ ] OpenAI Compatible（LM Studio / vLLM / DeepSeek / 豆包）可切换并翻译
- [ ] 主 Provider 失败时按 `translation_chain` 顺序回退
- [ ] 全部失败时在字幕区提示「翻译失败」而非静默
- [ ] 翻译缓存命中（相同短语）显著降低二次延迟
- [ ] 并发同句 in-flight 去重：负载场景下相同文本只打一次 Provider（service._inflight）
- [ ] 草稿翻译去抖（draft_debounce_ms，默认 160ms）生效，可经 set_draft_debounce 调参
- [ ] 术语表生效，且缓存键随术语表变化失效
- [ ] `api_key` 明文 / `api_key_environment` 环境变量两种注入均可

### 字幕渲染（Overlay）
- [ ] 浮窗显示 / 隐藏 / 置顶（topmost）开关正常
- [ ] 影院 / 会议 / 阅读三种预设样式切换并重绘
- [ ] 双行 / 内联 / 单行交替三种布局正确
- [ ] 草稿「翻译中…」过渡帧不闪烁（已做 repaint 合并，见 window.py）
- [ ] 窗口拖拽 / 缩放 / 九宫格锚点吸附、持久化
- [ ] 播放器最小化 / 全屏跟随与自动 profile 切换

### 历史与导出
- [ ] SQLite 字幕会话持久化，可回看
- [ ] SRT / ASS / WebVTT 导出内容与时序正确

### 设置与持久化
- [ ] 翻译链、端点、样式、布局、锚点重启后保持
- [ ] 窗口化 / 全屏 profile 互不串扰

## 二、P0 性能基建回归（自动化）

- [ ] `pytest` 全绿（186 用例含 12 新增、2 个真实 Provider 构建/连接异常路径）
- [ ] `e2e_latency` 基准：fake 模拟 + 真实 Provider 均输出 JSON / CSV
- [ ] Ollama 不可达（WinError 10061）被正确捕获并抛 `translation_error`，计入异常不崩溃
- [ ] 分段耗时（recognition / translation / pipeline / render）拆分正确
- [ ] 多 Provider 横向对比表（P50 / P95 / P99）生成

## 三、边缘 / 异常场景

- [ ] 空文本、超长文本（> max_lines）不崩溃、不越界
- [ ] 网络抖动 / Provider 中途不可用：字幕降级而非卡死
- [ ] 窗口最小化时 GDI 像素探针降级为信号代理（不误报超时）
- [ ] 高并发负载（`--concurrency 4/8`）P99 抬升在可接受范围

## 四、发布门槛

- [ ] 上述手动项全部勾选
- [ ] `./tests/run_regression.sh full` 通过
- [ ] 延迟报告 `provider_summary` 中默认 Provider P95 满足发布阈值
- [ ] 构建产物（exe / 安装包）在干净环境可启动
