#!/usr/bin/env python3
"""从 .ipynb 里抽出执行错误，避免把整个 notebook JSON 塞进上下文。

用法:
    scripts/nb_errors.py <notebook.ipynb> [--tail N] [--cells]

退出码: 0 = 无错误；1 = 有错误
"""
from __future__ import annotations

import argparse
import json
import sys

MAX_SOURCE = 300
MAX_TRACEBACK = 8


def _join(src) -> str:
    return "".join(src) if isinstance(src, list) else str(src or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook")
    ap.add_argument("--tail", type=int, default=15, help="无错误时展示最后一格多少行 stdout")
    ap.add_argument("--cells", action="store_true", help="同时列出所有 cell 的首行，便于定位")
    args = ap.parse_args()

    try:
        nb = json.load(open(args.notebook, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"无法解析 {args.notebook}: {exc}", file=sys.stderr)
        return 2

    cells = nb.get("cells", [])
    code_cells = [c for c in cells if c.get("cell_type") == "code"]

    if args.cells:
        print(f"== {len(code_cells)} 个 code cell ==")
        for i, c in enumerate(code_cells):
            first = _join(c.get("source")).strip().splitlines()
            print(f"  [{i:3d}] {first[0][:110] if first else '(空)'}")
        print()

    errors = 0
    for i, c in enumerate(code_cells):
        for out in c.get("outputs", []) or []:
            if out.get("output_type") != "error":
                continue
            errors += 1
            src = _join(c.get("source")).strip().splitlines()
            print("=" * 72)
            print(f"CELL {i}  {out.get('ename')}: {out.get('evalue')}")
            if src:
                print(f"--- 该格首行: {src[0][:MAX_SOURCE]}")
            tb = out.get("traceback") or []
            for line in tb[-MAX_TRACEBACK:]:
                # 去掉 ANSI 颜色
                clean = line
                for token in ("\x1b[0;31m", "\x1b[0m", "\x1b[31m", "\x1b[1;32m", "\x1b[0;32m"):
                    clean = clean.replace(token, "")
                print("   " + clean)
            # 该格错误前的 stdout / stderr 也很有价值
            for o2 in c.get("outputs", []) or []:
                if o2.get("output_type") == "stream" and o2.get("text"):
                    text = _join(o2["text"]).strip()
                    if text:
                        print(f"--- {o2.get('name', 'stdout')} (该格错误前):")
                        for line in text.splitlines()[-8:]:
                            print("   " + line)

    if errors == 0:
        print("== 没有 error 输出 ==")
        if code_cells:
            last = code_cells[-1]
            outs = last.get("outputs") or []
            prints = []
            for o in outs:
                if o.get("output_type") == "stream" and o.get("text"):
                    prints.extend(_join(o["text"]).splitlines())
                elif o.get("output_type") in {"execute_result", "display_data"}:
                    prints.append(f"[{o['output_type']}: {list((o.get('data') or {}).keys())}]")
            if prints:
                print(f"--- 最后一格输出（后 {args.tail} 行）:")
                for line in prints[-args.tail:]:
                    print("   " + line)

    print("=" * 72)
    print(f"共 {errors} 处错误" if errors else "共 0 处错误")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
