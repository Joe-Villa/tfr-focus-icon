#!/usr/bin/env python3
"""RMBG-1.4 抠图（ONNX 版，不需要 rembg）。

为什么有这个文件
----------------
彩色色键（`iconify.key_white` / `chroma_cutout`）在**主体自身含键色**时会坏掉：

    国旗的白条纹   -> 被当成背景掏空
    白头海雕的头部 -> 出现空洞
    白衬衫/白柱体  -> 和背景一起被切掉

`rembg` 能解决，但**本机装不了**（要求 numpy>=2.3，会顶掉系统 numpy 1.26.4，scipy 直接崩）。
RMBG-1.4 存成 ONNX 图后只需要 `onnxruntime`，与 numpy 1.26 / scipy 1.11 共存无冲突。

用法
----
    python3 rmbg.py --download                  # 约 176 MB，走 ModelScope 直连
    python3 rmbg.py in.png -o out.png           # 抠一张
    python3 rmbg.py in.png -o out.png --key     # 同时跑色键对比

作为库：

    from rmbg import cutout, available
    rgba = cutout(Image.open("element.png"))
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

CACHE = Path.home() / ".cache" / "tfricon"
MODEL = CACHE / "rmbg14.onnx"
# ModelScope 镜像了 briaai/RMBG-1.4，国内直连（约 1.3 MB/s）
MODEL_URL = ("https://modelscope.cn/api/v1/models/AI-ModelScope/RMBG-1.4/repo"
             "?Revision=master&FilePath=onnx/model.onnx")


def download(force: bool = False) -> Path:
    if MODEL.exists() and not force:
        print(f"模型已存在：{MODEL} ({MODEL.stat().st_size / 1e6:.0f} MB)")
        return MODEL
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"下载 {MODEL_URL}\n  -> {MODEL}")
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连，不走代理
    tmp = MODEL.with_suffix(".part")
    with opener.open(MODEL_URL, timeout=1800) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("content-length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:7.1f}/{total / 1e6:.0f} MB", end="", flush=True)
    tmp.rename(MODEL)
    print(f"\n已保存：{MODEL} ({MODEL.stat().st_size / 1e6:.0f} MB)")
    return MODEL


def available() -> bool:
    """能不能用 RMBG（onnxruntime 装了 + 模型在本地）。"""
    if not MODEL.exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


class Matter:
    """单例 CPU session；1024×1024 一张约 0.6-0.8 秒。"""

    _inst: "Matter | None" = None

    def __init__(self, path: Path | None = None):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        p = path or MODEL
        if not p.exists():
            raise FileNotFoundError(f"{p} 不存在 —— 先跑：python3 {Path(__file__).name} --download")
        self.sess = ort.InferenceSession(str(p), opts, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0]
        self.out = self.sess.get_outputs()[0].name
        shape = self.inp.shape
        self.size = (int(shape[3]) if isinstance(shape[3], int) else 1024,
                     int(shape[2]) if isinstance(shape[2], int) else 1024)

    @classmethod
    def get(cls) -> "Matter":
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def alpha(self, img: Image.Image) -> np.ndarray:
        w, h = img.size
        small = img.convert("RGB").resize(self.size, Image.BILINEAR)
        x = np.asarray(small).astype(np.float32) / 255.0
        x = (x - 0.5) / 1.0                      # RMBG-1.4 用 mean .5 / std 1.0
        x = np.transpose(x, (2, 0, 1))[None]
        m = np.squeeze(self.sess.run([self.out], {self.inp.name: x})[0]).astype(np.float32)
        if m.ndim == 3:
            m = m[0]
        if m.min() < 0 or m.max() > 1:           # logits → sigmoid
            m = 1.0 / (1.0 + np.exp(-m))
        lo, hi = float(m.min()), float(m.max())
        m = (m - lo) / max(1e-6, hi - lo)
        return np.asarray(Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L")
                          .resize((w, h), Image.BILINEAR)).astype(np.float32) / 255.0


def cutout(img: Image.Image, *, erode: float = 0.5, blur: float = 0.6,
           matter: "Matter | None" = None) -> Image.Image:
    """RGBA cutout。要求背景干净（纯色/白底）、主体与背景分离。"""
    m = (matter or Matter.get()).alpha(img)
    if erode:
        m = np.clip((m - 0.1 * erode) / max(1e-6, 1 - 0.1 * erode), 0, 1)
    alpha = Image.fromarray((m * 255).astype(np.uint8), "L")
    if blur:
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    rgba = img.convert("RGB").copy()
    rgba.putalpha(alpha)
    return rgba


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?", help="输入图")
    ap.add_argument("-o", "--out", help="输出 PNG（默认 <image>_cut.png）")
    ap.add_argument("--download", action="store_true", help="下载模型后退出")
    ap.add_argument("--key", action="store_true", help="顺带跑色键抠图做对比")
    args = ap.parse_args(argv)

    if args.download:
        download()
        return 0
    if not args.image:
        ap.error("需要一张图（或用 --download）")

    src = Image.open(args.image)
    out_path = Path(args.out or (Path(args.image).with_suffix("").as_posix() + "_cut.png"))
    cut = cutout(src)
    cut.save(out_path)
    a = np.asarray(cut)[..., 3] / 255.0
    print(f"{out_path}  {cut.size}  opaque={(a > .5).mean():.3f}  soft={((a > .05) & (a < .95)).mean():.3f}")

    if args.key:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import iconify as ic

            k = ic.auto_cutout_full(src.convert("RGB"), mode="legacy")
            ka = np.asarray(k)[..., 3] / 255.0
            print(f"  色键：opaque={(ka > .5).mean():.3f}（差 {(a > .5).mean() - (ka > .5).mean():+.3f}）")
        except Exception as e:  # noqa: BLE001
            print(f"  色键对比跳过：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
