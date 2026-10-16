"""TFR 风格"照片拼贴"(photobash) 合成器。

真实 TFR 国策 icon 的做法不是"方形底图 + 边框"，而是把 2~5 个已抠好的人物/军械/
地标/旗帜/扁平 logo 斜向叠压成一个不规则剪影，每个元素带一层深色投影把彼此分开，
最后整体再补一圈深色描边 + 外发光。本模块只做"排布 + 投影"这一层，
描边/发光/调色/缩放交给 iconify.py。

只依赖 PIL + numpy。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


@dataclass
class Element:
    """一个待贴上去的抠图元素。

    box: 归一化包围盒 (x0, y0, x1, y1)，取值 0~1，相对最终画布。
         元素等比缩放到恰好放进这个盒子（contain），再居中。
    z:   层级，小的先画（在后面）。
    """

    image: Image.Image
    box: tuple[float, float, float, float]
    z: float = 0.0
    flip: bool = False
    shadow: bool = True
    shadow_dy: float = 0.012
    shadow_blur: float = 0.010
    shadow_alpha: int = 150
    name: str = ""

    def prepared(self) -> Image.Image:
        return self.image.transpose(Image.FLIP_LEFT_RIGHT) if self.flip else self.image


def _trim(rgba: Image.Image) -> Image.Image:
    bbox = rgba.split()[-1].getbbox()
    return rgba.crop(bbox) if bbox else rgba


def load_element(path: str | Path, box, **kw) -> Element:
    return Element(Image.open(path).convert("RGBA"), box, **kw)


def contain_fit(img: Image.Image, box_px: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int]]:
    """等比缩放 img 使其放进 box_px，返回 (缩放后图, 左上角坐标)。"""
    x0, y0, x1, y1 = box_px
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    s = min(bw / img.width, bh / img.height)
    nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
    return img.resize((nw, nh), Image.LANCZOS), (x0 + (bw - nw) // 2, y0 + (bh - nh) // 2)


def _shadow_layer(alpha: Image.Image, size: tuple[int, int], offset: tuple[int, int],
                  blur: float, alpha_v: int, color=(0, 0, 0)) -> Image.Image:
    sh = Image.new("RGBA", size, (0, 0, 0, 0))
    body = Image.new("RGBA", size, color + (0,))
    body.paste(alpha, offset)
    if blur > 0:
        body = body.filter(ImageFilter.GaussianBlur(blur))
    body.putalpha(body.split()[-1].point(lambda v: int(v * alpha_v / 255)))
    sh.alpha_composite(body)
    return sh


def compose(canvas_size: tuple[int, int], elements: list[Element],
            background: Image.Image | None = None) -> Image.Image:
    """把 elements 按 z 序贴到画布上。background 可选（铺满的底图，例如放射海报底）。"""
    W, H = canvas_size
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if background is not None:
        out.alpha_composite(background.convert("RGBA").resize((W, H), Image.LANCZOS))

    for el in sorted(elements, key=lambda e: e.z):
        img = _trim(el.prepared())
        if img.width < 2 or img.height < 2:
            continue
        x0, y0, x1, y1 = el.box
        rs, pos = contain_fit(img, (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
        if el.shadow:
            sh = _shadow_layer(rs.split()[-1], (W, H),
                               (pos[0], pos[1] + int(round(el.shadow_dy * H))),
                               max(0.5, el.shadow_blur * H), el.shadow_alpha)
            out.alpha_composite(sh)
        out.alpha_composite(rs, pos)
    return out


# 经典 TFR 货架：hero 右偏撑满高度；support 铺左上；accent 塞角落。
SLOTS: list[tuple[float, float, float, float]] = [
    (0.30, 0.06, 1.00, 1.00),
    (0.00, 0.02, 0.62, 0.72),
    (0.00, 0.52, 0.50, 1.00),
    (0.58, 0.00, 0.96, 0.34),
    (0.60, 0.66, 1.00, 1.00),
]


def auto_layout(elements: list[Image.Image], names: list[str] | None = None,
                aspect: float = 1.18, jitter: float = 0.0,
                seed: int = 0) -> tuple[tuple[int, int], list[Element]]:
    """按"英雄元素主导 + 其余错落"的规则生成盒子。"""
    rng = np.random.default_rng(seed)
    H = 1000
    W = int(round(H * aspect))
    n = len(elements)
    if n == 0:
        return (W, H), []

    areas = [im.width * im.height for im in elements]
    hero_i = int(np.argsort(areas)[::-1][0])
    nm = names or [f"el{i}" for i in range(n)]

    els = [Element(elements[hero_i], SLOTS[0], z=10, name=nm[hero_i])]
    rest = sorted([i for i in range(n) if i != hero_i], key=lambda i: areas[i], reverse=True)
    for k, i in enumerate(rest):
        slot = SLOTS[min(1 + k, len(SLOTS) - 1)]
        jx = float(rng.uniform(-jitter, jitter)) if jitter else 0.0
        jy = float(rng.uniform(-jitter, jitter)) if jitter else 0.0
        box = (max(0.0, slot[0] + jx), max(0.0, slot[1] + jy),
               min(1.0, slot[2] + jx), min(1.0, slot[3] + jy))
        els.append(Element(elements[i], box, z=1 + k, shadow_dy=0.010,
                           shadow_blur=0.008, name=nm[i]))
    return (W, H), els


def diagonal_stack(elements: list[Image.Image], aspect: float = 1.18,
                   overlap: float = 0.34) -> tuple[tuple[int, int], list[Element]]:
    """主元素在右下、次元素依次往左上错开（TFR 最常见的构图骨架）。"""
    H = 1000
    W = int(round(H * aspect))
    n = len(elements)
    if n == 0:
        return (W, H), []
    els: list[Element] = []
    for i, im in enumerate(elements):
        t = i / max(1, n - 1) if n > 1 else 0.0
        x0, y0 = overlap * t, overlap * t
        size = 0.88 - 0.26 * t
        box = (x0, y0, min(1.0, x0 + size), min(1.0, y0 + size))
        els.append(Element(im, box, z=n - i, name=f"el{i}"))
    return (W, H), els
