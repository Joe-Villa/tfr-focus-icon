# 危险动作下沉到子 Agent（+ 没有子 Agent 时的兜底）

## 原理：为什么有些活必须推出去

对话是**只进不出**的：图像字节一旦进入某条对话的历史，之后每个请求都会把它重发一遍。
所以坏图的代价不是"这一轮失败"，而是**这条对话从此废掉**（2026-10-16 实测事故，见 pitfalls §8：
截断的参考图让上游连续两次 400，只能开新会话）。

推论：

- **自己脚本产出的 PNG 是可信来源**（PIL 写的，编码器就在我们手里）→ 主 Agent 可以直接
  `view_image` 看成品，铁律 6 的"亲眼看图"不受影响。
- **外部来的图不可信**（网络下载、别人给的路径、第三方工具产出）→ 进去之前必须过闸门。
  最干净的做法是：连"取图 + 看图"一起交给子 Agent，坏图只污染它那条一次性对话。

## 分工

| 活 | 为什么危险 | 怎么办 |
|---|---|---|
| 联网取参考图 + 看参考图 | 坏图毒死对话；且参考图常常要看好几张 | **下沉给子 Agent**（下面的模板） |
| 十几张候选的初筛 | 主对话被塞满；任何一张出问题都致命 | **下沉给子 Agent**，只回排名 + 理由 |
| 联网调研（找教程、查资料） | 网页抓下来的东西鱼龙混杂 | 优先下沉；至少把下载过 `fetch_ref.py`/`refguard.py` |
| 写 design JSON、跑 `make_icon.py`、看图挑成品、过数字关 | 产出可信、且铁律 6 要求主 Agent 亲眼判断 | **主 Agent 自己做**，别外包 |

## 子 Agent 模板

把 `<...>` 换成实际内容后交给 `spawn_agent`（任务名随意，建议 `ref_scout`）：

```
你负责为本轮 icon 取参考图。铁规则：任何未过闸门的图片，都不要当成"可用素材"交出去。

1) 下载（会自动校验+重试，坏文件不会留在正式路径）：
   python3 ~/.codex/skills/tfr-focus-icon/scripts/fetch_ref.py <refs>/<name>.jpg \
       --commons "File:<Xxx>.jpg" --width 1600 --manifest <refs>/manifest.jsonl
   # 或 --url "https://..."
2) 入会话闸门（只有 PASS 的才允许看）：
   python3 ~/.codex/skills/tfr-focus-icon/scripts/refguard.py gate <refs>/*.jpg <refs>/*.png --probe
3) 只对 GATE PASS 的图 view_image，然后写 <refs>/NOTES.md：
   每张 1-2 句 —— 画面内容 / 能否直接用 / 建议怎么裁或用在哪。
4) 回给我的必须是文字：PASS 的绝对路径清单 + manifest 路径 + NOTES 摘要。
   不要贴图、不要贴 base64、不要描述失败文件的内容。
5) 如果全部下载或校验失败，如实回"没有可用参考图"并说明原因（缺图也比坏图好）。
```

主 Agent 收到后只管两件事：**用哪些路径**（只信 PASS 清单）、**画面怎么设计**。

## 兜底：不能开子 Agent 时（工具里没有 `spawn_agent`，或用户关了多智能体）

主 Agent 自己干，但顺序**必须**是"先校验、后入会话"：

1. 用脚本取图，不要用裸 `curl`：

   ```bash
   python3 scripts/fetch_ref.py refs/x.jpg --commons "File:X.jpg" --manifest refs/manifest.jsonl
   ```

   它下完立刻验结构（截断/EOF/扩展名/严格解码），坏了自动重试、可以续传，
   最终仍坏就退出码 1，且**不会**把坏文件留在正式路径上。

2. 入会话前的最后一道闸门：

   ```bash
   python3 scripts/refguard.py gate refs/*.jpg refs/*.png --probe
   ```

   - `check` → 能修就 `fix`（重编码成干净 PNG，原文件不动）→ 再 `check`；
   - `--probe` 再把图真的发给聊天用的模型 API 一次，确认它**收得下**
     （这是唯一能抓住"上游特有洁癖"的检查——本地怎么看都正常、上游照样 400 的情况是存在的）；
   - 打印 `GATE PASS/FAIL`。**只有 PASS 的文件允许 `view_image`。**

3. 一次只喂 1-3 张图，别一口气 `view_image` 十几张（历史膨胀 + 单点坏图的风险都会放大）。

4. 别把失败藏起来：**不要**写 `cmd | grep -E "saved|downloading"` 这类管道——
   管道退出码会变成 `grep` 的 0，把 curl 的失败伪装成成功（这正是本次事故的成因之一）；
   也不要把 `identify` 当校验（它对截断 JPEG 不报错）。

## 万一还是中毒了（坏图已进对话）

症状：某一轮突然报 `input[N].image[0]: You have uploaded an unsupported image...`，
而且**之后每条消息都在几秒内以同一条报错失败**。

1. 别改格式、别重试同一句话——图上标着 424 号，改的是"那张图"，不是"格式"。
2. 修磁盘上的文件**救不回**这条对话：坏字节在会话存档里，会被一直重发。
3. 处置：把已确认的产物（`refs/manifest.jsonl`、`NOTES.md`、`design/*.json`、产出目录）
   留在磁盘上 → **开新会话** → 新会话从 manifest / 记录继续，不要凭记忆重做。

## 验证记录（2026-10-16）

| 检查项 | 结果 |
|---|---|
| 真凶（截断 JPEG，90169B，缺 EOI） | `refguard check` 判 `BAD`；`probe` 复现上游原话 `input[0].image[0]: You have uploaded an unsupported image...` |
| 同一张图 `fix` 后 | 重编码为干净 PNG → 复检 `OK` → `probe` HTTP 200 |
| 完整 JPEG / PNG / 空文件 / HTML 错误页 / 扩展名不符 / `.tga` | 分别是 `OK / OK / BAD / BAD / BAD / BAD`，与预期一致 |
| `fetch_ref.py` 遇到只返回半截图的源 | 重试后 `FETCH FAIL`、退出码 1、正式路径上**没有**文件、坏文件只留 `<out>.rejected`（`--keep-rejected` 时） |
| 本次事故的那个 Commons 文件重下 | `FETCH OK` 1254x630 / 326397B，`probe` 200 |
