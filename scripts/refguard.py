#!/usr/bin/env python3
"""参考图闸门：把"坏图"挡在对话之外。

为什么需要它（2026-10-16 实测事故）：
  图像字节一旦进入对话历史，之后**每个**请求都会把它重发一遍。上游 API 对图片做严格
  校验，遇到截断/格式不符的文件直接 400：
      input[424].image[0]: You have uploaded an unsupported image. Please make sure
      your image is valid and has one of the following formats: webp, png, jpeg, and gif.
  这条文案会误导人去改格式（其实那张图就是 JPEG，只是没下完）。真正致命的是：坏图跟着
  历史永远重发，**整个会话从此每条消息都失败**，只能开新会话。

  所以：外部来源的图（网络下载、别人给的路径、第三方工具产出）在进对话（view_image）之前
  必须过闸门；自己脚本用 PIL 写出来的 PNG 不用（可信来源）。

用法：
  refguard.py check PATH... [--min-side 64] [--json]       # 只检查
  refguard.py fix   PATH... [--inplace]                    # 修复能解码但被截断/异常的文件
  refguard.py probe PATH... [--url BASE] [--key K] [--model M]   # 真问一次聊天 API 收不收得下
  refguard.py gate  PATH... [--probe] [--min-side 64]      # check → 能修就 fix → 再 check

退出码：0 = 全部通过；1 = 有文件没通过。
"""
import argparse
import base64
import hashlib
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 上游 API 白名单只有这四种
ALLOWED_KINDS = {"png", "jpeg", "gif", "webp"}
EXT_KINDS = {
    ".png": {"png"},
    ".jpg": {"jpeg"},
    ".jpeg": {"jpeg"},
    ".jfif": {"jpeg"},
    ".gif": {"gif"},
    ".webp": {"webp"},
}


def sniff(head: bytes, tail: bytes, size: int):
    """按字节判断真实格式，不看扩展名。"""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    # 上游不收、但很常见的格式：认出来，好给一句人话
    if head[:2] in (b"BM",) and len(head) > 14:
        return "bmp"
    if head[:2] in (b"II", b"MM") and len(head) > 4 and head[2:4] in (b"*\x00", b"\x00*"):
        return "tiff"
    if head[:4] in (b"\x00\x00\x02\x00", b"\x00\x00\x0a\x00", b"\x00\x00\x03\x00"):
        return "tga"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    low = head[:64].lstrip().lower()
    if low.startswith(b"<!doctype") or low.startswith(b"<html") or low.startswith(b"<?xml"):
        return "html"
    if size == 0:
        return "empty"
    return "unknown"


def _pil():
    try:
        import PIL.Image as Image
        import PIL.ImageFile as ImageFile

        ImageFile.LOAD_TRUNCATED_IMAGES = False  # 关键：严格模式
        return Image
    except Exception:
        return None


def check_one(path: str, min_side: int = 64, ext_hint: str = None) -> dict:
    """返回 {'status': 'OK'|'BAD', 'reasons': [...], ...}，不抛异常。

    ext_hint: 下载中途用 `<out>.part` 这类临时文件名时，按最终目标的扩展名来判
    （否则好图也会因为 ".part 不在白名单" 被误判成坏图）。
    """
    rec = {"path": path, "status": "OK", "reasons": [], "kind": None,
           "dims": None, "bytes": None, "sha256": None}

    if not os.path.exists(path):
        rec["status"] = "BAD"
        rec["reasons"].append("文件不存在")
        return rec
    if os.path.isdir(path):
        rec["status"] = "BAD"
        rec["reasons"].append("是目录，不是文件")
        return rec

    size = os.path.getsize(path)
    rec["bytes"] = size
    with open(path, "rb") as f:
        raw_head = f.read(64)
        f.seek(0)
        sha = hashlib.sha256()
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            sha.update(chunk)
        tail = b""
        if size > 0:
            f.seek(max(0, size - 32))
            tail = f.read()
    rec["sha256"] = sha.hexdigest()

    if size == 0:
        rec["status"] = "BAD"
        rec["reasons"].append("空文件（下载中途失败常见形态）")
        return rec

    kind = sniff(raw_head, tail, size)
    rec["kind"] = kind
    if kind == "html":
        rec["status"] = "BAD"
        rec["reasons"].append("内容是 HTML/XML 而不是图片（很可能是 404 错误页被存成了图）")
        return rec
    if kind == "unknown":
        rec["status"] = "BAD"
        rec["reasons"].append("不是可识别的图片（magic=%s）" % raw_head[:8].hex())
        return rec
    if kind not in ALLOWED_KINDS:
        rec["status"] = "BAD"
        rec["reasons"].append("格式 %s 不在上游白名单（只收 webp/png/jpeg/gif）" % kind)

    ext = (ext_hint or os.path.splitext(path)[1]).lower()
    if ext not in EXT_KINDS:
        rec["status"] = "BAD"
        rec["reasons"].append("扩展名 %s 不在白名单（改成 .png/.jpg/.jpeg/.gif/.webp）" % (ext or "<无>"))
    elif kind not in EXT_KINDS[ext]:
        rec["status"] = "BAD"
        rec["reasons"].append("扩展名 %s 与真实格式 %s 不符（mime 会撒谎，上游可能拒收）" % (ext, kind))

    # 结构完整性：截断的图靠这一步抓（本次事故就是缺 JPEG 的 EOI 结尾标记）
    if kind == "jpeg" and not tail.endswith(b"\xff\xd9"):
        rec["status"] = "BAD"
        rec["reasons"].append("JPEG 截断：缺 EOI 结尾标记（文件没下完）")
    if kind == "png" and b"IEND" not in tail:
        rec["status"] = "BAD"
        rec["reasons"].append("PNG 截断：缺 IEND 结尾块（文件没下完）")
    if kind == "gif" and not tail.endswith(b"\x3b"):
        rec["status"] = "BAD"
        rec["reasons"].append("GIF 截断：缺 0x3B 结尾")
    if kind == "webp" and len(raw_head) >= 12:
        declared = struct.unpack("<I", raw_head[4:8])[0] + 8
        if abs(declared - size) > 1:
            rec["status"] = "BAD"
            rec["reasons"].append("WEBP 截断：RIFF 声明 %d 字节，实际 %d 字节" % (declared, size))

    # 严格解码 + 尺寸
    Image = _pil()
    if Image is not None:
        try:
            im = Image.open(path)
            w, h = im.size
            rec["dims"] = [w, h]
            im.load()
            if min(w, h) < min_side:
                rec["status"] = "BAD"
                rec["reasons"].append("尺寸过小：%dx%d（< %d）" % (w, h, min_side))
        except Exception as e:
            rec["status"] = "BAD"
            rec["reasons"].append("严格解码失败：%s" % str(e)[:120])
    return rec


# ---------------------------------------------------------------- 显示

def fmt_line(rec: dict, omit_status: bool = False) -> str:
    dims = "x".join(str(x) for x in rec["dims"]) if rec["dims"] else "?"
    size = rec["bytes"] if rec["bytes"] is not None else "?"
    sha = (rec["sha256"] or "")[:8]
    body = "%-44s %-5s %-11s %9sB  sha:%s  %s" % (
        rec["path"][-44:], rec["kind"] or "?", dims, size, sha,
        "; ".join(rec["reasons"]))
    return body if omit_status else ("%-3s " % rec["status"]) + body


def report(recs, as_json=False, fail_code=True):
    if as_json:
        print(json.dumps(recs, ensure_ascii=False, indent=1))
    else:
        for r in recs:
            print(fmt_line(r))
    n_bad = sum(1 for r in recs if r["status"] != "OK")
    print("refguard: %d OK, %d BAD" % (len(recs) - n_bad, n_bad))
    if n_bad and fail_code:
        return 1
    return 0


# ---------------------------------------------------------------- 修复

def fix_one(path: str, inplace=False, min_side=64):
    """把"能解码但被截断/异常"的图重编码成干净的 PNG。返回 (新路径, 说明)。"""
    Image = _pil()
    if Image is None:
        return None, "没有 PIL，无法修复"
    import PIL.ImageFile as ImageFile

    prev = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True  # 只在这里容忍，尽量把内容抢救出来
    try:
        im = Image.open(path)
        im.load()
        out_img = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
    except Exception as e:
        return None, "连容错解码都失败：%s" % str(e)[:120]
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = prev

    if inplace:
        target = path
        ext = os.path.splitext(path)[1].lower()
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF",
               ".webp": "WEBP"}.get(ext, "PNG")
        if fmt == "JPEG":
            out_img = out_img.convert("RGB")
        bak = path + ".bak"
        if not os.path.exists(bak):
            with open(path, "rb") as src, open(bak, "wb") as dst:
                dst.write(src.read())
    else:
        target = os.path.splitext(path)[0] + ".fixed.png"
        fmt = "PNG"
        out_img = out_img.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else out_img.convert("RGB")

    tmp = target + ".tmp"
    out_img.save(tmp, format=fmt)
    os.replace(tmp, target)
    rec = check_one(target, min_side)
    note = "重编码为 %s" % fmt
    if rec["status"] != "OK":
        return target, note + "，但复检仍不过：%s" % "; ".join(rec["reasons"])
    return target, note + "，复检通过"


# ---------------------------------------------------------------- 上游探针

def probe_config():
    """从 ~/.codex/config.toml 读当前聊天 provider（key 不打印）。"""
    base = key = model = None
    cfg_path = os.path.expanduser("~/.codex/config.toml")
    try:
        import tomllib

        with open(cfg_path, "rb") as f:
            cfg = tomllib.load(f)
        name = cfg.get("model_provider")
        prov = (cfg.get("model_providers") or {}).get(name, {}) or {}
        base = prov.get("base_url")
        key = prov.get("experimental_bearer_token")
        if not key and prov.get("env_key"):
            key = os.environ.get(prov["env_key"])
        model = cfg.get("model")
    except Exception as e:
        print("refguard: 读 %s 失败（%s）" % (cfg_path, e), file=sys.stderr)
    return base, key, model


def probe_images(paths, url=None, key=None, model=None):
    """把图真的发给聊天 API 一次，看它收不收。这是唯一能抓住"上游特有洁癖"的检查。"""
    cfg_base, cfg_key, cfg_model = probe_config()
    base = url or cfg_base
    key = key or cfg_key
    model = model or cfg_model
    if not base or not key:
        print("refguard: 没有可用的 探针 endpoint/key（--url/--key 或 ~/.codex/config.toml）",
              file=sys.stderr)
        return None

    candidates = []
    for cand in (base.rstrip("/") + "/v1/responses", base.rstrip("/") + "/responses"):
        if cand not in candidates:
            candidates.append(cand)

    results = []
    endpoint = None
    for path in paths:
        if endpoint is None:
            for cand in candidates:
                try:
                    status, body = _post_probe(cand, key, model, path)
                except Exception as e:
                    results.append((path, None, "连不上：%s" % str(e)[:120]))
                    break
                if status == 404:
                    continue
                endpoint = cand
                results.append((path, status, body))
                break
            else:
                results.append((path, None, "所有候选 endpoint 都 404：%s" % candidates))
        else:
            try:
                status, body = _post_probe(endpoint, key, model, path)
                results.append((path, status, body))
            except Exception as e:
                results.append((path, None, "连不上：%s" % str(e)[:120]))
    return results


def _post_probe(endpoint, key, model, path):
    import urllib.error
    import urllib.request

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/png")
    payload = {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": "ok?"},
            {"type": "input_image", "image_url": "data:%s;base64,%s" % (mime, b64)},
        ]}],
        "max_output_tokens": 16,
    }
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def cmd_probe(paths, url, key, model, min_side=64, show_local=True):
    local = check_one_batch(paths, min_side)
    if show_local:
        for r in local:
            print(fmt_line(r))
    bad = sum(1 for r in local if r["status"] != "OK")
    results = probe_images(paths, url, key, model)
    if results is None:
        print("refguard: PROBE SKIP（没有 endpoint/key，本地检查结果如上）")
        return 1 if bad else 0
    for path, status, body in results:
        if status is None:
            print("PROBE WARN %s  %s" % (path, body))
        elif 200 <= status < 300:
            print("PROBE OK   %s  HTTP %d（上游收得下）" % (path, status))
        else:
            bad += 1
            print("PROBE BAD  %s  HTTP %s  %s" % (path, status, body))
    return 1 if bad else 0


def check_one_batch(paths, min_side=64):
    return [check_one(p, min_side) for p in paths]


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="参考图闸门（截断/格式/上游收不收）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="只检查")
    p_check.add_argument("paths", nargs="+")
    p_check.add_argument("--min-side", type=int, default=64)
    p_check.add_argument("--json", action="store_true")

    p_fix = sub.add_parser("fix", help="修复能解码但被截断/异常的文件")
    p_fix.add_argument("paths", nargs="+")
    p_fix.add_argument("--inplace", action="store_true",
                       help="原地覆盖（先备份 <file>.bak）；默认只产出 <name>.fixed.png")
    p_fix.add_argument("--min-side", type=int, default=64)

    p_gate = sub.add_parser("gate", help="入会话前的最后一道：check → fix → check [→ probe]")
    p_gate.add_argument("paths", nargs="+")
    p_gate.add_argument("--probe", action="store_true", help="再真问一次聊天 API")
    p_gate.add_argument("--json", action="store_true")
    p_gate.add_argument("--min-side", type=int, default=64)
    p_gate.add_argument("--url")
    p_gate.add_argument("--key")
    p_gate.add_argument("--model")

    p_probe = sub.add_parser("probe", help="真问一次聊天 API 收不收")
    p_probe.add_argument("paths", nargs="+")
    p_probe.add_argument("--url")
    p_probe.add_argument("--key")
    p_probe.add_argument("--model")
    p_probe.add_argument("--min-side", type=int, default=64)

    args = ap.parse_args()

    if args.cmd == "check":
        return report(check_one_batch(args.paths, args.min_side), args.json)

    if args.cmd == "fix":
        bad = 0
        for p in args.paths:
            rec = check_one(p, args.min_side)
            if rec["status"] == "OK":
                print("SKIP %s 本来就是好的" % p)
                continue
            print("FIX  %s  原因：%s" % (p, "; ".join(rec["reasons"])))
            new, note = fix_one(p, args.inplace, args.min_side)
            if new is None:
                bad += 1
                print("     ✗ %s" % note)
            else:
                print("     → %s（%s）" % (new, note))
                if "复检通过" not in note:
                    bad += 1
        return 1 if bad else 0

    if args.cmd == "gate":
        recs = check_one_batch(args.paths, args.min_side)
        if args.json:
            print(json.dumps(recs, ensure_ascii=False, indent=1))
        else:
            for r in recs:
                print(fmt_line(r))
        fixed = []
        for r in recs:
            if r["status"] != "OK":
                new, note = fix_one(r["path"], False, args.min_side)
                if new:
                    print("FIX  %s → %s（%s）" % (r["path"], new, note))
                    fixed.append(new)
                else:
                    print("FIX  %s ✗ %s" % (r["path"], note))
        good = [r["path"] for r in recs if r["status"] == "OK"] + fixed
        if fixed:
            print("--- 复检修复产物 ---")
            recheck = check_one_batch(fixed, args.min_side)
            for r in recheck:
                print(fmt_line(r))
        bad = [r for r in check_one_batch(good, args.min_side) if r["status"] != "OK"]
        code = 1 if bad else 0
        if args.probe and good:
            # gate 上面已经打过本地检查行了，这里不重复
            code = cmd_probe(good, args.url, args.key, args.model, args.min_side,
                             show_local=False) or code
        print("GATE %s" % ("FAIL（下面这些不要喂给对话：%s）" % ", ".join(r["path"] for r in bad)
                           if bad else "PASS（%d 张可用：%s）" % (len(good), ", ".join(good))))
        return code

    if args.cmd == "probe":
        return cmd_probe(args.paths, args.url, args.key, args.model, args.min_side)

    return 2


if __name__ == "__main__":
    sys.exit(main())
