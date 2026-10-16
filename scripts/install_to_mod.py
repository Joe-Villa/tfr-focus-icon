#!/usr/bin/env python3
"""把一个已生成的 icon 接进 HOI4 mod：拷 PNG + 生成 gfx 片段 + 打印后续改动。

用法：
    python3 install_to_mod.py final.png --mod "/path/to/mod" --tag PRC \
        --slug PRC_one_korea --loc "统一朝鲜" --dry-run

做的事：
  1. 校验 PNG（RGBA、透明像素占比、尺寸）——不合格就警告（除非 --force）。
  2. 拷到 <mod>/gfx/interface/goals/<TAG>/<slug>.png
  3. 在 <mod>/interface/<gfx 文件> 里追加/更新 SpriteType 片段（默认写进
     zz_tfr_custom_icons.gfx，不动 mod 原有文件）。
  4. 打印还需要人工加的两行：national_focus 里的 icon = GFX_focus_<slug>，
     以及本地化 yml 的键值。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def check_png(p: Path, expect_h: int = 150, tolerance: int = 12) -> list[str]:
    warn = []
    with Image.open(p) as im:
        if im.mode != "RGBA":
            warn.append(f"不是 RGBA（当前 {im.mode}），HOI4 需要带 alpha 通道的 PNG")
            return warn
        a = np.asarray(im)[..., 3]
        if (a > 0).mean() > 0.995:
            warn.append("几乎没有透明像素——是不是忘了抠图？")
        if (a > 0).mean() < 0.25:
            warn.append("不透明像素过少（<25%），图形可能太碎")
        if abs(im.height - expect_h) > tolerance:
            warn.append(f"高度 {im.height} 偏离 TFR 常见值 {expect_h}±{tolerance}")
        if im.width < 100 or im.width > 260:
            warn.append(f"宽度 {im.width} 不在 TFR 常见区间 140-208")
    return warn


def upsert_sprite(gfx_path: Path, sprite_name: str, texture: str) -> str:
    """在 gfx 文件里写入/更新 SpriteType。返回动作描述。"""
    block = f'\tSpriteType = {{\n\t\tname = "{sprite_name}"\n\t\ttexturefile = "{texture}"\n\t}}\n'
    gfx_path.parent.mkdir(parents=True, exist_ok=True)
    if not gfx_path.exists():
        gfx_path.write_text("spriteTypes = {\n" + block + "}\n", encoding="utf-8")
        return f"新建 {gfx_path} 并写入 {sprite_name}"
    text = gfx_path.read_text(encoding="utf-8")
    if re.search(r'name\s*=\s*"%s"' % re.escape(sprite_name), text):
        return f"{sprite_name} 已存在，跳过（如需改贴图请手动编辑）"
    idx = text.rstrip().rfind("}")
    new = text[:idx] + block + text[idx:]
    gfx_path.write_text(new, encoding="utf-8")
    return f"在 {gfx_path.name} 追加 {sprite_name}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--mod", required=True, help="mod 根目录（含 gfx/ interface/ common/）")
    ap.add_argument("--tag", required=True, help="国家 TAG，如 PRC")
    ap.add_argument("--slug", required=True, help="文件名/精灵名后缀，如 PRC_one_korea")
    ap.add_argument("--gfx-file", default="zz_tfr_custom_icons.gfx", help="interface/ 下的 gfx 文件名")
    ap.add_argument("--loc", default=None, help="国策显示名（打印本地化片段用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略校验警告")
    args = ap.parse_args()

    src = Path(args.png)
    mod = Path(args.mod).expanduser()
    if not src.exists():
        print("找不到文件:", src, file=sys.stderr)
        return 2
    if not mod.exists():
        print("找不到 mod 目录:", mod, file=sys.stderr)
        return 2

    warns = check_png(src)
    for w in warns:
        print("  [warn]", w)
    if warns and not args.force:
        print("有警告，未写入。确认没问题就加 --force。")
        return 1

    dst_dir = mod / "gfx" / "interface" / "goals" / args.tag
    dst = dst_dir / f"{args.slug}.png"
    rel_texture = f"gfx/interface/goals/{args.tag}/{args.slug}.png"
    sprite = f"GFX_focus_{args.slug}"

    if args.dry_run:
        print(f"[dry-run] 会拷贝 {src} -> {dst}")
        print(f"[dry-run] 会写入 {mod/'interface'/args.gfx_file}: {sprite} -> {rel_texture}")
    else:
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("已拷贝:", dst)
        msg = upsert_sprite(mod / "interface" / args.gfx_file, sprite, rel_texture)
        print("gfx:", msg)

    print("\n还需要你在 mod 里加两处：")
    print(f"  1) common/national_focus/*.txt 对应国策里：icon = {sprite}")
    if args.loc:
        print(f"  2) localisation/english/*.yml: {args.slug if args.slug.startswith('focus_') else 'focus_' + args.slug}:0 \"{args.loc}\"")
        print(f"     localisation/simp_chinese/*.yml: {('focus_' + args.slug)}:0 \"{args.loc}\"")
    else:
        print("  2) localisation/*/*.yml 里补这个国策的键值（可传 --loc 让本脚本打印片段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
