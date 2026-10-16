#!/usr/bin/env python3
"""自评：把成品 icon 和真品 TFR icon 一起丢给视觉模型打分，并给出可执行的修改意见。

眼睛（view_image）永远是第一位的；这个脚本的作用是**在没有人类反馈时给自己一个对照**，
以及把"哪里不像"说成人话。

用法：
    python3 review.py out/final_01.png out/final_02.png
    python3 review.py out/*.png --tag PRC --nref 4 --json review.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402
import sf  # noqa: E402

RUBRIC = """你在评估"游戏《钢铁雄心4》mod《The Fire Rises》的国策图标"生成质量。
前面 {nref} 张是**该 mod 的原版真品**（基准），后面 {nour} 张是**待评估的生成图**。

对每张生成图，按 0-5 分打分并给一句话理由：
1. 风格相似度：是否像"写实照片剪贴（photobash）"，而不是插画/矢量/平面海报。
2. 剪影形态：轮廓是否是紧凑实心的团块（不是散开的贴纸堆，也不是规整的矩形照片）。
3. 色彩与对比：是否高饱和高对比、有强烈的红/金/黑倾向。
4. 元素关系：主体是否明确、元素之间是否有叠压层次（而不是并排摆放）。
5. 缩到 150px 后的可读性：主体是否一眼可辨、文字是否清晰。
6. 有没有明显穿帮（残留背景、方形投影、水印、糊成一团的地方）。

只输出 JSON：
{{"items":[{{"file":"<文件名>","style":N,"silhouette":N,"color":N,"layers":N,"readability":N,
"defects":["…"],"verdict":"一句话","fixes":["最多 3 条可执行的修改建议"]}}],
"overall":"总体结论，指出生成图和真品最本质的差距"}}
不要输出 JSON 以外的任何内容。"""


def sample_refs(mod: Path, tag: str | None, n: int, seed: int = 0) -> list[Path]:
    base = Path(mod)
    if tag:
        files = sorted((base / tag).glob("*.png"))
    else:
        files = sorted(base.rglob("*.png"))
    if not files:
        return []
    random.seed(seed)
    return random.sample(files, min(n, len(files)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tag", default=None, help="参考真品取哪个国家目录（默认全库随机）")
    ap.add_argument("--nref", type=int, default=4)
    ap.add_argument("--mod", default=None,
                    help="真品目录 <mod>/gfx/interface/goals（也可用环境变量 HOI4_MOD_GOALS_DIR）")
    ap.add_argument("--model", default="Qwen/Qwen3-VL-32B-Instruct")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    ours = [Path(f) for f in args.files]
    mod = M.resolve_mod(args.mod)
    refs = sample_refs(mod, args.tag, args.nref)
    if not refs:
        print(M.mod_hint(mod), file=sys.stderr)
        return 2

    print("参考真品:")
    for r in refs:
        print("  ", r)
    prompt = RUBRIC.format(nref=len(refs), nour=len(ours))
    text = sf.vlm([str(p) for p in refs] + [str(p) for p in ours], prompt, model=args.model, max_tokens=2000)

    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[raw.find("{"):]
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:
        print("视觉模型没给出合法 JSON，原始输出：\n", text)

    print("\n客观指标（对比真品分布）:")
    for f in ours:
        m = M.measure(f)
        print(f"  {f.name}: cov={m['cov']} soft={m['soft']} tight={m['tight']} comp={m['comp']} edge={m['edge']} ({m['w']}x{m['h']})")

    if parsed:
        print("\n评分:")
        for it in parsed.get("items", []):
            print(f"  {it.get('file')}: 风格{it.get('style')} 剪影{it.get('silhouette')} 色彩{it.get('color')} "
                  f"层次{it.get('layers')} 可读{it.get('readability')} | {it.get('verdict','')}")
            for d in it.get("defects", []):
                print("      缺陷:", d)
            for fx in it.get("fixes", []):
                print("      建议:", fx)
        print("\n总体:", parsed.get("overall", ""))
    if args.json:
        Path(args.json).write_text(json.dumps({"raw": text, "parsed": parsed}, ensure_ascii=False, indent=2))
        print("\n结果已存:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
