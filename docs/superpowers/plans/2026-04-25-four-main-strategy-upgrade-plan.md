# Four Main Strategies Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收敛主策略为 4 条（`trend_leader_unified`、`earnings_surprise`、`hundred_day_high`、`monthly_slow_rise`），并把高价值因子补强到这 4 条策略中。  

**Architecture:** 采用“先收敛策略分层，再按策略补因子，再统一集成收口”的三段式推进。`run_fast_review_bundle` 保持日常核心 3 条（`earnings/hundred_day_high/trend_leader`），`monthly_slow_rise` 作为低频扩展。其余策略不删文件，只降级到观察/专题层，不与主策略平级。  

**Tech Stack:** Python, pandas, pytest, 本地策略脚本（`scripts/select_*.py`）, 快复盘入口（`scripts/run_fast_review_bundle.py`）, 策略配置（`config/local_strategy_profile.json`）, 文档（`docs/*.md`）。  

---

## File Structure And Ownership

- 主入口与策略分层
  - Modify: `config/local_strategy_profile.json`
  - Modify: `scripts/run_fast_review_bundle.py`
  - Test: `tests/test_fast_review_daily_bundle.py`
- 业绩主策略
  - Modify: `scripts/select_earnings_surprise_candidates.py`
  - Modify: `scripts/collect_earnings_observation_snapshots.py`
  - Test: `tests/test_earnings_surprise_signal_flow.py`
  - Test: `tests/test_earnings_observation_signal_flow.py`
  - Docs: `docs/EARNINGS_SURPRISE_TRACKING.md`, `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`, `docs/EARNINGS_SURPRISE_PLAYBOOK.md`
- 百日新高策略
  - Modify: `scripts/select_hundred_day_high_candidates.py`
  - Test: `tests/test_hundred_day_high_signal_flow.py`
  - Docs: `docs/KLINE_SELECTOR_GUIDE.md`
- 趋势龙头策略
  - Modify: `src/services/trend_leader_strategy_service.py`
  - Modify: `scripts/select_trend_leader_candidates.py`
  - Test: `tests/test_trend_leader_strategy_service.py`, `tests/test_trend_leader_signal_flow.py`
  - Docs: `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
- 月线慢牛策略
  - Modify: `scripts/select_monthly_slow_rise_candidates.py`
  - Test: `tests/test_monthly_slow_rise_candidates.py`
  - Docs: `docs/MONTHLY_SLOW_RISE_SCAN.md`
- 集成收口（由集成人员执行）
  - Modify: `docs/LOCAL_STRATEGY_CATALOG.md`, `docs/LOCAL_STRATEGY_BASELINE.md`, `docs/AI_MODIFICATION_LOG.md`, `docs/CHANGELOG.md`
  - Optional: `config/local_strategy_profile.json` 最终默认参数再收口

## Task 1: 策略分层与默认日常收敛

**Files:**
- Modify: `scripts/run_fast_review_bundle.py`
- Modify: `config/local_strategy_profile.json`
- Test: `tests/test_fast_review_daily_bundle.py`

- [ ] Step 1: 固化默认主策略集合为 `earnings,hundred_day_high,trend_leader`
- [ ] Step 2: 保留 `continuous_up_ratio`、`continuous_up_streak` 可选执行能力，但从“主策略层”降级为观察输出（不参与主策略优先级说明）
- [ ] Step 3: 在 `tests/test_fast_review_daily_bundle.py` 补充默认 include 与排除逻辑断言
- [ ] Step 4: 运行 `python -m pytest tests/test_fast_review_daily_bundle.py -q`

**Acceptance:**
- 默认日常入口只跑 3 条主策略（不含 `monthly_slow_rise`）
- 连涨类策略可按需启用，且不会破坏现有 CLI 兼容

## Task 2: `earnings_surprise` 补强（PEAD + 多季度持续性 + 行业确认）

**Files:**
- Modify: `scripts/select_earnings_surprise_candidates.py`
- Modify: `scripts/collect_earnings_observation_snapshots.py`
- Test: `tests/test_earnings_surprise_signal_flow.py`
- Test: `tests/test_earnings_observation_signal_flow.py`
- Docs: `docs/EARNINGS_SURPRISE_TRACKING.md`, `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`

- [ ] Step 1: 明确 `event_reaction`（公告后 1D/3D 反应）与 `persistent_quality`（多季连续性）在总分中的权重与边界
- [ ] Step 2: 新增或增强“多季度 surprise history”字段（至少近 8~12 季统计摘要）并写入 `metrics_payload`
- [ ] Step 3: 保持硬门槛优先级（`blocked_quality_risk` 等）不被新因子绕过
- [ ] Step 4: 补 observation 链路字段透传，确保观察池可基于新字段做后续演化
- [ ] Step 5: 运行 `python -m pytest tests/test_earnings_surprise_signal_flow.py -q`
- [ ] Step 6: 运行 `python -m pytest tests/test_earnings_observation_signal_flow.py -q`

**Acceptance:**
- 业绩策略评分可解释输出包含：事件反应、多季度连续性、行业确认
- observation 链路不破坏现有生命周期语义

## Task 3: `hundred_day_high` 补强（行业动量 + 模板 + 假突破过滤）

**Files:**
- Modify: `scripts/select_hundred_day_high_candidates.py`
- Test: `tests/test_hundred_day_high_signal_flow.py`
- Docs: `docs/KLINE_SELECTOR_GUIDE.md`

- [ ] Step 1: 在 profile 规则中加入行业相对强度/板块确认字段（不改共享层接口）
- [ ] Step 2: 加入 Minervini 模板核心条件（`50/150/200MA`、距 52 周低点、距高点约束）
- [ ] Step 3: 加入突破质量过滤（突破前波动收缩 + 突破日量能确认）
- [ ] Step 4: 加入突破后 3~5 日跟随质量指标，降低假突破留存
- [ ] Step 5: 运行 `python -m pytest tests/test_hundred_day_high_signal_flow.py -q`

**Acceptance:**
- `breakout_balanced_with_earnings` 继续可用
- 新增质量过滤后，输出字段可解释且回测链路不报错

## Task 4: `trend_leader_unified` 补强（Stage-2 + 行业分位 + VCP/base quality + 过热惩罚）

**Files:**
- Modify: `src/services/trend_leader_strategy_service.py`
- Modify: `scripts/select_trend_leader_candidates.py`
- Test: `tests/test_trend_leader_strategy_service.py`
- Test: `tests/test_trend_leader_signal_flow.py`
- Test: `tests/test_trend_leader_daily_bundle.py`
- Docs: `docs/TREND_LEADER_UNIFIED_STRATEGY.md`

- [ ] Step 1: 在策略服务层新增显式 Stage-2 模板得分组件
- [ ] Step 2: 新增行业排名/板块广度/板块内强度分位组件（先用当前可得字段）
- [ ] Step 3: 新增 base quality/VCP 结构评分（底部收敛、回撤结构、突破健康度）
- [ ] Step 4: 新增过热惩罚（离均线过远、单周爆量、短期乖离）
- [ ] Step 5: 保持现有 earnings gate 负向拦截集合兼容
- [ ] Step 6: 运行 `python -m pytest tests/test_trend_leader_strategy_service.py tests/test_trend_leader_signal_flow.py -q`

**Acceptance:**
- 排序靠前样本同时具备趋势、行业、结构质量，不再过度偏“短期情绪冲高”

## Task 5: `monthly_slow_rise` 补强（周线压缩 + 业绩连续性 + 资金稳定）

**Files:**
- Modify: `scripts/select_monthly_slow_rise_candidates.py`
- Test: `tests/test_monthly_slow_rise_candidates.py`
- Docs: `docs/MONTHLY_SLOW_RISE_SCAN.md`

- [ ] Step 1: 增强周线级波动压缩指标（周线振幅、周线实体波动、收敛斜率）
- [ ] Step 2: 强化业绩连续性阈值（营收/利润/ROE/现金流至少 2~4 季一致）
- [ ] Step 3: 增强流动性和资金稳定性过滤（如 20 日成交额下限、流动性稳定分）
- [ ] Step 4: 保持 `strict/robust/balanced/loose` 四档语义稳定，只调整默认阈值与评分细节
- [ ] Step 5: 运行 `python -m pytest tests/test_monthly_slow_rise_candidates.py -q`

**Acceptance:**
- `monthly_slow_rise` 继续作为低频扩展策略，不进入默认每日
- 结果更偏“慢牛质量”，而非仅月线形态

## Task 6: 非主策略降级治理（不删文件）

**Files:**
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`（集成人员）
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`（集成人员）
- Optional: `scripts/run_fast_review_bundle.py` 文案层（若需）
- Test: `tests/test_fast_review_daily_bundle.py`（若改 CLI 文案）

- [ ] Step 1: 将 `continuous_up_ratio`、`continuous_up_streak` 标注为观察层（非主 alpha）
- [ ] Step 2: 将 `select_kline_candidates.py` 标注为底层工具层
- [ ] Step 3: 将 `theme_core_mapper`、`commodity_price_pass_through`、`dragon_head_candidate` 标注为专题层
- [ ] Step 4: 检查 `README` 是否必须同步；若不改，需在交付说明注明信息落点在专题文档

**Acceptance:**
- “主策略/观察层/专题层/工具层”四层口径一致

## Task 7: 集成收口与验证矩阵

**Files:**
- Modify: `docs/AI_MODIFICATION_LOG.md`（集成人员）
- Modify: `docs/CHANGELOG.md`（集成人员）
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`（集成人员）
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`（集成人员）

- [ ] Step 1: 执行四条策略最小验证测试集
- [ ] Step 2: 执行 `python -m py_compile` 覆盖变更脚本
- [ ] Step 3: 执行 `./scripts/ci_gate.sh`（若环境允许）
- [ ] Step 4: 汇总“改了什么/为什么/验证情况/未验证项/风险点/回滚方式/是否进共享层”
- [ ] Step 5: 只在集成人员阶段更新冻结文档与 changelog，避免并行冲突

**Command Matrix:**
- `python -m pytest tests/test_earnings_surprise_signal_flow.py -q`
- `python -m pytest tests/test_earnings_observation_signal_flow.py -q`
- `python -m pytest tests/test_hundred_day_high_signal_flow.py -q`
- `python -m pytest tests/test_trend_leader_signal_flow.py tests/test_trend_leader_strategy_service.py -q`
- `python -m pytest tests/test_monthly_slow_rise_candidates.py -q`
- `python -m pytest tests/test_fast_review_daily_bundle.py -q`

## Delivery Sequence

1. `earnings_surprise`
2. `monthly_slow_rise`
3. `hundred_day_high`
4. `trend_leader_unified`
5. 集成人员统一收口冻结文件与总文档

## Risks And Rollback

- 风险 1: 新评分组件导致候选分布漂移  
  - 回滚：按策略维度回退新增组件，保留字段但关闭权重
- 风险 2: 多策略并行改动引发字段命名分叉  
  - 回滚：以集成人员版本统一字段字典后再合入
- 风险 3: 默认入口口径与文档不一致  
  - 回滚：以 `config/local_strategy_profile.json` + `run_fast_review_bundle.py` 实际行为为准，立即回补文档
