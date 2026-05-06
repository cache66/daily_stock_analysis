# 板块周期扫描设计

## 1. 背景

当前仓库已经有多类“个股视角”的本地策略与专题能力：

- `trend_leader_unified`：偏强势龙头筛选
- `monthly_slow_rise`：偏中线结构与行业确认
- `earnings_surprise`：偏业绩触发
- `board_recognizability`：偏板块内辨识度前排
- `theme_core_mapper` / `board_theme_core`：偏主题与子主题核心拆解
- `commodity_beneficiary`：偏主题链条和业绩释放验证

但用户新增的场景不是继续看单只股票，而是希望回答这类问题：

- 哪些板块整体在变好
- 一个板块内部谁是龙头，谁只是跟涨
- 一个板块里哪些股票不仅走势强，还有业绩支撑
- 同一只股票在多个板块里时，应该把它重点归到哪里

这说明当前缺的是一个“板块视角”的组合层，而不是再新增一个完全独立的个股策略。

## 2. 目标

首版目标：

1. 提供一个可单独运行的 `board_cycle_scan`，用于批量分析指定板块，而不是挂到每日默认复盘。
2. 输出板块层结论：
   - 板块是否整体转强
   - 板块内部是否有足够的前排强股
   - 板块内业绩支撑是否足够
3. 输出个股层结论：
   - 哪些股票可视为该板块龙头
   - 哪些股票属于“强势 + 业绩支撑”
   - 哪些股票仅作观察
4. 同一只股票允许属于多个板块，但最终给出 `primary_board` 和 `related_boards`，避免结果重复膨胀。
5. 尽量复用现有板块、主题、业绩、强势、原因标签等接口和字段，不新造平行系统。

## 3. 非目标

首版不做：

- 不做“每日自动扫描全市场所有变好的板块”
- 不做新的数据库表或新的独立快照协议
- 不强行把首版接入 `/signals` 页面
- 不以“主营收入占比”作为主归属唯一依据
- 不新增一套与 `trend_leader_unified`、`theme_core_mapper`、`commodity_beneficiary` 平行的独立分析体系
- 不处理无限泛化的“任何自定义板块口径”；首版先支持官方 `industry/concept board`，并保留后续接主题池的接口

## 4. 可复用资产

首版优先复用以下现有能力：

### 4.1 板块成分股获取

- `DataFetcherManager.get_board_constituents(board_name, board_type=...)`
- 现有 `scripts/select_board_theme_core_candidates.py` 已验证“先取板块成分股，再在板块内分析”的入口模式可行

### 4.2 个股强势与结构字段

- `trend_leader_unified`
- `hundred_day_high`
- `dragon_head_candidate`
- 共享字段：
  - `recognizability_score`
  - `sector_leadership_score`
  - `industry_strength_*`
  - `reason_summary`
  - `cause_tags`

### 4.3 业绩与质量验证

- `SharedSignalFactorsService.build_quality_overlay_factors(...)`
- `SharedSignalFactorsService.build_industry_strength_factors(...)`
- 现有 `earnings_surprise` 相关字段

### 4.4 主题映射与链条识别

- `theme_core_mapper`
- `board_theme_core`
- `commodity_pass_through_service`

它们可用于补充：

- 当前股票在板块中的主题角色
- 当前上涨逻辑和板块的匹配度
- 是否存在“业绩释放概率”或链条受益逻辑

## 5. 推荐方案

推荐采用“先判板块周期，再选板块龙头和业绩支撑股”的方案。

原因：

1. 这是最接近用户描述“板块版 trend_leader”的形态。
2. 它先回答“板块是不是整体在变好”，而不只是“板块里有哪些业绩股”。
3. 它可以自然复用现有个股级因子，而不需要重造新的底层分析逻辑。
4. 后续若要扩展到主题池、自定义专题池，只需要替换“板块成分股提供器”，不需要推翻评分框架。

## 6. 首版整体结构

首版分为四层：

1. `universe layer`
   - 获取待分析板块及其成分股
2. `stock factor layer`
   - 为板块内个股补齐强势、业绩、逻辑匹配等因子
3. `board scoring layer`
   - 汇总个股因子，计算板块周期分
4. `board output layer`
   - 生成板块总结和板块内重点股结果

建议新增一个独立脚本：

- `scripts/select_board_cycle_candidates.py`

建议新增一个板块周期服务：

- `src/services/board_cycle_scan_service.py`

职责划分：

- 脚本层：
  - 参数解析
  - 输入板块列表
  - 调用服务
  - 输出 CSV / Markdown
- 服务层：
  - 板块成分股加载
  - 个股因子汇总
  - 板块评分
  - 主归属判定
  - 输出结构组装

## 7. 输入与运行方式

建议脚本输入：

```bash
python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒
```

扩展参数建议：

- `--boards`
  - 逗号分隔板块名，必填
- `--board-type`
  - `auto | industry | concept`
- `--snapshot-date`
  - 可选，便于复盘指定日期
- `--top-per-board`
  - 每个板块保留多少重点股，默认 `3`
- `--limit-per-board`
  - 调试时限制每个板块成分股数量
- `--max-workers`
  - 并发数
- `--minimum-board-score`
  - 板块最低保留分，默认可不启用

首版支持“手工指定若干板块一次跑完”，不进入默认每日流程。

## 8. 个股因子层设计

每只股票在某个板块上下文里生成一份 `board_stock_evaluation`。

### 8.1 强势与龙头因子

优先复用或推导：

- `recognizability_score`
- `sector_leadership_score`
- `industry_strength_score`
- `leader_probability` 或近似龙头字段
- 近期是否进入：
  - `trend_leader_unified`
  - `hundred_day_high`
  - `earnings_surprise`

这些字段用于判断该股是不是板块内前排。

### 8.2 业绩支撑因子

优先复用：

- `earnings_validation_status`
- `earnings_release_probability`
- `quality_overlay_score`
- `earnings_continuity_score`
- `industry_strength_confirmed`

其中：

- `earnings_validation_status` 用于判断是否已有明确业绩验证
- `earnings_release_probability` 用于判断是否存在“业绩释放预期”
- `quality_overlay_score` 用于判断基本面连续性

### 8.3 逻辑匹配因子

优先复用：

- `reason_summary`
- `cause_tags`
- `industry_strength_label`
- `theme_core_mapper` 输出的 `theme_key / subtheme_key / stock_role`
- 需要时复用 `commodity_pass_through_service` 的链条角色和释放概率字段

用途：

- 判断当前上涨原因和板块逻辑是否一致
- 判断该股是板块主线受益，还是弱关联跟风

### 8.4 业务相关性因子

首版不把主营占比做主规则，但保留一个辅助因子：

- `business_relevance_score`

它用于表示“该公司和该板块的业务相关性大致高低”。

首版来源建议：

1. 板块名与 `industry_strength_label`、`theme_key`、`subtheme_key` 的语义匹配
2. `commodity_pass_through_service` / `theme_core_mapper` 对业务角色的识别
3. 若未来仓库已有稳定的主营构成结构化数据，再补充进入该因子

结论：

- 主归属不能只看主营占比
- 主营/业务相关性只作为综合判定中的辅助输入

## 9. 板块层评分设计

首版只保留四类板块分，避免过度复杂。

### 9.1 `breadth_score`

衡量板块整体是否真的在转强，而不是只有一只股票上涨。

建议输入：

- 成分股中进入强势观察范围的数量
- 强势股占比
- 板块内是否存在多只同向个股

### 9.2 `leadership_score`

衡量板块里是否有足够明确的前排带队股票。

建议输入：

- 板块前 3 名股票的综合强度均值
- 是否存在明显第一梯队
- 龙头分布是否过度集中到单一股票

### 9.3 `earnings_support_score`

衡量该板块的上涨是否有一定基本面支撑，而不是纯题材脉冲。

建议输入：

- 板块重点股中 `earnings_validation_status` 为正向的数量
- `earnings_release_probability >= medium` 的数量或比例
- `quality_overlay_score` 的均值或中位数

### 9.4 `structure_score`

衡量板块逻辑是否足够清晰。

建议输入：

- 板块内股票的主题集中度
- `industry_strength_confirmed`
- 当前上涨原因标签是否相对一致
- 是否存在过多弱关联股票造成噪音

### 9.5 `board_cycle_score`

最终板块总分由上述四类分合成：

`board_cycle_score = breadth + leadership + earnings_support + structure`

首版不强制公开精细权重，只需要固定实现并在文档中说明构成来源。

最终板块状态建议分为：

- `strengthening`
- `observing`
- `weak`

## 10. 个股层评分与角色设计

每只股票在某个板块下生成 `board_stock_score` 和角色标签。

### 10.1 个股总分构成

建议按四部分合成：

- `board_leader_score`
  - 看是否处于板块前排
- `earnings_support_score`
  - 看是否具备业绩或质量支撑
- `logic_match_score`
  - 看上涨逻辑是否与板块主线一致
- `business_relevance_score`
  - 看业务相关性是否足够强

### 10.2 个股角色

首版只定义三类，避免标签泛滥：

- `leader`
  - 板块前排强股，且逻辑匹配度高
- `earnings_supported`
  - 走势不错，且业绩/质量支撑明确
- `watch`
  - 逻辑相关，但还不足以归为前两类

同一只股票可以同时具备多个正向特征，但最终输出时只保留一个主角色：

- 优先 `leader`
- 再 `earnings_supported`
- 最后 `watch`

## 11. 一股多板块处理

这是首版必须显式定义的规则。

### 11.1 板块层

同一只股票允许同时计入多个板块。

原因：

- 现实市场中，一只股票本来就可能同时属于多个概念或行业
- 如果强行一股一板块，会丢失板块联动信息

### 11.2 个股展示层

同一只股票最终只在一个板块里“重点出现”，其他板块作为关联说明保留。

建议输出字段：

- `primary_board`
- `related_boards`
- `primary_board_reason`

### 11.3 `primary_board` 判定优先级

按以下顺序判定：

1. 当前行情逻辑与哪个板块最一致
2. 该股在那个板块里的排名是否更靠前
3. 该板块内该股的 `board_stock_score` 是否更高
4. `business_relevance_score` 是否更高
5. 若仍打平，再用稳定兜底规则：
   - 行业板块优先于概念板块，或
   - 板块名称字典序/输入顺序兜底

这样可以兼顾：

- 不丢失多板块事实
- 重点榜单不重复膨胀
- 判定逻辑可重复、可测试

## 12. 输出设计

建议输出目录：

- `data/board_cycle_scan/<date>/`

首版输出文件：

- `board_summary.csv`
- `board_summary.md`
- `board_stock_candidates.csv`
- `board_stock_candidates.md`
- `run_summary.txt`

### 12.1 板块汇总表

建议字段：

- `board_name`
- `board_type`
- `constituent_count`
- `qualified_stock_count`
- `leader_count`
- `earnings_supported_count`
- `breadth_score`
- `leadership_score`
- `earnings_support_score`
- `structure_score`
- `board_cycle_score`
- `board_cycle_label`
- `top_leaders`
- `top_earnings_supported`

### 12.2 个股明细表

建议字段：

- `board_name`
- `board_type`
- `code`
- `name`
- `board_rank`
- `board_stock_score`
- `stock_role`
- `primary_board`
- `related_boards`
- `primary_board_reason`
- `board_leader_score`
- `earnings_support_score`
- `logic_match_score`
- `business_relevance_score`
- `recognizability_score`
- `sector_leadership_score`
- `earnings_validation_status`
- `earnings_release_probability`
- `quality_overlay_score`
- `reason_summary`
- `cause_tags`

Markdown 输出原则：

- `board_summary.md` 先看板块，再看每个板块的龙头与业绩支撑股
- `board_stock_candidates.md` 保持结构化，便于人工复盘

## 13. 数据流

建议处理流程：

1. 解析用户输入的板块列表
2. 逐个板块获取成分股
3. 对成分股逐只补齐个股强势、业绩、逻辑匹配等因子
4. 在板块内计算 `board_stock_score` 与 `stock_role`
5. 聚合板块层四类分数，得到 `board_cycle_score`
6. 对多板块重复股票执行 `primary_board` 判定
7. 输出板块汇总和个股明细

## 14. 失败处理与降级

必须保持 fail-open。

规则：

1. 单个板块拉取失败，不中断整次运行；记录 warning，跳过该板块
2. 单只股票缺少业绩数据，不剔除；标记为 `earnings_unavailable`
3. 逻辑匹配因子不足时，不中断；仅降低 `logic_match_score`
4. 无法确定主归属时，使用稳定兜底规则
5. 首版不要求所有个股都拿到完整的主题链条判断

目标是保证该能力先可用、可复盘，再逐步加深精度。

## 15. 测试与验证

首版至少覆盖以下验证：

### 15.1 单元测试

- 板块层评分能按输入因子稳定生成标签
- 个股角色判定正确
- 同一只股票在多个板块中能稳定判定 `primary_board`
- 缺失业绩字段时仍能正常输出结果

### 15.2 脚本级 smoke

建议先验证：

- `锂矿`
- `白酒`

检查点：

- 板块层排序是否符合直觉
- 板块内前排股票是否合理
- 业绩支撑标签是否有明显误判
- 同一只重复股票是否只在一个主板块中重点出现

## 16. 实施边界

首版预计会涉及：

- `scripts/select_board_cycle_candidates.py`
- `src/services/board_cycle_scan_service.py`
- 对少量现有共享服务的复用接入
- 对应测试文件
- 对应本地策略文档与留痕文档

首版暂不要求：

- 接入每日默认复盘
- 接入 Web `/signals`
- 落库为新的日度快照类型

## 17. 后续演进

若首版验证有效，后续可按下面路径扩展：

1. 将“板块成分股提供器”从官方板块扩展到主题池/专题池
2. 引入历史对比，识别“板块正在转强”而不是只看静态当日结果
3. 视需要接入快照落库和 `/signals` 展示
4. 视需要和每日复盘做轻量联动，但仍保持独立运行入口

## 18. 结论

首版应做成一个独立的“板块周期扫描器”，本质是“板块版 trend_leader”：

- 先判断板块整体是否转好
- 再在板块里找龙头和业绩支撑股
- 对一股多板块显式给出 `primary_board + related_boards`
- 复用现有板块、主题、业绩、强势、原因标签能力

这样既能满足“分析锂矿、白酒这类板块”的实际使用场景，也不会继续把仓库推进到更多分散、重叠的策略入口。
