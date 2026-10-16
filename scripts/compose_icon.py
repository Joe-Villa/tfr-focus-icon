#!/usr/bin/env python3
"""元素拼贴模式：每个元素单独生成 → 各自抠图 → 按布局叠压 → 整体描边/发光/调色 → 缩到国策尺寸。

比单张直出（make_icon.py）更接近 TFR 的"分层贴片"质感，代价是 API 调用更多
（每个元素一次生成 + 重试）。元素多、构图要精确时用它。

design JSON（元素模式）示例见 --example。
    python3 compose_icon.py --design job_elements.json --out out/ --variants 2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compose as C  # noqa: E402
import iconify as ic  # noqa: E402
import make_icon as M  # noqa: E402
import sf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

BG = "placed on a flat uniform backdrop of pure magenta color, nothing else in the frame"
TAIL = "cut-out element floating with no ground, floor or cast shadow, clean sharp edges, no text, no frame"


def element_prompt(desc: str) -> str:
    return M.FRAME + "photograph of " + desc + ", " + BG + ", " + TAIL


def gen_element(desc: str, out_dir: Path, tag: str, tries: int = 6, model: str = "Qwen/Qwen-Image", size: str = "1024x1024"):
    """单个元素：带重试的生成 + 抠图。返回 RGBA 或 None。

    重试是对付随机审核拒绝（code 20021）用的，**不是**用来规避审核：
    同一段描述被反复拒绝，说明这个元素不合适，改 design。
    """
    for attempt in range(tries):
        p = element_prompt(desc)
        try:
            paths = sf.gen_image(p, model=model, size=size, negative_prompt=M.NEG_BASE, out_dir=out_dir, tag=tag)
            img = ic.crop_watermark(Image.open(paths[0]).convert("RGB"))
            rgba = ic.auto_cutout_full(img)
            # RMBG 会保留"被主体包围"的键色区域（提手孔、镂空里的脏粉）→ 按颜色清掉，
            # 否则那块粉色会一路进成品（qa_gate 判 FAIL / 肉眼也看得见）。
            rgba = ic.despill_key_color(rgba)
            rgba = ic.solidify(rgba, close_radius=3, fill_hole_ratio=0.01)
            rgba = ic.keep_largest_object(rgba, 0.02)
            if ic.looks_like_full_frame(rgba):
                # 背景没抠干净（残留整块方形底）→ 这张不能用，重生成
                print(f"  [retry] {tag}: 抠图后仍是整块画面（背景没抠掉），重新生成")
                continue
            return ic.trim(rgba, pad=2)
        except sf.SFError as e:
            if not e.is_moderation:
                print(f"  [err] {tag}: {e}")
                return None
    print(f"  [fail] {tag} 反复被审核拒绝（{tries} 次）：这个元素不合适，换元素或改画面")
    return None


def build_variant(design: dict, elements: list[Image.Image], names: list[str], seed: int, out_dir: Path):
    layout = design.get("layout", "hero_right")
    aspect = float(design.get("aspect", 1.18))
    if layout == "auto":
        size, els = C.auto_layout(elements, names=names, aspect=aspect, jitter=0.05, seed=seed)
    elif layout == "hero_right":
        size, els = C.auto_layout(elements, names=names, aspect=aspect, jitter=0.0, seed=seed)
    else:
        size, els = C.diagonal_stack(elements, aspect=aspect, overlap=float(design.get("overlap", 0.3)))
    canvas = C.compose(size, els)
    t = ic.grade(canvas, saturation=float(design.get("saturation", 1.18)), contrast=float(design.get("contrast", 1.12)))
    fin = ic.hug_canvas(t, height=int(design.get("height", 150)), max_width=int(design.get("max_width", 208)))
    # 描边/发光在最终尺寸上做（原版的深描边只有 ~3px）
    fin = ic.add_outline_glow(fin, outline_px=float(design.get("outline_px", 2.2)),
                              outline_soft=float(design.get("outline_soft", 0.35)),
                              glow_px=float(design.get("glow_px", 3.5)), glow_alpha=int(design.get("glow_alpha", 120)))
    fin = ic.trim(fin, pad=1)
    if design.get("soften", True):
        fin = ic.soften_alpha(fin, blur_px=float(design.get("soften_blur", 1.2)),
                              expand_px=float(design.get("soften_expand", 0.6)))
    slogan = design.get("slogan")
    if isinstance(slogan, str):
        slogan = {"text": slogan}
    if slogan and slogan.get("text"):
        fin = ic.add_slogan(fin, slogan["text"],
                            size_ratio=float(slogan.get("size_ratio", 0.3)),
                            color=tuple(slogan.get("color", (255, 255, 255, 255))),
                            stroke=tuple(slogan.get("stroke", (12, 10, 10, 255))),
                            position=slogan.get("position", "bottom"))
    return fin


EXAMPLE = {
    "tag": "PRC",
    "slug": "PRC_steel_five_years",
    "layout": "hero_right",
    "palette": "crimson red and steel grey",
    "elements": [
        "a Chinese steelworker in overalls and a hard hat gripping a hammer, chest-up, harsh side light",
        "a giant blast furnace erupting with sparks and molten steel",
        "a red hanging banner with a golden gear-and-wheat emblem"
    ],
    "slogan": {"text": "钢铁五年", "position": "bottom", "size_ratio": 0.3},
    "variants": 2,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--design")
    ap.add_argument("--out", default="out_elements")
    ap.add_argument("--variants", type=int, default=None)
    ap.add_argument("--example", action="store_true")
    ap.add_argument("--no-gen", action="store_true", help="复用 out/elem_*.png，只重新排版")
    args = ap.parse_args()
    if args.example:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    if not args.design:
        ap.error("需要 --design job.json（--example 看模板）")

    design = json.loads(Path(args.design).read_text())
    out = Path(args.out)
    elem_dir = out / "elements"
    elem_dir.mkdir(parents=True, exist_ok=True)
    model = design.get("model", "Qwen/Qwen-Image")

    elements, names = [], []
    for i, desc in enumerate(design["elements"]):
        name = f"elem{i:02d}"
        cached = elem_dir / f"{name}_cut.png"
        if args.no_gen and cached.exists():
            rgba = Image.open(cached).convert("RGBA")
        else:
            rgba = gen_element(desc, elem_dir, name, model=model)
            if rgba is None:
                continue
            rgba.save(cached)
        elements.append(rgba)
        names.append(name)
        print(f"  [elem] {name} <- {desc[:50]}")

    if not elements:
        print("没有任何元素生成成功", file=sys.stderr)
        return 2

    n = args.variants or int(design.get("variants", 2))
    finals = []
    for v in range(n):
        fin = build_variant(design, elements, names, seed=100 + v, out_dir=out)
        p = out / f"final_v{v}.png"
        fin.save(p)
        finals.append(fin)
        print("  ->", p)
    sheet = M.contact_sheet(finals, str(out / "contact.png"), zoom=3, labels=[f"v{i}" for i in range(len(finals))])
    print("对比图:", sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
