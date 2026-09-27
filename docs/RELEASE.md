# 标准化版本发布流程

`scripts/release.sh` 提供一键式原子化版本发布：合规校验 → 变更日志生成 → 回归门禁 → 版本标签提交 → （可选）远程推送。任意阶段失败均触发全链路回滚，不留中间状态。

## 前置条件

- 位于 Git 工作仓库内
- 当前分支为 `master`
- 工作区干净（无未提交 / 未暂存变更）
- 目标标签在本地不存在
- 存在 `tests/run_regression.sh`（回归门禁，支持 `quick` / `full`）

## 使用

```bash
./scripts/release.sh <version> [options]
```

| 参数 | 说明 |
|------|------|
| `<version>` | 目标版本号（必填），语义化 `vX.Y.Z`，支持 `alpha` / `beta` / `rc` 预发布后缀，如 `v1.0.0`、`v1.1.0-beta.1` |
| `--dry-run` | 试运行：仅输出流程预览与变更日志，不执行任何实际修改（脏工作区仅提示不拦截） |
| `--full-test` | 执行全量回归（默认 `quick` 快速门禁） |
| `--push` | 发布成功后推送标签至 `origin`（默认仅本地发布；推送失败仅告警、不回滚） |
| `--force` | 跳过工作区干净检查（不推荐） |
| `-h, --help` | 显示帮助 |

示例：

```bash
./scripts/release.sh v1.0.0 --dry-run      # 预览
./scripts/release.sh v1.0.0                # 正式发布（quick 门禁）
./scripts/release.sh v1.0.0 --full-test    # 全量回归门禁
./scripts/release.sh v1.0.0 --push         # 发布并推送标签
```

## 流程阶段

| 阶段 | 内容 | 失败后果 |
|------|------|----------|
| 1/4 环境校验 | 仓库有效性、分支、版本格式、标签唯一性、工作区干净 | 直接终止，无任何状态变更 |
| 2/4 变更日志生成 | 按上一标签取增量提交，按 Conventional Commit 前缀分类，写入 `CHANGELOG.md` 头部 | 回滚文件 |
| 3/4 回归测试门禁 | `bash tests/run_regression.sh quick` / `full`，退出码非零即不通过 | 回滚变更日志 |
| 4/4 版本标签提交 | 提交 `chore(release): vX.Y.Z`（仅含 `CHANGELOG.md`），创建附注标签 | 回滚标签、提交、变更日志 |
| 远程推送（可选） | `git push origin <tag>` | 仅告警，保留本地标签 |

变更日志分类映射：`feat` → 新特性、`fix` → 修复、`docs` → 文档、`refactor` → 重构、`test` → 测试、`chore` → 其他（无前缀提交亦归入此类）。

首个版本（无历史标签）生成全量提交日志；后续版本取 `上一个标签..HEAD` 增量。

## 原子性与回滚

采用回滚栈：每个可变更步骤注册对应回滚动作，异常时**逆序**执行，覆盖三类变更：

| 变更类型 | 回滚动作 |
|----------|----------|
| 文件修改 | 从备份恢复（文件原已存在）或删除（文件为新建） |
| Git 提交 | `git reset --hard HEAD~1` |
| Git 标签 | `git tag -d <version>` |

已撤销或无操作的步骤会跳过并告警，保证回滚流程完整执行完毕。

## 幂等性

- 重复发布同一版本：`标签已存在` 直接拦截，不产生任何变更
- 中断后重跑：受标签唯一性与工作区干净检查保护，不会重复生成日志 / 提交 / 打标签
- `--dry-run` 不产生任何修改

## 配置

脚本头部集中配置，可用环境变量覆盖：

| 变量 | 默认值 |
|------|--------|
| `RELEASE_BRANCH` | `master` |
| `CHANGELOG_FILE` | `CHANGELOG.md` |
| `REGRESSION_SCRIPT` | `./tests/run_regression.sh` |
| `REMOTE_NAME` | `origin` |

## 验证

```bash
./scripts/verify_release.sh
```

在临时 scratch 仓库中隔离验证，不触碰真实仓库，覆盖 6 项：

| 用例 | 验证点 |
|------|--------|
| A | 回归脚本缺失 → 门禁失败 → 回滚（新建文件删除） |
| B | 完整成功流程：提交仅含 CHANGELOG、附注标签正确 |
| C | 幂等性：重复发布被拦截 |
| D | 门禁失败 → 回滚（备份恢复已有 CHANGELOG） |
| E | 注入标签创建失败（`reference-transaction` hook）→ 回滚发布提交与变更日志 |
| F | 标签回滚动作有效性 |

## 常见问题

**工作区脏被拦截**：先提交或暂存变更。注意**暂存不等于提交**——`git add` 后仍会被拦截，必须 `git commit`。

**`--force` 的风险**：脚本只 `git add CHANGELOG.md`，不会把未跟踪文件带入提交；但标签会指向缺少未提交工作的树，仅在确知「仅打标签」时使用。

**`warning: LF will be replaced by CRLF`**：Git `core.autocrlf` 的正常提示，不影响内容。

**发布后需推送**：本次未加 `--push` 时标签仅在本地，可手动 `git push origin <tag>`。
