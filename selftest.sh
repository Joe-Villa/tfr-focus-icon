#!/usr/bin/env bash
# 自检：**不花 API 钱、不联网**，只验证仓库里的工具链是不是好的。
#
#   ./selftest.sh
#
# 覆盖：脚本能否编译 / 出图驱动的模板能否生成 / 参考图闸门能否抓出坏图 /
#       指标与废品检查能否跑 / 计费账本汇总是否正确。
# 需要网络的加图测试不在自检范围（那要给 SILICONFLOW_API_KEY，且有花费）。
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
pass=0; fail=0

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1（期望 $3，实际 $2）"; fi; }

echo "1) 脚本编译"
python3 -m py_compile scripts/*.py && ok "scripts/*.py 全部编译通过" || bad "有脚本编译不过"

echo "2) 出图驱动的 design 模板"
python3 scripts/make_icon.py --example > "$work/example.json" 2>"$work/err1"
check "make_icon.py --example 输出合法 JSON" "$(python3 -c "import json,sys;json.load(open('$work/example.json'));print('y')" 2>/dev/null || echo n)" "y"

echo "3) 探针/闸门：坏图必须被拦，好图必须放行"
python3 - "$work" <<'PY'
import sys
from pathlib import Path
from PIL import Image
d = Path(sys.argv[1])
im = Image.new("RGB", (200, 160), (200, 40, 40))
im.save(d / "good.png")
b = (d / "good.png").read_bytes()          # 故意截断一张真图
(d / "trunc.png").write_bytes(b[: len(b) // 2])
(d / "error_page.jpg").write_text("<!DOCTYPE html><html>404</html>")
(d / "empty.png").write_bytes(b"")
PY
python3 scripts/refguard.py check "$work/good.png" >/dev/null 2>&1; check "好图放行（退出码 0）" "$?" "0"
for f in trunc.png error_page.jpg empty.png; do
  python3 scripts/refguard.py check "$work/$f" >/dev/null 2>&1
  [ $? -ne 0 ] && ok "坏图被拦：$f" || bad "漏放坏图：$f"
done
python3 scripts/refguard.py fix "$work/trunc.png" >/dev/null 2>&1
python3 scripts/refguard.py check "$work/trunc.fixed.png" >/dev/null 2>&1; check "截断图可被 fix 救回（退出码 0）" "$?" "0"

echo "4) 指标与废品检查（跑得动即可，合成图不保证 PASS）"
python3 - "$work" <<'PY'
import sys
from pathlib import Path
from PIL import Image, ImageDraw
d = Path(sys.argv[1])
im = Image.new("RGBA", (170, 150), (0, 0, 0, 0))
dr = ImageDraw.Draw(im)
dr.ellipse((20, 20, 150, 130), fill=(180, 60, 60, 255))
im.save(d / "synthetic_icon.png")
PY
python3 scripts/metrics.py "$work/synthetic_icon.png" >/dev/null 2>&1; check "metrics.py 跑通（退出码 0）" "$?" "0"
python3 scripts/qa_gate.py "$work/synthetic_icon.png" >/dev/null 2>&1
rc=$?; [ "$rc" -le 1 ] && ok "qa_gate.py 跑通（退出码 $rc，0=PASS/1=FAIL 都正常）" || bad "qa_gate.py 崩了（退出码 $rc）"

echo "5) 计费账本汇总"
cat > "$work/usage.jsonl" <<'JSON'
{"ts":"2026-01-01T00:00:00","epoch":1,"run":"t","kind":"image","model":"Qwen/Qwen-Image","units":6,"unit":"image","cost_cny":1.8,"billed":true,"price_source":"p","tag":"cand00"}
{"ts":"2026-01-01T00:00:01","epoch":2,"run":"t","kind":"image","model":"Qwen/Qwen-Image","units":1,"unit":"image","cost_cny":0.3,"billed":true,"price_source":"p","tag":"cand01"}
JSON
got="$(python3 scripts/cost.py --log "$work/usage.jsonl" --run t --per-icon 7 \
       | sed -n 's/.*每张 ¥\([0-9.]*\).*/\1/p')"
check "cost.py 汇总正确（7 张 ¥2.1 → 每张 0.300）" "$got" "0.300"

echo
echo "自检：$pass 通过 / $fail 失败"
[ "$fail" -eq 0 ] || exit 1
