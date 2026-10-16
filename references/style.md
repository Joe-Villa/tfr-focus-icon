# 风格卡：TFR 国策 icon 的提示词与后处理

## 原版到底长什么样（先看图再动手）

真品在 `<TFR mod>/gfx/interface/goals/**/*.png`，6034 张。抽样 400 张的 alpha 统计
（脚本 `scripts/metrics.py --stats`，结果在 `references/tfr_stats.json`）：

| 指标 | 中位数 | 含义 |
|---|---|---|
| 宽 × 高 | 175 × 150 | 不是正方形；高度集中在 140-150 |
| cov | 0.66 | 内容占画布 66%（几乎不留白） |
| soft | 0.15 | 15% 的像素是半透明（外发光/羽化边） |
| tight | 0.68 | 剪影是**实心团块**（包围盒填充率）——我们最容易做差的一项 |
| comp | 46 | 轮廓复杂度（周长²/面积）：太碎就显碎纸片 |
| edge | 1.0 | 平均有 1 条边内容顶到画布边缘 |

视觉语言：**写实照片剪贴（photobash）**——2-5 个抠好的照片元素（人物/军械/旗帜/地标/徽记）
斜向叠压成一个不规则剪影，每个元素有各自的深色描边或投影，整体再压一遍高饱和/高对比，
约三成带粗体标语（中/英/日）。

## 提示词规则（实测有效）

必须用游戏美术身份开头，否则模型给扁平插画：

```
Photo-collage artwork for a grand-strategy game focus icon: <treatment>
```

`<treatment>` 用这几种句式（`make_icon.py` 里的 `DEFAULT_TREATMENTS` 就是它们）：

```
photograph of {subject}, {props}gritty texture, film grain, harsh contrast, hard rim light,
oversaturated {palette} color grade, deep blacks, dirt and scratches, high micro-detail
```

```
photobashed photographic cut-outs: {subject}, {props}layered tightly with hard cut edges,
posterized, ink speckles and halftone dots, oversaturated {palette}, deep shadows
```

```
cinematic still of {subject}, {props}dramatic backlight, volumetric haze, strong shadows,
35mm film look, oversaturated {palette}, high contrast
```

结尾固定加背景条款（抠图靠它）：

```
placed on a flat uniform backdrop of pure magenta color, nothing else in the frame,
cut-out elements floating without any ground, floor or cast shadow, no environment, no frame, no text
```

### 有效 / 无效用词

| 想表达 | 用 | 别用 |
|---|---|---|
| 写实照片感 | `photograph of …`、`photobashed photographic cut-outs`、`35mm film look` | `icon`、`poster`、`vector`、`illustration`、`flat`（→ 扁平插画） |
| 高饱和冲击 | `oversaturated … color grade`、`deep blacks`、`harsh contrast` | `vivid colors`（太弱） |
| 干净主体边缘 | `hard cut-out edges`、`sharp clean subject edges` | `transparent background`（模型不懂 alpha，会给棋盘格或整块底） |
| 留白/环境 | —— | `sky`、`clouds`、`room`、`ground`、`studio backdrop with shadow`（会被当成素材留在图里） |

负面词（`make_icon.py` 的 `NEG_BASE`）：

```
flat vector, cartoon, anime, cel shading, illustration, drawing, painting,
sky, clouds, landscape, scenery, room, environment, gradient background,
ground, floor, soil, cast shadow, horizon, stage,
frame, border, rounded rectangle, ui panel, letters, caption, signature
```

注意：**不要**在 negative 里写 `watermark`（有触发审核的记录），**不要**传 `watermark` 参数。

## 后处理参数（`make_icon.py` 的默认值，2026-10-16 冻结版）

全部是 `finish()` 里的实际默认值，改之前先读 [pitfalls.md](pitfalls.md) 为什么这么定。

| 步骤 | 默认参数 | 说明 |
|---|---|---|
| 生成画布 | `size: "1280x960"`（默认） | 真品是 175×150 的**横构图**。同一条 prompt 用 1024² 直出会得到 111px 宽的竖幅人像，改成横画布 + 横构图提示词后内容宽 162px。**两条要一起改**，只改画布模型仍会把主体摆成方形 |
| 水印 | `crop_watermark` | 右下角"AI生成"落在会被裁掉的角落，抠图后自然消失 |
| 抠图 | `auto_cutout_full(mode="auto")` | RMBG-1.4 ONNX 与色键**都算**：`rmbg/色键 ≥ 0.8` 用 RMBG（适合单主体、含白色主体），否则用色键（适合多元素拼贴）。见 `scripts/rmbg.py` |
| 收紧 | `densify_silhouette(target=0.62)` | 按目标实心度自动收紧剪影（真品 tight 中位 0.68）。手写 `close_radius` 可回退老行为 |
| 调色 | `saturation=1.12`、`contrast=1.08` | 原版更冲，可上到 1.25/1.2 |
| 统一色调 | `cast: {"color":"#8B1A1A","strength":0.2,"mode":"overlay"}`（可选） | 模拟人类用"叠加/软光"图层统一色调的做法 |
| 缩放 | `hug_canvas(height=height-budget, max_width=208)` | 先给标语留预算（`height × size_ratio × 1.5`），**缩到最终尺寸后再做描边发光** |
| 描边 | `outline_px=2.2`、`outline_soft=0.35`、色 `(18,14,12)` | 对齐真品剖面：边界往里第 1 圈亮度 ≈28（内部 ≈85） |
| 外发光 | `glow_px=3.5`、`glow_alpha=120` | 做出 soft≈0.15 的半透明边 |
| 柔边 | `soften_alpha(blur=1.2, expand=0.6)` | 真品边缘不是硬剪的，把 `soft` 从 0.05 拉到 0.16 |
| 高度收口 | `fit_to_target(height=150)` | 请求多少就导出多少（之前会出 167，因为发光晕圈留在画布里） |
| 标语 | `size_ratio=0.17`、白字 + 深色描边（宽≈字号 10%）、字体 `NotoSansCJK-Black.ttc` | 真品带标语的 icon 里文字约占画布高 0.19-0.20；**不要加黑底板**，真品是纯文字+粗描边直接压在画面上 |

验收数字（2026-10-16 实测，命令见 SKILL.md）：

| 成品 | 尺寸 | cov | soft | tight | 真品中位 |
|---|---|---|---|---|---|
| JAP 日出 | 149×150 | 0.669 | 0.163 | 0.740 | 0.661 / 0.151 / 0.682 |
| FRA 大陆封锁（4 张） | 158-177×150 | 0.571-0.609 | 0.122-0.140 | 0.626-0.672 | 同上 |
| SOV 新联盟（3 张） | 150×150 | 0.598-0.625 | 0.162-0.180 | 0.637-0.665 | 同上 |
| CNM 与丰川祥子和解（3 张，元素拼贴 + 动漫立绘） | 157-162×150 | 0.663-0.682 | 0.163-0.171 | 0.710-0.712 | 同上 |
| CNM "我们的海"援助计划（4 张，元素拼贴） | 151×150 | 0.768-0.774 | 0.172-0.180 | 0.847-0.854 | 同上 |

`tight` 偏高（0.85）不是坏事 —— 贴片重叠多、留白少；**偏低**（元素散开、<0.55）才是废品方向。

**发现的一个反例**：把 treatment 换成 "photobash / collage / 每块单独抠出来+黑边" 之后，
模型给出的是一整块**带撕纸边的矩形**（剪影更方、更不像真品）。
所以默认模板继续用 `photograph of …` / `cinematic still of …`，不要往"collage/拼贴"措辞上靠。

## 元素池：各自单独生成时的 prompt（用于 `compose.py` 拼贴）

元素一律：`<元素描述>`, 英文/中文皆可，末尾统一加
`，摄影写实，纯洋红平背景（#FF00FF），除主体外画面里什么都没有，边缘干净`
（或英文 `, photographed on a flat pure magenta (#FF00FF) backdrop, nothing else in frame, clean edges`）。

| 角色 | prompt 示例 |
|---|---|
| 人物（主） | `a stern middle-aged man in a dark green dress uniform with rows of medals, arms crossed, chest-up, harsh side light` |
| 军械 | `a main battle tank in three-quarter view, dust, harsh sunlight` / `a fighter jet climbing, afterburner glow` |
| 旗帜 | `a huge red flag waving in the wind, fabric folds, backlit` |
| 徽记 | `a golden gear-and-wheat emblem on a dark red shield, metallic relief` |
| 地标/建筑 | `a brutalist government building with a giant banner, low angle, dramatic sky`（天空用旗/布幔替代） |
| 人群 | `a dense crowd of workers raising fists, dramatic backlight, silhouettes` |
| 背景板 | `a radial sunburst poster panel in dark red with halftone dots, no text` |

经验：**主元素只要 1 个**（决定谁是画面主角），支撑元素 1-3 个；同一个 icon 里别放两个同等重要的主体。
