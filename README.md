# FiveLangTranslator v0.4.0-alpha.2

本地离线实时双语悬浮字幕工具。v0.4.0-alpha.1 打通了「真实音频 → Silero VAD → whisper.cpp 五语识别 → Ollama/OpenAI 兼容翻译 → 悬浮双语字幕」全链路；本版本把这条链路上的结果可靠地保存下来，并补齐术语表编辑器与字幕导出。

## 安装运行

前置依赖：

1. Python 3.11
2. whisper.cpp Vulkan 版 + 多语言 GGML 语音模型 + 常驻 whisper-server
3. Ollama 本地服务 + 已 pull 的翻译模型（默认 `qwen3:4b`），或任意 OpenAI `/chat/completions` 兼容服务

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[audio]"
python -m app.main
```

OpenAI Compatible 的密钥只从环境变量读取，不写入配置文件：

```powershell
$env:OPENAI_API_KEY="your-key"
python -m app.main
```

## v0.4.0-alpha.2 新增

- SQLite 历史数据库：会话、字幕片段、持久翻译缓存、术语表
- 字幕会话：手动开始 / 停止，或在开始音频采集时自动开启会话
- 翻译页状态区：生效 Provider、实际模型、最近 50 次 P50 / P95 延迟、缓存命中率（数字卡片 + 迷你折线）
- 错误与连接测试结果走 3 秒 toast；「测试连接」有 loading 态；未应用改动会高亮应用按钮
- 跟随 Windows 亮/暗主题（读 `AppsUseLightTheme`，实时切换）；控制中心关闭后收起到托盘
- 历史写入规则：DRAFT 不入库，低版本或低状态不覆盖 FINAL，识别刷新不清空已有译文
  - `segments` 记录 provider / model / latency_ms / cache_hit / source_type（`asr` 或 `ocr`）
- 双层翻译缓存（现有内存 LRU 不重写，只在外面加一层）：
  - 查：L1 内存 LRU（TTL 300 秒）→ L2 SQLite（可带过期时间）→ Provider
  - 写：Provider 返回后同时写 L1 与 L2
  - 启动时从 SQLite 预热最近 500 条到 L1；L2 超过 20000 条按最后访问时间淘汰
  - UI 区分「内存命中 / SQLite命中 / 未命中」
- 术语表可视化编辑器：增删改、应用到翻译服务、JSON 导入导出
- 字幕导出：SRT / WebVTT / ASS / TXT / JSON，支持双语、仅译文、仅原文
- 控制中心新增「术语表」与「历史与导出」两个页面

数据库位置：

```text
%USERPROFILE%\.five_lang_translator\history.db
```

## 翻译配置

1. 启动 Ollama 或其他兼容服务。
2. 在「翻译」页面选择 Provider，填写地址与模型名。
3. 选择目标语言（简中 / 英语 / 日语 / 俄语 / 德语）与风格（影院精简 / 完整翻译 / 直译）。
4. 点击「测试连接」，通过后点击「应用翻译设置」。
5. 需要固定译法时到「术语表」页面添加词条并应用。

翻译页下拉固定 4 个 Provider：

| Provider | 说明 | 密钥环境变量 |
|---|---|---|
| Ollama | 本地推理 | 不需要 |
| OpenAI Compatible | LM Studio / vLLM / 自建网关 | `OPENAI_API_KEY` |
| DeepSeek | 复用 OpenAI 兼容适配层的预设 | `DEEPSEEK_API_KEY` |
| 豆包（火山方舟） | 同上，模型位填推理接入点 ID（`ep-xxxxxxxx`，必填校验） | `ARK_API_KEY` |

- 页面里「回退链顺序」列表可上移/下移/加入/移除，最前面的先应答
- 选中豆包但 endpoint id 为空时输入框标红、「测试连接」禁用并弹 toast
- 「测试连接」只测当前选中的 Provider；没有对应密钥会直接报「未检测到密钥」

## 历史与导出

1. 在「历史与导出」页面填写会话名称，点击「开始记录」（也可由音频采集自动开启）。
2. 识别过程中 STABLE / FINAL 字幕会实时写入 SQLite。
3. 停止采集后点击「停止记录」。
4. 选择会话、格式与内容模式，点击「导出当前会话」。

## 当前完整链路

```text
系统声音或麦克风
→ 16 kHz 有状态重采样
→ Silero VAD
→ DRAFT / FINAL 语音段
→ 常驻 whisper-server
→ 五语识别与语言锁定
→ 字幕共同前缀稳定
→ Ollama 本地翻译
→ 最近三句上下文 + 术语表
→ 双层翻译缓存
→ 悬浮双语字幕
→ SQLite 历史与导出
```

## 当前限制

- 更准确的影院两行裁剪尚未完成
- OpenAI Compatible 密钥尚未接入 Windows Credential Manager
- Provider 固定 4 个（Ollama / OpenAI Compatible / DeepSeek / 豆包）；云厂商扩展口保留，当前不再新增
- 翻译页的 Provider 下拉与回退链排序 UI 尚未补上（目前通过 settings.json 配置）
- 持续 OCR（区域框选、RapidOCR、图像变化检测、去重与 OCR 翻译）尚未开始
- 安装包尚未提供

## 测试

```powershell
python -m compileall app
python -m pytest
```

当前基线：`compileall` 通过，单元测试 60 项全部通过。
