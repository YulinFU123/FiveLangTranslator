# 变更日志

## [v1.0.7] - 2026-10-01

### 修复 (fix)
- fix(translation): 修复 v1.0.6 背压导致字幕翻译变慢/几乎不更新的回归——原逻辑会取消"正在执行"的翻译任务（在快出结果前被腰斩，永远跑不完）；改为队列式背压：最多 2 条真正在跑，其余进队列，仅在队列溢出时丢弃"未开始"的最旧句，跑完腾出槽位再取最新，字幕始终锚定实时语音

### 其他 (chore)
- 构建产物: dist/FiveLangTranslator-1.0.7-win64.zip（PyInstaller onedir 绿色版，已嵌入版本信息）

## [v1.0.6] - 2026-10-01

### 修复 (fix)
- fix(translation): 新增翻译背压与过期丢弃，消除字幕严重滞后（积压滚雪球导致音画不符）；待翻译 FINAL 段最多保留最新 2 个，落后最新语音超过 20s 的旧翻译直接丢弃，字幕锚定实时语音

### 其他 (chore)
- 构建产物: dist/FiveLangTranslator-1.0.6-win64.zip（PyInstaller onedir 绿色版，已嵌入版本信息）

## [v1.0.4] - 2026-09-28

### 新特性 (feat)
- feat(ui): 模型下载界面补充就绪状态、完整性校验与下载源选择
- feat(packaging): 应用图标、Windows 版本信息、关闭控制台黑窗，识别页新增 VAD 激活概率指示
- feat(ui): 识别页模型管理下载界面，含进度/重试/SHA256校验与未就绪拦截
- feat(packaging): 冻结路径适配、PyInstaller onedir 构建与模型下载器核心

### 修复 (fix)
- fix(packaging): 模型下载后自动补齐 whisper.cpp 二进制与 VAD 模型，否则无法达到就绪状态

### 其他 (chore)
- perf(vad): Silero VAD 改用纯 onnxruntime 推理，移除 torch 依赖
- 构建产物: dist/FiveLangTranslator-1.0.4-win64.zip（51.7 MB，PyInstaller onedir 绿色版，已嵌入图标与版本信息）


## [v1.0.3] - 2026-09-27

### 新特性 (feat)
- feat(release): 发布脚本支持 --bump 同步版本号，补充验证用例与文档

### 其他 (chore)
- chore: 同步项目版本号至 1.0.1，与 v1.0.1 标签对齐


## [v1.0.2] - 2026-09-27


## [v1.0.1] - 2026-09-27

### 修复 (fix)
- fix(release): 修复回滚后索引未刷新导致的幽灵脏状态，补充验证套件与发布文档


## [v1.0.0] - 2026-09-27

### 新特性 (feat)
- feat: 五语翻译字幕主链路与性能/发布基建
- feat(overlay): 字幕样式配置核心层——SubtitleStyleManager+字体/颜色/双行单行交替+实时预览持久化

