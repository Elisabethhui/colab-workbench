#!/usr/bin/env python3
"""流式 rPPG 演示：摄像头 / 录像 -> 滑动窗口 -> EfficientPhys -> 逐窗心率。

与 toolbox 离线评测的区别（这才是"流式"的意思）：
  离线：整段视频读完 -> 切 180 帧 chunk -> 整段 FFT -> **一个**心率
  流式：每来一帧就更新一个环形缓冲 -> 每 stride 帧出一组波形 -> **当前**心率
        只用到"过去 6 秒"，不碰未来帧；延迟可测。

用法（本机 Mac，真摄像头，CPU）：
    python stream_demo.py --source 0 --device cpu
    python stream_demo.py --source 0 --record /tmp/clip.mp4 --duration 20 --no-gui

用法（Colab VM，没有摄像头，回放录像，走 CUDA）：
    python stream_demo.py --source /content/clip.mp4 --device cuda --no-gui \
        --save-csv /content/drive/MyDrive/Colab_Projects/projects/rppg/logs/stream_demo.csv

关键参数：
  --window 180      滑动窗口帧数（30fps => 6 秒），也是模型一次吃进的帧数
  --stride 15       每多少帧算一次心率（15 => 每 0.5 秒刷新）
  --min-hz/--max-hz 心率搜索带，0.75–2.5 Hz 即 45–150 bpm
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import deque

import cv2
import numpy as np
import torch

# ---------------------------------------------------------------- 模型与权重

def load_model(repo: str, weights: str, device: torch.device, img_size: int = 72,
               frame_depth: int = 10):
    """按 toolbox 的方式建 EfficientPhys 并加载作者在 PURE 上训的权重。"""
    sys.path.insert(0, repo)
    # 仓库里的目录名是小写 neural_methods；macOS 虽然大小写不敏感，但 importlib 按
    # listdir 的真实文件名做精确匹配，所以大写写法在 Mac 上会 ModuleNotFoundError。
    try:
        from neural_methods.model.EfficientPhys import EfficientPhys  # noqa: E402
    except ModuleNotFoundError:
        from Neural_methods.model.EfficientPhys import EfficientPhys  # noqa: E402

    model = EfficientPhys(frame_depth=frame_depth, img_size=img_size)
    state = torch.load(weights, map_location="cpu")
    # checkpoint 是 DataParallel 存的，键名带 module. 前缀，先剥掉
    state = {k[len("module."):] if k.startswith("module.") else k: v for k, v in state.items()}
    model.load_state_dict(state)
    model.to(device).eval()
    return model


def forward_window(model, frames: np.ndarray, device: torch.device) -> np.ndarray:
    """frames: (T, 72, 72, 3) uint8 -> 返回 (T-1,) 的 PPG 波形。

    和 toolbox 的 EfficientPhysTrainer.test() 完全一致：
      1. 标准化（z-score，按窗口内统计量近似数据集统计量）
      2. 转成 (T, 3, 72, 72)，送进模型（模型内部对时间轴做 torch.diff）
      3. 输出逐帧 PPG 值
    """
    x = frames.astype(np.float32)
    x = (x - x.mean()) / (x.std() + 1e-8)
    x = np.transpose(x, (0, 3, 1, 2))                     # (T,3,H,W)
    t = torch.from_numpy(x).to(device)
    with torch.no_grad():
        out = model(t)                                    # (T-1, 1)
    return out.squeeze(-1).float().cpu().numpy()


# ---------------------------------------------------------------- 心率估计

def hr_from_waveform(wave: np.ndarray, fs: float, min_hz: float, max_hz: float) -> float:
    """对一段 PPG 波形做 FFT，取心率带内主频（单位 bpm）。流式里每窗调用一次。"""
    w = wave - np.mean(wave)
    if np.std(w) < 1e-6 or len(w) < 16:
        return float("nan")
    w = w * np.hanning(len(w))                            # 抑制频谱泄漏
    spec = np.abs(np.fft.rfft(w))
    freqs = np.fft.rfftfreq(len(w), d=1.0 / fs)
    band = (freqs >= min_hz) & (freqs <= max_hz)
    if not band.any():
        return float("nan")
    return float(freqs[band][np.argmax(spec[band])] * 60.0)


# ---------------------------------------------------------------- 人脸裁剪

class FaceCropper:
    """复刻 toolbox 的 crop_face_resize：

    - 多个脸取最大；检不到就用整帧（和上游行为一致）
    - USE_LARGE_FACE_BOX: True / LARGE_BOX_COEF: 1.5 -> 以脸中心放大 1.5 倍
    - 流式里按 detect_every 帧重检测（对应上游 DO_DYNAMIC_DETECTION=True）；
      上游 UBFC 配置是静态框（只检一次），那样人一动就废，流式必须跟踪。

    人脸检测后端三选一：
      cascade  OpenCV 4 的 Haar（Colab 的 opencv-python-headless 4.10 用这个，和上游一致）
      yunet    OpenCV 5 已删除 CascadeClassifier，改用 YuNet（需要 onnx 模型，232 KB）
      none     不检测，整帧缩放（只验证模型链路时用）
    """

    def __init__(self, size: int = 72, coef: float = 1.5, detect_every: int = 30,
                 backend: str = "auto", cascade: str | None = None,
                 yunet_model: str | None = None):
        self.size = size
        self.coef = coef
        self.detect_every = detect_every
        self.box = None                                   # (x, y, w, h)
        self.misses = 0
        self.backend = "none"
        self.detector = None
        self._yunet = None

        if backend in ("auto", "cascade") and hasattr(cv2, "CascadeClassifier"):
            path = cascade or (cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            det = cv2.CascadeClassifier(path)
            if not det.empty():
                self.detector, self.backend = det, "cascade"

        if self.backend == "none" and backend in ("auto", "yunet"):
            model = yunet_model or os.environ.get("YUNET_MODEL") or "/tmp/yunet.onnx"
            if os.path.exists(model):
                self._yunet = cv2.FaceDetectorYN_create(model, "", (320, 320), 0.8, 0.3, 5000)
                self.backend = "yunet"

        if self.backend == "none":
            if backend != "none":
                print("[警告] 没有可用的人脸检测器（cv2 %s，无 CascadeClassifier，也没找到 %s）"
                      "→ 退化为整帧缩放" % (cv2.__version__, yunet_model or "/tmp/yunet.onnx"))
            self.backend = "none"
        print(f"[流式] 人脸检测 = {self.backend}" + ("" if self.backend != "yunet"
                                                   else "（YuNet, 每 %d 帧一次）" % detect_every))

    def _detect(self, frame):
        if self.backend == "cascade":
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            zones = self.detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
            if len(zones) == 0:
                return None
            return tuple(int(v) for v in zones[int(np.argmax(zones[:, 2]))])
        if self.backend == "yunet":
            h, w = frame.shape[:2]
            self._yunet.setInputSize((w, h))
            _, faces = self._yunet.detect(frame)
            if faces is None or len(faces) == 0:
                return None
            row = faces[int(np.argmax(faces[:, 2]))]      # 取最宽的那张脸
            return tuple(int(v) for v in row[:4])
        return None

    def crop(self, frame, idx: int) -> np.ndarray:
        if self.box is None or (self.detect_every and idx % self.detect_every == 0):
            found = self._detect(frame)
            if found is not None:
                self.box, self.misses = found, 0
            elif self.box is not None:
                self.misses += 1
        if self.box is None:                              # 完全没脸：整帧
            x, y, w, h = 0, 0, frame.shape[1], frame.shape[0]
        else:
            x, y, w, h = self.box
            # 以中心放大 1.5 倍
            cx, cy = x + w / 2, y + h / 2
            w, h = w * self.coef, h * self.coef
            x, y = cx - w / 2, cy - h / 2
            x, y = max(int(x), 0), max(int(y), 0)
            w, h = int(w), int(h)
        patch = frame[y:y + h, x:x + w]
        if patch.size == 0:
            patch = frame
        return cv2.resize(patch, (self.size, self.size), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- 主循环

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="流式 rPPG 演示（摄像头或录像）")
    ap.add_argument("--source", default="0", help="摄像头索引(0/1) 或视频文件路径")
    ap.add_argument("--repo", default=os.environ.get("RPPG_REPO", "/tmp/t_repo"),
                    help="rPPG-Toolbox 仓库路径（钉死版本 b7500b8 的 clone）")
    ap.add_argument("--weights", default=None,
                    help="默认 <repo>/final_model_release/PURE_EfficientPhys.pth")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"],
                    help="auto = 有 CUDA 用 CUDA，否则 CPU。MPS 上游代码跑不了（见 forward 注释）")
    ap.add_argument("--window", type=int, default=180, help="滑动窗口帧数（30fps×6s）")
    ap.add_argument("--stride", type=int, default=15, help="每多少帧出一次心率")
    ap.add_argument("--frame-depth", type=int, default=10, help="TSM 分段数（=MODEL.FRAME_DEPTH）")
    ap.add_argument("--min-hz", type=float, default=0.75)
    ap.add_argument("--max-hz", type=float, default=2.5)
    ap.add_argument("--detect-every", type=int, default=30, help="每多少帧重检测人脸")
    ap.add_argument("--face", default="auto", choices=["auto", "cascade", "yunet", "none"],
                    help="人脸检测后端（OpenCV4 用 cascade，OpenCV5 用 yunet）")
    ap.add_argument("--yunet-model", default="/tmp/yunet.onnx", help="YuNet onnx 路径")
    ap.add_argument("--session", default=None,
                    help="只做记录：这次跑在哪台 Colab session 上（写进 csv 首行注释）")
    ap.add_argument("--duration", type=float, default=0, help="跑多久后自动停（秒，0=不限）")
    ap.add_argument("--no-gui", action="store_true", help="不开窗（VM 上必须开）")
    ap.add_argument("--record", default=None, help="把输入同时录成 mp4（给 VM 回放用）")
    ap.add_argument("--save-csv", default=None, help="把每次心率写进 csv")
    args = ap.parse_args(argv)

    # ---- 设备
    # 注意：MPS 会在上游 EfficientPhys.py:123 的 d8.view(...) 上报
    # "view size is not compatible with input tensor's size and stride"
    # （Apple MPS 的中间张量不连续），上游只用 CUDA/CPU，所以 auto 不选 MPS。
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
        if args.device == "mps":
            print("[警告] MPS 可能在上游 d8.view() 上报 stride 错误，建议 --device cpu")

    # ---- 权重/仓库
    weights = args.weights or os.path.join(args.repo, "final_model_release", "PURE_EfficientPhys.pth")
    if not os.path.exists(weights):
        print(f"[错误] 找不到权重 {weights}（先 git clone rPPG-Toolbox 并 checkout b7500b8）")
        return 2
    size = 72
    model = load_model(args.repo, weights, device, img_size=size, frame_depth=args.frame_depth)
    print(f"[流式] 设备={device}  窗口={args.window}帧({args.window/30:.1f}s)  "
          f"刷新=每{args.stride}帧  权重={os.path.basename(weights)}")

    # ---- 输入源
    src = args.source
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        print(f"[错误] 打不开输入源 {src}")
        return 2
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src.isdigit():
        src_fps = 30.0
    writer = None
    if args.record:
        writer = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), src_fps,
                                 (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640,
                                  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480))
    csv_f = None
    csv_w = None
    if args.save_csv:
        os.makedirs(os.path.dirname(args.save_csv) or ".", exist_ok=True)
        csv_f = open(args.save_csv, "w", newline="")
        csv_w = csv.writer(csv_f)
        csv_w.writerow(["wall_time", "frame_idx", "hr_bpm", "infer_ms", "window_fps"])

    cropper = FaceCropper(size=size, detect_every=args.detect_every,
                          backend=args.face, yunet_model=args.yunet_model)
    frames = deque(maxlen=args.window + 1)      # +1 因为模型要吃 T+1 帧出 T 个值
    hr_hist = deque(maxlen=5)                   # 中值平滑，压掉单窗抖动
    t_start = time.time()
    idx = 0
    n_infer = 0
    infer_ms_sum = 0.0
    hr_last = float("nan")
    fps_win = src_fps
    fail_streak = 0
    # 摄像头刚打开时前几帧经常读不出来（AVFoundation 预热 + 权限弹窗），
    # 所以不能一失败就 break；文件源失败了才算真结束。
    max_fail = 30 if src.isdigit() else 1

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                fail_streak += 1
                if fail_streak >= max_fail:
                    break
                time.sleep(0.05)
                continue
            fail_streak = 0
            if writer is not None:
                writer.write(frame)

            frames.append(cropper.crop(frame, idx))
            idx += 1

            # 窗口没满先不出结果（流式不改这个事实：必须等够一个窗口）
            if len(frames) < args.window + 1:
                pass
            elif (idx - args.window) % args.stride == 0:
                t0 = time.perf_counter()
                wave = forward_window(model, np.stack(frames), device)
                infer_ms = (time.perf_counter() - t0) * 1000.0
                hr = hr_from_waveform(wave, fps_win, args.min_hz, args.max_hz)
                if np.isfinite(hr):
                    hr_hist.append(hr)
                    hr_last = float(np.median(hr_hist))
                n_infer += 1
                infer_ms_sum += infer_ms
                line = (f"\r[{idx:5d}帧 {idx/src_fps:6.1f}s] "
                        f"HR={hr_last:5.1f} bpm  (本窗 {hr:5.1f})  "
                        f"推理 {infer_ms:5.1f} ms  窗口延迟 {args.window/src_fps:.1f}s ")
                print(line, end="", flush=True)
                if csv_w:
                    csv_w.writerow([time.strftime("%H:%M:%S"), idx,
                                    f"{hr_last:.2f}", f"{infer_ms:.1f}", f"{fps_win:.1f}"])

            if not args.no_gui:
                vis = frame.copy()
                if cropper.box is not None:
                    x, y, w, h = cropper.box
                    cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
                txt = f"HR {hr_last:.1f} bpm" if np.isfinite(hr_last) else "warming up..."
                cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                cv2.imshow("stream rPPG (q to quit)", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if args.duration and (time.time() - t_start) >= args.duration:
                break
            # 录像回放时按原速播放，模拟"帧陆续到达"
            if not src.isdigit():
                target = idx / src_fps
                lag = target - (time.time() - t_start)
                if lag > 0:
                    time.sleep(lag)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if csv_f:
            csv_f.close()
        if not args.no_gui:
            cv2.destroyAllWindows()

    elapsed = time.time() - t_start
    print(f"\n[汇总] 处理 {idx} 帧 / {elapsed:.1f}s  "
          f"推理 {n_infer} 次  平均 {infer_ms_sum/max(n_infer,1):.1f} ms/次  "
          f"最终 HR={hr_last:.1f} bpm")
    if args.record:
        print(f"[录像] {args.record}")
    if args.save_csv:
        print(f"[指标] {args.save_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
