# 实测踩坑表（2026-10-15，硅基流动 Qwen-Image / Z-Image-Turbo）

以下都是本机实测结论（每条都做过可复现的对照实验）。

## 1. 内容审核拒绝（code 20021）

| 现象 | 实测数据 | 对策 |
|---|---|---|
| 同一 prompt 时过时拒 | 同一句 prompt 重复 8 次 × 2 个模型：**16/16 全过**；但换一批写法，6 次里挂 5 次 | 并行多出 + 自动重试（`make_icon.py` 内置，每个候选最多试几次）；**一次被拒不能判定题材不可用** |
| 题材本身敏感 | 真实政治人物、真实军事/战争场面会被**稳定**拒绝（与措辞无关） | **换题材或换画面元素**。核心判断：反复被拒说明题材不合适；用同义替换、拆词、改写去规避审核属于滥用，不做 |
| 「写实人像」措辞 | `Photorealistic portrait of …` 多组被拒 | 换成 `Photo-collage artwork for a grand-strategy game focus icon: photograph of …`（这是风格选择，不是为了绕审核） |
| **传了 `watermark` 参数** | 同 prompt 只差这一个参数：不带 → 通过；带 `watermark: false` → 20021 | 永远不要传这个参数（上游 bug） |

结论一句话：**审核是随机的，所以重试是工程手段；但"能过"不等于"该做"**——真实人物与真实军事题材
不在这个工具的适用范围里。`make_icon.py` 的重试只在几种"美术方向"之间切换（措辞/光照/影调），
不含任何针对审核的同义替换词表。

## 2. 生成风格

| 现象 | 原因 | 对策 |
|---|---|---|
| 出来是扁平插画/矢量海报 | prompt 里出现 `icon / poster / collage / illustration / flat` 且主体写成名词短语 | 用 `photograph of …`、`photobashed photographic cut-outs`、`film grain`、`harsh contrast`，并以游戏美术身份开头 |
| 主体被"卡片化"（圆角矩形白框） | 模型对 `icon` 一词的字面理解 | 同上；负面词加 `rounded rectangle, frame, border, ui panel` |
| 画面里出现天空/地面/房间 | 提示词/元素描述里带了环境词 | 元素只写主体；负面词加 `sky, clouds, landscape, room, ground, floor, cast shadow` |
| 右下角「AI生成」水印 | 服务端加的 | 它落在空白背景上，抠图时自然消失；必要时 `iconify.crop_watermark()` 先糊掉右下角 |

## 3. 抠图

**默认路线（2026-10-16 起）：RMBG-1.4 ONNX，色键只做兜底。** 实测对照（同一批生成图）：

| 元素 | 白底色键不透明占比 | RMBG-1.4 | 目视 |
|---|---|---|---|
| 含白条纹的美国旗 | 0.740 | 0.853 | 条纹被掏空 / 完整 |
| 白头海雕 | 0.526 | 0.552 | 头部周围残留白块 / 边缘干净 |
| 白底政治人物 | 0.596 | 0.552 | 右下角水印被当主体留下 / 干净 |
| 白宫（白色建筑） | 0.461 | 0.703 | 白色柱体连同背景一起被切掉 / 建筑完整 |

但 RMBG 只认**一个显著主体**：一张图里"人物 + 旗帜 + 头盔"的拼贴，它只留下人物
（实测 opaque 0.129 vs 色键 0.295）。所以 `auto_cutout_full(mode="auto")` 两条都算，
`rmbg/色键 ≥ 0.8` 才用 RMBG —— 实测单元素 1.03-1.37、多元素拼贴 0.44-0.57，分得很开。
日志里会打印 `[matting] RMBG 只留下主主体（0.129 vs 色键 0.295），改用色键…`，别慌，那是正常分支。

| 现象 | 原因 | 对策 |
|---|---|---|
| 白旗条纹/白柱体被挖空 | 白底色键把主体自身的白当背景 | 装 onnxruntime + `python3 scripts/rmbg.py --download`，让 auto 走 RMBG |
| 抠完只剩一个人，旗帜/道具没了 | RMBG 是单主体显著物体模型 | auto 模式已自动改回色键；或把主体拆成多个元素用 `compose_icon.py` |
| 人脸被抠出"洞"、浅色高光消失 | 模型给的"纯洋红"背景其实偏脏粉（实测键色 `(244,75,163)`），RGB 距离阈值把浅肤色也吃进去了 | 用 HSV 色键：`iconify.hsv_chroma_cutout()`（色相 ±30° + 饱和度阈值），浅肤色/白高光不受影响 |
| 主体内部出现洋红空洞（例如金属齿轮中心、徽记内圈） | 主体里**真的有洋红色**，且与画布边缘的背景连通 → 色键会把它当背景挖掉（`/tmp` 验收测试实测） | 要么改 prompt 换掉主体里的洋红元素；要么 `design.cutout = "rembg"` 走 u2net 抠图（能保住主体，但会丢"背景型"元素如远景烟囱）。两种方式各有取舍，看图决定 |
| 抠完边缘一圈脏色 | 半透明像素混了背景色 | `iconify` 的 `_decontaminate` 按 alpha 反解背景色 |
| 背景是天空/场景，根本抠不干净 | 没让模型把主体放纯色平底 | 生图阶段就要 `on a flat uniform backdrop of pure magenta color` |
| 抠完还剩一条"地面" | 模型自己加了地平面 | 提示词 `cut-out elements floating without any ground`；仍不行就在 design 里把主体改成半身/胸像构图 |

## 4. 尺寸与合成

- TFR 原版实测分布（6034 张）：高度集中在 140-170（众数 150），宽度 140-208（众数 170）。
  76% 的图内容顶到画布边缘，**不是**留白居中的方图。`to_focus_size(height=150, max_width=200)` 对齐这个习惯。
- 全部为 RGBA PNG。HOI4 不要求正方形，宽高按内容走。
- 缩到 150px 后细节会消失：主体占画面比例要够大（>55%），别塞 5 个同等重要的元素。

### 4.1 横构图是硬要求（2026-10-16 实测）

同一份 design、同一模型，只改生成画布：

| 生成尺寸 | 抠完的成品 | tight |
|---|---|---|
| `1024x1024`（方图） | **111 × 150**（细高竖条，不像原版） | 0.67 |
| `1280x960`（4:3，默认） | **162 × 150**（对上真品 175×150） | 0.68 |

prompt 里也要写 `elements spread out in a wide horizontal composition that deliberately overflows the frame`
（已进 `build_prompt` 的默认 tail）。只改画布不改措辞，模型还是会把人和旗竖着叠起来。

### 4.2 标语会把画布撑高（已修）

标语是叠在**缩放之后**的，实测请求 `height=150` 导出 **167**。现在 `finish()` 先给标语
留预算（`height × size_ratio × 1.5`），末尾再用 `iconify.fit_to_target()` 收口，
导出高度==请求高度（只缩不放，不糊）。同时标语默认字号从 0.26 降到 0.17（真品大约占高的 1/6）。

## 5. 文字

- 让模型出中文小字必糊。**标语一律本地用 PIL 叠**（`iconify.add_slogan`），
  字体用 `Noto Sans CJK Black`（`/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc`），
  白色填充 + 深色描边（描边宽 ≈ 字号 10%），底部对齐。
- 英文标语可以交给模型（TFR 里也有），但同样建议本地叠，方便改。

## 6. 环境

- 系统 python3 有 numpy 1.26 / scipy / PIL 10.2，够用（不需要 torch）。
- 若某些包被装进 `~/.local` 且版本冲突（numpy 报 `cannot import name 'broadcast_to'`），
  用 `PYTHONNOUSERSITE=1` 跑脚本即可回退到系统包。
- 本机代理 `http://127.0.0.1:7890` **只给** YouTube/Google/GitHub；硅基流动、B 站等国内站点直连。
- 抠图依赖：`pip install --break-system-packages onnxruntime`（实测 1.31.0 与 numpy 1.26.4 /
  scipy 1.11.4 共存无冲突），模型 `~/.cache/tfricon/rmbg14.onnx`（`python3 scripts/rmbg.py --download`）。
  **不要装 rembg**：它要求 numpy>=2.3，会把系统 numpy 顶掉，scipy 直接
  `numpy.core.multiarray failed to import`（踩过）。
  1024×1024 单张 RMBG 抠图约 0.6-0.8 秒（CPU）。
- 已知残留缺陷（还没根治）：模型有时在主体下方画一层"地面反光/投影"，抠完留一条浅灰带。
  `NEG_BASE` 里的 `ground, floor, cast shadow, horizon` 只能减少不能杜绝；
  目前靠"多出候选 + 看图挑一张"绕过。

## 7. 已有立绘的两个角色同框（2026-10-16 实测，做《与丰川祥子和解》时踩出来的）

需求形态：题材里的"人"已经有现成立绘（游戏 mod 里的动漫角色、皮套等），
要的是"这两个人同框做某个动作"，而不是凭文字描述画人。
这时别用 `make_icon.py`（文生图），用**图生图**：`sf.edit_image` / `Qwen/Qwen-Image-Edit-2509`。

| 实测结论 | 证据 |
|---|---|
| `image` 字段只收**一张**——传数组直接 `20015 image should be a string` | 四种 payload 形态探针，只有 `{"image": A, "image2": B}` 返回 200 |
| 但 `image` + `image2` 是**真多图**，模型会把两个角色都画上，并保持各自服装发型 | 输入两个立绘 → 输出两人面对面握手，两边角色都对 |
| 输出尺寸**不看** `image_size`，跟着输入图的宽高比走 | 输入 356×968 → 输出 616×1688；输入 1280×960 → 输出 1176×880 |
| 想要半身/中景，**先把参考立绘自己裁成半身再拼进输入图**；只在提示词里写"腰部以上的中景"没用，模型会沿用输入构图 | 全身边排输入 → 全身输出；半身并排输入 → 半身输出 |

抠图与拼贴：

- 两人握手时剪影是连在一起的，`auto_cutout_full` 的 RMBG 会把两个都留下（实测 opaque 0.43）；
  色键反而会把白袖子掏空（0.35），所以这一档走 RMBG。
- 但"两个人 + 中间空隙"的 tight 只有 **0.42**（真品 0.68）——光靠 `densify_silhouette` 补不回来。
  正解是在背后垫一块元素（旗帜/背景板）把空隙填掉，顺带把主题元素加进画面。本次垫的就是
  该运动的旗帜，tight 0.712。
- 合成稿**直接输出 RGBA**、再用 `finish_rgba` 那套收尾（densify → grade → outline_glow →
  slogan → soften → fit_to_target，跳过抠图），就没有"洋红底 → 抠图"那一步的粉色残边
  （走洋红两遍的版本 6 倍放大能看见一圈粉边，污染指标 0.009 却在阈值内、骗得过 `qa_gate`）。

多元素合成的通用做法在 `scripts/compose_icon.py`（布局 / 图层 / 投影 / 文字层）。
"先把立绘裁半身再并排""先抠图预览再合成""旗帜布褶与明暗"这类一次性脚本没有随 skill 分发，
需要时照上面的思路现写即可（`iconify.py` 里的 `auto_cutout_full` / `densify_silhouette` /
`grade` / `outline_glow` 是可直接复用的积木）。

## 8. 一张坏参考图会毒死整个会话（2026-10-16 实测事故）

这是目前为止最贵的一次坑：**不是**某一轮失败，而是那条对话直接报废。

| 项 | 内容 |
|---|---|
| 症状 | 干着活突然报 `input[424].image[0]: You have uploaded an unsupported image. Please make sure your image is valid and has one of the following formats: webp, png, jpeg, and gif.`；之后**每条**消息都在几秒内以同一条报错失败，重试无效 |
| 真凶 | 一张从维基共享资源下载的参考图（`mare_nostrum_map.jpg`）：90169 字节的**截断 JPEG**，缺 EOI 结尾标记，`PIL` 报 `image file is truncated (36 bytes not processed)`。报错文案说"格式不受支持"是误导——它就是 JPEG，只是没下完 |
| 为什么重试没用 | 坏图的 base64 已经写进对话历史，之后每个请求都会重发；修磁盘上的文件也救不回那条会话（坏字节在会话存档里）。**只能开新会话** |

根因是三层防护同时失效（缺一层就不会出事）：

1. 下载命令用了 `curl --max-time 120`，直连 `upload.wikimedia.org` 那次卡住 → curl 超时被杀，
   `-o` 在磁盘上留了半截文件；
2. 命令写成 `(...) 2>&1 | grep -E "saved|downloading"`，curl 的非零退出码和 stderr 一起被管道吃掉，
   工具回报 `Process exited with code 0`——**失败被伪装成成功**；
3. 顺手敲的 `identify` 对截断 JPEG 不报错，照样打印 `1254x630 90169B`。

随后 `view_image` 把坏字节原样编码进对话（记录里那张图的 sha256 与磁盘上的坏文件完全一致），
上游严格校验拒收 → 整条对话报废，用户只能另开新会话继续（22:48 与 22:55 连续两次同错）。

### 对策（已落进 skill）

| 层 | 做法 |
|---|---|
| 分工 | 外部参考图的"取图 + 看图"下沉给子 Agent——坏图只污染它那条一次性对话 → [subagents.md](subagents.md) |
| 下载 | `scripts/fetch_ref.py`：`--retry-all-errors` + 续传 + **下完即校验**，反复不过就退出码 1 且不留文件 |
| 入会话闸门 | `scripts/refguard.py gate <图…> --probe`：结构校验（截断/EOF/扩展名/严格解码）→ 能修就重编码成干净 PNG → **再真问一次聊天 API 收不收** |
| 禁令 | 不用 `cmd \| grep` 吞退出码；不拿 `identify` 当校验 |

闸门与上游的一致性已验证：同一张坏图，`refguard check` 判 BAD，`probe` 复现出上游原话
`input[0].image[0]: You have uploaded an unsupported image...`；`fix` 重编码后 `probe` 200。
负例（空文件 / HTML 404 页 / 扩展名与真实格式不符 / `.tga`）全部被判 BAD。

## 9. 键色污染判据会误伤"画师画的艳紫/艳绿"（2026-10-16 修）

做《"我们的海"一揽子援助计划》时踩到：4 张成品被 `qa_gate` 判
`FAIL 洋红键色污染 0.079-0.086`，但那是**误判**。

| 项 | 数据 |
|---|---|
| 症状 | 元素拼贴 + 动漫立绘（紫发皮套），`metrics` 全绿灯，`qa_gate` 全 FAIL |
| 查法 | 逐像素看污染像素：H≈287-309°、S 0.56-0.87，位置全在头发的洋红紫高光里 |
| 真因 | 旧判据 `(R-G)>60 且 (B-G)>60` 只看**色相**，把画师画的深紫也算成键色 |
| 误伤率 | 800 张 TFR 真品里 **2.25%** 会被误判（真品本来就有艳紫/艳红的画面） |

**改法**：再加一条亮度门槛 —— 键色是亮的，画师画的深紫不是。

```python
mx = np.maximum(np.maximum(r, g), b)
magenta = ((r - g) > 60) & ((b - g) > 60) & (mx >= 190)
green   = ((g - np.maximum(r, b)) > 60) & (mx >= 190)   # 同理：三色旗的实心绿旧判据能到 0.027-0.205
```

| 样本 | 旧判据 | 新判据 |
|---|---|---|
| 真品 800 张里 >0.05 的比例 | 2.25%（洋红）/ 1.00%（绿） | **1.00% / 0.25%** |
| 已知废品样本 A（元素拼贴，残留整块键色底板） | 0.278 | 0.272（照样 FAIL） |
| 已知废品样本 B（单个元素没抠净） | 0.612 | 0.611（照样 FAIL） |
| 正常 `runA/runB` | 0.000-0.004 | 0.000-0.003 |
| 紫发皮套成品 | 0.079-0.086 ❌ | 0.004-0.006 ✅ |

结论：**判据是启发式，不是真理**。真品自己也有 ~1% 会被判到 —— 遇到 FAIL 先按上表
"逐像素 → 色相分布 → 落在画面哪一块"三步自查，分清楚是残留还是画师的艳色，别直接丢掉一张好图。

### 9.1 顺带：真的残留要按颜色清

硅基流动给的不是纯洋红，而是**脏粉** ≈`(235,60,180)`。RMBG 对**被主体包围**的键色区域
（木箱提手孔、镂空）会判成主体留下来，形状指标看不出来，肉眼却看得见。

```python
rgba = ic.despill_key_color(rgba)   # 到脏粉色族 RGB 距离 < 72 → alpha 清零 + 边缘反解背景色
```

已接进 `compose_icon.gen_element()`（每个元素抠完就清一遍）。实测木箱：污染 0.0099 → 0.0003，
严格残留像素 2428 → 0。阈值 72 的依据：木色/绳色离键色 ≥130、脏粉残留 ≤40，中间很空。
注意它是**破坏性**的 —— 只在"这元素确实是洋红底生成的"路径用，别拿去处理外部来的成品图。
