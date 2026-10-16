#!/usr/bin/env python3
"""TFR 风格 HOI4 国策 icon 生成器（端到端）。

输入：一份 design JSON（见 skill/reference/design_schema.md 或 --example 打印示例）
输出：若干候选 PNG（透明背景、已缩到 HOI4 尺寸）+ 一张对比 contact sheet。

典型用法：
    python3 make_icon.py --design job.json --out ./out --variants 4
    python3 make_icon.py --design job.json --out ./out --no-gen      # 只处理已有图
    python3 make_icon.py --example                                   # 打印 design 模板

设计要点（都是实测结论，别乱改）：
  * 走"写实摄影/拼贴"措辞，不要说 icon/poster/illustration，否则模型给扁平插画。
  * 不要说"transparent background"（模型不理解 alpha），要说纯色平背景，本地再抠。
  * 不要传 watermark 参数（会直接触发 20021）。
  * 内容审核是**随机**的，同一 prompt 会时过时拒 → 必须并行多出 + 自动重试。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import iconify as ic  # noqa: E402
import sf  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

# --------------------------------------------------------------------------- #
# 风格卡：prompt 模板
# --------------------------------------------------------------------------- #
NEG_BASE = (
    "flat vector, cartoon, anime, cel shading, illustration, drawing, painting, "
    "sky, clouds, landscape, scenery, room, environment, gradient background, "
    "ground, floor, soil, cast shadow, horizon, stage, "
    "frame, border, rounded rectangle, ui panel, letters, caption, signature"
)

# 框架句非常重要：实测"游戏美术拼贴"这个身份比"写实人像/portrait"通过率高得多，
# 而且能压住模型往扁平插画跑的倾向。别删。
FRAME = "Photo-collage artwork for a grand-strategy game focus icon: "

DEFAULT_TREATMENTS = [
    "photograph of {subject}, {props}gritty texture, film grain, harsh contrast, hard rim light, "
    "oversaturated {palette} color grade, deep blacks, dirt and scratches, high micro-detail",
    "photobashed photographic cut-outs: {subject}, {props}layered tightly with hard cut edges, "
    "posterized, ink speckles and halftone dots, oversaturated {palette}, deep shadows",
    "cinematic still of {subject}, {props}dramatic backlight, volumetric haze, strong shadows, "
    "35mm film look, oversaturated {palette}, high contrast",
]

BG_CLAUSE = {
    "magenta": "placed on a flat uniform backdrop of pure magenta color, nothing else in the frame",
    "green": "placed on a flat uniform backdrop of pure chroma-key green color, nothing else in the frame",
    "white": "placed on a flat pure white seamless backdrop, nothing else in the frame",
    "none": "nothing else in the frame",
}

def build_prompt(design: dict, treatment: str | None = None) -> str:
    """按 design 拼提示词。

    关于"被审核拒绝"：`code 20021` 是**随机**的（同一 prompt 时过时拒），所以重试是正解。
    但重试只换"美术方向"（treatments 里换一种措辞/光照），**不做同义词替换去规避审核**——
    真实政治人物、真实军事行动这类题材本身不合适，就换题材或换画面元素。
    """
    subject = design["subject"]
    props = design.get("props") or []
    props_txt = ("; ".join(props) + ", ") if props else ""
    tmpl = treatment or (design.get("treatments") or DEFAULT_TREATMENTS)[0]
    body = tmpl.format(subject=subject, props=props_txt, palette=design.get("palette", "red and gold"))
    # 默认纯白影棚底：RMBG-1.4 在白底上最稳，而且白底不会像洋红那样被模型压暗成玫瑰灰
    bg = BG_CLAUSE.get(design.get("background", "white"), BG_CLAUSE["white"])
    tail = design.get(
        "tail",
        # 真品尺寸是 175×150 的**横构图**；让模型横向铺开、故意出血，
        # 抠图后才不会是一张竖幅人像（实测同一 prompt 由竖构图 111px 宽 → 横构图 162px 宽）
        "elements spread out in a wide horizontal composition that deliberately overflows the frame, "
        "cut-out elements floating without any ground, floor or cast shadow, no environment, no frame, no text",
    )
    return FRAME + body + ", " + bg + ", " + tail


# --------------------------------------------------------------------------- #
# 生成（带审核重试梯子）
# --------------------------------------------------------------------------- #
def gen_candidates(design: dict, out_dir: Path, n: int = 4, workers: int = 3, max_tries_per_variant: int = 3, size: str | None = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    model = design.get("model", "Qwen/Qwen-Image")
    neg = design.get("negative") or NEG_BASE
    treatments = design.get("treatments") or DEFAULT_TREATMENTS
    # 默认 4:3：真品 icon 是横构图（175×150），方图会抠出一张竖幅人像
    size = size or design.get("size", "1280x960")

    def one(i: int):
        last = None
        reason = "moderation"
        for attempt in range(max_tries_per_variant * 2):
            # 每次重试换一种美术方向（措辞/光照），不换同义词
            tmpl = None if design.get("prompt_override") else treatments[(i + attempt) % len(treatments)]
            p = design.get("prompt_override") or build_prompt(design, tmpl)
            try:
                paths = sf.gen_image(
                    p,
                    model=model,
                    size=size,
                    negative_prompt=neg,
                    seed=design.get("seed") if i == 0 else None,
                    out_dir=out_dir,
                    tag="cand%02d" % i,
                )
                if design.get("validate", True):
                    # 抠图穿帮检测：背景没抠掉（残留整块方形底）就重来，别把废图交给后处理
                    probe = ic.keep_largest_object(
                        ic.auto_cutout_full(ic.crop_watermark(Image.open(paths[0]).convert("RGB"))), 0.02
                    )
                    if ic.looks_like_full_frame(probe):
                        print("  [retry] cand%02d: 抠图后仍是整块画面，重新生成" % i, flush=True)
                        reason = "cutout_bleed"
                        continue
                    # 键色污染（模型给了"白底 + 洋红卡片"）：形状指标看不出来，颜色侧才抓得到
                    contam = ic.key_color_contamination(probe)
                    if contam > float(design.get("max_key_contamination", 0.05)):
                        print("  [retry] cand%02d: 键色污染 %.3f，重新生成" % (i, contam), flush=True)
                        reason = "key_contamination"
                        continue
                    contamination = round(contam, 4)
                else:
                    contamination = None
                return {"variant": i, "ok": True, "path": paths[0], "tries": attempt + 1, "prompt": p,
                        "treatment": tmpl, "key_contamination": contamination}
            except sf.SFError as e:
                last = e
                if not e.is_moderation:
                    return {"variant": i, "ok": False, "error": str(e), "tries": attempt + 1}
                # 被审核拒：换下一种"美术方向"再试。**不做同义替换去绕审核**；
                # 若几个方向都被拒，说明题材本身不合适 → 回去改 design，而不是改措辞绕过。
                reason = "moderation"
        return {"variant": i, "ok": False, "error": reason, "tries": attempt + 1, "detail": str(last)[:120]}

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, i) for i in range(n)]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(("  [ok]   " if r["ok"] else "  [fail] ") + "cand%02d (try %d) %s" % (r["variant"], r["tries"], r.get("path", r.get("error"))), flush=True)
    results.sort(key=lambda r: r["variant"])
    return results


# --------------------------------------------------------------------------- #
# 后处理
# --------------------------------------------------------------------------- #
def finish(path: str, design: dict, out_path: str):
    img = Image.open(path)
    img = ic.crop_watermark(img.convert("RGB"))
    mode = design.get("cutout", "auto")
    rgba = ic.auto_cutout_full(img, mode=mode)
    if design.get("close_radius") is not None:
        # 手写半径时按老路子走（可复现历史结果）
        rgba = ic.solidify(
            rgba,
            close_radius=int(design["close_radius"]),
            fill_hole_ratio=float(design.get("fill_hole_ratio", 0.012)),
            min_island_ratio=float(design.get("min_island_ratio", 0.002)),
        )
    else:
        # 默认：按目标实心度收紧（真品 tight 中位 0.68，这里保守取 0.62）。
        # 单张 AI 直出只有 0.5-0.6，不收紧就会比原版"散"。
        rgba = ic.densify_silhouette(rgba, target=float(design.get("tight_target", 0.62)))
    rgba = ic.keep_largest_object(rgba, 0.015)
    t = ic.trim(rgba, pad=int(design.get("pad", 4)))
    t = ic.grade(t, saturation=float(design.get("saturation", 1.12)), contrast=float(design.get("contrast", 1.08)))
    cast = design.get("cast")
    if cast:
        col = cast.get("color", "#8B1A1A")
        if isinstance(col, str):
            col = col.lstrip("#")
            col = tuple(int(col[i:i + 2], 16) for i in (0, 2, 4))
        t = ic.unify_cast(t, col, strength=float(cast.get("strength", 0.2)), mode=cast.get("mode", "overlay"))
    slogan = design.get("slogan") or {}
    if isinstance(slogan, str):
        slogan = {"text": slogan}
    height = int(design.get("height", 150))
    # 标语是叠在缩放之后的，会把画布撑高（实测 150 → 167）。
    # 所以先给它留出预算：内容按 height - budget 缩，标语按 budget 排。
    size_ratio = float(slogan.get("size_ratio", 0.17))
    budget = int(round(height * size_ratio * 1.5)) if slogan.get("text") else 0
    content_h = max(40, height - budget)
    if design.get("canvas") == "fixed":
        fin = ic.to_focus_size(
            t,
            height=content_h,
            max_width=int(design.get("max_width", 200)),
            margin=int(design.get("margin", 3)),
        )
    else:
        fin = ic.hug_canvas(
            t,
            height=content_h,
            max_width=int(design.get("max_width", 208)),
            margin=int(design.get("margin", 2)),
        )
    # 描边/发光必须在**最终尺寸**上做：原版那圈深描边只有 ~3px（150px 画布下）
    fin = ic.add_outline_glow(
        fin,
        outline_px=float(design.get("outline_px", 2.2)),
        outline_soft=float(design.get("outline_soft", 0.35)),
        glow_px=float(design.get("glow_px", 3.5)),
        glow_alpha=int(design.get("glow_alpha", 120)),
    )
    # add_outline_glow 会在四周留出画布余量，贴回去，避免白白多出一圈透明边
    fin = ic.trim(fin, pad=int(design.get("edge_pad", 1)))
    if slogan.get("text"):
        fin = ic.add_slogan(
            fin,
            slogan["text"],
            font_path=slogan.get("font_path", "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"),
            size_ratio=size_ratio,
            color=tuple(slogan.get("color", (255, 255, 255, 255))),
            stroke=tuple(slogan.get("stroke", (12, 10, 10, 255))),
            position=slogan.get("position", "bottom"),
            angle=float(slogan.get("angle", 0.0)),
        )
    if design.get("soften", True):
        # 真实 TFR 有 ~16% 的半透明边，我们的硬边只有 ~5% —— 最后柔化一次
        fin = ic.soften_alpha(fin, blur_px=float(design.get("soften_blur", 1.2)), expand_px=float(design.get("soften_expand", 0.6)))
    # 最后收口：请求多少高度就导出多少高度（只缩不放），宽度不超过 max_width
    fin = ic.fit_to_target(
        fin,
        height=height,
        max_width=int(design.get("max_width", 208)),
        pad_to_height=bool(design.get("pad_to_height", True)),
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fin.save(out_path)
    return fin


def contact_sheet(finals, out_path: str, zoom: int = 3, labels=None):
    if not finals:
        return None
    cell_w = max(f.width for f in finals) * zoom
    cell_h = max(f.height for f in finals) * zoom
    pad = 10
    cols = min(4, len(finals))
    rows = (len(finals) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cell_w + pad) + pad, rows * (cell_h + pad + 14) + pad), (28, 28, 32))
    d = ImageDraw.Draw(sheet)
    for i, f in enumerate(finals):
        z = f.resize((f.width * zoom, f.height * zoom), Image.LANCZOS)
        x = pad + (i % cols) * (cell_w + pad)
        y = pad + (i // cols) * (cell_h + pad + 14)
        board = Image.new("RGB", (cell_w, cell_h), (255, 0, 255))
        px = board.load()
        for yy in range(0, cell_h, 8):
            for xx in range(0, cell_w, 8):
                if ((xx // 8) + (yy // 8)) % 2 == 0:
                    for a in range(xx, min(xx + 8, cell_w)):
                        for b in range(yy, min(yy + 8, cell_h)):
                            px[a, b] = (44, 44, 50)
        board.paste(z, ((cell_w - z.width) // 2, (cell_h - z.height) // 2), z)
        sheet.paste(board, (x, y))
        d.text((x + 2, y + cell_h + 2), (labels[i] if labels else str(i)), fill=(230, 230, 230))
    sheet.save(out_path)
    return out_path


EXAMPLE = {
    "tag": "PRC",
    "focus_name": "One Korea",
    "slug": "PRC_one_korea",
    "subject": "an elderly man in an olive-green ceremonial uniform with rows of medals, standing with arms crossed",
    "props": ["a huge waving red flag behind him", "a Type-99 main battle tank silhouette on the left"],
    "palette": "red and gold",
    "slogan": {"text": "統一", "position": "bottom"},
    "variants": 4,
    "height": 150,
    "size": "1280x960",
    "background": "white",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", help="design JSON 路径")
    ap.add_argument("--out", default="out", help="输出目录")
    ap.add_argument("--variants", type=int, default=None)
    ap.add_argument("--size", default=None, help="生成尺寸，默认 1280x960（横构图，别用方图）")
    ap.add_argument("--no-gen", action="store_true", help="不生成，只处理 out/*.png")
    ap.add_argument("--example", action="store_true", help="打印 design 模板")
    ap.add_argument("--prompt-only", action="store_true", help="只打印 prompt")
    args = ap.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return
    if not args.design:
        ap.error("需要 --design job.json（或用 --example 看模板）")
    design = json.loads(Path(args.design).read_text())
    if args.prompt_only:
        print(build_prompt(design))
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = args.variants or int(design.get("variants", 4))

    if args.no_gen:
        raws = sorted(out.glob("*.png"))
        raws = [p for p in raws if not p.name.startswith(("final_", "contact"))]
    else:
        print("生成 %d 个候选（模型=%s）…" % (n, design.get("model", "Qwen/Qwen-Image")))
        res = gen_candidates(design, out, n=n, size=args.size)
        raws = [Path(r["path"]) for r in res if r.get("ok")]
        if not raws:
            print("全部候选都被内容审核拦下，请修改 subject/props 后再试", file=sys.stderr)
            return 2

    finals = []
    for p in raws:
        fp = out / ("final_" + p.stem + ".png")
        finals.append(finish(str(p), design, str(fp)))
        print("  ->", fp)
    sheet = contact_sheet(finals, str(out / "contact.png"), zoom=3, labels=[p.stem for p in raws])
    print("对比图:", sheet)


if __name__ == "__main__":
    raise SystemExit(main())
