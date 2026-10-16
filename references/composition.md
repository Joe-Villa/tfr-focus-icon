# 元素拼贴模式（更像原版，也更费 API）

单张直出（`make_icon.py`）适合快速出图：一次生成就把主体+旗帜+军械画在同一张照片里。
但它有个天花板——**画面会是"一张照片"，缺少 TFR 那种"多个贴片叠压"的分层感**，
而且元素之间的透明缝隙填不满（客观指标 `tight` 会停在 0.5 左右，原版是 0.68）。

要更像原版，用元素模式：**每个元素单独生成、单独抠图，再按布局叠起来**。

```bash
python3 scripts/compose_icon.py --example > job_elements.json
python3 scripts/compose_icon.py --design job_elements.json --out out_steel --variants 2
```

## design JSON

```json
{
  "tag": "PRC",
  "slug": "PRC_steel_five_years",
  "layout": "hero_right",
  "elements": [
    "a Chinese steelworker in overalls and a hard hat gripping a hammer, chest-up, harsh side light",
    "a giant blast furnace erupting with sparks and molten steel",
    "a red hanging banner with a golden gear-and-wheat emblem"
  ],
  "slogan": {"text": "钢铁五年", "position": "bottom", "size_ratio": 0.3},
  "variants": 2
}
```

- `elements[0]` 会被当作主元素（面积最大者优先占据主位），写**一个**主体，别写两个同等重要的。
- 每个元素都会被自动包上游戏美术框架句 + 纯洋红背景 + "无地面无阴影"的尾巴，
  并走同一套审核重试梯子（`compose_icon.element_prompt`）。

## 布局

`compose.py` 提供三种，够用：

| layout | 效果 | 什么时候用 |
|---|---|---|
| `hero_right`（默认） | 主元素占右侧一大块，元素按左上→左下→右上错落 | 人像 + 旗帜/徽记类，最稳 |
| `auto` | 同上但加 5% 随机抖动，每个 variant 位置不同 | 想要几张不同的排版 |
| `diagonal` | 元素沿对角线依次错开、互相压 30% | 想强调纵深/冲刺感 |

想精确控制就自己写 `Element(img, box=(x0,y0,x1,y1), z=…)`（box 是 0-1 归一化坐标，
`z` 小的先画、在后面），直接调 `compose.compose((W,H), elements)`。

## 排布的硬规矩（踩过的坑）

1. **必须重叠 20-40%**。不重叠就变成"漂浮贴纸"，画面上到处是透明缝，
   `metrics.py` 里 `tight` 会掉到 0.4 以下。
2. **主元素要够大**（占画布高度 ≥ 80%），支撑元素小一圈。
3. **别让元素被画布切掉**：`box` 上限取 1.0，渲染完再 `hug_canvas` 裁到 150px。
4. 半身人像抠图的 alpha 常接近一整块矩形，直接加投影会在人物背后糊出一个深色矩形——
   要么给元素开 `shadow=False`，要么先把 alpha 收紧（`iconify.solidify`）。
5. 每个元素都走 `iconify.solidify` 再拼；拼完整体再做一次描边/发光/调色。

## 验收

拼完一定跑一次客观指标 + 自己看图：

```bash
python3 scripts/metrics.py out_steel/final_v*.png
```

看三件事：`tight` 是否接近 0.6+、`comp` 是否在 100 以内、`cov` 是否 ≥ 0.6。
不达标优先"把元素再压紧一点/放大主体"，而不是继续加元素。
