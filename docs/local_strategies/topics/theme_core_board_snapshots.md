# 模块主题核心快照

## 1. 目标

这条脚本用于把“某个模块的成分股 + 模块内部主题核心股结果”按日期保存下来。

这样做的意义是：

- 不用每次都临时重新找模块成分股
- 可以观察同一个模块随时间的成分和核心股变化
- 后面如果要接定时任务，只需要周期性调用这个脚本

## 2. 脚本

脚本：

`scripts/collect_board_theme_core_snapshots.py`

默认输出目录：

`data/board_theme_core_snapshots/`

目录结构：

`data/board_theme_core_snapshots/<snapshot_date>/<board_name>/`

## 3. 常用命令

按日期保存 `CPO` 概念模块快照：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_board_theme_core_snapshots.py --board-name CPO --board-type concept --commodity-hint optical_fiber
```

指定快照日期：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_board_theme_core_snapshots.py --board-name CPO --board-type concept --commodity-hint optical_fiber --snapshot-date 2026-04-11
```

## 4. 输出内容

每次快照会保存：

- 模块成分股 CSV
- 模块内部主题核心候选股 CSV/TXT/MD
- `snapshot_manifest.json`

其中 `snapshot_manifest.json` 会记录：

- `board_name`
- `board_type`
- `commodity_hint`
- `snapshot_date`
- `universe_size`
- `selected_count`

## 5. 更新方式

当前版本先做成“按日期落盘”：

- 你可以手动周期运行
- 也可以后续接到现有 schedule

这一步先把快照结构固定下来，后面再决定是否接数据库或 `/signals`。

## 6. 接入 schedule

现在这条能力已经可以接到现有 `python main.py --schedule`。

对应配置项：

- `BOARD_THEME_CORE_SNAPSHOT_ENABLED`
- `BOARD_THEME_CORE_SNAPSHOT_TARGETS_JSON`

示例：

```env
BOARD_THEME_CORE_SNAPSHOT_ENABLED=true
BOARD_THEME_CORE_SNAPSHOT_TARGETS_JSON=[{"board_name":"CPO","board_type":"concept","commodity_hint":"optical_fiber"},{"board_name":"通信设备","board_type":"industry","commodity_hint":"optical_fiber","top_per_subtheme":2}]
```

含义：

- 每次 schedule 主分析跑完后
- 会依次刷新这些模块的成分股和模块内部主题核心结果
- 结果按日期保存到：
  - `data/board_theme_core_snapshots/<snapshot_date>/<board_name>/`
