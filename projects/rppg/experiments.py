"""rPPG 实验目录 —— 每个实验一个名字，CPU 调试和 GPU 复跑走同一个入口。

来历：这些内容原来散在 `Untitled-1.ipynb` 的第 16~30 格，靠"滚到某一格点运行"。
那样做在 CPU→GPU 的两阶段里会失效：切硬件 = 换 VM = 换 kernel，`/content` 里的变量
全部清零。所以"调通"的成果必须落成**代码**（进 git）而不是**内存状态**（留在格子里）。

所以这里只做三件事：
    1. 把源配置改成本机能跑的版本（路径 / DO_PREPROCESS / 测试区间 / DEVICE）
    2. 跑 `main.py --config_file`，日志落 Drive（换机器还在）
    3. 从日志里抠出 FFT 指标

用法（notebook 第 1 格）：

    from experiments import run, summary, list_experiments

    list_experiments()                 # 看有哪些、各要什么硬件
    run("baseline_classical", ctx)             # 古典基线，CPU 就能跑
    run("baseline_classical", ctx, limit=2)    # 只跑前 2 个 subject（冒烟，1~2 分钟）
    run("effphys_pure", ctx)                   # 要 GPU
    run("train_e1", ctx, dry_run=True)         # 只生成配置，不跑
    summary(ctx)                               # 汇总所有日志 + 当前版本戳

约定：
    * 路径一律来自 `ctx.paths`（第 0 格的返回值），不要在这里重新定义 REPO_DIR/RAW_DIR
    * 生成出来的配置写在仓库内（`ctx.paths.repo`），所以换 VM 会随 clone 重新生成
    * 日志写 `ctx.paths.logs`（Drive），所以换 VM 后 `summary()` 仍然能看到历史
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 上游配置里写死的作者集群路径
GRSCRATCH_CACHE = '/gscratch/ubicomp/xliu0/data3/mnt/Datasets/rppg_toolbox/PreprocessedData'

_METRIC_RE = re.compile(r'FFT\s+(MAE|RMSE|MAPE|Pearson|SNR|BA)[^:]*:\s*(-?[0-9.]+)')
_LIVE_RE = re.compile(r'FFT\s+(MAE|RMSE|MAPE|Pearson|SNR|BA)|Traceback|Error|error|Epoch|epoch', re.I)


# ------------------------------------------------------------------ 配置改写
def _swap_paths(text: str, view: Path, cache: Path) -> str:
    """把所有 /gscratch/... 的数据路径换成本机视图 + 本机缓存。

    不写死 UBFC 的 raw 路径：早期版本只替换 UBFC-rPPG 那条，PURE 那条漏掉，
    结果 E1 的测试集指向作者集群（不存在）——这里用正则一把梭。
    """
    text = re.sub(r'"/gscratch/[^"]*RawData"', f'"{view}"', text)
    text = text.replace(f'"{GRSCRATCH_CACHE}"', f'"{cache}"')
    text = re.sub(r'"/gscratch/[^"]*"', f'"{cache}"', text)   # 兜底：其余 /gscratch 一律换缓存
    return text


def _split_section(text: str, name: str):
    """把 `TRAIN:` / `TEST:` 顶层段切出来 → (段内文本, 起止位置)。找不到返回 (None, None)。"""
    m = re.search(rf'(?m)^{name}:\s*$', text)
    if not m:
        return None, None
    start = m.end()
    nxt = re.search(r'(?m)^[A-Z_]+:\s*$', text[start:])
    end = start + nxt.start() if nxt else len(text)
    return text[start:end], (start, end)


def _set_range(text: str, section: str, begin: Optional[float], end: Optional[float]) -> str:
    """只改 `section` 段里的 BEGIN/END（DATASET 段的第一个），不动 TRAIN 的同名字段。"""
    seg, span = _split_section(text, section)
    if seg is None:
        return text
    if begin is not None:
        seg = re.sub(r'(?m)^(\s*)BEGIN:\s*[0-9.]+', rf'\g<1>BEGIN: {begin}', seg, count=1)
    if end is not None:
        seg = re.sub(r'(?m)^(\s*)END:\s*[0-9.]+', rf'\g<1>END: {end}', seg, count=1)
    return text[:span[0]] + seg + text[span[1]:]


def _ranges(text: str) -> Dict[str, tuple]:
    out = {}
    for sec in ('TRAIN', 'TEST', 'VALID', 'UNSUPERVISED'):
        seg, _ = _split_section(text, sec)
        if seg is None:
            continue
        b = re.search(r'(?m)^\s*BEGIN:\s*([0-9.]+)', seg)
        e = re.search(r'(?m)^\s*END:\s*([0-9.]+)', seg)
        if b and e:
            out[sec] = (float(b.group(1)), float(e.group(1)))
    return out


def _leak_guard(text: str, name: str) -> Dict[str, tuple]:
    """训练区间与测试区间不许有正长度重叠（重叠 = 指标虚高，而且是事后才发现的）。"""
    r = _ranges(text)
    tr, te = r.get('TRAIN'), r.get('TEST')
    print(f'    [区间] ' + ' | '.join(f'{k} {v[0]}–{v[1]}' for k, v in r.items()))
    if tr and te:
        overlap = min(tr[1], te[1]) - max(tr[0], te[0])
        if overlap > 1e-9:
            raise RuntimeError(
                f'    [拒绝运行] {name}: TRAIN {tr[0]}–{tr[1]} 与 TEST {te[0]}–{te[1]} '
                f'重叠 {overlap:.3f}（同一批 subject 既训练又测试，指标会虚高）'
            )
    return r


def _set_device_cpu(text: str) -> str:
    if re.search(r'(?m)^DEVICE:\s*cuda', text):
        return re.sub(r'(?m)^DEVICE:\s*\S+', 'DEVICE: cpu', text, count=1)
    return text


# ------------------------------------------------------------------ 五个实验
def _t_baseline(text, ctx, view, cache):
    """古典基线：POS / CHROM（无需权重、无需 GPU）。"""
    text = text.replace('DO_PREPROCESS: False', 'DO_PREPROCESS: True')
    text = text.replace('["ICA", "POS", "CHROM", "GREEN", "LGI", "PBV", "OMIT"]',
                        '["POS", "CHROM"]')
    return text


def _t_plain(text, ctx, view, cache):
    text = text.replace('DO_PREPROCESS: False', 'DO_PREPROCESS: True')
    return text


def _t_test20(text, ctx, view, cache):
    """作者预训练模型只跑后 20%（与 E1 同区间，才能公平对比）。"""
    text = _set_range(text, 'TEST', begin=0.8, end=1.0)
    text = text.replace('DO_PREPROCESS: False', 'DO_PREPROCESS: True')
    return text


def _t_e1(text, ctx, view, cache):
    """自己训练：UBFC 前 80% 训练 / 后 20% 测试。

    上游训练配置本来就是 TRAIN 0.0–0.8 / TEST(PURE) 0.0–1.0，
    这里把 TEST 换成 UBFC-rPPG 的 0.8–1.0 —— 与 TRAIN 的 0.8 正好接上，不重叠。
    """
    text = text.replace('MODEL_FILE_NAME: UBFC_UBFC_PURE_efficientphys',
                        'MODEL_FILE_NAME: E1_UBFC_train80_test20')
    seg, span = _split_section(text, 'TEST')
    if seg is not None:
        seg = re.sub(r'(?m)^(\s*)DATASET:\s*\S+', r'\g<1>DATASET: UBFC-rPPG', seg, count=1)
        text = text[:span[0]] + seg + text[span[1]:]
    text = _set_range(text, 'TEST', begin=0.8, end=1.0)
    text = text.replace('DO_PREPROCESS: False', 'DO_PREPROCESS: True')
    return text


EXPERIMENTS: Dict[str, Dict[str, Any]] = {
    'baseline_classical': dict(
        needs='cpu', minutes='8–20 分钟（CPU）',
        src='configs/infer_configs/UBFC-rPPG_UNSUPERVISED.yaml',
        out='COLAB_UBFC_UNSUPERVISED.yaml', transform=_t_baseline,
        note='POS/CHROM 古典基线，不需要权重，CPU 就能出真指标',
    ),
    'effphys_pure': dict(
        needs='gpu', minutes='~6 分钟（T4）',
        src='configs/infer_configs/PURE_UBFC-rPPG_EFFICIENTPHYS.yaml',
        out='COLAB_PURE_UBFC_EFFPHYS.yaml', transform=_t_plain,
        note='PURE 训的 EfficientPhys → UBFC 全部 42 个',
    ),
    'effphys_scamps': dict(
        needs='gpu', minutes='~6 分钟（T4）',
        src='configs/infer_configs/SCAMPS_UBFC-rPPG_EFFICIENTPHYS.yaml',
        out='COLAB_SCAMPS_UBFC_EFFPHYS.yaml', transform=_t_plain,
        note='SCAMPS 训的 EfficientPhys → UBFC 全部 42 个',
    ),
    'train_e1': dict(
        needs='gpu', minutes='20–40 分钟（T4）',
        src='configs/train_configs/UBFC-rPPG_UBFC-rPPG_PURE_EFFICIENTPHYS.yaml',
        out='COLAB_E1_UBFC.yaml', transform=_t_e1,
        note='UBFC 前 80% 训练 / 后 20% 测试（30 epoch）',
    ),
    'author_test20': dict(
        needs='gpu', minutes='~2 分钟（T4）',
        src='configs/infer_configs/PURE_UBFC-rPPG_EFFICIENTPHYS.yaml',
        out='COLAB_PURE_UBFC_TEST20.yaml', transform=_t_test20,
        note='作者预训练模型只跑后 20%，和 E1 同区间对照',
    ),
}


def list_experiments() -> None:
    print(f'{"实验名":22s} {"硬件":5s} {"预期":18s} 说明')
    print('-' * 96)
    for name, e in EXPERIMENTS.items():
        print(f'{name:22s} {e["needs"]:5s} {e["minutes"]:18s} {e["note"]}')


# ------------------------------------------------------------------ 硬件 / 视图
def _cuda_available() -> bool:
    r = subprocess.run([sys.executable, '-c',
                        'import torch;print(1 if torch.cuda.is_available() else 0)'],
                       capture_output=True, text=True)
    return (r.stdout or '').strip().endswith('1')


def _build_view(ctx, limit: Optional[int] = None) -> Path:
    """把 Drive 里的 subject 目录用软链拼成本机视图（Drive 不支持软链，本地盘可以）。

    limit=N 只链前 N 个 subject —— 用来在 CPU 上做 1~2 分钟的冒烟，不用等全量。
    """
    import glob
    import os

    root = Path(ctx.paths.datasets) / 'kaggle'
    vids = []
    for d in sorted(root.glob('*/')):
        vids += sorted(glob.glob(str(d / '**' / 'subject*_vid.avi'), recursive=True))
    if not vids:
        raise RuntimeError(f'Drive 里没有 subject*_vid.avi（查 {root}）')
    view = Path(ctx.paths.vmcache) / ('UBFC' if not limit else f'UBFC_smoke{limit}')
    view.mkdir(parents=True, exist_ok=True)
    for v in vids[:limit] if limit else vids:
        v = Path(v)
        d = view / v.name.replace('_vid.avi', '')
        d.mkdir(parents=True, exist_ok=True)
        for src, dst in [(v, 'vid.avi'),
                         (Path(str(v).replace('_vid.avi', '_ground_truth.txt')), 'ground_truth.txt')]:
            link = d / dst
            if not link.exists() and src.exists():
                os.symlink(src, link)
    n = len([p for p in view.iterdir() if p.is_dir()])
    print(f'  视图          {view}（{n} 个 subject' + ('，limit 冒烟' if limit else '') + '）')
    return view


# ------------------------------------------------------------------ 跑
def run(name: str, ctx, limit: Optional[int] = None, dry_run: bool = False,
        force: bool = False) -> Dict[str, Any]:
    """跑一个实验。返回 {rc, log, metrics, cfg}。

    limit    只跑前 N 个 subject（CPU 冒烟用）
    dry_run  只生成配置并打印关键字段，不执行
    force    忽略"硬件不够"的提示，硬跑（例如 CPU 上跑 GPU 实验，会非常慢）
    """
    if name not in EXPERIMENTS:
        raise KeyError(f'没有这个实验: {name!r}；可选: {list(EXPERIMENTS)}')
    exp = EXPERIMENTS[name]
    repo = Path(ctx.paths.repo)
    cache = Path(ctx.paths.vmcache) / 'rppg'
    cache.mkdir(parents=True, exist_ok=True)

    src = repo / exp['src']
    if not src.exists():
        raise FileNotFoundError(f'源配置不存在: {src}（钉的版本对吗？）')

    view = _build_view(ctx, limit)
    text = _swap_paths(src.read_text(encoding='utf-8'), view, cache)
    text = exp['transform'](text, ctx, view, cache)

    cuda = _cuda_available()
    # 硬件不够时只拦"真跑"；dry_run 是给 CPU 阶段验证配置用的，必须放行
    if exp['needs'] == 'gpu' and not cuda and not dry_run and not force:
        print(f'  ⚠ {name} 需要 GPU，但当前 torch.cuda.is_available()=False。')
        print('     先 dry_run=True 验证配置，或 force=True 硬跑（会很慢），或切到 GPU 内核。')
        return {'rc': None, 'skipped': 'no-gpu'}
    if cuda:
        print('  设备          GPU')
    elif dry_run:
        print('  设备          CPU（dry_run 只生成配置，DEVICE 保持源配置原值）')
    else:
        text = _set_device_cpu(text)
        print('  设备          CPU（DEVICE 已改写为 cpu）')

    _leak_guard(text, name)

    out_rel = exp['out']
    (repo / out_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / out_rel).write_text(text, encoding='utf-8')
    print(f'  配置          {out_rel}')

    key_lines = [l.strip() for l in text.splitlines()
                 if re.match(r'\s*(TOOLBOX_MODE|DEVICE|MODEL_PATH|NAME|EPOCHS|DATASET|BEGIN|END|METHOD):', l)]
    for l in key_lines[:14]:
        print(f'                {l}')

    if dry_run:
        print('  dry_run       配置已生成，未执行 main.py')
        return {'rc': None, 'cfg': str(repo / out_rel)}

    logs = Path(ctx.paths.logs)
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f'{name}{"" if not limit else f"_smoke{limit}"}.log'
    cmd = [sys.executable, 'main.py', '--config_file', f'./{out_rel}']
    print(f'  运行          {" ".join(cmd)}  (cwd={repo})')
    print(f'  日志          {log_path}')
    print('-' * 66)

    metrics: Dict[str, str] = {}
    with log_path.open('w', encoding='utf-8') as fh:
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            fh.write(line)
            fh.flush()
            m = _METRIC_RE.search(line)
            if m:
                metrics[m.group(1)] = m.group(2)
            if m or _LIVE_RE.search(line):
                print(line.rstrip()[:400])
        rc = proc.wait()
    print('-' * 66)
    print(f'  退出码        {rc}')
    if metrics:
        print('  指标          ' + '  '.join(f'{k}={v}' for k, v in metrics.items()))
    else:
        print('  指标          日志里没抓到 FFT 指标（看日志尾部找原因）')
        tail = log_path.read_text(errors='ignore').strip().splitlines()[-8:]
        for l in tail:
            print(f'                {l[:300]}')
    return {'rc': rc, 'log': str(log_path), 'metrics': metrics, 'cfg': str(repo / out_rel)}


# ------------------------------------------------------------------ 汇总
def summary(ctx, only: Optional[List[str]] = None) -> Dict[str, Dict[str, str]]:
    """把所有日志里的 FFT 指标汇总成一张表，并带上版本戳（哪一版跑出来的）。"""
    logs = Path(ctx.paths.logs)
    out: Dict[str, Dict[str, str]] = {}
    for log in sorted(logs.glob('*.log')):
        if only and log.stem not in only:
            continue
        vals = {}
        for line in log.read_text(errors='ignore').splitlines():
            m = _METRIC_RE.search(line)
            if m:
                vals[m.group(1)] = m.group(2)
        if vals:
            out[log.stem] = vals

    stamp_path = Path(ctx.paths.project) / 'env.json'
    stamp = {}
    try:
        stamp = json.loads(stamp_path.read_text())
    except Exception:  # noqa: BLE001
        pass

    print('=' * 96)
    keys = ['MAE', 'RMSE', 'MAPE', 'Pearson', 'SNR']
    print(f'{"实验":26s}' + ''.join(f'{k:>10s}' for k in keys))
    print('-' * 96)
    for name, v in out.items():
        print(f'{name:26s}' + ''.join(f'{v.get(k, "-"):>10s}' for k in keys))
    if not out:
        print('（还没有任何日志；先 run 一个实验）')
    repo = stamp.get('repo', {})
    if stamp:
        print('-' * 96)
        print(f'版本戳: 上游 {str(repo.get("head", "?"))[:8]} | 工作流 '
              f'{str(stamp.get("workflow_head", "?"))[:8]} | '
              f'{stamp.get("env", {}).get("accelerator", "?")} '
              f'{stamp.get("env", {}).get("gpu", "")} | 落盘 {stamp.get("stamped_at", "?")}')
        if repo.get('ref') and repo.get('head') and repo['ref'] != repo['head']:
            print('⚠ 当前代码不是 repo_ref 钉的版本，指标可能对不上')
    return out
