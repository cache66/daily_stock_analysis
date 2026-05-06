# 模块内主题核心扫描

## 1. 目标

这条能力用于：

- 先拿某个模块/板块的全部成分股
- 再在这个模块内部跑 `theme_core_mapper`
- 最后按 `子主题` 自动筛出更像核心股的股票

它适合回答：

- 这个模块里的票，到底分成哪些真子主题
- 每个子主题里最核心的是谁

## 2. 脚本

脚本：

`scripts/select_board_theme_core_candidates.py`

默认输出目录：

`data/board_theme_core_candidates/`

## 3. 常用命令

按 `CPO` 概念板块分析成分股：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_board_theme_core_candidates.py --board-name CPO --board-type concept --commodity-hint optical_fiber
```

按行业板块分析：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_board_theme_core_candidates.py --board-name 通信设备 --board-type industry --commodity-hint optical_fiber
```

每个子主题保留前 2 只：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_board_theme_core_candidates.py --board-name CPO --board-type concept --commodity-hint optical_fiber --top-per-subtheme 2
```

## 4. 输出内容

会同时输出：

- 模块成分股列表
- 子主题核心候选股列表

重点字段：

- `theme_key`
- `subtheme_key`
- `stock_role`
- `subtheme_core_probability`
- `leader_type`
- `leader_probability`

## 5. 使用建议

如果你已经知道模块名称，这条链路通常比全市场扫描更适合：

1. 先锁定模块
2. 拉模块成分股
3. 在模块内部拆子主题
4. 每个子主题只保留最核心的 1-2 只票

这样会比直接从整个大板块里凭感觉挑票更清楚。
