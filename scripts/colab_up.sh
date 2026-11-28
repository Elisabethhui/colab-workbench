#!/usr/bin/env bash
# 幂等地确保一个 Colab session 存在。
#
# 用法:
#   scripts/colab_up.sh <session> [--gpu T4|L4|G4|H100|A100] [--tpu v5e1|v6e1] [--high-mem] [--restart]
set -uo pipefail

SESSION=""
GPU=""
TPU=""
HIGHMEM=0
RESTART=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU="$2"; shift 2 ;;
    --tpu) TPU="$2"; shift 2 ;;
    --high-mem) HIGHMEM=1; shift ;;
    --restart) RESTART=1; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) SESSION="$1"; shift ;;
  esac
done

[[ -z "$SESSION" ]] && { echo "用法: $0 <session> [--gpu T4]" >&2; exit 2; }
command -v colab >/dev/null || { echo "colab CLI 未安装: uv tool install google-colab-cli" >&2; exit 2; }

# 注意: `colab status -s X` 在 session 不存在时**退出码仍是 0**（只打印 not found），
# 所以必须解析 `colab sessions` 的 `[name]` 前缀来判断。
#
# 而且 `colab sessions` 走的是账号 API（GET /tun/m/assignments），实测会卡 70s+ 甚至无响应，
# 所以：给 12s 上限；超时/失败就直接读本地 sessions.json（只读，不写）兜底。
session_exists() {
  local out
  out=$(timeout 12 colab sessions 2>/dev/null)
  if grep -qE "\[$SESSION\]" <<<"$out"; then return 0; fi
  if [[ -z "$out" ]]; then
    echo "[warn] colab sessions 无响应（>12s），改用本地 sessions.json 判断" >&2
    python3 - "$SESSION" <<'PY'
import json, sys, pathlib, datetime
name = sys.argv[1]
p = pathlib.Path.home() / ".config/colab-cli/sessions.json"
try:
    d = json.loads(p.read_text())
except Exception:
    sys.exit(1)
s = d.get(name)
if not s:
    sys.exit(1)
exp = s.get("token_expires_at") or ""
left = "?"
if exp:
    try:
        e = datetime.datetime.fromisoformat(exp.replace("Z", "+00:00"))
        left = f"{int((e - datetime.datetime.now(datetime.timezone.utc)).total_seconds() // 60)}min"
    except Exception:
        pass
print(f"[local] {name} 存在于 sessions.json（endpoint={s.get('endpoint')}, token 剩 {left}）")
sys.exit(0)
PY
    return $?
  fi
  return 1
}

if [[ $RESTART -eq 1 ]] && session_exists; then
  echo "[up] 重启 $SESSION 的内核..."
  colab restart-kernel -s "$SESSION"
fi

if session_exists; then
  echo "[up] session '$SESSION' 已存在:"
  colab status -s "$SESSION"
  exit 0
fi

ARGS=(-s "$SESSION")
[[ -n "$GPU" ]] && ARGS+=(--gpu "$GPU")
[[ -n "$TPU" ]] && ARGS+=(--tpu "$TPU")
[[ $HIGHMEM -eq 1 ]] && ARGS+=(--high-mem)

echo "[up] 创建 session '$SESSION'..."
if ! colab new "${ARGS[@]}"; then
  cat >&2 <<'EOF'
[up] 创建失败。常见原因：
  - GPU/TPU 免费额度用尽（HTTP 400）→ 去掉 --gpu 用 CPU，或等额度恢复
  - --gpu 值拼错（T4/L4/G4/H100/A100 全大写）→ 大小写错误会被静默当成 A100 然后失败
  - 并发 runtime 数已达上限 → colab sessions 看有没有遗留的，停掉不用的
EOF
  exit 1
fi

colab status -s "$SESSION"
