#!/usr/bin/env python3
"""Colab 工作流引导器 —— 每个 notebook 的第 0 格只需要调用 `setup()`。

它只做五件事，每件都幂等：

  1. 挂载 Drive（已挂则跳过）
  2. 判断这台 VM 是不是新的（哨兵文件 /content/.wb/<project>.ready）
  3. 装依赖 + 打补丁（探针按"版本"判断，项目特有逻辑走 projects/<name>/hooks.py）
  4. 载入凭据（HF / Kaggle）→ 环境变量，只打印脱敏摘要
  5. 准备数据（看 manifest，缺才下载；已有则 0 次网络请求）

设计原则
  - 代码每次重新拿（GitHub → /content），数据一次不重拿（Drive）
  - 凭据永不打印明文、永不进 git
  - 任何一步重复执行都不会改变结果
"""
from __future__ import annotations

import importlib.util
import json
import time
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# ------------------------------------------------------------------ 常量
VMLOCAL = Path(os.environ.get("WB_VMLOCAL", "/content"))      # 本地盘：随 VM 生灭
DRIVE_ROOT = Path(os.environ.get("WB_DRIVE_ROOT", "/content/drive/MyDrive"))
SENTINEL_DIR = VMLOCAL / ".wb"
CACHE_DIR = VMLOCAL / "_cache"                   # 只放暂存/杂项，绝不放数据集
WB_HOME = Path(__file__).resolve().parent                       # 工作流仓库根

INTERACTIVE = sys.stdin.isatty() or bool(os.environ.get("WB_INTERACTIVE"))


# ------------------------------------------------------------------ 小工具
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "(空)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def sh(cmd: str | List[str], cwd: Optional[Path] = None, check: bool = False,
       quiet: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, timeout=timeout)
    if not quiet:
        out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
        if out:
            print(out[-2000:])
        if err and (check or r.returncode):
            print(err[-800:])
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败({r.returncode}): {cmd}\n{(r.stderr or '')[-600:]}")
    return r


def _kaggle_bin() -> str:
    """kaggle CLI 的真实可执行文件（`python -m kaggle` 没有 __main__）。"""
    found = shutil.which("kaggle")
    if found:
        return found
    import sysconfig
    cand = Path(sysconfig.get_path("scripts")) / "kaggle"
    if cand.exists():
        return str(cand)
    sh([sys.executable, "-m", "pip", "install", "-q", "kaggle"], check=True)
    return shutil.which("kaggle") or "kaggle"


def _py_version_of(pkg: str) -> str:
    """用子进程查版本（内核里已 import 的模块不会热替换，必须开新进程）。"""
    code = (
        "import importlib.metadata as m;"
        f"print(m.version({pkg!r}))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip()


# ------------------------------------------------------------------ 上下文
@dataclass
class Paths:
    project: Path
    data: Path
    logs: Path
    outputs: Path
    repo: Path
    cache: Path
    drive_root: Path

    def show(self) -> None:
        print("  路径：")
        for k in ("repo", "data", "logs", "outputs", "cache"):
            print(f"    {k:8s} {getattr(self, k)}")


@dataclass
class Ctx:
    project: str
    cfg: Dict[str, Any]
    fresh_vm: bool
    paths: Paths
    secrets_source: str = "(无)"
    state: Dict[str, Any] = field(default_factory=dict)

    # 便捷访问
    @property
    def name(self) -> str:
        return self.project

    def __getitem__(self, key: str):
        return self.cfg.get(key, {})


# ------------------------------------------------------------------ 1. Drive
def _drive_mounted() -> bool:
    """真挂载判定：/content/drive 必须是 FUSE 挂载点。

    为什么不用 "MyDrive 目录存在"：目录不存在时 mkdir 也会成功，
    于是 Drive 没挂上时程序会安静地把数据写进本地盘（曾经的 bug）。
    本地开发用 WB_SKIP_DRIVE=1 显式绕过。
    """
    if os.environ.get("WB_SKIP_DRIVE"):
        return DRIVE_ROOT.is_dir()
    for _ in range(1):
        if os.path.ismount(DRIVE_ROOT.parent) or os.path.ismount(DRIVE_ROOT):
            return True
    return False


def mount_drive() -> bool:
    """挂不上就**硬失败**，绝不退化成"写本地盘"。"""
    if _drive_mounted():
        print("  Drive         已挂载（跳过）")
        return True

    if os.environ.get("WB_SKIP_DRIVE"):
        print("  Drive         跳过（WB_SKIP_DRIVE=1，仅本地开发）")
        DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
        return True

    if not INTERACTIVE:
        raise RuntimeError(
            "Drive 未挂载，且当前不是交互环境（CLI exec 无法弹授权）。\n"
            "  二选一：\n"
            "    1) 在 VS Code 里点运行这一格（会弹一次授权）\n"
            "    2) 先执行: colab drivemount -s <session>\n"
            "  拒绝继续：否则数据会静默落进 Colab 本地盘，这是明确禁止的。"
        )

    print("  Drive         挂载中（浏览器会弹一次授权）...")
    from google.colab import drive  # noqa: PLC0415  (只在 Colab 里存在)
    drive.mount("/content/drive")

    for _ in range(20):  # FUSE 挂载有几百毫秒延迟
        if _drive_mounted():
            break
        time.sleep(0.25)
    if not _drive_mounted():
        raise RuntimeError("drive.mount() 返回了，但 /content/drive 仍不是挂载点，拒绝继续")
    print("  Drive         已挂载")
    return True


# ------------------------------------------------------------------ 2. 哨兵
def sentinel_path(project: str) -> Path:
    return SENTINEL_DIR / f"{project}.ready"


def is_fresh_vm(project: str) -> bool:
    return not sentinel_path(project).exists()


def mark_ready(project: str, info: dict) -> None:
    SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
    sentinel_path(project).write_text(json.dumps(info, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 3. 配置 / hooks
def load_config(project: str, config_dir: Optional[Path] = None) -> Dict[str, Any]:
    cfg_dir = config_dir or (WB_HOME / "config")
    for cand in (cfg_dir / f"{project}.toml", cfg_dir / f"project.{project}.toml"):
        if cand.exists():
            with cand.open("rb") as fh:
                return tomllib.load(fh)
    raise FileNotFoundError(f"找不到项目配置: {cfg_dir}/{project}.toml")


def load_hooks(project: str) -> Any:
    """projects/<name>/hooks.py 可选；提供 install(ctx) / patch(ctx) / prepare(ctx)。"""
    p = WB_HOME / "projects" / project / "hooks.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"wb_hooks_{project}", p)
    mod = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
    spec.loader.exec_module(mod)                  # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------ 4. 代码
def ensure_repo(cfg: Dict[str, Any], dest: Path) -> Path:
    proj = cfg.get("project", {})
    url = proj.get("repo_url")
    if not url:
        dest.mkdir(parents=True, exist_ok=True)
        print(f"  代码          无 repo_url，使用空目录 {dest}")
        return dest
    if (dest / ".git").exists():
        sh(["git", "-C", str(dest), "pull", "-q"], quiet=False)
        print(f"  代码          git pull → {dest}")
    elif dest.exists() and any(dest.iterdir()):
        print(f"  代码          已存在（非 git 目录）→ {dest}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", url, str(dest)], check=True)
        print(f"  代码          git clone → {dest}")
    head = sh(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"]).stdout.strip()
    if head:
        print(f"  代码          HEAD = {head}")
    return dest


# ------------------------------------------------------------------ 5. 依赖
def missing_deps(cfg: Dict[str, Any]) -> List[str]:
    """探针：只看"项目特有 + 版本敏感"的包，不看 Colab 预装的常客。"""
    out: List[str] = []
    for pkg, want in (cfg.get("deps") or {}).items():
        if isinstance(want, dict) or pkg in {"commands", "requirements"}:
            continue
        have = _py_version_of(pkg)
        if not have:
            out.append(f"{pkg} (未安装)")
        elif want and not have.startswith(str(want)):
            out.append(f"{pkg} (有 {have}，需要 {want})")
    return out


def install_deps(cfg: Dict[str, Any], ctx: "Ctx", hooks: Any) -> None:
    deps = cfg.get("deps") or {}
    lacks = missing_deps(cfg)

    if not lacks:
        print("  依赖          探针命中，全部就绪（跳过安装）")
    else:
        print(f"  依赖          缺少: {', '.join(lacks)}")
        try:
            if hooks and hasattr(hooks, "install"):
                print("  依赖          交给 hooks.install() 处理")
                hooks.install(ctx)
            else:
                req = deps.get("requirements")
                if req:
                    sh(f"{sys.executable} -m pip install -q -r {req}", cwd=ctx.paths.repo, check=True)
                pkgs = [x.split(" ")[0] for x in lacks]
                if pkgs:
                    sh([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True)
        except Exception as exc:  # noqa: BLE001
            print("  依赖          ✗ 安装失败，本轮不写哨兵（修好后重跑这一格即可）")
            print(f"                {str(exc).splitlines()[0][:300]}")
            raise
        still = missing_deps(cfg)
        print("  依赖          安装完成" if not still else f"  依赖          ⚠ 仍缺: {still}")

    for cmd in deps.get("commands") or []:
        print(f"  依赖          执行: {cmd}")
        sh(cmd, cwd=ctx.paths.repo, check=True)


def apply_patches(ctx: "Ctx", hooks: Any) -> None:
    if hooks and hasattr(hooks, "patch"):
        hooks.patch(ctx)
    else:
        print("  补丁          无（该项目没有 hooks.patch）")


# ------------------------------------------------------------------ 6. 凭据
SECRETS_DIR = DRIVE_ROOT / "Colab_Projects" / "_secrets"   # 三个手填文件都在这
SECRET_CANDIDATES = [
    ("Drive", SECRETS_DIR / "secrets.toml"),
    ("workspace", WB_HOME / "_secrets" / "secrets.toml"),
]


def load_secrets() -> tuple[Dict[str, Any], str]:
    """按优先级合并三个可能的手工填写文件：
      Colab_Projects/_secrets/secrets.toml   (推荐，可整份留空)
      Colab_Projects/_secrets/kaggle.json    (Kaggle 官方格式)
      Colab_Projects/_secrets/huggingface_token.txt  (一行 token)
    非空值优先，空值让位给下一个来源。"""
    sec: Dict[str, Any] = {}
    used: List[str] = []

    def merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        for k, v in (src or {}).items():
            if isinstance(v, dict):
                merge(dst.setdefault(k, {}), v)
            elif _real(v) and not _real((dst or {}).get(k)):
                dst[k] = v

    for label, path in SECRET_CANDIDATES:
        if not path.exists():
            continue
        try:
            with path.open("rb") as fh:
                merge(sec, tomllib.load(fh))
            used.append(label)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ 读不了 {path.name}: {type(exc).__name__}")

    secrets_dir = SECRETS_DIR
    kj = secrets_dir / "kaggle.json"
    if kj.exists():
        try:
            raw = json.loads(kj.read_text())
            merge(sec, {"kaggle": {"username": raw.get("username", ""),
                                    "key": raw.get("key", "")}})
            used.append("kaggle.json")
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ kaggle.json 格式不对（应该是 {{\"username\":..,\"key\":..}}）: {exc}")

    hf = secrets_dir / "huggingface_token.txt"
    if hf.exists():
        token = hf.read_text().strip()
        if token:
            merge(sec, {"huggingface": {"token": token}})
            used.append("huggingface_token.txt")

    return sec, (" + ".join(used) if used else "(未找到)")


def _real(value: Any) -> str:
    """过滤空值 / 占位符 / 明显没填的值，避免把模板文字当成凭据发出去。"""
    v = str(value or "").strip()
    if not v:
        return ""
    bad = ("填", "your", "xxx", "XXXX", "<", ">", "api_key_here", "none", "null")
    if any(b in v for b in bad) or any(c.isspace() for c in v):
        return ""
    return v


def apply_secrets(sec: Dict[str, Any]) -> None:
    hf = sec.get("huggingface") or {}
    token = _real(hf.get("token"))
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)

    kg = sec.get("kaggle") or {}
    user, key = _real(kg.get("username")), _real(kg.get("key"))
    if user and key:
        os.environ["KAGGLE_USERNAME"] = user
        os.environ["KAGGLE_KEY"] = key
        # 官方 CLI 也认 ~/.kaggle/kaggle.json；写本地盘，随 VM 消失
        kdir = Path.home() / ".kaggle"
        kdir.mkdir(exist_ok=True)
        kjson = kdir / "kaggle.json"
        kjson.write_text(json.dumps({"username": user, "key": key}))
        kjson.chmod(0o600)

    wb = sec.get("wandb") or {}
    wkey = _real(wb.get("api_key"))
    if wkey:
        os.environ["WANDB_API_KEY"] = wkey


def print_secret_summary(sec: Dict[str, Any], source: str) -> None:
    print(f"  凭据          来源 = {source}")
    for section, field_name in (("huggingface", "token"), ("kaggle", "key"), ("wandb", "api_key")):
        v = _real((sec.get(section) or {}).get(field_name))
        print(f"                {section}.{field_name} = {_mask(v) if v else '(未填)'}")


# ------------------------------------------------------------------ 7. 数据
def _data_specs(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = cfg.get("data") or []
    return [raw] if isinstance(raw, dict) else list(raw)


def _data_dir(root: Path, spec: Dict[str, Any]) -> Path:
    src = spec.get("source", "local")
    if src == "kaggle":
        tag = str(spec.get("slug", "dataset")).replace("/", "__")
    elif src == "hf":
        tag = str(spec.get("repo", "dataset")).replace("/", "__")
    else:
        tag = spec.get("name", "data")
    if spec.get("tag"):
        tag = f"{tag}@{spec['tag']}"
    return root / src / tag


def _manifest_ok(d: Path, spec: Dict[str, Any]) -> bool:
    mf = d / ".manifest.json"
    if not mf.exists():
        return False
    try:
        m = json.loads(mf.read_text())
    except Exception:  # noqa: BLE001
        return False
    check = spec.get("check")
    if check:
        import glob as _glob
        n = len(_glob.glob(str(d / "**" / check), recursive=True))
        want = spec.get("count")
        if want is not None and n < int(want):
            print(f"                 manifest 存在但只有 {n}/{want} 个文件 → 视为未就绪")
            return False
    return True


def _write_manifest(d: Path, spec: Dict[str, Any]) -> None:
    import glob as _glob
    files = sum(1 for p in d.rglob("*") if p.is_file())
    entry = {
        "source": spec.get("source"),
        "id": spec.get("slug") or spec.get("repo"),
        "tag": spec.get("tag"),
        "fetched_at": _now(),
        "files": files,
        "check": spec.get("check"),
        "count": spec.get("count"),
    }
    (d / ".manifest.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2))
    reg = d.parent.parent / "registry.jsonl"
    with reg.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fetch_data(spec: Dict[str, Any], dest: Path) -> bool:
    """把数据集直接写进 Drive 的 dest 目录。

    硬约束（用户定的目标）：数据只进 Google Drive，不在 /content 留存。
    技术约束：Drive 的 FUSE 不支持符号链接，所以两个官方通道都必须走
    「直落模式」，不能走它们默认的 symlink 缓存：
        kaggle → kagglehub.dataset_download(..., output_dir=dest)
        hf     → snapshot_download(..., local_dir=dest)
    下载过程中的压缩包临时文件必然会在本地盘停留一会儿（流式解压），
    跑完由 _clean_kaggle_temp() 清掉，数据集本体一个字节都不留在本地。
    """
    src = spec.get("source")
    dest.mkdir(parents=True, exist_ok=True)

    if src == "kaggle":
        slug = spec["slug"]
        print(f"                 kagglehub.dataset_download({slug!r}) → {dest}")
        try:
            import kagglehub  # noqa: PLC0415
        except ModuleNotFoundError:
            sh([sys.executable, "-m", "pip", "install", "-q", "kagglehub"], check=True)
            import kagglehub  # noqa: PLC0415

        # 压缩包的临时落脚点放本地盘（必须的临时文件），数据集本体直落 Drive
        os.environ["KAGGLEHUB_CACHE"] = str(CACHE_DIR / "kagglehub")
        try:
            kagglehub.dataset_download(slug, force_download=False, output_dir=str(dest))
        except TypeError:
            # 老版本 kagglehub 没有 output_dir：缓存模式会建 symlink，Drive 上必然失败，
            # 所以缓存放本地、解引用后搬到 Drive（仅此一条退路）
            print("                 ⚠ 这个 kagglehub 版本没有 output_dir，改走「本地暂存 → 搬 Drive」")
            got = Path(kagglehub.dataset_download(slug, force_download=False))
            staging = CACHE_DIR / "staging" / dest.name
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            for item in got.iterdir():
                dst = staging / item.name
                if item.is_dir():
                    shutil.copytree(item, dst, symlinks=False, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dst)
            for item in staging.iterdir():
                target = dest / item.name
                if not target.exists():
                    shutil.move(str(item), str(target))
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            print(f"                 ✗ 失败 {type(exc).__name__}: {str(exc)[:200]}")
            print("                   401/403 = 数据集私有或没接受规则 → 填 Drive/_secrets/kaggle.json")
            _clean_kaggle_temp()
            return False
        _clean_kaggle_temp()

    elif src == "hf":
        from huggingface_hub import snapshot_download  # noqa: PLC0415
        # local_dir 模式：直接写目标目录，不建 blobs/snapshots 软链
        snapshot_download(repo_id=spec["repo"], revision=spec.get("revision"),
                          repo_type=spec.get("repo_type", "dataset"),
                          local_dir=str(dest))
    elif src == "url":
        sh(["curl", "-fL", spec["url"], "-o", str(dest / (spec.get("filename") or "download"))],
           check=True)
    else:
        print(f"                 跳过未知 source={src}")
        return False
    return True


def _clean_kaggle_temp() -> None:
    """删掉 kagglehub 留在本地盘的压缩包/解压临时文件（数据集本体已经在 Drive 了）。"""
    cache = CACHE_DIR / "kagglehub"
    if not cache.exists():
        return
    freed = 0
    for p in cache.rglob("*"):
        if p.is_file() and p.suffix in (".archive", ".zip", ".tar", ".gz"):
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
    shutil.rmtree(cache, ignore_errors=True)
    if freed:
        print(f"                 已清掉本地临时包 {freed / 2**30:.1f} GB（数据本体只在 Drive）")


def prepare_data(cfg: Dict[str, Any], ctx: "Ctx", hooks: Any) -> None:
    specs = _data_specs(cfg)
    if not specs:
        print("  数据          配置里没有 [[data]]（跳过下载）")
        if hooks and hasattr(hooks, "prepare"):
            hooks.prepare(ctx)
        return
    need_download = [s for s in specs if not _manifest_ok(_data_dir(ctx.paths.data, s), s)]
    if not need_download:
        print(f"  数据          {len(specs)} 个数据集已在 Drive 就绪（0 次网络请求）")
        if hooks and hasattr(hooks, "prepare"):
            hooks.prepare(ctx)
        return
    for spec in need_download:
        d = _data_dir(ctx.paths.data, spec)
        print(f"  数据          下载 {spec.get('source')}:{spec.get('slug') or spec.get('repo')} → {d}")
        if fetch_data(spec, d):
            _write_manifest(d, spec)
            print("                 完成")
    for spec in specs:  # 补齐缺失的 manifest
        d = _data_dir(ctx.paths.data, spec)
        if d.exists() and not (d / ".manifest.json").exists():
            _write_manifest(d, spec)

    if hooks and hasattr(hooks, "prepare"):
        hooks.prepare(ctx)


# ------------------------------------------------------------------ 主入口
def setup(project: str,
          config_dir: Optional[str | Path] = None,
          skip_deps: bool = False,
          skip_data: bool = False,
          verbose: bool = False) -> Ctx:
    """Cell 0 唯一入口。幂等，可重复执行。"""
    print("=" * 66)
    print(f"工作流引导  project={project}")
    print("=" * 66)

    cfg = load_config(project, Path(config_dir) if config_dir else None)
    proj_cfg = cfg.get("project") or {}

    # 1) Drive —— 挂不上直接抛错，不允许退化成写本地盘
    mount_drive()

    # 2) 哨兵
    fresh = is_fresh_vm(project)
    print(f"  这台机器      {'新 VM（本地盘是空的）' if fresh else '旧 VM（哨兵存在）'}")

    # 3) 路径
    drive_proj = DRIVE_ROOT / "Colab_Projects" / project
    paths = Paths(
        project=drive_proj,
        data=DRIVE_ROOT / "Colab_Projects" / "_data",
        logs=drive_proj / "logs",
        outputs=drive_proj / "outputs",
        repo=VMLOCAL / (proj_cfg.get("repo_name") or proj_cfg.get("name") or project),
        cache=CACHE_DIR,
        drive_root=DRIVE_ROOT,
    )
    for p in (paths.project, paths.data, paths.logs, paths.outputs, paths.cache):
        p.mkdir(parents=True, exist_ok=True)
    paths.show()

    ctx = Ctx(project=project, cfg=cfg, fresh_vm=fresh, paths=paths)

    # 4) 凭据
    sec, src = load_secrets()
    ctx.secrets_source = src
    apply_secrets(sec)
    print_secret_summary(sec, src)

    # 5) HF 缓存放本地盘（放 Drive 会慢到不可用）
    os.environ.setdefault("HF_HOME", str(CACHE_DIR / "hf"))
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

    # 6) 代码
    ensure_repo(cfg, paths.repo)

    # 7) 依赖 / 补丁
    hooks = load_hooks(project)
    if hooks:
        print("  hooks         已加载 projects/%s/hooks.py" % project)
    lacks = missing_deps(cfg)
    if fresh or verbose or os.environ.get("WB_FORCE_INSTALL") or lacks:
        if not fresh and (lacks or os.environ.get("WB_FORCE_INSTALL")):
            print(f"  依赖          探针发现差异 {lacks or '(强制)'} → 重新安装")
        install_deps(cfg, ctx, hooks)
    else:
        print("  依赖          哨兵在 + 探针命中（跳过安装）")

    # 补丁很便宜且幂等：每次都跑，防止 git pull 把补丁冲掉
    apply_patches(ctx, hooks)

    # 8) 数据
    if not skip_data:
        prepare_data(cfg, ctx, hooks)

    # 9) 落哨兵
    mark_ready(project, {"ready_at": _now(), "repo": str(paths.repo),
                         "secrets": src, "fresh": fresh})

    # 10) 硬件摘要
    try:
        import torch  # noqa: PLC0415
        if torch.cuda.is_available():
            print(f"  硬件          GPU: {torch.cuda.get_device_name(0)} | torch {torch.__version__}")
        else:
            print(f"  硬件          CPU | torch {torch.__version__}")
    except Exception:  # noqa: BLE001
        print("  硬件          torch 未安装")

    print("=" * 66)
    print("环境就绪 → 往下跑业务格")
    return ctx
