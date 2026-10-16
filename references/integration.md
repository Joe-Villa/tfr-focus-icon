# 把 icon 接进 HOI4 mod

实测对象：参考实现用的是 TFR 主 mod `interface/TFR_goals.gfx`（6034 张 icon 全是显式 `SpriteType`
引用，不是 HOI4 那套"文件名自动生成精灵"的写法），另参考了 `interface/PRC_Gundam.gfx` 等子 mod 文件。
换成任何别的 mod，只要按下面三个改动点做就行。

## 三个改动点

| # | 改哪里 | 内容 |
|---|---|---|
| 1 | `gfx/interface/goals/<TAG>/<slug>.png` | 生成的 PNG 原样拷进去（RGBA、高 ~150、宽 140-208） |
| 2 | `interface/zz_custom_icons.gfx`（**自己新建的独立文件**） | `SpriteType{ name = "GFX_focus_<slug>" texturefile = "gfx/interface/goals/<TAG>/<slug>.png" }` |
| 3 | `common/national_focus/*.txt` | 在那个国策块里加 `icon = GFX_focus_<slug>` |

可选：`localisation/simp_chinese/*.yml` 加 ` <focus_id>:0 "国策名"`，` <focus_id>_desc:0 "..."`。

**为什么用独立的 gfx 文件名**：mod 本体更新时会覆盖它自己那份 `interface/<mod>_goals.gfx`。
写进自己的 `zz_custom_icons.gfx` 就不会被冲掉（`zz_` 前缀保证加载顺序靠后）。

## 自动化

```bash
python3 scripts/install_to_mod.py out/final_x.png \
    --mod "/path/to/your_mod" --tag PRC --slug PRC_steel_five_years \
    --loc "钢铁五年" --dry-run     # 先看它要做什么，确认后去掉 --dry-run
```

它做三件事：校验 PNG（RGBA / 透明占比 / 尺寸）→ 拷贝到 `gfx/interface/goals/<TAG>/` →
在 `interface/<gfx 文件>` 里追加或跳过同名 `SpriteType`，最后打印你要手写的 `icon = …` 与本地化行。
重复调用是幂等的（同名 sprite 跳过）。

## 结果长这样

`<mod>/interface/zz_custom_icons.gfx`：

```txt
spriteTypes = {
	SpriteType = {
		name = "GFX_focus_PRC_steel_five_years"
		texturefile = "gfx/interface/goals/PRC/PRC_steel_five_years.png"
	}
}
```

`<mod>/common/national_focus/prc.txt` 里对应的国策：

```txt
focus = {
	id = PRC_steel_five_years
	icon = GFX_focus_PRC_steel_five_years
	x = 3
	y = 2
	cost = 8
	...
}
```

## 校验清单

1. PNG 是 **RGBA**（无 alpha 的图在游戏里会是黑底方块）。
2. 高 140-150、宽 140-208；`install_to_mod.py` 会对偏离值给警告。
3. `interface/*.gfx` 的 `spriteTypes = { … }` 括号闭合；每条 `SpriteType` 的 `name` 唯一。
4. HOI4 的 gfx 路径**不区分大小写**，但 `gfx/interface/goals/<TAG>/` 的目录名要和国策 TAG 对应，便于维护。
5. 改完在启动器里勾上新 mod，进游戏开控制台 `focus.autocomplete`，或直接看国策树。

## 已知边界

- 不要直接写进 `~/.steam/.../3350890356` 之类的 workshop 目录——更新会覆盖，而且会污染原 mod。
  用独立的子 mod（`descriptor.mod` 里 `dependencies = { "The Fire Rises" }`）。
- TFR 有 `TFR_goals_shine.gfx`（"Mod Shines"）给特殊国策做闪光效果；普通新 icon 不需要它。
