# GitHub 发布说明书

面向维护者：把本项目的**源码**与**可直接运行的 Windows 便携包**发布到 GitHub，让别人一键下载。

- 仓库：`https://github.com/YulinFU123/FiveLangTranslator`
- 用户下载页：`https://github.com/YulinFU123/FiveLangTranslator/releases/latest`

> README 里的下载链接指向 `/releases/latest`，因此**每次发版不需要改 README**。

---

## 0. 环境准备（只需一次）

本项目是 Windows 工程，但发布脚本是 bash，需要 **Git for Windows** 自带的 bash。

| 工具 | 路径 |
| --- | --- |
| git | `C:\Program Files\Git\bin\git.exe` |
| bash（跑发布脚本） | `C:\Program Files\Git\bin\bash.exe`（或开始菜单打开 *Git Bash*） |

设置身份（**提交作者目前是占位符 `你的用户名 <你的邮箱>`**，建议改成你自己的）：

```bash
git config --global user.name "你的名字"
git config --global user.email "你的GitHub邮箱"
```

推送凭据：GitHub 已不支持账号密码，需在 GitHub → *Settings → Developer settings → Personal access tokens* 生成 **PAT**（勾选 `repo`），推送时用户名填 GitHub 账号、密码填 PAT。

---

## 1. 日常开发提交

提交信息遵循 **Conventional Commits**（`release.sh` 按前缀生成 CHANGELOG）：

```
feat: 新功能
fix: 修复
docs: 文档
refactor: 重构
test: 测试
chore: 构建/其他
```

常用命令（PowerShell）：

```powershell
$g = "C:\Program Files\Git\bin\git.exe"
& $g add -A
& $g status                      # 提交前务必核对：不要有密钥、数据库、.venv、dist
& $g commit -m "fix(ocr): 识别框停止后仍显示"
& $g push origin master
```

**切勿提交**（`.gitignore` 已挡，但仍要留意）：
- `settings.json`（含 DPAPI 加密的 API Key）、`*.db` / `*.sqlite*`（含历史与隐私）
- `.venv/`、`dist/`、`build/`、`*.zip`、模型与 whisper.cpp 二进制（大文件，GitHub 单文件限 100MB）

---

## 2. 发版（推荐：`release.sh` 一键流程）

`scripts/release.sh` 是原子化发布：合规校验 → 生成 CHANGELOG → 回归门禁 → 版本提交 → 打标签，任一步失败自动回滚。

在 **Git Bash** 中执行：

```bash
./scripts/release.sh v1.0.6 --dry-run        # 先预览
./scripts/release.sh v1.0.6 --bump --push    # 正式发布：同步版本号 + 推送标签
```

| 参数 | 说明 |
| --- | --- |
| `--dry-run` | 只预览，不改动 |
| `--bump` | **强烈建议常加**：把版本号写回 `pyproject.toml`，避免标签与包版本漂移 |
| `--push` | 发布后推送标签到 `origin`（默认只本地打标签） |
| `--full-test` | 全量回归（默认 `quick`） |

自检脚本（在临时仓库里验证，不碰真实仓库）：

```bash
./scripts/verify_release.sh
```

---

## 3. 发版（手动流程，脚本不可用时的退路）

v1.0.5 就是用这条路径发的：

```powershell
# 1) 改版本号 pyproject.toml: version = "1.0.6"
# 2) 构建便携包（不加 -SkipZip 才会出 zip）
.\scripts\build.ps1
#   产出 dist\FiveLangTranslator-<版本>-win64.zip

# 3) 提交并推送
& $g add -A
& $g commit -m "chore(release): bump version to 1.0.6"
& $g push origin master

# 4) 打标签并推送（触发 CI 自动发布）
& $g tag -a v1.0.6 -m "v1.0.6: 说明"
& $g push origin v1.0.6
```

---

## 4. 自动发布（GitHub Actions）

`.github/workflows/release.yml` 已配置：**推送 `v*` 标签即自动触发**。

流程：`windows-latest` → 装 Python 3.12 → `pip install -e ".[audio,dev]"` → `scripts/build.ps1` → 把 `dist\FiveLangTranslator-*.zip` 作为附件发布到 Releases（自动生成 release notes）。

发布后检查：
1. 仓库页 → **Actions**：确认 `release` 工作流是绿色
2. 仓库页 → **Releases**：确认出现对应版本与 zip 附件

**CI 失败时的退路**：手动上传本地已构建的 zip
`dist\FiveLangTranslator-<版本>-win64.zip` → Releases → *Draft a new release* → 选标签 → 拖入附件 → Publish。

也可以在 Actions 页面用 *Run workflow* 手动触发（无需打标签）。

---

## 5. 常见坑（都真实发生过）

| 现象 | 原因 / 处理 |
| --- | --- |
| **标签已存在**（`fatal: tag 'v1.0.5' already exists`） | 该版本已发布过。**换一个新版本号**（如 v1.0.6），不要覆盖旧标签 |
| 工作区脏被拦截 | `release.sh` 要求干净工作区；`git add` 不算，必须 `git commit` |
| `warning: LF will be replaced by CRLF` | `core.autocrlf` 正常提示，忽略即可 |
| 推送失败 / 要求认证 | 用 PAT 代替密码（见第 0 节） |
| 本地领先远端却没发布 | 推送 `master` 只是同步源码；**必须再推送标签**才会触发发布 |
| 本地包能跑、CI 构建的不能跑 | CI 是干净环境，检查是否误依赖本机才有的文件（模型、whisper 二进制等） |

---

## 6. 发布后清单

- [ ] Releases 页面能看到新版本与 zip 附件
- [ ] 下载 zip 到**另一台干净机器**解压试运行（验证 CI 产物）
- [ ] 确认 zip 内不含你的 `settings.json` / 数据库 / API Key
- [ ] CHANGELOG 已由 `release.sh` 更新（手动流程需自己补）
- [ ] 如需更新"首次使用"说明，改 README 的「下载」小节

---

## 7. 命令速查

```powershell
$g = "C:\Program Files\Git\bin\git.exe"

& $g status                          # 看改动
& $g log --oneline -5                # 看提交
& $g rev-list --left-right --count origin/master...HEAD   # 左=落后 右=领先
& $g push origin master              # 推源码
& $g tag                             # 列标签
& $g push origin v1.0.6              # 推标签（触发发布）
```

相关文档：
- `docs/RELEASE.md` —— `release.sh` 详细参数与回滚机制
- `docs/SELF_CHECK.md`、`CHANGELOG.md`
- `docs/WHISPER_CPP_SETUP.md`、`docs/WHISPER_SERVER.md` —— 语音后端（用户首次运行需按此准备模型）
