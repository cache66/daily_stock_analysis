# 主题核心候选池扫描

## 1. 目标

这个脚本用于把一批股票按：

- 大主题
- 真子主题
- 个股角色

自动分组，并直接找出每个子主题里更像核心股的票。

它适合解决：

- 同一个大方向里，哪些不是同一条交易逻辑
- 每个子主题里最值得重点看的 1-2 只票是谁

## 2. 脚本

脚本：

`scripts/select_theme_core_candidates.py`

默认输出目录：

`data/theme_core_candidates/`

## 3. 常用命令

扫描光通信相关主题里，按子主题找核心股：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_theme_core_candidates.py --commodity-hint optical_fiber
```

只扫前 100 只票做调试：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_theme_core_candidates.py --commodity-hint optical_fiber --limit 100
```

每个子主题保留前 2 只：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_theme_core_candidates.py --commodity-hint optical_fiber --top-per-subtheme 2
```

## 4. 输出重点

重点字段包括：

- `theme_key`
- `subtheme_key`
- `stock_role`
- `core_driver_type`
- `subtheme_core_probability`
- `theme_core_probability`
- `leader_type`
- `leader_probability`

你可以把它理解成：

- `theme_key`
  - 这只票属于哪个大方向
- `subtheme_key`
  - 它到底属于哪个真子主题
- `stock_role`
  - 它在这个子主题里扮演什么角色
- `subtheme_core_probability`
  - 它是不是这个子主题里的核心股

## 5. 使用建议

最适合的流程是：

1. 先用这个脚本把每个子主题的核心候选找出来
2. 再对少数重点票单独跑：
   - `theme_core_mapper`
   - `commodity_price_pass_through`
   - `dragon_head`

这样比直接从大板块里凭感觉挑票更清楚。
