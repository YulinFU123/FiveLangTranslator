# 变更日志

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

