#!/usr/bin/env bash
# 从 projects/_template 生成一个新项目目录。
#
# 用法: scripts/new-project.sh <项目名>
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${1:-}"
[[ -z "$NAME" ]] && { echo "用法: $0 <项目名>" >&2; exit 2; }
[[ "$NAME" == "_template" ]] && { echo "项目名不能是 _template" >&2; exit 2; }

DEST="$BASE/projects/$NAME"
[[ -e "$DEST" ]] && { echo "已存在: $DEST" >&2; exit 1; }

cp -R "$BASE/projects/_template" "$DEST"
# 占位符替换
find "$DEST" -type f \( -name "*.md" -o -name "*.ipynb" -o -name "*.txt" \) -print0 |
  while IFS= read -r -d '' f; do
    sed -i '' "s/__PROJECT_NAME__/$NAME/g" "$f" 2>/dev/null || \
      sed -i "s/__PROJECT_NAME__/$NAME/g" "$f"
  done

echo "已创建: projects/$NAME"
find "$DEST" -type f | sed "s|$BASE/||" | sed 's/^/  /'
cat <<EOF

下一步:
  1. 编辑 projects/$NAME/README.md 写清目标
  2. ./scripts/colab_up.sh $NAME            # CPU
     ./scripts/colab_up.sh $NAME --gpu T4   # 免费 T4
  3. ./scripts/colab_run_nb.sh projects/$NAME/01_experiment.ipynb $NAME
EOF
