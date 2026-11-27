"""rPPG-Toolbox 的项目特有逻辑（bootstrap 通过它注入）。

bootstrap.py 是通用的，这里才放"这个仓库的奇怪之处"：
  - requirements.txt 钉死的是 2021 年版本（numpy 1.22 / scipy 1.5.2），
    在 Python 3.13 上装不了，必须先剥掉版本号并过滤掉要编译的 CUDA 包
  - opencv 必须换成 headless 4.10.0.84（Colab 预装 5.0）
  - 6 处源码补丁（Mamba / np.mat / PBV / num_workers / 多进程配额）

所有操作幂等：重复执行不会把文件改坏。
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

GRSCRATCH_RAW = '/gscratch/ubicomp/xliu0/data3/mnt/Datasets/UBFC-rPPG/RawData'
GRSCRATCH_CACHE = '/gscratch/ubicomp/xliu0/data3/mnt/Datasets/rppg_toolbox/PreprocessedData'


# ------------------------------------------------------------------ 依赖
def _sh(cmd, cwd=None, check=False, quiet=True):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd,
                       capture_output=True, text=True)
    if not quiet:
        out = (r.stdout or '').strip()
        if out:
            print(out[-1500:])
    if check and r.returncode != 0:
        raise RuntimeError(f"失败: {cmd}\n{(r.stderr or '')[-600:]}")
    return r


def _cv2_version() -> str:
    r = subprocess.run([sys.executable, '-c', 'import cv2;print(cv2.__version__)'],
                       capture_output=True, text=True)
    return r.stdout.strip() or 'missing'


def install(ctx):
    repo = ctx.paths.repo
    print('    [hooks] 生成过滤版 requirements.txt')
    _sh("sed 's/==.*//' requirements.txt | grep -v -E 'causal-conv1d|mamba|opencv' > req_final.txt",
        cwd=repo, check=True)
    print('    [hooks] pip install -r req_final.txt（约 30 秒）')
    _sh(f'{sys.executable} -m pip install -q -r req_final.txt', cwd=repo, check=True)
    _sh(f'{sys.executable} -m pip install -q yacs neurokit2 timm', cwd=repo, check=True)

    if not _cv2_version().startswith('4.10'):
        print('    [hooks] 重装 opencv-headless 4.10.0.84')
        _sh(f'{sys.executable} -m pip uninstall -y -q opencv-python opencv-python-headless '
            'opencv-contrib-python opencv-contrib-python-headless', quiet=True)
        shutil.rmtree(os.path.join(sysconfig.get_paths()['purelib'], 'cv2'), ignore_errors=True)
        _sh(f'{sys.executable} -m pip install -q --no-cache-dir "opencv-python-headless==4.10.0.84"',
            check=True)
    print(f'    [hooks] cv2 = {_cv2_version()}')


# ------------------------------------------------------------------ 补丁
def _patch(label, relpath, marker, transform, repo: Path):
    p = repo / relpath
    if not p.exists():
        print(f'    [skip] {label}: 缺文件 {relpath}')
        return
    text = p.read_text(encoding='utf-8', errors='ignore')
    if marker and marker in text:
        print(f'    [ok]   {label}（已打过）')
        return
    new = transform(text)
    if new == text:
        print(f'    [warn] {label}: 没匹配到可改内容')
        return
    p.write_text(new, encoding='utf-8')
    print(f'    [done] {label}')


def patch(ctx):
    repo = ctx.paths.repo

    def _mamba(t):
        return ''.join(('# ' + ln if 'Mamba' in ln and not ln.lstrip().startswith('#') else ln)
                       for ln in t.splitlines(True))

    _patch('注释掉 Mamba import', 'neural_methods/trainer/__init__.py',
           '# import neural_methods.trainer.PhysMambaTrainer', _mamba, repo)

    for rel in sorted(glob.glob(str(repo / 'unsupervised_methods/methods/*.py'))):
        p = Path(rel)
        t = p.read_text(encoding='utf-8', errors='ignore')
        if 'np.mat(' in t:
            p.write_text(t.replace('np.mat(', 'np.asmatrix('), encoding='utf-8')
            print(f'    [done] np.mat → np.asmatrix: {p.name}')
        elif 'np.asmatrix(' in t:
            print(f'    [ok]   np.mat → np.asmatrix: {p.name}（已打过）')

    _patch('PBV 单样本求解', 'unsupervised_methods/methods/PBV.py',
           'np.swapaxes(pbv, 0, 1)[..., None])[..., 0]',
           lambda t: t.replace('W = np.linalg.solve(Q, np.swapaxes(pbv, 0, 1))',
                               'W = np.linalg.solve(Q, np.swapaxes(pbv, 0, 1)[..., None])[..., 0]'),
           repo)

    _patch('main.py num_workers 16→2', 'main.py', 'num_workers=2',
           lambda t: t.replace('num_workers=16', 'num_workers=2'), repo)

    _patch('BaseLoader 多进程配额→2', 'dataset/data_loader/BaseLoader.py', 'multi_process_quota=2',
           lambda t: re.sub(r'multi_process_quota=\d+', 'multi_process_quota=2', t), repo)


# ------------------------------------------------------------------ 数据后处理
def prepare(ctx):
    """数据已在 bootstrap 里下载到 Drive；这里只做"拼视图"：
    用符号链接把各个 subject 目录拼成工具要的 /tmp/UBFC（本地盘支持软链，Drive 不支持）。"""
    import bootstrap  # 自己

    # 数据只在 Drive 上
    root = ctx.paths.datasets / 'kaggle'
    vids = []
    for d in sorted(root.glob('*/')):
        vids += sorted(glob.glob(str(d / '**' / 'subject*_vid.avi'), recursive=True))
    view = bootstrap.VMLOCAL / 'UBFC'
    view.mkdir(parents=True, exist_ok=True)

    if not vids:
        print('  数据          ⚠ Drive 里没找到 subject*_vid.avi')
        print(f'                检查 {root}')
        return
    built = 0
    for v in vids:
        v = Path(v)
        d = view / v.name.replace('_vid.avi', '')
        d.mkdir(parents=True, exist_ok=True)
        for src, dst in [(v, 'vid.avi'),
                         (Path(str(v).replace('_vid.avi', '_ground_truth.txt')), 'ground_truth.txt')]:
            link = d / dst
            if not link.exists() and src.exists():
                os.symlink(src, link)
        built += 1
    print(f'  数据          视图 {view} 已就绪（{built} 个 subject，全部是软链，不占空间）')

    # 顺手把 config 里的 /gscratch 路径替换好，生成可直接跑的配置文件
    for pattern, out_name in [
        ('configs/infer_configs/UBFC-rPPG_UNSUPERVISED.yaml', 'COLAB_UBFC_UNSUPERVISED.yaml'),
    ]:
        src = ctx.paths.repo / pattern
        if not src.exists():
            continue
        text = src.read_text()
        text = text.replace(GRSCRATCH_RAW, str(view))
        # 预处理缓存是可再生的中间产物 → 放 VM 本地盘（vmcache），不进 Drive
        text = text.replace(GRSCRATCH_CACHE, str(ctx.paths.vmcache / 'rppg'))
        text = text.replace('DO_PREPROCESS: False', 'DO_PREPROCESS: True')
        text = text.replace('["ICA", "POS", "CHROM", "GREEN", "LGI", "PBV", "OMIT"]', '["POS", "CHROM"]')
        (ctx.paths.repo / 'configs/infer_configs' / out_name).write_text(text)
        print(f'  数据          已生成 configs/infer_configs/{out_name}')
