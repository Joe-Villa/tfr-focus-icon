#!/usr/bin/env python3
"""icon 成品验收闸门（install_qa 独立实现，不 import skill 的 metrics.py）。

为什么需要它：metrics.py 只看 alpha 通道的形状统计。实测 runC/final_v0.png
（画面中央残留一整块洋红卡片）的 cov/tight/comp 全部落进真品区间 → 被判通过。
形状指标发现不了"颜色错了"，所以这里补上颜色侧的硬检查。

用法：
    python3 qa_gate.py out/final_*.png        # 逐张判 PASS/WARN/FAIL
    python3 qa_gate.py --stats <dir>          # 拿一批图当对照（真品），看误报率

判据（阈值按真品分布 + 已知废品样本校准得来）：
  FAIL 键色污染 : 不透明像素里 洋红/绿键 颜色占比 > 5%   （废品 0.278/0.611，正常样本 <=0.010）
  FAIL 没抠图   : 不透明占比 > 0.95                     （背景压根没抠掉）
  FAIL 太碎     : 不透明占比 < 0.30
  WARN 尺寸     : 画布高不在 138-155，或长宽比不在 1.05-1.45（真品 10-90% 带）
  WARN 剪影     : tight < 0.55（真品中位 0.68）
  WARN 边角     : 四个角里出现不透明像素（真品是抠图轮廓，角上应是透明的）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# 阈值按真品分布校准过：收紧到"10-90% 带"会让 40 张真品里 12 张 WARN（误报），
# 所以这里只取"几乎不越界"的宽带来抓真正的异常；窄带数值写进报告供人看。
TRUE_H = (135, 200)        # 真品画布高全距 140-196（n=300）
TRUE_AR = (1.00, 1.60)     # 真品长宽比 10-90%: 1.05-1.43；放宽到 1.60 以免误伤 1.5 左右的长条图
CONTAM_MAX = 0.05
TIGHT_MIN = 0.55


def measure(path: Path) -> dict:
    with Image.open(path) as im:
        mode = im.mode
        if mode != "RGBA":
            im = im.convert("RGBA")
        a = np.asarray(im).astype(np.int16)
    al = a[..., 3].astype(np.float32) / 255.0
    rgb = a[..., :3]
    op = al > 0.5
    any_ = al > 0.05
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # 洋红/紫："R、B 明显高于 G" 且**够亮**。亮度和门槛是 2026-10-16 加的：
    # 纯色相判据会把画师画的深紫（例如紫发皮套）当成键色残留 —— 实测 800 张 TFR 真品里
    # 2.25% 会被误判，加上 mx>=190 后降到 1.00%；而两个已知废品样本（0.278/0.611）照样被抓住。
    mx = np.maximum(np.maximum(r, g), b)
    magenta = ((r - g) > 60) & ((b - g) > 60) & (mx >= 190)
    # 绿同理：意大利三色旗那种"实心绿条纹"实测 0.027-0.205（旧判据），加亮度门槛后 0.000-0.001。
    green = ((g - np.maximum(r, b)) > 60) & (mx >= 190)
    n_op = max(1, int(op.sum()))
    ys, xs = np.where(any_)
    m = {
        "path": str(path), "mode": mode, "w": a.shape[1], "h": a.shape[0],
        "opaque": float(op.mean()), "cov": float(any_.mean()),
        "contam": float((magenta & op).sum() / n_op),
        "contam_green": float((green & op).sum() / n_op),
        "corner_op": int(op[0, 0]) + int(op[0, -1]) + int(op[-1, 0]) + int(op[-1, -1]),
    }
    if len(ys):
        ch, cw = int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)
        box = op[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        m["content_h"], m["content_w"] = ch, cw
        m["ar"] = cw / ch
        m["tight"] = float(box.sum() / max(1, box.size))
    else:
        m.update(content_h=0, content_w=0, ar=0.0, tight=0.0)
    return m


def judge(m: dict) -> tuple[str, list[str]]:
    fails, warns = [], []
    if m["mode"] != "RGBA":
        fails.append("不是 RGBA（%s）" % m["mode"])
    worst = max(m["contam"], m["contam_green"])
    if worst > CONTAM_MAX:
        kind = "洋红" if m["contam"] >= m["contam_green"] else "绿"
        fails.append("%s键色污染 %.3f > %.2f（背景没抠干净 / 模型画了色卡）" % (kind, worst, CONTAM_MAX))
    if m["opaque"] > 0.95:
        fails.append("不透明占比 %.2f > 0.95（压根没抠图）" % m["opaque"])
    if m["opaque"] < 0.30:
        fails.append("不透明占比 %.2f < 0.30（图形太碎）" % m["opaque"])
    if not (TRUE_H[0] <= m["h"] <= TRUE_H[1]):
        warns.append("画布高 %d 出真品带 %d-%d" % (m["h"], TRUE_H[0], TRUE_H[1]))
    if m["ar"] and not (TRUE_AR[0] <= m["ar"] <= TRUE_AR[1]):
        warns.append("长宽比 %.2f 出真品带 %.2f-%.2f" % (m["ar"], TRUE_AR[0], TRUE_AR[1]))
    if m["tight"] and m["tight"] < TIGHT_MIN:
        warns.append("剪影 tight %.2f < %.2f（元素太散）" % (m["tight"], TIGHT_MIN))
    if m["corner_op"]:
        warns.append("有 %d 个角不透明（真品角上是透明的）" % m["corner_op"])
    return ("FAIL" if fails else ("WARN" if warns else "PASS")), fails + warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pngs", nargs="*", type=Path)
    ap.add_argument("--stats", type=Path, help="只统计一批图的判定分布（误报率对照）")
    args = ap.parse_args()
    paths = sorted(args.stats.rglob("*.png")) if args.stats else args.pngs
    if not paths:
        ap.error("给几个 png，或用 --stats 指一个目录")
    tally = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for p in paths:
        try:
            m = measure(p)
        except Exception as e:
            print("FAIL %s: 读不了（%s）" % (p, e))
            tally["FAIL"] += 1
            continue
        verdict, reasons = judge(m)
        tally[verdict] += 1
        if args.stats:
            continue
        print("%-4s %-26s %dx%d 不透明%.2f 污染%.3f tight%.2f ar%.2f"
              % (verdict, Path(m["path"]).name, m["w"], m["h"], m["opaque"], max(m["contam"], m["contam_green"]), m["tight"], m["ar"]))
        for r in reasons:
            print("      - " + r)
    if args.stats:
        n = sum(tally.values())
        print("%d 张：PASS %d / WARN %d / FAIL %d" % (n, tally["PASS"], tally["WARN"], tally["FAIL"]))
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
