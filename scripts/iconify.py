"""后处理：白底抠图 → TFR 风格描边/外发光/调色 → 缩到 HOI4 尺寸 → 可选标语文字。

只依赖 PIL + numpy + scipy（系统自带）。如果装了 onnxruntime 且下载了抠图模型，
可以走 rembg 后端（见 cutout_rembg）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- #
# 抠图
# --------------------------------------------------------------------------- #
def _white_mask(arr: np.ndarray, thresh: int = 244, sat_max: int = 18) -> np.ndarray:
    """返回 True=背景(白/浅色低饱和)。"""
    r, g, b = arr[..., 0].astype(np.int16), arr[..., 1].astype(np.int16), arr[..., 2].astype(np.int16)
    mn = np.minimum(np.minimum(r, g), b)
    mx = np.maximum(np.maximum(r, g), b)
    return (mn >= thresh) & ((mx - mn) <= sat_max)


def key_white(img: Image.Image, thresh: int = 240, sat_max: int = 24, feather: float = 1.2, keep_watermark_crop: bool = True) -> Image.Image:
    """把纯白背景抠成透明。做法：从四边 flood fill 出背景区域 + 边缘一带按亮度做软过渡。

    比"全局阈值"稳，因为主体内部的白色（衣服、白字）不会被误删。
    """
    rgb = img.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    h, w = arr.shape[:2]
    white = _white_mask(arr, thresh, sat_max)

    # 从边界做连通域：只有与画布边缘连通的白才算背景
    from scipy import ndimage

    lab, n = ndimage.label(white)
    border_labels = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    border_labels.discard(0)
    bg = np.isin(lab, list(border_labels))

    alpha = np.where(bg, 0.0, 1.0).astype(np.float32)
    # 软化边缘：对 alpha 做一点高斯，让缩放后不至于锯齿硬边
    if feather > 0:
        a = Image.fromarray((alpha * 255).astype(np.uint8))
        a = a.filter(ImageFilter.GaussianBlur(feather))
        alpha = np.asarray(a).astype(np.float32) / 255.0

    out = np.dstack([np.asarray(rgb), (alpha * 255).astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def cutout_via_rembg(img: Image.Image, model_name: str = "u2net", session=None) -> Image.Image:
    """需要 pip install rembg。失败就抛异常，由调用方回退到 key_white。"""
    from rembg import remove, new_session  # type: ignore

    sess = session or new_session(model_name)
    return remove(img.convert("RGBA"), session=sess)


def _border_pixels(rgb: np.ndarray, band: int = 4) -> np.ndarray:
    parts = [rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3), rgb[:, :band].reshape(-1, 3), rgb[:, -band:].reshape(-1, 3)]
    return np.concatenate(parts, axis=0)


def detect_key_color(rgb: np.ndarray) -> np.ndarray:
    return np.median(_border_pixels(rgb), axis=0)


def _soft_alpha(dist: np.ndarray, t_in: float, t_out: float) -> np.ndarray:
    a = (dist - t_in) / max(1e-6, (t_out - t_in))
    return np.clip(a, 0.0, 1.0)


def _decontaminate(rgb: np.ndarray, alpha: np.ndarray, key: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """边缘像素里混了背景色，按 alpha 反解掉（否则缩到 150px 会有一圈脏边）。"""
    a = np.clip(alpha, 1e-3, 1.0)[..., None]
    fg = (rgb - (1.0 - a) * key) / a
    return np.clip(rgb * (1 - strength) + fg * strength, 0, 255)


def _cut_by_key(img: Image.Image, key, t_in: float, t_out: float, feather: float = 0.8, hard_cut: float | None = None) -> Image.Image:
    from scipy import ndimage

    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    key = np.asarray(key, dtype=np.float32)
    dist = np.sqrt(((rgb - key) ** 2).sum(-1))
    bg_soft = dist < t_out
    bg_hard = dist < (t_in if hard_cut is None else hard_cut)
    lab, n = ndimage.label(bg_soft)
    if n:
        border_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
        border_labels.discard(0)
        bg = np.isin(lab, list(border_labels)) | bg_hard
    else:
        bg = bg_hard
    alpha = np.where(bg, 0.0, _soft_alpha(dist, t_in, t_out))
    alpha[bg_hard] = 0.0
    alpha = ndimage.median_filter(alpha, size=3)
    if feather > 0:
        a_img = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(feather))
        alpha = np.asarray(a_img).astype(np.float32) / 255.0
    rgb2 = _decontaminate(rgb, alpha, key)
    rgba = np.dstack([rgb2.astype(np.uint8), (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def chroma_cutout(img: Image.Image, key_color=None, t_in: float = 70.0, t_out: float = 130.0, feather: float = 0.8) -> Image.Image:
    if key_color is None:
        key_color = detect_key_color(np.asarray(img.convert("RGB")).astype(np.float32))
    return _cut_by_key(img, key_color, t_in, t_out, feather)


def white_cutout(img: Image.Image, t_in: float = 26.0, t_out: float = 62.0, feather: float = 1.0) -> Image.Image:
    return _cut_by_key(img, (255, 255, 255), t_in, t_out, feather)


def auto_cutout_full(img: Image.Image, mode: str = "auto", **kw) -> Image.Image:
    """抠图。默认走 RMBG-1.4（onnxruntime），不可用时退回色键。

    mode:
      auto    —— 有 RMBG 就用 RMBG，否则按背景色自动选色键（默认）
      rmbg    —— 强制 RMBG，不可用就抛异常
      legacy  —— 强制走色键判据（背景纯色 → HSV 色键；白/灰 → 白底）
      white / chroma / rembg —— 强制指定某一种

    为什么要 RMBG：色键在"主体自身含键色"时必坏（白条纹旗、白头海雕、
    白衬衫、白柱体），实测见 references/pitfalls.md。

    但 RMBG 是**单主体显著物体**模型：一张图里"人物 + 旗帜 + 头盔"这种拼贴，
    它只会留下人物，把旗帜和头盔丢掉（实测 cand00: rmbg 0.129 vs 色键 0.295）。
    所以 auto 模式会两条都算，按覆盖率比选：
        rmbg / 色键 >= 0.8  → 认为 RMBG 没漏东西（色键多半还掏了洞）→ 用 RMBG
        否则                → RMBG 丢了元素 → 用色键
    实测这个比值：单元素 1.03-1.37，多元素拼贴 0.44-0.57，分得很开。
    """
    key_cut = None
    key_done = False

    def legacy():
        nonlocal key_cut, key_done
        if not key_done:
            key_cut = _auto_cutout_key(img, **kw)
            key_done = True
        return key_cut

    if mode in ("auto", "rmbg"):
        rmbg_cut = None
        try:
            import rmbg  # 同目录

            if rmbg.available():
                rmbg_cut = rmbg.cutout(img.convert("RGB"))
            elif mode == "rmbg":
                raise RuntimeError("RMBG 不可用：先 pip install onnxruntime 且 "
                                   "python3 scripts/rmbg.py --download")
            else:
                print("  [matting] RMBG 模型不在本地，退回色键（建议先跑 rmbg.py --download）", flush=True)
        except ImportError:
            if mode == "rmbg":
                raise
            print("  [matting] 没装 rmbg.py/onnxruntime，退回色键", flush=True)
        if rmbg_cut is not None:
            if mode == "rmbg":
                return rmbg_cut
            a_r = float((np.asarray(rmbg_cut)[..., 3] > 128).mean())
            a_k = float((np.asarray(legacy())[..., 3] > 128).mean())
            if a_r >= 0.8 * max(a_k, 1e-6):
                return rmbg_cut
            print("  [matting] RMBG 只留下主主体（%.3f vs 色键 %.3f），改用色键保留多元素拼贴"
                  % (a_r, a_k), flush=True)
            return key_cut
    if mode == "legacy":
        return legacy()
    if mode == "white":
        return white_cutout(img, **kw)
    if mode == "chroma":
        return chroma_cutout(img, **kw)
    if mode == "rembg":
        # 用 u2net/BiRefNet 这类抠图模型。需要 rembg（见 SKILL.md 的环境说明）。
        return cutout_via_rembg(img.convert("RGB") if img.mode != "RGBA" else img)
    return _auto_cutout_key(img, **kw)


def _auto_cutout_key(img: Image.Image, **kw) -> Image.Image:
    """老的自动色键判据：背景是高饱和纯色 → HSV 色键；接近白/灰 → 白底抠。"""
    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    key = detect_key_color(rgb)
    mx, mn = float(key.max()), float(key.min())
    sat = (mx - mn) / max(1.0, mx)
    if sat > 0.45 and mx > 90:
        # 模型给的"纯色背景"通常是脏的（偏粉/偏暗），用 RGB 距离会把浅肤色一起吃掉；
        # 改用 HSV 色相 + 饱和度判据。
        return hsv_chroma_cutout(img, key_color=tuple(key.astype(int)), **kw)
    return white_cutout(img, **kw)


def _rgb_to_hsv(arr: np.ndarray):
    """arr: float HxWx3 (0-255) -> (h_deg, s, v)，s/v 归一到 0-1。"""
    r, g, b = arr[..., 0] / 255.0, arr[..., 1] / 255.0, arr[..., 2] / 255.0
    mx = np.max(arr, axis=-1) / 255.0
    mn = np.min(arr, axis=-1) / 255.0
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-6
    # 分通道算色相
    r_mx = nz & (mx == r)
    g_mx = nz & (mx == g) & ~r_mx
    b_mx = nz & (mx == b) & ~r_mx & ~g_mx
    h[r_mx] = ((g - b)[r_mx] / d[r_mx]) % 6
    h[g_mx] = ((b - r)[g_mx] / d[g_mx]) + 2
    h[b_mx] = ((r - g)[b_mx] / d[b_mx]) + 4
    h = h * 60.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h, s, mx


def hsv_chroma_cutout(
    img: Image.Image,
    key_color=None,
    sat_min: float = 0.42,
    hue_tol: float = 30.0,
    val_min: float = 0.16,
    feather: float = 0.8,
) -> Image.Image:
    """HSV 色键：背景 = 「色相接近键色 且 饱和度够高」且与画布边缘连通。

    这样浅肤色（饱和度低）、灰白高光不会被误抠掉 —— 这是 RGB 阈值法最大的坑。
    """
    from scipy import ndimage

    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    if key_color is None:
        key_color = detect_key_color(rgb)
    key = np.asarray(key_color, dtype=np.float32).reshape(1, 1, 3)
    kh, ks, kv = _rgb_to_hsv(key)
    kh, ks, kv = float(kh[0, 0]), float(ks[0, 0]), float(kv[0, 0])

    h, s, v = _rgb_to_hsv(rgb)
    dh = np.abs(((h - kh) + 180.0) % 360.0 - 180.0)  # 环状色相差
    cand_hard = (dh <= hue_tol * 0.5) & (s >= max(0.30, sat_min)) & (v >= val_min)
    cand_soft = (dh <= hue_tol) & (s >= sat_min * 0.6) & (v >= val_min * 0.6)

    lab, n = ndimage.label(cand_soft | cand_hard)
    if n:
        border_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
        border_labels.discard(0)
        bg = np.isin(lab, list(border_labels))
        # 注意：不能用"全局 cand_hard"（否则物体内部恰好是洋红色的部分会被挖成洞，
        # 实测踩过：齿轮/徽记内部出现洋红空洞）。只认与画布边缘连通的背景。
    else:
        bg = cand_hard

    # 软过渡：越像键色（色相接近 + 饱和度高）→ alpha 越低
    soft = np.clip((hue_tol - dh) / max(1e-6, hue_tol), 0, 1) * np.clip(
        (s - sat_min * 0.6) / max(1e-6, sat_min * 0.4), 0, 1
    )
    alpha = np.clip(1.0 - soft, 0.0, 1.0)
    alpha = np.where(bg, 0.0, alpha)
    alpha = ndimage.median_filter(alpha, size=3)
    if feather > 0:
        a_img = Image.fromarray((alpha * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(feather))
        alpha = np.asarray(a_img).astype(np.float32) / 255.0
    rgb2 = _decontaminate(rgb, alpha, key.reshape(3))
    rgba = np.dstack([rgb2.astype(np.uint8), (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def keep_largest_object(rgba: Image.Image, min_ratio: float = 0.04) -> Image.Image:
    """去掉抠图残留的零碎小岛（保留面积 >= min_ratio * 最大面积的连通块）。"""
    from scipy import ndimage

    a = np.asarray(rgba)[..., 3]
    lab, n = ndimage.label(a > 40)
    if n <= 1:
        return rgba
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    biggest = sizes.max()
    keep = {i + 1 for i, s in enumerate(sizes) if s >= min_ratio * biggest}
    mask = np.isin(lab, list(keep))
    out = np.asarray(rgba).copy()
    out[..., 3] = np.where(mask, out[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def _disk(radius: int) -> np.ndarray:
    r = int(radius)
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r + 1e-9


def solidify(
    rgba: Image.Image,
    close_radius: int = 2,
    fill_hole_ratio: float = 0.006,
    min_island_ratio: float = 0.002,
    smooth: float = 0.8,
) -> Image.Image:
    """把抠出来的剪影"收紧成团"——TFR 的 alpha 是紧凑实心的团块，不是零碎贴纸。

    实测：真实 TFR icon 的 tight(包围盒填充率) ≈ 0.68，我们是 0.5；
    comp(周长²/面积) 真品 ≈ 46，我们 130+。这一步就是补这个差距的：
      1) 闭运算桥接元素之间的细缝（同一张图里的旗杆、坦克与人物连成一体）
      2) 填掉小的内部空洞（勋章缝、旗面褶皱造成的噪点洞）
      3) 去掉孤立小岛（抠图残留）
    最后用高斯把边缘重新软化，保留 TFR 那种"外发光"的半透明边。
    """
    from scipy import ndimage

    arr = np.asarray(rgba).copy()
    alpha = arr[..., 3].astype(np.float32) / 255.0
    mask = alpha > 0.45
    if not mask.any():
        return rgba
    total = mask.size

    if close_radius > 0:
        mask = ndimage.binary_closing(mask, structure=_disk(close_radius), border_value=0)

    if fill_hole_ratio > 0:
        filled = ndimage.binary_fill_holes(mask)
        holes = filled & ~mask
        lab, n = ndimage.label(holes)
        if n:
            sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
            keep = [i + 1 for i, s in enumerate(sizes) if s <= fill_hole_ratio * total]
            if keep:
                mask = mask | np.isin(lab, keep)

    if min_island_ratio > 0:
        lab, n = ndimage.label(mask)
        if n > 1:
            sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
            biggest = sizes.max()
            keep = [i + 1 for i, s in enumerate(sizes) if s >= min_island_ratio * biggest]
            mask = np.isin(lab, keep)

    m = Image.fromarray((mask * 255).astype(np.uint8), "L")
    if smooth > 0:
        m = m.filter(ImageFilter.GaussianBlur(smooth))
    new_alpha = np.asarray(m).astype(np.float32) / 255.0
    # 取并集：既不丢掉原有的柔边，也把收紧后的实体补上
    arr[..., 3] = (np.maximum(new_alpha, np.minimum(alpha, new_alpha * 1.35 + 0.05)) * 255).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def tightness(rgba: Image.Image, solid: float = 0.9) -> float:
    """剪影"实心度"：内容包围盒里不透明像素（alpha>0.9）的占比。

    真品 TFR 的中位数是 0.68（400 张实测），单张 AI 直出通常只有 0.5-0.6 ——
    因为照片里元素之间是真实缝隙。这是"看起来散"的量化来源。
    """
    a = np.asarray(rgba)[..., 3].astype(np.float32) / 255.0
    m = a > solid
    if not m.any():
        return 0.0
    ys, xs = np.where(m)
    box = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return float(box.mean())


def densify_silhouette(rgba: Image.Image, target: float = 0.62, max_radius: int = 7) -> Image.Image:
    """按目标实心度收紧剪影：从小半径往上试闭运算，够紧就停。

    每次都从**原始** alpha 重算（不做累加），避免重复闭运算把画面糊成一团。
    真品 tight 中位 0.68，这里默认目标 0.62（保守，别把细节压没）。
    """
    if tightness(rgba) >= target:
        return rgba
    best = rgba
    for r in range(2, max_radius + 1):
        cand = solidify(rgba, close_radius=r, fill_hole_ratio=0.012, min_island_ratio=0.002)
        t = tightness(cand)
        if t > tightness(best):
            best = cand
        if t >= target:
            return cand
    return best


def fit_to_target(rgba: Image.Image, height: int = 150, max_width: int = 208,
                  pad_to_height: bool = True, min_margin: int = 1) -> Image.Image:
    """最终尺寸兜底：只缩不放，保证导出高度 == height、宽度 <= max_width。

    为什么需要它：标语是**叠在缩放之后**的，会把画布往下撑高（实测请求 150、
    导出 167）。这里统一在最后收口，请求多少就是多少。
    """
    img = trim(rgba)
    if img.height <= 0 or img.width <= 0:
        return rgba
    s = 1.0
    if img.height > height - 2 * min_margin:
        s = min(s, (height - 2 * min_margin) / img.height)
    if img.width > max_width - 2 * min_margin:
        s = min(s, (max_width - 2 * min_margin) / img.width)
    if s < 1.0:
        img = img.resize((max(1, int(round(img.width * s))), max(1, int(round(img.height * s)))),
                         Image.LANCZOS)
    if pad_to_height and img.height < height:
        canvas = Image.new("RGBA", (img.width + 2 * min_margin, height), (0, 0, 0, 0))
        canvas.alpha_composite(img, (min_margin, (height - img.height) // 2))
        return canvas
    return img


def crop_watermark(img: Image.Image, frac_w: float = 0.20, frac_h: float = 0.09) -> Image.Image:
    """去掉模型右下角水印：只有当该角落区域"很白/纯净"时才裁——即水印确实在空白背景上。"""
    w, h = img.size
    box = (int(w * (1 - frac_w)), int(h * (1 - frac_h)), w, h)
    corner = img.crop(box).convert("RGB")
    a = np.asarray(corner).astype(np.int16)
    if a.size == 0:
        return img
    # 背景纯度：绝大多数像素接近白
    nonwhite = (~_white_mask(a, 235, 30)).mean()
    if nonwhite < 0.06:
        out = img.copy()
        out.paste((255, 255, 255), box)
        return out
    return img


# --------------------------------------------------------------------------- #
# 风格化
# --------------------------------------------------------------------------- #
def trim(rgba: Image.Image, pad: int = 0) -> Image.Image:
    bbox = rgba.split()[-1].getbbox()
    if not bbox:
        return rgba
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(rgba.width, x1 + pad), min(rgba.height, y1 + pad)
    return rgba.crop((x0, y0, x1, y1))


def bleed_edges(rgba: Image.Image, thresh: float = 0.30) -> int:
    """抠图穿帮检测：返回"不透明内容顶到图像边缘"的边数（0-4）。

    正常情况下主体是漂浮的贴片，最多偶尔碰到 1 条边。
    如果 3-4 条边全被顶满，说明背景没抠掉（残留方形底），必须重生成。
    """
    a = np.asarray(rgba)[..., 3] if rgba.mode == "RGBA" else np.asarray(rgba.convert("RGBA"))[..., 3]
    solid = a > 128
    if solid.size == 0:
        return 4
    edges = 0
    if solid[0].mean() > thresh:
        edges += 1
    if solid[-1].mean() > thresh:
        edges += 1
    if solid[:, 0].mean() > thresh:
        edges += 1
    if solid[:, -1].mean() > thresh:
        edges += 1
    return edges


def looks_like_full_frame(rgba: Image.Image) -> bool:
    """主体几乎铺满整张图 → 多半是背景没抠掉（或模型直接给了整幅画面）。"""
    a = np.asarray(rgba)[..., 3] if rgba.mode == "RGBA" else np.asarray(rgba.convert("RGBA"))[..., 3]
    return bool((a > 128).mean() > 0.9) or bleed_edges(rgba) >= 3


# 硅基流动给"纯洋红背景"的实际颜色：脏粉，不是 (255,0,255)。实测 2026-10-16。
KEY_FAMILY = ((255, 0, 255), (244, 75, 163), (240, 60, 180), (235, 60, 180))


def despill_key_color(rgba: Image.Image, keys=KEY_FAMILY, dist: float = 72.0) -> Image.Image:
    """清掉"抠图后仍不透明的键色残留"（例：木箱提手孔里那块脏粉），并压掉边缘粉色描边。

    为什么需要单独一步：RMBG 对**被主体包围**的键色区域（提手孔、镂空）会判成主体留下；
    形状指标（cov/tight）看不出来，只有按颜色查。阈值 72 是实测值：木箱的木色/绳色离
    键色 ≥ 130，而脏粉残留距离 ≤ 40 —— 分得很开。
    """
    a = np.asarray(rgba.convert("RGBA")).astype(np.float32)
    rgb, al = a[..., :3], a[..., 3] / 255.0
    d = np.stack([np.linalg.norm(rgb - np.asarray(k, dtype=np.float32), axis=2) for k in keys]).min(0)
    al = np.where((d < dist) & (al > 0.05), 0.0, al)
    key = np.asarray(keys[-1], dtype=np.float32)
    rgb2 = _decontaminate(rgb, al, key)
    return Image.fromarray(np.dstack([rgb2, (np.clip(al, 0, 1) * 255)]).astype(np.uint8), "RGBA")


def key_color_contamination(rgba: Image.Image) -> float:
    """键色污染：不透明像素里"洋红/绿"色调的占比。

    抓的是这一种废品：模型没给"纯色平底"，而是给了「白底 + 一张洋红卡片」，
    键色从四边取样取到白色 → 把白底抠掉、整块卡片留下。形状指标（cov/tight）
    反倒会因为卡片而好看，所以必须在颜色侧判。

    实测（install_qa 的独立实验）：废品 0.278 / 0.611，正常成品 ≤ 0.010。
    """
    arr = np.asarray(rgba.convert("RGBA")).astype(np.int16)
    a = arr[..., 3] > 128
    if not a.any():
        return 0.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    # 亮度门槛（mx>=190）的理由见 qa_gate.py 同名判据：只按色相会把画师画的深紫误判成键色残留。
    mx = np.maximum(np.maximum(r, g), b)
    magenta = ((r - g) > 60) & ((b - g) > 60) & (mx >= 190)
    green = ((g - np.maximum(r, b)) > 60) & (mx >= 190)
    return float(((magenta | green) & a).sum() / a.sum())


def add_outline_glow(
    rgba: Image.Image,
    outline_px: float = 2.2,
    outline_color=(18, 14, 12),
    outline_soft: float = 0.6,
    glow_px: float = 3.5,
    glow_alpha: int = 110,
    glow_color=(0, 0, 0),
) -> Image.Image:
    """给抠好的图加：贴边深色描边 + 外侧柔光/阴影。"""
    w, h = rgba.size
    m = int(max(outline_px, glow_px) * 4) + 8
    canvas = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
    canvas.alpha_composite(rgba, (m, m))

    alpha = canvas.split()[-1]

    def grown(dilate_px: float) -> Image.Image:
        if dilate_px <= 0:
            return alpha
        # 用 MaxFilter 做膨胀（半径取整），小数部分用模糊近似
        r = int(np.floor(dilate_px))
        a = alpha
        if r >= 1:
            a = a.filter(ImageFilter.MaxFilter(2 * r + 1))
        frac = dilate_px - r
        if frac > 0.05:
            a2 = a.filter(ImageFilter.GaussianBlur(frac))
            a = ImageChops.lighter(a, a2.point(lambda v: v))
        return a

    # 外发光/阴影：膨胀 + 模糊
    glow_mask = grown(glow_px * 0.6).filter(ImageFilter.GaussianBlur(glow_px))
    glow_layer = Image.new("RGBA", canvas.size, glow_color + (0,))
    glow_layer.putalpha(glow_mask.point(lambda v: int(v * glow_alpha / 255)))

    # 描边：膨胀出来的一圈，颜色暗
    ring = grown(outline_px)
    if outline_soft > 0:
        ring = ring.filter(ImageFilter.GaussianBlur(outline_soft))
    ring_only = ImageChops.subtract(ring, alpha)
    outline_layer = Image.new("RGBA", canvas.size, outline_color + (0,))
    outline_layer.putalpha(ring_only)

    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(glow_layer)
    out.alpha_composite(outline_layer)
    out.alpha_composite(canvas)
    return out


def soften_alpha(rgba: Image.Image, blur_px: float = 0.8, expand_px: float = 0.4) -> Image.Image:
    """把 alpha 边缘整体柔化一点。

    实测：真实 TFR icon 有 ~16% 的像素是半透明（外发光/羽化边），我们只有 ~5%，
    边缘太"剪"了。这里对最终尺寸的 alpha 做一次小半径模糊（可先轻微膨胀补偿变淡）。
    """
    arr = np.asarray(rgba).copy()
    a = Image.fromarray(arr[..., 3], "L")
    if expand_px > 0:
        r = int(np.ceil(expand_px))
        a = a.filter(ImageFilter.MaxFilter(2 * r + 1)) if r >= 1 else a
    if blur_px > 0:
        a = a.filter(ImageFilter.GaussianBlur(blur_px))
    arr[..., 3] = np.asarray(a)
    return Image.fromarray(arr, "RGBA")


def grade(img: Image.Image, saturation: float = 1.12, contrast: float = 1.08, brightness: float = 1.0, keep_alpha: bool = True) -> Image.Image:
    alpha = img.split()[-1] if keep_alpha else None
    rgb = img.convert("RGB")
    from PIL import ImageEnhance

    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if keep_alpha:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(alpha)
    return rgb


def unify_cast(img: Image.Image, color, strength: float = 0.22, mode: str = "overlay") -> Image.Image:
    """统一色调：像教程里那样铺一层彩色混合层（overlay / soft_light）。

    人类做 TFR/TNO icon 时会用 50+ 图层、大量"叠加/软光"图层把不同来源的素材调成同一个色调。
    这一步就是在最后做同样的事——把整张图往一个主色调上拉，效果比单纯加饱和度更像原版。
    """
    if strength <= 0:
        return img
    arr = np.asarray(img.convert("RGBA")).astype(np.float32)
    rgb = arr[..., :3] / 255.0
    c = np.array(color, dtype=np.float32) / 255.0
    if mode == "soft_light":
        blend = np.where(rgb <= 0.5,
                         2 * rgb * c + rgb ** 2 * (1 - 2 * c),
                         2 * rgb * (1 - c) + np.sqrt(np.clip(rgb, 0, 1)) * (2 * c - 1))
    elif mode == "multiply":
        blend = rgb * c
    else:  # overlay
        blend = np.where(rgb <= 0.5, 2 * rgb * c, 1 - 2 * (1 - rgb) * (1 - c))
    out = rgb * (1 - strength) + blend * strength
    arr[..., :3] = np.clip(out * 255, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def to_focus_size(rgba: Image.Image, height: int = 150, min_width: int = 140, max_width: int = 200, margin: int = 2) -> Image.Image:
    """缩放到 HOI4 国策 icon 尺寸：高度固定 150，画布紧贴内容（TFR 的内容都不留大空白）。"""
    img = trim(rgba)
    scale = height / img.height
    w = max(min_width, min(max_width, int(round(img.width * scale))))
    h = height
    inner_w, inner_h = w - 2 * margin, h - 2 * margin
    s = min(inner_w / img.width, inner_h / img.height)
    rs = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(rs, ((w - rs.width) // 2, (h - rs.height) // 2))
    return canvas


def hug_canvas(rgba: Image.Image, height: int = 150, max_width: int = 208, margin: int = 2) -> Image.Image:
    """画布贴着内容裁（TFR 的做法：内容几乎填满画布，alpha 覆盖率约 0.66）。

    和 to_focus_size 的区别：这里宽度不固定，而是按内容的真实宽高比走，
    只在超过 max_width 时才让步（此时内容会被略微缩小）。
    """
    img = trim(rgba)
    s = (height - 2 * margin) / img.height
    w = img.width * s
    if w > max_width - 2 * margin:
        s = (max_width - 2 * margin) / img.width
    rw, rh = max(1, int(round(img.width * s))), max(1, int(round(img.height * s)))
    rs = img.resize((rw, rh), Image.LANCZOS)
    canvas = Image.new("RGBA", (rw + 2 * margin, rh + 2 * margin), (0, 0, 0, 0))
    canvas.alpha_composite(rs, (margin, margin))
    return canvas


# --------------------------------------------------------------------------- #
# 标语文字
# --------------------------------------------------------------------------- #
def add_slogan(
    rgba: Image.Image,
    text: str,
    *,
    font_path: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    font_index: int = 0,
    size_ratio: float = 0.26,
    color=(255, 255, 255, 255),
    stroke=(12, 10, 10, 255),
    stroke_w_ratio: float = 0.10,
    position: str = "bottom",
    angle: float = 0.0,
    align: str = "center",
) -> Image.Image:
    """在 icon 上叠一句粗体标语（TFR 常见：中文/英文粗体 + 深色描边）。"""
    out = rgba.copy()
    if not text.strip():
        return out
    base = out.height
    fsize = max(8, int(base * size_ratio))
    try:
        font = ImageFont.truetype(font_path, fsize, index=font_index)
    except Exception:
        return out
    stroke_w = max(1, int(fsize * stroke_w_ratio))

    tmp = Image.new("RGBA", (out.width * 3, out.height * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.multiline_text((tmp.width // 2, tmp.height // 2), text, font=font, fill=color, stroke_width=stroke_w, stroke_fill=stroke, anchor="mm", align=align, spacing=int(fsize * 0.15))
    if angle:
        tmp = tmp.rotate(angle, resample=Image.BICUBIC, expand=True)
    bb = tmp.getbbox()
    if not bb:
        return out
    tmp = tmp.crop(bb)
    # 宽度不能超出 icon
    max_w = int(out.width * 0.96)
    if tmp.width > max_w:
        s = max_w / tmp.width
        tmp = tmp.resize((max_w, max(1, int(tmp.height * s))), Image.LANCZOS)
    x = (out.width - tmp.width) // 2
    if position == "bottom":
        y = out.height - tmp.height - max(0, int(base * 0.02))
    elif position == "top":
        y = max(0, int(base * 0.02))
    elif position == "center":
        y = (out.height - tmp.height) // 2
    else:
        y = out.height - tmp.height - 2
    out.alpha_composite(tmp, (x, y))
    return out


# --------------------------------------------------------------------------- #
# 预览（给自己看，用来判断视觉效果）
# --------------------------------------------------------------------------- #
def preview(icon: Image.Image, path: str, zoom: int = 5, bg_magenta: bool = True):
    """1:1 原尺寸 + N 倍放大（品红底/深底），方便肉眼检查边缘与构图。"""
    z = icon.resize((icon.width * zoom, icon.height * zoom), Image.NEAREST)
    pad = 12
    W = max(icon.width, z.width) + pad * 2
    H = icon.height + z.height + pad * 3
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    # 1:1 放在品红底上（TFR 的透明度检查色）
    cell = Image.new("RGB", (icon.width + pad * 2, icon.height + pad * 2), (255, 0, 255) if bg_magenta else (40, 40, 46))
    cell.paste(icon, (pad, pad), icon)
    sheet.paste(cell, (pad, pad))
    d.text((pad + 2, 2), f"{icon.width}x{icon.height}", fill=(200, 200, 200))
    sheet.paste(z, (pad, icon.height + pad * 3), None)
    # 放大图也要垫底：品红 + 深灰棋盘
    board = Image.new("RGB", (z.width + pad * 2, z.height + pad * 2), (255, 0, 255))
    px = board.load()
    step = 16
    for yy in range(board.height):
        for xx in range(board.width):
            if ((xx // step) + (yy // step)) % 2 == 0:
                px[xx, yy] = (40, 40, 46)
    board.paste(z, (pad, pad), z)
    sheet.paste(board, (0, icon.height + pad * 2))
    sheet.save(path)
    return path
