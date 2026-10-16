#!/usr/bin/env python3
"""用 alpha 通道的客观指标衡量"像不像 TFR"，并可和真实 icon 的分布对比。

指标（只依赖 alpha 通道，所以对风格判断是中性的）：
  cov   = alpha>0.05 的像素占比            —— 画面被内容占据多少
  soft  = 0.05<alpha<0.95 的占比           —— 外发光/羽化边缘厚度
  tight = 不透明像素 / alpha 包围盒面积    —— 剪影的"实心/紧凑"程度（TFR ≈ 0.74）
  comp  = 周长² / 面积                     —— 轮廓碎不碎（TFR ≈ 27-37）
  edge  = 内容贴到画布边缘的边数(0-4)

用法：
    python3 metrics.py --stats                       # 从真品算参考分布（需要 --mod 或 HOI4_MOD_GOALS_DIR）
    python3 metrics.py out/final_01.png              # 单张
    python3 metrics.py out/*.png --vs references/tfr_stats.json

真品目录怎么给：`--mod <mod>/gfx/interface/goals`，或环境变量 `HOI4_MOD_GOALS_DIR`。
没给就退回本机常见路径（steam workshop 的 TFR），找不到会打印提示让你自己指过去。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

STEAM_MOD = Path.home() / ".steam/debian-installation/steamapps/workshop/content/394360/3350890356/gfx/interface/goals"
DEFAULT_MOD = Path(os.environ.get("HOI4_MOD_GOALS_DIR") or STEAM_MOD)
STATS = Path(__file__).resolve().parent.parent / "references" / "tfr_stats.json"


def resolve_mod(value: str | None = None) -> Path:
    """真品目录的优先级：命令行 --mod > 环境变量 HOI4_MOD_GOALS_DIR > 本机默认路径。"""
    if value:
        return Path(value)
    env = os.environ.get("HOI4_MOD_GOALS_DIR")
    return Path(env) if env else STEAM_MOD


def mod_hint(mod: Path) -> str:
    return (f"在 {mod} 里没找到真品 icon。\n"
            f"指向你自己那份安装目录，例如：\n"
            f'  --mod "/path/to/mod/gfx/interface/goals"     # 或 export HOI4_MOD_GOALS_DIR=<那个目录>\n'
            f"（TFR/TNO 一系 mod 的真品都在 <mod>/gfx/interface/goals/<TAG>/*.png）")


def measure(path) -> dict:
    with Image.open(path) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        a = np.asarray(im)[..., 3].astype(np.float32) / 255.0
    h, w = a.shape
    solid = a > 0.5
    any_ = a > 0.05
    soft = ((a > 0.05) & (a < 0.95)).mean()
    cov = any_.mean()
    ys, xs = np.where(any_)
    if len(ys) == 0:
        return {"file": str(path), "w": w, "h": h, "cov": 0.0, "soft": 0.0, "tight": 0.0, "comp": 0.0, "edge": 0}
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    bbox_area = (y1 - y0) * (x1 - x0)
    tight = solid.sum() / max(1, bbox_area)

    # 周长：对 solid 做 4 邻域差分
    pad = np.pad(solid, 1)
    peri = int(np.sum(solid & ~pad[:-2, 1:-1])) + int(np.sum(solid & ~pad[2:, 1:-1])) \
        + int(np.sum(solid & ~pad[1:-1, :-2])) + int(np.sum(solid & ~pad[1:-1, 2:]))
    area = max(1, int(solid.sum()))
    comp = (peri ** 2) / area

    edges = 0
    if ys.min() == 0:
        edges += 1
    if ys.max() == h - 1:
        edges += 1
    if xs.min() == 0:
        edges += 1
    if xs.max() == w - 1:
        edges += 1
    return {"file": str(path), "w": w, "h": h, "cov": round(cov, 3), "soft": round(soft, 3),
            "tight": round(tight, 3), "comp": round(comp, 1), "edge": edges}


def summarize(rows):
    keys = ["cov", "soft", "tight", "comp", "edge", "w", "h"]
    out = {}
    for k in keys:
        v = np.array([r[k] for r in rows], dtype=float)
        out[k] = {"mean": round(float(v.mean()), 3), "median": round(float(np.median(v)), 3),
                  "p25": round(float(np.percentile(v, 25)), 3), "p75": round(float(np.percentile(v, 75)), 3)}
    out["n"] = len(rows)
    return out


def edge_profile(path, rings: int = 4) -> dict:
    """边缘剖面：不透明区往内 k 圈的平均亮度，以及外侧半透明圈的平均亮度。

    真实 TFR：ring1≈28、ring2≈20、ring4≈26（一圈 ~3px 的深描边，很快回到内部亮度），
    外侧半透明圈偏亮（≈101），不是黑晕。
    """
    from scipy import ndimage

    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGBA")).astype(np.float32)
    alpha = arr[..., 3] / 255.0
    lum = arr[..., :3].mean(-1)
    core = alpha > 0.95
    if not core.any():
        return {}
    dist = ndimage.distance_transform_edt(core)
    out = {"core": round(float(lum[core].mean()), 1)}
    for k in range(1, rings + 1):
        m = (dist > k - 1) & (dist <= k)
        out[f"ring{k}"] = round(float(lum[m].mean()), 1) if m.any() else None
    outer = (alpha > 0.0) & (alpha <= 0.3)
    out["outer"] = round(float(lum[outer].mean()), 1) if outer.any() else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--stats", action="store_true", help="从真品 mod 重新计算参考分布并写入 references/tfr_stats.json")
    ap.add_argument("--mod", default=None,
                    help="真品目录 <mod>/gfx/interface/goals（也可用环境变量 HOI4_MOD_GOALS_DIR）")
    ap.add_argument("--n", type=int, default=400, help="真品采样数量")
    ap.add_argument("--vs", default=None, help="和参考分布对比（默认自动找 references/tfr_stats.json）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--profile", action="store_true", help="额外打印边缘剖面")
    args = ap.parse_args()

    if args.stats:
        import random

        mod = resolve_mod(args.mod)
        files = sorted(mod.rglob("*.png"))
        if not files:
            print(mod_hint(mod), file=sys.stderr)
            return 2
        random.seed(0)
        pick = random.sample(files, min(args.n, len(files)))
        rows = [measure(p) for p in pick]
        s = summarize(rows)
        STATS.parent.mkdir(parents=True, exist_ok=True)
        STATS.write_text(json.dumps({"source": str(mod), "n": s["n"], "stats": s}, ensure_ascii=False, indent=2))
        print("参考分布已写入", STATS)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    if not args.files:
        ap.error("给几个 png，或用 --stats")
    rows = [measure(f) for f in args.files]
    ref = None
    if args.vs:
        ref = json.loads(Path(args.vs).read_text())["stats"]
    elif STATS.exists():
        ref = json.loads(STATS.read_text())["stats"]

    print(f"{'file':44s} {'cov':>6s} {'soft':>6s} {'tight':>6s} {'comp':>7s} {'edge':>4s}")
    for r in rows:
        print(f"{Path(r['file']).name:44s} {r['cov']:6.3f} {r['soft']:6.3f} {r['tight']:6.3f} {r['comp']:7.1f} {r['edge']:4d}")
    if ref:
        print("\n真实 TFR 参考（median）：cov=%.3f soft=%.3f tight=%.3f comp=%.1f edge=%.1f"
              % (ref["cov"]["median"], ref["soft"]["median"], ref["tight"]["median"], ref["comp"]["median"], ref["edge"]["median"]))
        for r in rows:
            tips = []
            if r["tight"] < ref["tight"]["p25"]:
                tips.append("剪影太散（往中心压紧/合并元素/填小洞）")
            if r["comp"] > ref["comp"]["p75"]:
                tips.append("边缘太碎（少用细碎元素、收敛外发光）")
            if r["cov"] < ref["cov"]["p25"]:
                tips.append("内容占比偏低（放大主体/减小留白）")
            print(f"  {Path(r['file']).name}: " + ("; ".join(tips) if tips else "指标落在真品区间内 ✅"))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    if args.profile:
        print("\n边缘剖面（core=不透明区亮度, ring k=往内第 k 圈, outer=外侧半透明圈）")
        for r in rows:
            print(f"  {Path(r['file']).name}: {edge_profile(r['file'])}")
        print("  真实 TFR 参考: core≈85, ring1≈28, ring2≈21, ring3≈22, ring4≈26, outer≈101")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
