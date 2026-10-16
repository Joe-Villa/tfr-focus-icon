#!/usr/bin/env python3
"""安全的参考图下载器：下完**立刻校验**，坏文件绝不留在正式路径上。

教训（2026-10-16）：commons/curl 那条路用了 `--max-time 120`，图太大太慢时 curl 超时被杀，
只在磁盘上留了 90KB 的半截 JPEG；而当时的命令写成 `... | grep -E "saved|downloading"`，
管道退出码被 grep 换成了 0，于是"下载失败"被伪装成"成功"，坏图随后被 view_image 读进对话，
把整个会话毒死（详见 refguard.py 顶部）。

这个脚本把那条路修好：
  * curl 带 `--retry-all-errors --retry 2 --connect-timeout 20 --max-time 300`，超时值放宽；
  * 断了可以续传（`-C -`），下次从断点接着下；
  * 每次下完都跑 refguard 的结构校验（截断/EOF/扩展名/严格解码），不过就重下；
  * 反复不过 → 退出码 1，坏文件只留一份 `<out>.rejected` 供查，**绝不写进 `<out>`**。

用法：
  fetch_ref.py OUT --commons "File:ItalianMareNostrum.jpg" [--width 1600]
  fetch_ref.py OUT --url "https://..." 
  可选：--retry 3 --max-time 300 --min-side 64 --manifest refs/manifest.json --keep-rejected
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refguard  # noqa: E402

UA = "tfricon-refs/0.1 (personal icon reference fetch)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def commons_url(title: str, width: int) -> str:
    params = {"action": "query", "format": "json", "titles": title,
              "prop": "imageinfo", "iiprop": "url", "iiurlwidth": width}
    url = COMMONS_API + "?" + urllib.parse.urlencode(params)
    out = subprocess.run(["curl", "-s", "--max-time", "40", "-A", UA, url],
                         capture_output=True, text=True)
    try:
        data = json.loads(out.stdout)
    except Exception:
        raise SystemExit("commons api 返回的不是 JSON：%s" % out.stdout[:200])
    for _, page in data["query"]["pages"].items():
        info = page.get("imageinfo")
        if not info:
            raise SystemExit("commons 找不到 %s" % title)
        return info[0].get("thumburl") or info[0]["url"]
    raise SystemExit("commons 返回里没有 pages")


def curl_once(url: str, part: str, max_time: int, resume: bool):
    cmd = ["curl", "-sSL", "--fail-with-body",
           "--retry", "2", "--retry-delay", "2", "--retry-all-errors",
           "--connect-timeout", "20", "--max-time", str(max_time),
           "-A", UA, "-o", part]
    if resume and os.path.exists(part) and os.path.getsize(part) > 0:
        cmd += ["-C", "-"]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stderr or p.stdout).strip()[:200]


def append_manifest(path: str, rec: dict):
    rec = dict(rec)
    rec["verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["tool"] = "fetch_ref.py"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="下载参考图，下完即校验")
    ap.add_argument("out")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--commons", help='例如 "File:ItalianMareNostrum.jpg"')
    ap.add_argument("--width", type=int, default=1600, help="commons 缩略图宽度")
    ap.add_argument("--retry", type=int, default=3, help="下载+校验的总尝试次数")
    ap.add_argument("--max-time", type=int, default=300, help="单次 curl 上限秒数")
    ap.add_argument("--min-side", type=int, default=64)
    ap.add_argument("--manifest", help="把结果按 JSONL 追加到这里")
    ap.add_argument("--keep-rejected", action="store_true")
    ap.add_argument("--force", action="store_true", help="目标已存在且合格时也重下")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    if os.path.exists(out) and not args.force:
        rec = refguard.check_one(out, args.min_side)
        if rec["status"] == "OK":
            print("SKIP     已存在且合格：%s" % refguard.fmt_line(rec, omit_status=True))
            if args.manifest:
                append_manifest(args.manifest, {k: rec[k] for k in
                                                ("path", "kind", "dims", "bytes", "sha256")})
            return 0
        print("WARN 已存在的 %s 不合格，重新下载：%s" % (out, "; ".join(rec["reasons"])))

    url = args.url or commons_url(args.commons, args.width)
    print("URL  %s" % url)

    part = out + ".part"
    rejected = out + ".rejected"
    ext_hint = os.path.splitext(out)[1]  # 临时文件按最终目标的扩展名校验
    last_rec = None
    for attempt in range(1, args.retry + 1):
        code, err = curl_once(url, part, args.max_time, resume=(attempt > 1))
        if not os.path.exists(part) or os.path.getsize(part) == 0:
            print("TRY%d curl 退出 %s，没拿到任何数据：%s" % (attempt, code, err))
            last_rec = {"status": "BAD", "reasons": ["curl 没拿到数据（退出码 %s：%s）" % (code, err)]}
            continue
        rec = refguard.check_one(part, args.min_side, ext_hint=ext_hint)
        last_rec = rec
        if rec["status"] == "OK":
            os.replace(part, out)  # 只有校验通过才落到正式路径
            print("FETCH OK %s" % refguard.fmt_line({**rec, "path": out}, omit_status=True))
            if args.manifest:
                append_manifest(args.manifest, {
                    "path": out, "url": url, "kind": rec["kind"], "dims": rec["dims"],
                    "bytes": rec["bytes"], "sha256": rec["sha256"]})
            return 0
        print("TRY%d 校验不过（curl 退出 %s）：%s" % (attempt, code, "; ".join(rec["reasons"])))
        os.replace(part, rejected)  # 留一份给人工看，但绝不留在正式路径
        if not args.keep_rejected and attempt == args.retry:
            os.remove(rejected)

    print("FETCH FAIL %s" % (last_rec["reasons"][0] if last_rec else "未知原因"))
    print("         没有可用的图。换个源、或用 refguard.py fix 抢救（若能解码），"
          "但**不要**把 %s 喂给对话。" % (rejected if os.path.exists(rejected) else out))
    return 1


if __name__ == "__main__":
    sys.exit(main())
