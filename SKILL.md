---
name: tfr-focus-icon
description: 生成《The Fire Rises》(TFR)/TNO 风格的 HOI4 国策 icon——写实照片拼贴、不规则剪影、透明背景 PNG、最小改动接进 mod。默认后处理参数照 TFR 真品量化校准（尺寸/内容占比/剪影实心度/半透明边/描边），用于"给某个国家/国策做一张 TFR 风格图标"的需求。
metadata:
  short-description: 生成 TFR 风格国策 icon
---

# TFR 风格国策 icon

产出：一张能直接放进 TFR/TFR 系 mod 的 PNG——**透明背景、不规则剪影、写实照片拼贴、深色描边 + 柔和外发光、高饱和**，
高 140-150px、宽 140-208px（真品中位 175×150），可选叠一句粗体标语（真品里约三成带中/英/日标语）。

**这套风格就是 TFR 的风格**（TNO 一系同源）：照片拼贴、元素斜向叠压、深描边加外发光。
真品就在 TFR 及 TFR 系子 mod 的 `<mod>/gfx/interface/goals/**/*.png`。
**本仓库不含参考图**（真品是他人作品，不随包分发）——要定基准就指向你自己那份安装目录，
用本仓库自带的工具现拼一张对照图（纯本地，不联网）：

```bash
python3 scripts/refsheet.py --mod "<mod>/gfx/interface/goals" --out refs/_contact_sheet.png --n 16
```

然后 `view_image` 看 `refs/_contact_sheet.png`，定好基准再写 prompt。手边没有 mod 时，
直接拿 [references/style.md](references/style.md) 的统计值当基准（cov≈0.66 / soft≈0.15 / tight≈0.68 / ~3px 深描边）。

## 安装与依赖

- 装进 Codex：在仓库根目录跑 `./install.sh`（把本仓库拷进 `$CODEX_HOME/skills/tfr-focus-icon`；
  这是拷贝不是软链，改过仓库就重跑一次）。
- 本地依赖：Python 3 + `Pillow` + `numpy`；抠图走 RMBG-1.4 ONNX，需要 `onnxruntime`，模型首次用
  `python3 scripts/rmbg.py --download` 拉到 `~/.cache/tfricon/`。**本地不需要 GPU。**
- 出图必须走云端 API：环境变量 `SILICONFLOW_API_KEY`（参考实现用硅基流动；`scripts/sf.py` 只依赖标准库）。
- 真品基准目录（可选）：`--mod <mod>/gfx/interface/goals`，或环境变量 `HOI4_MOD_GOALS_DIR`。

## 铁律

0. **外部来的图必须过闸门再进对话。** 图像字节一进对话历史就会被永久重发，一张坏图
   （截断的 JPEG 等）会让上游 400 `input[N].image[0]: You have uploaded an unsupported image…`，
   而且**该会话从此每条消息都失败，只能开新会话**（2026-10-16 实测，见 pitfalls §8）。
   优解是把"取图 + 看图"整个交给子 Agent；不能开子 Agent 时用
   `fetch_ref.py` + `refguard.py gate --probe` 兜底。**自己脚本产出的 PNG 属可信来源，不受此限。**
   完整分工与模板见 [references/subagents.md](references/subagents.md)。
1. 生成走云端 API：硅基流动（读环境变量 `SILICONFLOW_API_KEY`）。本机无 GPU，不要试图本地跑扩散模型。
2. 提示词必须以游戏美术身份开头：`Photo-collage artwork for a grand-strategy game focus icon: …`
   - 光写 `icon / poster / collage / illustration` → 模型给扁平插画，完全不像。
   - **反例（实测过）**：把 treatment 换成"每个元素单独剪出来 + 黑边 + 拼贴"这类措辞，模型会给
     一整块带撕纸边的**矩形**，剪影反而更不像真品。默认模板别往 collage 措辞上靠。
3. **审核拒绝（code 20021）是随机的**：同一 prompt 会时过时拒，所以要并行多出 + 自动重试
   （`make_icon.py` 内置：重试只换"美术方向"，不换同义词）。但**随机被拒≠题材可用**：
   真实政治人物、真实军事行动这类题材不要做，反复被拒就改题材或改画面元素，别去绕审核。
   永远不要传 `watermark` 参数（必挂）。
4. 模型不会给真透明背景。让它把主体放在纯色平底上（默认纯白，RMBG 最稳），本地抠图。
   提示词里不要出现 sky/clouds/ground/room/stage——会被当素材留在画面里。
5. 缩到最终尺寸（150px 高）**之后**再加描边和外发光；在 1024 原图上加等于没加。
6. 出图后**必须自己 `view_image` 看**（1:1 预览 + 与真品并排）。"逻辑上对"不等于好看。
7. 每张成品都要过一遍数字检查：`scripts/metrics.py`（对齐真品统计值）+ `scripts/qa_gate.py`（硬性废品检查）。
   **两个都过才算合格**；只看 metrics 会被"洋红底板"这种废品骗过去（见 pitfalls.md 里的废品案例）。

## 停机规则（硬性，优先于"再像一点"）

每个 icon 任务都有交付预算。达到任一上限就**立即收口**，不要用"还能更像"继续开新版本。

- **生成预算**：直出路径默认 **1 轮 × 6 张候选**，最多 2 轮/12 张；元素拼贴或图生图路径默认
  **1 套元素 + 1 次排版**，最多 2 次排版。任何路径都不允许第 3 轮；首轮有 PASS 候选时，
  从中选最好的，不再整轮重出。
- **修整预算**：首轮后最多 **2 次局部修整**。每次修整必须对应 `metrics.py --strict` 的具体提示，
  或一个明确的视觉硬伤（人物不对、旗帜/文字错误、剪影矩形化）；"感觉还能更像"不算修整理由。
- **看图预算**：整个任务默认最多 **8 次 `view_image`**。每轮只看 `contact.png`；定稿再看 1 次 1:1
  和 1 次与真品并排。禁止逐版全量看图；外部参考图按铁律 0 交给子 Agent，不占主对话的看图预算。
- **墙钟预算**：普通出图默认 **20 分钟**，元素拼贴/图生图默认 **30 分钟**。到点立即交付当前最佳版本。
- **交付条件**：`qa_gate.py` 返回 `PASS`，`metrics.py --strict` 返回 0，且 1:1 预览没有人物/旗帜/文字硬伤。
  三者同时满足就**停止优化并交付**，不再做"更像原版"的微调。
- **不满足也必须停**：预算用尽仍不满足时，交付当前最佳版本，列出未达标指标和下一轮建议；
  **不要继续自动生成**。只有用户明确说"继续修"才追加一轮预算。
- **禁止的循环**：同一张图在同一指标上连续调整 2 次仍无改善；已经 PASS 后只为"更像"继续调参；
  已经生成最终 PNG 又开新变体。出现任一情况，立即交付并结束本任务。

## 工作流

1. **问清楚**（缺了就直接问用户）：国家 TAG、国策名（中/英）、画面要表达什么、要不要标语、装到哪个 mod。
2. **取参考图**（有外部参考图时；这是最危险的一步，见铁律 0）：优先交给子 Agent 去取去看
   （模板见 [references/subagents.md](references/subagents.md)），拿它回传的"PASS 清单"；
   兜底是自己跑

   ```bash
   python3 scripts/fetch_ref.py refs/x.jpg --commons "File:X.jpg" --manifest refs/manifest.jsonl
   python3 scripts/refguard.py gate refs/*.jpg refs/*.png --probe
   ```

   **只有 `GATE PASS` 的文件才允许 `view_image`**；FAIL 的换源或放弃，别硬看。
3. **定基准**：

   ```bash
   python3 scripts/refsheet.py --mod "<你那份 mod>/gfx/interface/goals" --tag PRC \
       --out refs/_contact_sheet.png --n 16      # 本仓库不带参考图，这张表是现拼的
   ```

   `view_image` 看它，必要时再看用户指定国家的真品 icon。手边没有 mod 就跳过这步，
   按 [references/style.md](references/style.md) 的统计值走。
4. **写 design JSON**：`python3 scripts/make_icon.py --example` 打印模板。关键字段：
   `tag / focus_name / slug / subject（1 个主元素，英文视觉描述）/ props[]（1-3 个支撑元素）/
   palette / slogan{text,position} / variants / height`。可选：`size`（默认 `1280x960`）、
   `treatments`、`cast`、`tight_target`、`cutout`。
   - **主体用"照片"措辞**：`a stern man in a dark navy ceremonial uniform with gold braid, arms crossed, chest-up`。
   - 元素数 3-4 个且彼此重叠时剪影最像真品；元素彼此拉开距离会掉到 tight 0.52（真品中位 0.68）。
5. **出候选**：

   ```bash
   python3 scripts/make_icon.py --design job.json --out out/ --variants 6
   ```

   自动并行出图 → 抠图 → 紧密化 → 描边发光 → 分级 → 缩到国策尺寸 → 输出 `out/final_*.png`、`out/contact.png`。
   默认只跑这一轮；有 PASS 候选就进入挑选，不要因为"可能还有更好的"再跑一轮。
6. **看图挑**（`view_image` 看 `out/contact.png`，定稿时最多再放大单张一次）。
   候选多的时候可以把"初筛排序"丢给子 Agent，但**最终定稿那张必须自己看**。
   若首轮全部 FAIL/不可用，先按停机规则判断是否还有第 2 轮预算；没有就直接交付最佳并说明原因。
7. **过数字**：

   ```bash
   python3 scripts/metrics.py out/final_*.png --strict --profile   # 返回 0 才算指标达标
   python3 scripts/qa_gate.py out/final_*.png             # PASS/WARN/FAIL（键色污染、长宽比、实心度）
   ```

   `tight` 偏低 → 元素放大压实（或调 `tight_target`）；出现 FAIL → 直接放弃这张，从已有候选里换一张。
   `--strict` 返回非 0 时，只允许按它打印的具体提示做一次针对性修整，不要凭感觉继续调参。
8. 需要"多贴片叠压"的拼贴质感时读 [references/composition.md](references/composition.md)，跑 `scripts/compose_icon.py`
   （每个元素单独出图，更费 API；元素图**必须**过 qa_gate 的污染检查）。
9. **交付**：满足交付条件，或预算用尽时，立即把 PNG 给用户；要装进 mod 就跑
   `python3 scripts/install_to_mod.py out/final_x.png --mod <mod根> --tag PRC --slug PRC_xxx`，
   细节（PNG 放哪、gfx 怎么写、国策怎么引用）见 [references/integration.md](references/integration.md)。

## 做不到的（别过度承诺）

- 生成的"人"是模型编的相似面孔，不是真人照片；TFR 原版多用真人。用户要的是具体历史人物时，先说清楚这一点。
- 模型偶尔会在主体下方画一层地面反光，抠完留一条浅灰带。负面词只能减少，不能杜绝 → 多出候选、看图挑。
- 单张直出的"分层拼贴感"仍弱于原版 3-4 个元素斜向叠压的效果；要那种质感得走元素拼贴模式，且更贵。

## 特殊情况：题材里的人物已经有现成立绘

（游戏 mod 立绘、动漫皮套、"让 A 和 B 同框做某个动作"这类需求）

这时走**图生图**：`sf.edit_image(prompt, [图1, 图2], model="Qwen/Qwen-Image-Edit-2509")`。
用法要点（实测，详见 [references/pitfalls.md](references/pitfalls.md) §7）：

- 参考立绘若是**从网上/别人那里拿的**，先过闸门（铁律 0）：`refguard.py gate <立绘>… --probe`。
  用户自己贴进对话的图由客户端负责，不用重复校验。
- `image` 只收一张，但 `image` + `image2` 能同时喂两个角色，模型确实会把两个都画上；
- 输出尺寸跟着**输入图的宽高比**走（`image_size` 不生效）——要横构图就先把输入拼成 4:3；
- 要半身构图，就把输入立绘自己裁成半身再拼，光写提示词没用；
- 抠图后两人之间的空隙会让 tight 掉到 0.4 出头，得在背后垫一块元素（旗帜等）填掉。

## 参考

- [references/style.md](references/style.md) — 风格卡：真品统计值、提示词模板、负面词、后处理默认参数、元素池。
- [references/composition.md](references/composition.md) — 元素拼贴模式：布局模板、图层顺序、文字层。
- [references/pitfalls.md](references/pitfalls.md) — 实测踩坑与排查表（审核、抠图、尺寸、水印、字体、废品检测）。
- [references/integration.md](references/integration.md) — 装进 mod 的最小改动（PNG 放哪、gfx 怎么写、国策怎么引用）。
- [references/review.md](references/review.md) — 用视觉模型（Qwen3-VL）对比真品打分：怎么用、分数怎么解读。
- [references/subagents.md](references/subagents.md) — 危险动作（取参考图/大批看图）下沉给子 Agent 的契约与模板；
  不能开子 Agent 时的兜底梯子、以及"坏图已经进对话"的处置流程。

## 脚本

| 脚本 | 作用 |
|---|---|
| `scripts/make_icon.py` | 主驱动：design JSON → 候选 icon（审核重试、抠图、紧密化、描边发光、标语、尺寸收口） |
| `scripts/sf.py` | 硅基流动客户端（chat / 文生图 / 图片编辑），只依赖标准库 |
| `scripts/fetch_ref.py` | 参考图下载：curl 重试/续传 + 下完即校验，坏文件**不会**留在正式路径上 |
| `scripts/refguard.py` | 参考图闸门：截断/格式/扩展名/严格解码 + 修复 + 上游 probe（`check`/`fix`/`gate`/`probe`） |
| `scripts/cost.py` | 汇总计费账本（`~/.cache/tfricon/usage.jsonl`）：按 run 标签算"每个 icon 花了多少" |
| `scripts/rmbg.py` | RMBG-1.4 ONNX 抠图（`--download` 拉模型到 `~/.cache/tfricon/`） |
| `scripts/iconify.py` | 后处理工具箱：抠图选路、紧密化、描边发光、调色、缩放、标语、预览 |
| `scripts/compose_icon.py` | 多元素拼贴合成（图层、投影、文字层、densify） |
| `scripts/metrics.py` | 与真品对齐的客观指标（cov/soft/tight/comp/edge + 边缘剖面） |
| `scripts/refsheet.py` | 从你自己那份 mod 的真品 icon 里现拼一张对照图（本仓库不分发参考图） |
| `scripts/qa_gate.py` | 废品硬检查（键色污染 / 实心度 / 长宽比） |
| `scripts/review.py` | 用视觉模型对比真品给自己打分（同样要 `--mod` 指向你自己那份真品目录） |
| `scripts/install_to_mod.py` | 写入 mod：`gfx/interface/goals/<TAG>/` + `interface/*.gfx` 片段 |

## 成本与节奏

**实测（2026-10-17，`cost.py` 账本口径）**：一轮 6 张候选 = **¥1.80**，端到端 **64 秒**；
也就是**交付一张 icon ≈ ¥1.80**（¥0.30/张 × 6，含抠图、描边发光、标语、尺寸收口这些本地步骤 ¥0）。
元素拼贴路线贵一倍：每张元素各出一次图，实测一轮排版用了 12 次生成 = **¥3.60/张**。

单价来自官方价目页（一手核对，2026-10-17 直连抓取）：
Qwen-Image / Qwen-Image-Edit(-2509) / Z-Image **¥0.30/张**、Z-Image-Turbo ¥0.10/张、Kolors 免费。
审核被拒**是否计费没有核实过**（账本里记成 `billed=null`，不按 ¥0 假设计入总和）。

**怎么自己复核**：硅基流动的余额接口已下线（`/v1/user/info` → 410），所以别指望读余额；
`sf.py` 会把每次成功调用写进 `~/.cache/tfricon/usage.jsonl`，用 `scripts/cost.py` 汇总：

```bash
TFRICON_RUN=某个标签 python3 scripts/make_icon.py --design job.json --out out/ --variants 6
python3 scripts/cost.py --run 某个标签 --per-icon 1     # → 每张 ¥1.800
```

节奏上仍是**先出 6 张挑 1 张**，比反复精修一张更快也更便宜。
