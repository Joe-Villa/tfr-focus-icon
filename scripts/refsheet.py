#!/usr/bin/env python3
"""从你自己那份 mod 的真品 icon 里现拼一张对照图（contact sheet），用来定风格基准。

本仓库**不含**参考图（真品属于他人作品，不随包分发），所以基准要指向你自己那份安装目录：

    python3 scripts/refsheet.py --mod "<mod>/gfx/interface/goals" --out refs/_contact_sheet.png
    python3 scripts/refsheet.py --mod "<mod>/gfx/interface/goals" --tag PRC --n 16 --scale 2

纯本地、不联网、不花 API 钱。默认从全库随机取 16 张（固定种子，可复现），每张按高度 150 缩放
（`--scale` 放大便于看清），拼在深灰底上——这样一眼能看出哪些图是真的透明边、哪些带白底/黑底残留。
也可以用环境变量 `HOI4_MOD_GOALS_DIR` 代替 `--mod`。
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402

BG = (58, 58, 58, 255)      # 深灰底：能看清透明边，也不刺眼
LIGHT = (128, 128, 128, 255)  # 每张图背后的半灰底（真品画布就是这种灰，方便对比剪影）
GAP = 10
MAX_W = 208


def collect(mod: Path, tag: str | None, n: int, seed: int) -> list[Path]:
    base = Path(mod)
    files = sorted((base / tag).glob("*.png")) if tag else sorted(base.rglob("*.png"))
    files = [f for f in files if not f.name.startswith(".")]
    if not files:
        return []
    random.seed(seed)
    return sorted(random.sample(files, min(n, len(files))))


def main() -> int:
    ap = argparse.ArgumentParser(description="把真品 icon 拼成一张对照图")
    ap.add_argument("--mod", default=None, help="真品目录（<mod>/gfx/interface/goals），也可用 HOI4_MOD_GOALS_DIR")
    ap.add_argument("--tag", default=None, help="只取某个国家目录（例如 PRC），默认全库随机")
    ap.add_argument("--out", default="refs/_contact_sheet.png")
    ap.add_argument("--n", type=int, default=16, help="取几张（默认 16）")
    ap.add_argument("--cols", type=int, default=None, help="列数，默认按张数自动（4 或 5 列）")
    ap.add_argument("--scale", type=float, default=2.0, help="放大倍数，1 = 真品原始尺寸（高 150）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    mod = M.resolve_mod(args.mod)
    files = collect(mod, args.tag, args.n, args.seed)
    if not files:
        print(M.mod_hint(mod), file=sys.stderr)
        return 2

    tiles = []
    for f in files:
        with Image.open(f) as im:
            im = im.convert("RGBA")
            s = args.scale
            w, h = max(1, round(im.width * s)), max(1, round(im.height * s))
            cap = max(1, round(MAX_W * s))  # 真品宽度上限 208
            if w > cap:
                h = max(1, round(h * cap / w))
                w = cap
            tiles.append(im.resize((w, h), Image.LANCZOS))

    cols = args.cols or (4 if len(tiles) > 8 else max(1, math.ceil(math.sqrt(len(tiles)))))
    rows = math.ceil(len(tiles) / cols)
    cell_w = max(t.width for t in tiles)
    cell_h = max(t.height for t in tiles)
    W = GAP + cols * (cell_w + GAP)
    H = GAP + rows * (cell_h + GAP)
    sheet = Image.new("RGBA", (W, H), BG)

    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        x = GAP + c * (cell_w + GAP) + (cell_w - t.width) // 2
        y = GAP + r * (cell_h + GAP) + (cell_h - t.height) // 2
        sheet.alpha_composite(Image.new("RGBA", t.size, LIGHT), (x, y))  # 灰底衬出剪影与透明边
        sheet.alpha_composite(t, (x, y))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGBA").save(out)
    print(f"对照图 {out}  {sheet.width}x{sheet.height}  取自 {mod}"
          f"{f' / {args.tag}' if args.tag else ''}  {len(tiles)} 张")
    print("下一步：view_image 看这张图，定好基准再写 prompt。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
