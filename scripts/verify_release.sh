#!/usr/bin/env bash
# release.sh 隔离验证套件
# 在临时 scratch 仓库中验证发布全流程与回滚，完全不触碰真实仓库。
# 用法: ./scripts/verify_release.sh
set -uo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_SH="$SRC_DIR/release.sh"
WORK=/tmp/release_verify_$$

if [[ ! -f "$RELEASE_SH" ]]; then
  echo "未找到 release.sh: $RELEASE_SH" >&2
  exit 1
fi

rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK" || exit 1

git init -q .
git config user.email "verify@example.com"
git config user.name "Verify"
git config commit.gpgsign false

echo "# init" > README.md
git add README.md; git commit -q -m "chore: 初始化"
git branch -M master

echo a > a.txt; git add a.txt; git commit -q -m "feat(core): 新增特性A"
echo b > b.txt; git add b.txt; git commit -q -m "fix: 修复缺陷B"
echo c > c.txt; git add c.txt; git commit -q -m "docs: 更新文档C"
echo d > d.txt; git add d.txt; git commit -q -m "refactor: 重构模块D"
echo e > e.txt; git add e.txt; git commit -q -m "随机无前缀提交E"

mkdir -p scripts tests
cp "$RELEASE_SH" scripts/release.sh
chmod +x scripts/release.sh
echo ".githooks/" > .gitignore
git add .gitignore scripts
git commit -q -m "chore: 添加发布脚本"

echo "===== TEST A: 回归脚本缺失 -> 门禁失败 -> 回滚（新建文件删除）====="
./scripts/release.sh v1.0.0; echo "rc_A=$?"
echo "A_CHANGELOG_EXISTS=$(test -f CHANGELOG.md && echo yes || echo no)"
echo "A_COMMITS=$(git rev-list --count HEAD)"
echo "A_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "A_DIRTY=$(git status --porcelain | wc -l)"

echo
echo "===== TEST B: 完整成功流程 ====="
printf '#!/usr/bin/env bash\necho "[regression] ok"\nexit 0\n' > tests/run_regression.sh
chmod +x tests/run_regression.sh
git add tests; git commit -q -m "test: 添加回归脚本"
./scripts/release.sh v1.0.0; echo "rc_B=$?"
echo "B_CHANGELOG_EXISTS=$(test -f CHANGELOG.md && echo yes || echo no)"
echo "B_COMMITS=$(git rev-list --count HEAD)"
echo "B_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "B_TAG_TYPE=$(git cat-file -t v1.0.0 2>/dev/null)"
echo "B_RELEASE_FILES=[$(git show --name-only --pretty=format: HEAD | tr '\n' ' ')]"

echo
echo "===== TEST C: 幂等性（重复发布同一版本）====="
./scripts/release.sh v1.0.0; echo "rc_C=$?"
echo "C_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "C_COMMITS=$(git rev-list --count HEAD)"

echo
echo "===== TEST D: 门禁失败 -> 回滚（备份恢复已有 CHANGELOG）====="
printf '#!/usr/bin/env bash\necho "[regression] FAILED"\nexit 1\n' > tests/run_regression.sh
chmod +x tests/run_regression.sh
git add tests; git commit -q -m "test: 回归脚本改为失败"
DB=$(git rev-list --count HEAD)
./scripts/release.sh v1.1.0; echo "rc_D=$?"
echo "D_COMMITS_BEFORE=$DB AFTER=$(git rev-list --count HEAD)"
echo "D_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "D_CHANGELOG_HAS_v1.1.0=$(grep -c 'v1.1.0' CHANGELOG.md)"

echo
echo "===== TEST E: 注入标签创建失败 -> 回滚发布提交与变更日志 ====="
printf '#!/usr/bin/env bash\necho "[regression] ok"\nexit 0\n' > tests/run_regression.sh
chmod +x tests/run_regression.sh
git add tests; git commit -q -m "test: 回归脚本恢复通过"
mkdir -p .githooks
cat > .githooks/reference-transaction <<'HOOK'
#!/usr/bin/env bash
# 模拟故障：拒绝一切标签引用事务，用于验证标签创建失败时的回滚链路
while read -r old new ref; do
  case "$ref" in
    refs/tags/*) echo "hook: 拒绝标签事务（模拟标签创建失败）" >&2; exit 1 ;;
  esac
done
exit 0
HOOK
chmod +x .githooks/reference-transaction
git config core.hooksPath .githooks
EB=$(git rev-list --count HEAD)
./scripts/release.sh v1.0.1; echo "rc_E=$?"
echo "E_COMMITS_BEFORE=$EB AFTER=$(git rev-list --count HEAD)"
echo "E_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "E_CHANGELOG_HAS_v1.0.1=$(grep -c 'v1.0.1' CHANGELOG.md)"
echo "E_DIRTY=$(git status --porcelain | wc -l)"
git config --unset core.hooksPath

echo
echo "===== TEST G: --bump 版本号同步 ====="
printf '[project]\nname = "demo"\nversion = "0.4.0a2"\n' > pyproject.toml
git add pyproject.toml; git commit -q -m "chore: 添加版本文件"
./scripts/release.sh v1.1.0 --bump; echo "rc_G=$?"
echo "G_PYPROJECT_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
echo "G_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "G_RELEASE_FILES=[$(git show --name-only --pretty=format: HEAD | tr '\n' ' ')]"

echo
echo "===== TEST H: --bump 后门禁失败 -> 同时回滚变更日志与版本文件 ====="
printf '#!/usr/bin/env bash\necho "[regression] FAILED"\nexit 1\n' > tests/run_regression.sh
chmod +x tests/run_regression.sh
git add tests; git commit -q -m "test: 回归脚本改为失败"
echo "H_BEFORE_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
./scripts/release.sh v1.2.0 --bump; echo "rc_H=$?"
echo "H_AFTER_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
echo "H_TAGS=[$(git tag -l | tr '\n' ' ')]"
echo "H_CHANGELOG_HAS_v1.2.0=$(grep -c 'v1.2.0' CHANGELOG.md)"
echo "H_DIRTY=$(git status --porcelain | wc -l)"

echo
echo "===== TEST F: 标签回滚动作有效性 ====="
if git tag -d v1.0.0 >/dev/null 2>&1; then echo "F_TAG_DELETED=yes"; else echo "F_TAG_DELETED=no"; fi
echo "F_TAGS=[$(git tag -l | tr '\n' ' ')]"

cd /
rm -rf "$WORK"
echo
echo "CLEANED_SCRATCH_REPO"
