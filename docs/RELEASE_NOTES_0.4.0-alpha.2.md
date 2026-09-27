# FiveLangTranslator v0.4.0-alpha.2

本版本把识别和翻译结果可靠地保存到本地 SQLite，并补齐术语表编辑器与字幕导出能力，为后续 OCR 阶段复用同一套历史与导出链路。

## 新增

- SQLite 历史数据库（`~/.five_lang_translator/history.db`）
  - `sessions` 会话、`segments` 字幕片段、`translation_cache` 持久缓存、`glossary` 术语表
- 字幕会话概念
  - 手动「开始记录 / 停止记录」
  - 勾选后跟随音频采集自动开启会话（设置项 `auto_record_sessions`，默认开启）
  - 会话列表显示时间、名称、片段数与记录中标记
- 历史写入规则
  - DRAFT 不写库，只有 STABLE / FINAL 进入历史
  - 同句片段按 `(session_id, segment_key)` 覆盖更新
  - 低 revision 或更低状态等级不会覆盖 FINAL 结果
  - 译文为空的识别刷新不会清掉已有译文
  - 支持按原文 / 译文检索
- 持久化翻译缓存（双层，不重写现有内存 LRU）
  - L1：现有 `TranslationCache` 内存 LRU，增加可选 TTL（默认 300 秒）
  - L2：`SqliteCacheStore`，支持条目级过期时间
  - 查询顺序 L1 → L2 → Provider；写入时 L1 与 L2 同时落
  - 启动预热：从 SQLite 取最近访问的 500 条灌回 L1
  - 淘汰：先清过期条目，再按 accessed_at 淘汰到 20000 条以内
  - UI 区分「内存命中 / SQLite命中 / 未命中」
- `segments` 表补齐计费与诊断字段
  - provider / model / latency_ms / cache_hit
  - source_type 收敛为 `asr` / `ocr`（system_audio、microphone 归为 asr，OCR 阶段直接写 ocr）
- 术语表可视化编辑器
  - 表格直接添加、编辑、删除
  - 一键应用到翻译服务
  - JSON 导入 / 导出
  - 术语参与缓存键，修改术语后不会误用旧译文
- 字幕导出
  - SRT
  - WebVTT
  - ASS
  - TXT
  - JSON
  - 导出内容可选：双语 / 仅译文 / 仅原文
  - 导出前自动整理时间轴：排序、去重叠、最短 500 ms、最长 7000 ms

## UI

控制中心新增两个页面：

- 术语表
- 历史与导出

翻译页面的指标增加了缓存来源显示。

## 数据库位置

```text
%USERPROFILE%\.five_lang_translator\history.db
```

## 导出时间轴说明

whisper 给出的时间戳是相对于采集起点的偏移。会话开启时间与采集起点对齐，因此片段的 `start_ms` / `end_ms` 可以直接当作会话偏移用于导出。

## 测试

```text
python -m compileall app
python -m pytest
```

当前基线：`compileall` 通过，单元测试 69 项全部通过（v0.4.0-alpha.1 为 39 项）。
新增 `tests/test_cache_layers.py`：L1 TTL 过期后回落到 L2、双写回、启动预热、SQLite 条目过期与淘汰、`segments` 新字段、`source_type` 收敛。

## 尚未完善

- Provider 注册表与有序回退链已完成，Provider 固定为 Ollama / OpenAI Compatible / DeepSeek / 豆包 4 个；
  云厂商扩展口保留（抽象基类 + preset），未来需要时再加，当前不做
- 翻译页 UI：Provider 下拉、豆包 endpoint id 必填校验、回退链排序
- Windows Credential Manager 存放 API Key
- 持续 OCR：区域框选、RapidOCR、图像变化检测、去重与 OCR 翻译
- 安装包
