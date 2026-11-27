#!/usr/bin/env python3
"""Colab session 管理器：预览 / 清理 / 保活。

用法:
    scripts/sessions.py                       # 等同于 list
    scripts/sessions.py list [--json]
    scripts/sessions.py stop <name|all>       # 只停 CLI 建出来的 session
    scripts/sessions.py release <endpoint|name|all-foreign|all> [--dry-run]
                                              # 释放任意 session（含 VS Code 扩展建的）
    scripts/sessions.py keepalive <name> [--interval 300] [--count 0]
    scripts/sessions.py url <name>

为什么要区分两类 session:
    `colab sessions` 列的是**服务端 assignment**，里面混着别的客户端（如 VS Code 的
    Colab 扩展）创建的 runtime。CLI 只有在本地 `sessions.json` 里存有该 session 的
    JWT 才能操作它；没有的一律显示为 `[?]`，stop/exec 都会报 not found。
    本脚本会把 `[?]` 条目和 VS Code 的 Jupyter 内核缓存交叉比对，标出它的真身。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_PATH = Path(os.path.expanduser("~/.config/colab-cli/sessions.json"))
JUPYTER_CACHE = Path(
    os.path.expanduser(
        "~/Library/Application Support/Code/User/globalStorage/"
        "ms-toolsai.jupyter/remoteKernelSpecCache.json"
    )
)

SESSION_LINE = re.compile(
    r"^\[(?P<name>[^\]]+)\]\s+(?P<endpoint>\S+)\s*\|\s*Hardware:\s*(?P<accel>[^|]+?)\s*"
    r"\|\s*Shape:\s*(?P<shape>[^|]+?)\s*\|\s*Variant:\s*(?P<variant>\S+)"
)
REGION_RE = re.compile(r"\.([a-z0-9-]+)\.prod\.r\.colab\.dev")

# colab CLI 自己的 venv python（colab_cli 包只装在这里，系统 python 导入不了）
COLAB_TOOL_PY = Path.home() / ".local/share/uv/tools/google-colab-cli/bin/python"

# 直接调用 Colab 后端的账号级 unassign 接口：只需要 endpoint 名，
# **不需要**该 session 的 JWT，所以能释放别的客户端（VS Code 扩展）创建的 runtime。
_RELEASE_SNIPPET = r"""
import sys
from colab_cli.auth import AuthProvider, get_credentials
from colab_cli.client import Client, Prod

endpoint = sys.argv[1]
sess = get_credentials(None, provider=AuthProvider.OAUTH2)
Client(Prod(), sess).unassign(endpoint)
print("unassigned", endpoint)
"""


def remote_unassign(endpoint: str) -> subprocess.CompletedProcess:
    """调用 /tun/m/unassign/<endpoint>，释放任意归属于本账号的 runtime。"""
    if not COLAB_TOOL_PY.exists():
        sys.exit(f"找不到 colab CLI 的 python 解释器: {COLAB_TOOL_PY}\n"
                 "先跑: uv tool install google-colab-cli")
    return subprocess.run([str(COLAB_TOOL_PY), "-c", _RELEASE_SNIPPET, endpoint],
                          capture_output=True, text=True)


def colab_bin() -> str:
    found = shutil.which("colab")
    if found:
        return found
    fallback = Path(os.path.expanduser("~/.local/bin/colab"))
    if fallback.exists():
        return str(fallback)
    sys.exit("找不到 colab 命令，先跑: uv tool install google-colab-cli")


def run_colab(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [colab_bin(), *args], capture_output=True, text=True, **kw
    )


def server_sessions() -> list[dict]:
    """解析 `colab sessions`（服务端真实分配）。"""
    proc = run_colab(["sessions"])
    out = (proc.stdout or "") + (proc.stderr or "")
    rows = []
    for line in out.splitlines():
        m = SESSION_LINE.match(line.strip())
        if not m:
            continue
        rows.append({**m.groupdict(), "raw": line.strip()})
    return rows


def local_sessions() -> dict:
    if not SESSIONS_PATH.exists():
        return {}
    try:
        return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def jupyter_kernels() -> dict[str, dict]:
    """endpoint -> VS Code(Jupyter 扩展) 侧的内核信息，用于解释 `[?]` 条目。"""
    if not JUPYTER_CACHE.exists():
        return {}
    try:
        data = json.loads(JUPYTER_CACHE.read_text(encoding="utf-8")).get("data", {})
    except Exception:  # noqa: BLE001
        return {}
    found: dict[str, dict] = {}
    for items in data.values():
        for it in items:
            base = it.get("baseUrl") or ""
            m = re.search(r"^https?://\d+-(m-s-[a-z0-9-]+?)-[ab]\.", base)
            if not m:
                continue
            ep = m.group(1)
            km = it.get("kernelModel") or {}
            prev = found.get(ep, {})
            if km.get("id") or not prev:
                found[ep] = {
                    "baseUrl": base,
                    "kernel_id": km.get("id") or prev.get("kernel_id"),
                    "last_activity": km.get("last_activity") or prev.get("last_activity"),
                    "region": (REGION_RE.search(base).group(1) if REGION_RE.search(base) else "?"),
                }
    return found


def region_of(url: str) -> str:
    m = REGION_RE.search(url or "")
    return m.group(1) if m else "?"


def fmt_last(rec: dict) -> str:
    le = rec.get("last_execution")
    if not le:
        return "—"
    if isinstance(le, list) and len(le) >= 3:
        return str(le[2])
    return str(le)


def cmd_list(as_json: bool = False) -> int:
    server = server_sessions()
    local = local_sessions()
    kernels = jupyter_kernels()

    managed, foreign = [], []
    for row in server:
        rec = local.get(row["name"]) if row["name"] != "?" else None
        if rec and rec.get("endpoint") == row["endpoint"]:
            row.update(
                region=region_of(rec.get("url", "")),
                kernel_id=rec.get("kernel_id", "—"),
                last=fmt_last(rec),
                owner="cli",
            )
            managed.append(row)
        else:
            k = kernels.get(row["endpoint"], {})
            row.update(
                region=k.get("region", "?"),
                kernel_id=k.get("kernel_id") or "—",
                last=(k.get("last_activity") or "—")[:19].replace("T", " ")
                if k.get("last_activity")
                else "—",
                owner="VS Code / 其他客户端",
            )
            foreign.append(row)

    if as_json:
        print(json.dumps({"managed": managed, "foreign": foreign}, ensure_ascii=False, indent=2))
        return 0

    def table(rows, cols):
        widths = {c: max([len(c)] + [len(str(r.get(c, ""))) for r in rows]) for c in cols}
        head = "  ".join(c.upper().ljust(widths[c]) for c in cols)
        print(head)
        print("-" * len(head))
        for r in rows:
            print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))

    print()
    if managed:
        print(f"== CLI 管理的 session（{len(managed)} 个，可 exec / stop）==")
        table(managed, ["name", "accel", "shape", "region", "kernel_id", "last", "endpoint"])
    else:
        print("== CLI 管理的 session：无 ==")
        print("   （用 ./scripts/colab_up.sh <名字> [--gpu T4] 建一个）")

    if foreign:
        print()
        print(f"== 非 CLI 管理的 session（{len(foreign)} 个，CLI 碰不到）==")
        table(foreign, ["endpoint", "accel", "shape", "region", "kernel_id", "last"])
        print()
        print("   这些是 VS Code 的 Colab 扩展（Jupyter 内核）之类创建的 runtime。")
        print("   `colab sessions` 能看到（服务端分配），但 CLI 没有它们的 JWT，")
        print("   所以 stop / exec / status 一律报 not found —— 这是设计如此，不是 bug。")
        print("   要收掉它们：")
        print("     scripts/sessions.py release <endpoint>     # 命令行直接释放（推荐）")
        print("     scripts/sessions.py release all-foreign    # 一次释放全部非 CLI session")
        print("   或在 Colab 网页 Runtime → Manage sessions 里终止，或在 VS Code 断开内核。")

    print()
    usage = run_colab(["usage"])
    uout = ((usage.stdout or "") + (usage.stderr or "")).strip()
    if uout:
        for line in uout.splitlines():
            if line.strip():
                print("   " + line.strip())
    print()
    return 0


def cmd_release(target: str, dry_run: bool = False) -> int:
    """释放 session（含其他客户端创建的），走账号级 unassign 接口。"""
    local = local_sessions()
    server = server_sessions()

    if target in {"all", "all-foreign"}:
        targets = []
        for row in server:
            if row["name"] == "?" or row["endpoint"] not in {r.get("endpoint") for r in local.values()}:
                targets.append(row["endpoint"])
        if target == "all":
            targets = [r["endpoint"] for r in server]
    elif target.startswith("m-s-"):
        targets = [target]
    elif target in local:
        targets = [local[target]["endpoint"]]
    else:
        print(f"'{target}' 既不是本地 session 名也不是 m-s-* endpoint。", file=sys.stderr)
        print("本地 session: " + (", ".join(local) or "无"), file=sys.stderr)
        print("服务端 endpoint:", ", ".join(r["endpoint"] for r in server) or "无", file=sys.stderr)
        return 2

    if not targets:
        print("没有需要释放的 session。")
        return 0

    rc = 0
    for ep in targets:
        if dry_run:
            print(f"[dry-run] 将释放 {ep}")
            continue
        p = remote_unassign(ep)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode == 0:
            print(f"[ok] 已释放 {ep}")
            # 顺手清掉本地记录
            for name, rec in list(local.items()):
                if rec.get("endpoint") == ep:
                    local.pop(name, None)
                    SESSIONS_PATH.write_text(json.dumps(local, indent=2), encoding="utf-8")
                    print(f"     本地记录 '{name}' 已移除")
        else:
            print(f"[fail] {ep}: {out.splitlines()[-1] if out else '?'}")
            rc = 1

    if not dry_run:
        print()
        cmd_list()
    return rc


def cmd_stop(target: str) -> int:
    local = local_sessions()
    if target == "all":
        names = list(local.keys())
        if not names:
            print("没有 CLI 管理的 session 可停。")
            return 0
    else:
        if target not in local:
            print(f"'{target}' 不是 CLI 管理的 session（本地无记录）。")
            print("CLI 无法停止别的客户端创建的 runtime；请在 Colab 网页 Manage sessions 里处理。")
            print("当前 CLI 管理的：" + (", ".join(local) or "无"))
            return 1
        names = [target]

    rc = 0
    for n in names:
        print(f"停止 {n} ...")
        p = run_colab(["stop", "-s", n])
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        print("  " + (out.replace("\n", "\n  ") or "(无输出)"))
        # colab stop 成功会把本地记录删掉
        if p.returncode != 0:
            rc = p.returncode
    print()
    cmd_list()
    return rc


def cmd_keepalive(name: str, interval: int, count: int) -> int:
    if name not in local_sessions():
        print(f"'{name}' 不存在或不是 CLI 管理的，先 colab_up.sh {name}", file=sys.stderr)
        return 1
    print(f"保活 {name}：每 {interval}s 打一次心跳（次数 {'无限' if count == 0 else count}）")
    print("⚠ 免费档靠心跳维持 runtime 属于灰色地带，可能被判滥用；优先考虑接受回收后 4 秒重建。")
    print("Ctrl-C 结束。\n")
    i = 0
    try:
        while count == 0 or i < count:
            i += 1
            t0 = time.time()
            p = run_colab(["exec", "-s", name], input="pass\n")
            dt = time.time() - t0
            stamp = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
            if p.returncode == 0:
                print(f"[{stamp}] #{i} ok ({dt:.1f}s)")
            else:
                msg = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
                print(f"[{stamp}] #{i} 失败 ({dt:.1f}s): {msg[-1] if msg else '?'}")
                print("    session 很可能已被回收，重新 colab_up.sh 即可。")
                return 1
            if count == 0 or i < count:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止保活。")
    return 0


def cmd_url(name: str) -> int:
    p = run_colab(["url", "-s", name])
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    print(out)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("command", nargs="?", default="list")
    ap.add_argument("target", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()

    if a.help:
        print(__doc__)
        return 0
    if a.command in {"list", "ls"}:
        return cmd_list(a.json)
    if a.command == "stop":
        if not a.target:
            print("用法: scripts/sessions.py stop <name|all>", file=sys.stderr)
            return 2
        return cmd_stop(a.target)
    if a.command == "release":
        if not a.target:
            print("用法: scripts/sessions.py release <endpoint|name|all-foreign|all> [--dry-run]", file=sys.stderr)
            return 2
        return cmd_release(a.target, a.dry_run)
    if a.command == "keepalive":
        if not a.target:
            print("用法: scripts/sessions.py keepalive <name> [--interval 300]", file=sys.stderr)
            return 2
        return cmd_keepalive(a.target, a.interval, a.count)
    if a.command == "url":
        if not a.target:
            print("用法: scripts/sessions.py url <name>", file=sys.stderr)
            return 2
        return cmd_url(a.target)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
