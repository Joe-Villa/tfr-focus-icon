#!/usr/bin/env python3
"""汇总计费账本：这张 icon 到底花了多少钱。

账本由 `sf.py` 每次成功调用时追加一行（`~/.cache/tfricon/usage.jsonl`，
可用 `TFRICON_USAGE_LOG` 改路径）。

为什么不能直接读余额：硅基流动的余额接口 2026-10-17 已下线
（`/v1/user/info` → 410 code=20092），出图响应也没有 usage 字段。
所以成本 = **账本里的调用数 × 官方单价**；图片单价是一手核实过的
（https://siliconflow.cn/pricing），文本/VLM 的 token 单价标 estimated。

用法：
  cost.py                          # 全部
  cost.py --last 2                 # 最近 2 小时
  cost.py --run sea_normal          # 只看某个 run（TFRICON_RUN=sea_normal 跑出来的）
  cost.py --per-icon 1              # 除以交付的 icon 数，给"每张 icon 成本"
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

DEFAULT_LOG = os.path.expanduser("~/.cache/tfricon/usage.jsonl")


def load(path):
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def main():
    ap = argparse.ArgumentParser(description="汇总 API 计费账本")
    ap.add_argument("--log", default=os.environ.get("TFRICON_USAGE_LOG") or DEFAULT_LOG)
    ap.add_argument("--last", type=float, help="只看最近 N 小时")
    ap.add_argument("--run", help="只看这个 run 标签")
    ap.add_argument("--per-icon", type=int, default=0, help="除以交付的 icon 张数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    recs = load(args.log)
    if args.last:
        cut = time.time() - args.last * 3600
        recs = [r for r in recs if r.get("epoch", 0) >= cut]
    if args.run:
        recs = [r for r in recs if r.get("run") == args.run]

    if args.json:
        print(json.dumps(recs, ensure_ascii=False, indent=1))
        return 0

    if not recs:
        print("账本里没有记录（%s）" % args.log)
        print("提示：sf.py 从此版本起才会记账；更早的调用没有记录。")
        return 0

    by = defaultdict(lambda: {"n": 0, "units": 0, "cost": 0.0})
    est = unknown = 0.0
    for r in recs:
        key = "%s  %s" % (r.get("kind", "?"), r.get("model", "?"))
        b = by[key]
        b["n"] += 1
        b["units"] += r.get("units") or 0
        b["cost"] += r.get("cost_cny") or 0.0
        if r.get("price_source") == "estimated":
            est += r.get("cost_cny") or 0.0
        if r.get("billed") is None and r.get("extra", {}).get("rejected"):
            unknown += 1

    total = sum(b["cost"] for b in by.values())
    span = (min(r.get("epoch", 0) for r in recs), max(r.get("epoch", 0) for r in recs))
    print("账本 %s" % args.log)
    print("记录 %d 条，时间 %s → %s" % (
        len(recs),
        time.strftime("%m-%d %H:%M:%S", time.localtime(span[0])),
        time.strftime("%m-%d %H:%M:%S", time.localtime(span[1]))))
    print()
    print("%-42s %5s %10s %10s" % ("类型 / 模型", "次数", "计费单位", "¥"))
    for key, b in sorted(by.items(), key=lambda kv: -kv[1]["cost"]):
        print("%-42s %5d %10d %10.3f" % (key, b["n"], b["units"], b["cost"]))
    print("-" * 70)
    print("%-42s %5s %10s %10.3f" % ("合计", "", "", total))
    if est:
        print("  其中 token 计费部分 ¥%.3f 为**估算**（未一手核对输出单价）" % est)
    if unknown:
        print("  另有 %d 次审核被拒：是否计费未核实，按 ¥0 记但标了 billed=null" % int(unknown))
    if args.per_icon:
        print()
        print("交付 %d 张 → **每张 ¥%.3f**" % (args.per_icon, total / args.per_icon))
    return 0


if __name__ == "__main__":
    sys.exit(main())
