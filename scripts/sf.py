"""SiliconFlow minimal client: chat / text-to-image / image-edit.

只用标准库，避免额外依赖。生成结果落盘后返回本地路径。
环境变量：SILICONFLOW_API_KEY
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
PROXY = os.environ.get("TFRI_PROXY", "")

# ---------------------------------------------------------------- 计费账本
#
# 为什么要记：硅基流动的余额接口 2026-10-17 已下线（/v1/user/info 返回
# 410 code=20092），出图响应里也没有 usage 字段，所以"这张图花了多少钱"只能靠
# **调用计数 × 官方单价** 算。每成功一次调用就往账本追加一条，`cost.py` 汇总。
#
# 单价来源：https://siliconflow.cn/pricing（2026-10-17 直连抓取）
#   Qwen/Qwen-Image ¥0.30/张、Qwen-Image-Edit(-2509) ¥0.30/张、
#   Z-Image ¥0.30/张、Z-Image-Turbo ¥0.10/张、Kolors 免费
USAGE_LOG = os.environ.get("TFRICON_USAGE_LOG") or os.path.expanduser(
    "~/.cache/tfricon/usage.jsonl")
IMAGE_PRICE = {
    "Qwen/Qwen-Image": 0.30,
    "Qwen/Qwen-Image-Edit": 0.30,
    "Qwen/Qwen-Image-Edit-2509": 0.30,
    "Tongyi-MAI/Z-Image": 0.30,
    "Tongyi-MAI/Z-Image-Turbo": 0.10,
    "Kwai-Kolors/Kolors": 0.0,
}
DEFAULT_IMAGE_PRICE = 0.30
# 文本/VLM：¥/百万 token（输入, 输出）。价格页只给了"输入"，输出按同价估。
# 单价未经一手核对 → cost.py 里标 estimated。
TOKEN_PRICE = {
    "Qwen/Qwen3.5-27B": (4.80, 4.80),
    "Qwen/Qwen3-VL-32B-Instruct": (4.80, 4.80),
}
DEFAULT_TOKEN_PRICE = (4.80, 4.80)


def _log_usage(kind, model, *, units=0, unit="image", cost=0.0, billed=True,
               prompt_tokens=0, completion_tokens=0, price_source="", tag="", extra=None):
    """追加一条计费记录。记账本身绝不抛异常（不能因为记账失败把出图搞挂）。"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": round(time.time(), 3),
            "run": os.environ.get("TFRICON_RUN", ""),
            "kind": kind,
            "model": model,
            "units": units,
            "unit": unit,
            "cost_cny": round(cost, 6),
            "billed": billed,
            "price_source": price_source,
            "tag": tag,
        }
        if prompt_tokens or completion_tokens:
            rec["prompt_tokens"] = prompt_tokens
            rec["completion_tokens"] = completion_tokens
        if extra:
            rec["extra"] = extra
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class SFError(RuntimeError):
    def __init__(self, code, message, payload=None):
        super().__init__("[%s] %s" % (code, message))
        self.code = code
        self.message = message
        self.payload = payload

    @property
    def is_moderation(self) -> bool:
        return self.code == 20021 or "prohibited or sensitive" in str(self.message)


def _key() -> str:
    k = os.environ.get("SILICONFLOW_API_KEY")
    if not k:
        raise SystemExit("SILICONFLOW_API_KEY is not set")
    return k


_OPENER = None


def _opener():
    if not PROXY:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def _post(path: str, payload: dict, timeout: int = 600, retries: int = 3) -> dict:
    global _OPENER
    if _OPENER is None:
        _OPENER = _opener()
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + _key(),
            "Content-Type": "application/json",
            "User-Agent": "tfricon-skill/0.1",
        },
    )
    last = None
    for attempt in range(retries + 1):
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                d = json.loads(raw)
            except Exception:
                d = {}
            code = d.get("code", e.code)
            # 5xx / 限流 才重试；4xx（含审核 20021）直接抛
            if e.code >= 500 or e.code == 429:
                last = SFError(code, d.get("message", raw[:400]), d)
                time.sleep(3 * (attempt + 1))
                continue
            raise SFError(code, d.get("message", raw[:400]), d) from None
        except urllib.error.URLError as e:
            last = SFError(-1, "network error: %s" % e.reason)
            time.sleep(3 * (attempt + 1))
    raise last


def _get(url: str, timeout: int = 300) -> bytes:
    global _OPENER
    if _OPENER is None:
        _OPENER = _opener()
    last = None
    for attempt in range(4):
        try:
            with _OPENER.open(url, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise SFError(-1, "download image failed: %s" % last)


def image_to_data_url(path) -> str:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return "data:%s;base64," % mime + base64.b64encode(p.read_bytes()).decode()


def chat(messages, model: str = "Qwen/Qwen3.5-27B", temperature: float = 0.6, max_tokens: int = 2048, timeout: int = 300) -> str:
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    d = _post("/chat/completions", payload, timeout=timeout)
    u = d.get("usage") or {}
    pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    ip, op = TOKEN_PRICE.get(model, DEFAULT_TOKEN_PRICE)
    _log_usage("chat", model, units=pt + ct, unit="token",
               cost=(pt * ip + ct * op) / 1e6, prompt_tokens=pt, completion_tokens=ct,
               price_source="estimated")
    return d["choices"][0]["message"]["content"]


def vlm(image_paths, prompt: str, model: str = "Qwen/Qwen3-VL-32B-Instruct", max_tokens: int = 1024, temperature: float = 0.2) -> str:
    content = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(p)}})
    return chat([{"role": "user", "content": content}], model=model, temperature=temperature, max_tokens=max_tokens)


def gen_image(
    prompt: str,
    *,
    model: str = "Qwen/Qwen-Image",
    size: str = "1024x1024",
    negative_prompt=None,
    steps=None,
    guidance=None,
    seed=None,
    batch: int = 1,
    out_dir=".",
    tag: str = "gen",
    extra=None,
):
    payload = {"model": model, "prompt": prompt, "image_size": size, "batch_size": batch}
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if steps is not None:
        payload["num_inference_steps"] = steps
    if guidance is not None:
        payload["guidance_scale"] = guidance
    if seed is not None:
        payload["seed"] = seed
    if extra:
        payload.update(extra)
    t0 = time.time()
    try:
        d = _post("/images/generations", payload)
    except SFError as e:
        if e.is_moderation:
            # 审核被拒：是否计费未经一手核实 → 记成 billed=None（未知），别假装 0
            _log_usage("image", model, units=0, unit="image", cost=0.0, billed=None,
                       price_source="siliconflow.cn/pricing", tag=tag,
                       extra={"rejected": "moderation", "code": e.code})
        raise
    dt = time.time() - t0
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, item in enumerate(d.get("images", [])):
        p = out / ("%s_%02d.png" % (tag, i))
        if item.get("url"):
            p.write_bytes(_get(item["url"]))
        else:
            p.write_bytes(base64.b64decode(item["b64_json"]))
        paths.append(str(p))
    _log_usage("image", model, units=len(paths), unit="image",
               cost=len(paths) * IMAGE_PRICE.get(model, DEFAULT_IMAGE_PRICE),
               price_source="siliconflow.cn/pricing", tag=tag)
    (out / (tag + "_meta.json")).write_text(
        json.dumps({"model": model, "prompt": prompt, "size": size, "elapsed": round(dt, 1), "seed": d.get("seed"), "n": len(paths)}, ensure_ascii=False, indent=2)
    )
    return paths


def edit_image(
    prompt: str,
    images,
    *,
    model: str = "Qwen/Qwen-Image-Edit-2509",
    size=None,
    steps=None,
    guidance=None,
    seed=None,
    out_dir=".",
    tag: str = "edit",
):
    imgs = [images] if isinstance(images, (str, Path)) else list(images)
    data = [image_to_data_url(p) for p in imgs]
    payload = {"model": model, "prompt": prompt, "image": data[0] if len(data) == 1 else data}
    if size:
        payload["image_size"] = size
    if steps is not None:
        payload["num_inference_steps"] = steps
    if guidance is not None:
        payload["guidance_scale"] = guidance
    if seed is not None:
        payload["seed"] = seed
    try:
        d = _post("/images/generations", payload)
    except SFError as e:
        if e.is_moderation:
            _log_usage("edit", model, units=0, unit="image", cost=0.0, billed=None,
                       price_source="siliconflow.cn/pricing", tag=tag,
                       extra={"rejected": "moderation", "code": e.code})
        raise
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, item in enumerate(d.get("images", [])):
        p = out / ("%s_%02d.png" % (tag, i))
        p.write_bytes(_get(item["url"]))
        paths.append(str(p))
    _log_usage("edit", model, units=len(paths), unit="image",
               cost=len(paths) * IMAGE_PRICE.get(model, DEFAULT_IMAGE_PRICE),
               price_source="siliconflow.cn/pricing", tag=tag)
    (out / (tag + "_meta.json")).write_text(json.dumps({"model": model, "prompt": prompt, "n": len(paths)}, ensure_ascii=False, indent=2))
    return paths
