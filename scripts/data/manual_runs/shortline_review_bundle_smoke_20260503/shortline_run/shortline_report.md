# Shortline Hub Report

- run_id: shortline_bundle_20260503
- trade_date: 2026-05-03
- top_n: 1

## 结果概览

- 候选数量: 1
- 候选来源分布: wondertrader_real_engine=1
- 板块分布: 机床制造=1
- 形态分布: 涨停后高换手分歧=1
- 短线类别分布: 涨停接力=1
- 复盘层级分布: 高风险异动=1
- 风险标签分布: volume_reconstructed_from_amount=1
- 综合分区间: 159.1 ~ 159.1

## FinGenius Explain Summary

- explanation_sources: upstream_tools=1
- upstream_tools: ChipAnalysisTool=1, HotMoneyTool=1
- bridge_elapsed_total: 36990ms
- upstream_tool_elapsed_totals: ChipAnalysisTool=26458ms, HotMoneyTool=8110ms
- orchestrator_elapsed_total: 37472ms

## 候选概览

- `300083 创世纪`: 涨停接力 / 机床制造 / 涨停后高换手分歧 / 综合分 159.1 / 板块前排#1 / 风险 volume_reconstructed_from_amount；短线先看 机床制造 能否继续扩散，再看 涨停后高换手分歧 是否持续得到承接；当前偏多信号有 底部单峰密集，筹码高度集中。

## 今日主方向

- 机床制造: 主票 300083 创世纪 / 涨停接力 / 综合分 159.1

## 历史跟踪摘要

- 暂无连续跟踪样本。

## 今日最强

- 暂无可直接列为今日最强的标的；当前候选全部归入高风险异动，先看下方风险说明。

## 观察名单

- 暂无 watchlist。

## 高风险异动

- `300083 创世纪`: 涨停接力 / 机床制造 / 涨停后高换手分歧 / 综合分 159.1 / 板块前排#1 / 风险 volume_reconstructed_from_amount；短线先看 机床制造 能否继续扩散，再看 涨停后高换手分歧 是否持续得到承接；当前偏多信号有 底部单峰密集，筹码高度集中。

## 明日观察点

- `300083 创世纪`: 先看 机床制造 是否延续，再看 涨停后高换手分歧 是否继续承接。

## 候选明细

| symbol | name | shortline_category | review_tier | board_name | setup_tag | composite_score | risk_flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 300083 | 创世纪 | 涨停接力 | 高风险异动 | 机床制造 | 涨停后高换手分歧 | 159.1 | volume_reconstructed_from_amount |

## 逐票说明

### 300083 创世纪
- 短线类别: 涨停接力 (同类第 1 名)
- 复盘层级: 高风险异动
- 板块核心: 第 1 名 / 加分 0.0
- 板块/形态: 机床制造 / 涨停后高换手分歧
- 候选来源: wondertrader_real_engine
- 综合分: 159.1
- explain_source: upstream_tools
- upstream_tools: HotMoneyTool, ChipAnalysisTool
- upstream_tool_elapsed: ChipAnalysisTool=26458ms, HotMoneyTool=8110ms
- explain_elapsed: 36990ms
- tool_errors: -
- 风险标签: volume_reconstructed_from_amount
- 市场指标: 价格 10.19 / 当日涨幅 20.02% / 60日涨幅 5.93% / 成交额 40.03亿 / 换手 26.82% / 量比 6.91
- 资金热度: 创世纪 的 FinGenius 资金侧显示：换手 26.82%。当前更贴近 机床制造 方向。
- 大单视角: 创世纪 未开启 BigDealAnalysisTool，先用 HotMoney/行情做代理大单强度：换手 26.82%；量比 6.91。当前资金仍围绕 机床制造 博弈。
- 筹码结构: 创世纪 的筹码分析显示：平均成本线 8.75；获利盘 0.99%；主力成本乖离率16.46%，控盘程度：极度控盘；90%集中度0.00%，70%集中度0.00%；偏多信号 底部单峰密集，筹码高度集中。
- 风险说明: 创世纪 当前主要风险在 volume_reconstructed_from_amount，若 机床制造 转弱，短线波动会明显放大。
- 短线观点: 短线先看 机床制造 能否继续扩散，再看 涨停后高换手分歧 是否持续得到承接；当前偏多信号有 底部单峰密集，筹码高度集中。

候选来源字段 `scan_source` 用来区分候选来自本地桥接数据、真实引擎扫描还是缓存兜底；需要排查桥接状态时，可配合 `bridge_data` 目录和 `scripts/check_shortline_bridge_setup.py` 一起看。
风险说明未单独列出时，默认表示“当前未见明显额外风险提示”。
