# K 线处理记录

## 目的

这份文档用于沉淀 K 线筛选逻辑的当前状态、实际运行环境以及每次处理后的记录，方便下次直接接手，不必重复排查。

## 当前状态摘要

- 当前最完整、最新的一条链路是独立百日新高入口：`scripts/select_hundred_day_high_candidates.py`
- 这条链路现在已经能完成：
  - 筛选百日新高（可调窗口，如 `20/100/120` 日）
  - 按日落库到 `KlineSignalSnapshot`
  - 回看近 `180` 天同口径历史复现
  - 生成归因摘要：`reason_summary`
  - 生成结构化三段逻辑：
    - `industry_logic`
    - `news_logic`
    - `technical_logic`
  - 生成较保守的 `theme_label` 主题级海外映射
- 当前已验证稳定的部分：
  - 本地回归测试通过
  - CLI 参数、导出文件、零命中场景可用
  - 真实在线命中样本已验证可跑通
- 当前仍存在但不阻塞的外部问题：
  - `SearXNG` 公共实例偶发超时
  - `deepseek` 偶发空响应
  - 上述问题目前都会走 fail-open，不会打断百日新高主链路
- 下次如果要继续做，优先顺序建议：
  1. `src/services/signal_cause_analysis_service.py`
  2. `scripts/select_hundred_day_high_candidates.py`
  3. `tests/test_signal_cause_analysis_service.py`

## 当前基线（2026-03-30）

- 当前代码工作区：`D:\bb\daily_stock_analysis`
- 当前可用运行环境：`E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe`
- 运行环境 Python 版本：`3.10.3`
- `E:\Apps\daily_stock_analysis` 当前仅发现 `.venv`，未发现独立 Git 仓库；后续默认以 `D:\bb\daily_stock_analysis` 作为代码根目录，以 `E:\Apps\daily_stock_analysis\.venv` 作为执行环境。

## 上次处理到的 K 线逻辑范围

当前 K 线处理不是主分析链路的一部分，而是一个独立筛选器，核心文件如下：

- `src/services/kline_selector_service.py`
  - 独立 K 线筛选服务
  - 负责股票池加载、预过滤、历史 K 线抓取、规则判定、checkpoint 续跑
- `scripts/select_kline_candidates.py`
  - CLI 入口
  - 负责参数解析、运行服务、导出 `csv/txt/md`
- `tests/test_kline_selector_service.py`
  - 覆盖股票池过滤、规则命中、预过滤、并发、checkpoint / resume
- `docs/KLINE_SELECTOR_GUIDE.md`
  - 面向使用者的功能说明

### 当前处理链路

1. `get_a_share_universe()`
   - 优先通过现货/股票列表接口获取 A 股全集
   - 标准化为 `code/name/total_mv/...`
   - 自动排除北交所
   - 去重后按代码排序

2. `scan_market()`
   - 先按总市值做廉价预过滤
   - 再按 `KlineSelectorPrefilter` 做现货级预过滤
   - 支持 `checkpoint` 落盘和 `--resume` 续跑
   - 支持串行或 `ThreadPoolExecutor` 并发评估

3. `evaluate_stock()`
   - 使用精简版 `DataFetcherManager`
   - 默认走 `build_fast_a_share_manager()`，即 `Akshare` 单链路抓历史 K 线
   - 将历史数据标准化，补出 `prev_close`
   - 逐条执行规则，汇总 `metrics`、失败原因和命中结果

4. 默认规则集合 `build_rules()`
   - `UpDayRatioRule`
   - `MaxMarketCapRule`
   - `RecentLimitUpRule`
   - `HundredDayHighRule`

5. `export_results()`
   - 输出 `data/a_share_universe_no_bse.txt`
   - 输出 `data/kline_selector_candidates.csv`
   - 输出 `data/kline_selector_candidates.txt`
   - 输出 `data/kline_selector_candidates.md`

### 当前规则口径

- 最近 `10` 个交易日上涨占比 `>` `70%`
- 最近 `10` 个交易日至少出现 `1` 次涨停
- 最新一根 K 线的 `high` 创最近 `100` 日新高
- 总市值 `<= 500` 亿
- 需要的历史数据长度：`max(new_high_window + lookback_days + 5, 120)`

### 当前默认运行口径

- `max_workers=1`
- 默认启用现货预过滤
- 默认现货预过滤包含：`60日涨跌幅 >= 10`
- `checkpoint_path=data/kline_selector_checkpoint.json`
- `checkpoint_every=50`

## E:\Apps 运行方式

后续继续处理时，优先在仓库根目录 `D:\bb\daily_stock_analysis` 下使用 `E:\Apps` 的虚拟环境运行：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --help
```

小范围试跑：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --limit 100 --log-level DEBUG
```

断点续跑：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --resume
```

定向验证：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m py_compile src\services\kline_selector_service.py scripts\select_kline_candidates.py tests\test_kline_selector_service.py
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_kline_selector_service.py
```

## 本次分析结论

- 当前 K 线逻辑是“独立筛选器”，没有直接侵入 `main.py` 主分析流程。
- 性能策略以“尽快扫完整个 A 股市场”为优先，因此历史 K 线抓取默认采用 `Akshare` 单链路，而不是多数据源 fallback。
- 稳定性策略主要靠三层兜底：
  - 股票池加载有 provider fallback
  - 扫描前有市值和现货级预过滤，减少长历史抓取量
  - 长任务支持 checkpoint / `--resume`
- 本地工作区里，K 线相关文件当前是未提交状态；下次处理前，先确认是否已经提交、是否已同步到真正运行目录，避免“文档有、代码没部署”。

## 本次验证情况

已在 `D:\bb\daily_stock_analysis` 下使用 `E:\Apps\daily_stock_analysis\.venv` 验证：

- `python --version` -> `Python 3.10.3`
- `python -m py_compile src\services\kline_selector_service.py scripts\select_kline_candidates.py tests\test_kline_selector_service.py`
- `python scripts\select_kline_candidates.py --help`
- `python -m pytest tests\test_kline_selector_service.py`
  - 结果：`8 passed`

## 下次继续处理前的检查清单

1. 先确认代码根目录是不是仍然在 `D:\bb\daily_stock_analysis`。
2. 再确认运行环境是否仍然使用 `E:\Apps\daily_stock_analysis\.venv`。
3. 先看 `git status --short`，确认 K 线相关文件是否已经提交。
4. 若调整了规则、参数或输出结构，同步更新 `docs/KLINE_SELECTOR_GUIDE.md`。
5. 若变更已经影响 README 展示、CLI 行为或用户可见能力，再评估是否同步 `README.md` 和 `docs/CHANGELOG.md`。

## 处理记录

| 日期 | 类型 | 范围 | 环境 | 验证 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 2026-03-30 | 分析 / 文档沉淀 | 独立 K 线筛选器：`kline_selector_service.py`、`select_kline_candidates.py`、`test_kline_selector_service.py` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `py_compile` 通过；`--help` 可运行；`pytest tests/test_kline_selector_service.py` 为 `8 passed` | 已确认当前逻辑边界、默认规则、运行方式和续跑机制，并建立后续追加记录模板 |
| 2026-03-30 | 验证 / 小范围试跑 | `scripts/select_kline_candidates.py --limit 20 --max-workers 1` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `py_compile` 通过；`pytest tests/test_kline_selector_service.py` 为 `8 passed`；真实试跑在 5 分钟内未完成，最终超时；残留 Python 进程已清理 | 已确认单测和 CLI 帮助正常，但真实市场数据路径存在耗时/阻塞现象；优先排查股票池抓取阶段缺少超时控制或上游接口响应慢 |
| 2026-03-30 | 验证 / 股票池 fallback 定位 | `KlineSelectorService.get_a_share_universe(limit=5)` 与 Akshare 直接调用 | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `ak.stock_info_a_code_name()` 约 10.63 秒返回 `5494` 行；`ak.stock_zh_a_spot_em()` 报 `RemoteDisconnected`；服务层 fallback 最终可返回结果，但前 5 条包含“上证综合指数”等指数项 | 已确认真实问题不只是慢，还有股票池质量风险：当前 fallback 可能引入指数代码，后续应优先收紧股票池来源或补充“仅股票”过滤 |
| 2026-03-30 | 修复 / 股票池稳态修正 | `src/services/kline_selector_service.py`、`data_provider/baostock_fetcher.py`、`tests/test_kline_selector_service.py` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `py_compile` 通过；`pytest tests/test_kline_selector_service.py` 为 `9 passed`；`get_a_share_universe(limit=5)` 已返回个股；CLI 试跑 `--limit 5` 完整跑通并生成产物 | 已将股票池 fallback 顺序调整为优先 `akshare_code_name` 再退到 `baostock`，同时增加指数/ETF 排除与 Baostock `type/status` 过滤，修复了“混入指数”和“spot 失败后退化为脏股票池”的问题 |
| 2026-03-30 | 修复 / Akshare 低延迟路径 | `data_provider/akshare_fetcher.py`、`src/services/kline_selector_service.py`、`tests/test_kline_fast_manager.py` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `py_compile` 通过；`pytest tests/test_kline_fast_manager.py tests/test_kline_selector_service.py` 为 `11 passed`；CLI 试跑 `--limit 5 --max-workers 1` 完整跑通，总耗时 `28.89s`，相比前一次约 `103.40s` 明显下降 | 已为 K 线筛选专用 fast manager 启用低延迟 Akshare 配置：关闭默认随机 sleep，普通 A 股历史 K 线优先走 `sina -> tencent -> em`；当前单股历史 K 线约 `0.84s ~ 1.39s`，实时行情腾讯单股约 `0.07s ~ 0.09s`，新瓶颈主要转移到股票池加载阶段 |
| 2026-03-30 | 文档 / 操作说明整理 | `docs/KLINE_SELECTOR_GUIDE.md` | 代码目录 `D:\bb\daily_stock_analysis` | 已核对脚本入口、常用参数、输出文件名、当前性能数据和 `E:\Apps` 运行方式 | 已将 K 线说明文档整理为可直接使用的操作说明，便于后续直接按命令执行，不必再从处理日志反推运行方法 |
| 2026-03-30 | 运行 / 100 只股票试跑 | `scripts/select_kline_candidates.py --limit 100` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `--max-workers 4` 在历史 K 线阶段触发 `py_mini_racer` 原生崩溃；改为 `--max-workers 1` 后完整跑通，总耗时 `111.01s`；输出目录 `C:\Users\wenjin227\AppData\Local\Temp\kline_selector_run_100_safe_20260330` | 本次 100 只样本 `selected=0`；主要失败原因为最近 10 日上涨占比不足，最常见是 `40%` 和 `50%`；当前环境下多线程对 Akshare 底层依赖仍有稳定性风险，批量实跑建议优先使用单线程或更保守并发 |
| 2026-03-31 | 验证 / 在线数据源真实联网 smoke | `select_kline_candidates.py`、`select_hundred_day_high_candidates.py`、`LimitUpReviewService` | 代码目录 `D:\bb\daily_stock_analysis`，运行环境 `E:\Apps\daily_stock_analysis\.venv` | `select_kline_candidates.py --limit 5 --max-workers 1`、`select_hundred_day_high_candidates.py --limit 5 --max-workers 1`、两条脚本 `--resume` 均已真实跑通；`LimitUpReviewService(display_limit=5).get_review_rows()` 成功返回在线涨停复盘数据；`select_kline_candidates.py --limit 2 --max-workers 2` 小样本可通过 | 已确认当前稳定基线应为 `--max-workers 1`；全市场加速优先用分片而不是继续拉高 worker。`akshare_spot_em` 仍可能断开，但可 fallback 到 `akshare_code_name`；`Tushare` 当前因未配置 token 不可用 |

## 后续追加模板

后续每次处理 K 线逻辑时，在本节下方直接追加一条记录，建议使用下面的格式：

```md
### YYYY-MM-DD

- 类型：
- 目标：
- 涉及文件：
- 运行环境：
- 执行命令：
- 验证结果：
- 风险 / 未完成项：
- 下次接手提示：
```

## 2026-04-05 补充记录

### 2026-04-05

- 类型：实现 / 验证 / 文档沉淀
- 目标：把独立百日新高入口补成“筛选 -> 日度落库 -> 上涨原因归因 -> 历史复现回看”的完整闭环，并把当前真实稳定性问题压到可接受范围
- 涉及文件：
  - `src/storage.py`
  - `src/services/signal_cause_analysis_service.py`
  - `scripts/select_hundred_day_high_candidates.py`
  - `tests/test_signal_snapshot_storage.py`
  - `tests/test_signal_cause_analysis_service.py`
  - `tests/test_hundred_day_high_signal_flow.py`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
  - `README.md`
- 运行环境：
  - 代码目录：`D:\bb\daily_stock_analysis`
  - Python：`E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe`
- 执行命令：
  - `python -m py_compile src\storage.py src\services\kline_selector_service.py src\services\signal_cause_analysis_service.py scripts\select_hundred_day_high_candidates.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_storage.py tests\test_signal_cause_analysis_service.py tests\test_hundred_day_high_signal_flow.py tests\test_kline_selector_service.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --help`
  - 在线 smoke：
    - `scripts\select_hundred_day_high_candidates.py --limit 5 --max-workers 1 ...`
    - `scripts\select_hundred_day_high_candidates.py --limit 50 --new-high-window 20 --disable-spot-prefilter ...`
  - 命中样本复验：
    - 取 `2026-04-04` 强势池前 15 只，按 `20` 日新高口径挑出命中票，再走完整归因链路
- 验证结果：
  - 当前本地回归为 `22 passed`
  - `--help` 可正常运行
  - 小样本真实在线扫描已跑通
  - 使用 `2026-04-04` 强势池的命中样本复验时，`688485/300626/300006` 已验证：
    - 命中百日新高（收窄到 `20` 日窗口）
    - 可落库到 `KlineSignalSnapshot`
    - 可生成 `industry / reason_summary / theme_label`
    - 可生成结构化三段归因：`industry_logic / news_logic / technical_logic`
- 本次新增/确认的能力：
  - 新增通用快照表 `KlineSignalSnapshot`
    - 唯一键：`(signal_type, signal_date, code)`
    - 首版实际使用的 `signal_type`：`hundred_day_high`
  - `select_hundred_day_high_candidates.py` 新增：
    - `--snapshot-date`
    - `--history-lookback-days`
    - `--skip-cause-analysis`
    - `--skip-db-persist`
  - 导出结果新增字段：
    - `industry`
    - `reason_summary`
    - `industry_logic`
    - `news_logic`
    - `technical_logic`
    - `cause_tags`
    - `theme_label`
    - `latest_previous_hit_date`
    - `previous_hit_count`
    - `days_since_previous_hit`
- 当前真实稳定性结论：
  - `industry` 现在已经不再只依赖 `efinance` 的所属板块接口
    - 优先级：当日强势池/涨停池 `所属行业` -> 主营业务推断 -> `belong_boards`
    - 这样可以避开 `efinance` 在当前环境下的 `search-cache.json` 权限问题
  - `reason_summary` 即使新闻为空或 LLM 空响应，也会有 fallback，不再落空白
  - `theme_label` 已收紧：
    - 不再把 JSON 字段名本身参与关键词匹配
    - 改成多源加权打分
    - 证据不足时宁可留空，不再强贴 AI / 创新药 / 资源涨价
- 当前已确认的样本表现（2026-04-04 强势池命中复验）：
  - `688485 九州一轨`
    - `industry=轨交设备`
    - `theme_label=` 空
    - `industry_logic / technical_logic` 已稳定输出
  - `300626 华瑞股份`
    - `industry=电机Ⅱ`
    - `theme_label=` 空
    - `industry_logic / technical_logic` 已稳定输出
  - `300006 莱美药业`
    - `industry=化学制药`
    - `theme_label=创新药 / 医疗服务`
    - `industry_logic / technical_logic` 已稳定输出
- 风险 / 未完成项：
  - `SearXNG` 公共实例在当前环境仍可能超时，影响新闻质量，但不会阻断主流程
  - `deepseek` 仍会出现空响应；当前已靠 fallback 保证输出不断，但如要进一步提升归因质量，优先继续处理模型重试或备用模型
  - `theme_label` 现在比之前保守，但仍不是“个股级映射”，只是“主题级映射”
  - 双语文档（如 `docs/README_EN.md` / `docs/README_CHT.md`）尚未同步这一轮百日新高细节；当前信息已落在中文 `README.md` 和专题文档里
- 下次接手提示：
  - 若要继续提升“归因质量”，优先看：
    - `src/services/signal_cause_analysis_service.py`
    - `scripts/select_hundred_day_high_candidates.py`
    - `tests/test_signal_cause_analysis_service.py`
  - 若要继续提升“在线稳定性”，优先处理：
    - 搜索：`SearXNG` 超时与 fallback 顺序
    - LLM：`deepseek` 空响应的重试与备用模型
  - 若要复现这次最有价值的命中样本，不要直接扫代码排序前 N 只；优先取当日强势池，再按 `20` 日新高口径做小样本复验

### 2026-04-05（LLM 稳定性补充）

- 类型：修复 / 回归测试
- 目标：优先处理 `deepseek` 空响应问题，减少 `reason_summary` 只能走 fallback 的概率
- 涉及文件：
  - `src/analyzer.py`
  - `tests/test_market_analyzer_generate_text.py`
- 本次改动：
  - `Analyzer._call_litellm()` 现在会对“同模型空响应”做最多 `3` 次快速重试，再切换 fallback 模型
  - 新增 LiteLLM 响应文本提取逻辑，不再只认 `message.content` 为单个字符串；若返回 chunk/list 结构，也能拼接出文本
- 执行命令：
  - `python -m py_compile src\analyzer.py tests\test_market_analyzer_generate_text.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_market_analyzer_generate_text.py`
- 验证结果：
  - `pytest tests/test_market_analyzer_generate_text.py` 为 `9 passed`
- 结论：
  - 当前已经把“瞬时空响应直接判死”改成了“同模型先重试，再 fallback”
  - 这一步主要提升 `generate_text()` 稳定性，对百日新高归因和大盘复盘都生效

### 2026-04-05（搜索稳态化补充）

- 类型：修复 / 回归测试
- 目标：优先收拾 `SearXNG` 公共实例超时，避免新闻搜索把百日新高归因链路拖慢
- 涉及文件：
  - `src/search_service.py`
  - `tests/test_search_searxng.py`
- 本次改动：
  - 公共实例拉取超时从 `5s` 收紧到 `3s`
  - 公共实例单次搜索 failover 上限从 `3` 收紧到 `2`
  - 公共实例刷新失败退避从 `60s` 提到 `120s`
  - 新增公共实例失败冷却：
    - 某实例失败后，短时间内不再优先重试同一实例
    - 若当前实例池全部处于冷却中，则直接快速失败，不继续拖慢整条搜索链路
- 执行命令：
  - `python -m py_compile src\search_service.py tests\test_search_searxng.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_search_searxng.py`
- 验证结果：
  - `pytest tests/test_search_searxng.py` 为 `22 passed`
- 结论：
  - 当前 `SearXNG` 仍可能因为公共实例本身不稳定而失败，但“超时拖慢主链路”的影响已经被明显压小
  - 这一步主要提升新闻搜索 fail-open 的速度与稳定性，对百日新高归因里的 `news_logic` 有正向帮助

### 2026-04-05（百日新高查询接口补充）

- 类型：实现 / API 契约测试
- 目标：把已落库的 `hundred_day_high` 快照补成“可查询能力”，不再只依赖 CSV/Markdown
- 涉及文件：
  - `src/services/signal_snapshot_service.py`
  - `api/v1/schemas/signals.py`
  - `api/v1/endpoints/signals.py`
  - `api/v1/router.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
- 本次新增接口：
  - `GET /api/v1/signals/kline-snapshots`
    - 参数：`signal_type`、`signal_date`、可选 `code`、`limit`
    - 用于按日期查看当天命中的快照
  - `GET /api/v1/signals/kline-snapshots/{signal_type}/{code}`
    - 参数：可选 `days`、`limit`
    - 用于查看某只股票最近一段时间的同口径命中历史
- 当前接口返回重点：
  - 列表接口：
    - `industry`
    - `reason_summary`
    - `industry_logic`
    - `news_logic`
    - `technical_logic`
    - `theme_label`
    - `latest_previous_hit_date`
    - `previous_hit_count`
    - `days_since_previous_hit`
    - `close / latest_high / window_high`
  - 历史接口：
    - 连续命中摘要
    - 基于已落库快照的近似回撤摘要
- 执行命令：
  - `python -m py_compile src\services\signal_snapshot_service.py api\v1\schemas\signals.py api\v1\endpoints\signals.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
- 验证结果：
  - `pytest tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py` 为 `5 passed`
- 结论：
  - 当前已经具备最小后端查询闭环：按日期看当天命中的票，按股票看最近历史命中、连续新高和相对历史信号高点的近似回撤
  - 下一步若继续做，优先是前端查看入口或通知/调度接入，而不是继续增强脚本本身

### 2026-04-05（百日新高 Web 查看入口补充）

- 类型：实现 / 前端测试 / 构建验证
- 目标：把刚补好的百日新高查询 API 接成最小 Web 查看入口，避免只能手打 API 或看 CSV/Markdown
- 涉及文件：
  - `apps/dsa-web/src/api/signals.ts`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
  - `apps/dsa-web/src/App.tsx`
  - `apps/dsa-web/src/components/layout/SidebarNav.tsx`
  - `apps/dsa-web/src/components/layout/ShellHeader.tsx`
- 本次能力：
  - 新增 `/signals` 页面
  - 侧边导航新增“信号”入口
  - 默认按当天 `hundred_day_high` 查询
  - 左侧查看当天命中的快照列表
  - 右侧查看：
    - 连续新高次数
    - 相对最近 / 最大信号高点的近似回撤
    - `industry_logic / news_logic / technical_logic`
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py tests\test_signal_snapshot_storage.py tests\test_hundred_day_high_signal_flow.py`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关回归：`10 passed`
  - 前端页面测试：`2 passed`
  - Web 构建通过
- 结论：
  - 当前这条“百日新高快照 -> API -> Web 页面”的最小查看闭环已经打通
  - 下一步如果继续做，更适合往“通知/调度接入”或“前端交互细化”走，而不是再扩同类只读入口

### 2026-04-05（/signals 交互增强补充）

- 类型：前端增强 / 前端测试 / 构建验证
- 目标：继续细化 `/signals` 页交互，补上日期切换、排序和“只看连续新高”筛选
- 涉及文件：
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
  - `apps/dsa-web/src/components/common/Select.tsx`
- 本次新增交互：
  - 日期交互：
    - `上一天`
    - `下一天`
    - `今天`
  - 客户端排序：
    - 按 `latestHigh`
    - 按 `previousHitCount`
    - 按 `close`
    - 按 `code`
  - 客户端筛选：
    - `只看连续新高`
    - 当前实现基于已落库字段 `previousHitCount > 0` 且 `daysSincePreviousHit <= 4` 做近似判断
  - 列表顶部补充：
    - 当前筛选后数量
    - 连续新高数量
  - 历史面板保持不变，继续展示连续命中、近似回撤和三段结构化逻辑
- 执行命令：
  - `./node_modules/.bin/tsc.cmd -b`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - `SignalsPage.test.tsx` 为 `4 passed`
  - Web 构建通过
- 结论：
  - `/signals` 页现在已经具备基本“看盘 / 复盘”交互能力，不再只是静态展示
  - 如果继续做前端，下一步更适合补：
    - 更细的日期范围切换
    - 分页或虚拟滚动
    - 连续新高的显式 badge / 排序联动

### 2026-04-05（/signals 范围查询与多日对比补充）

- 类型：后端增强 / 前端增强 / API 与前端测试 / 构建验证
- 目标：继续增强 `/signals`，补上日期范围、多日对比、连续新高显式分组和分页
- 涉及文件：
  - `src/storage.py`
  - `src/services/signal_snapshot_service.py`
  - `api/v1/schemas/signals.py`
  - `api/v1/endpoints/signals.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/api/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
  - `apps/dsa-web/src/components/common/Select.tsx`
- 本次新增能力：
  - 后端列表查询支持：
    - `signal_date_from`
    - `signal_date_to`
    - `page`
    - `page_size`
  - 后端列表返回新增：
    - `compare_summary`
    - `is_consecutive_signal`
  - `/signals` 页新增：
    - 单日 / 日期范围切换
    - 多日对比卡片
    - 连续新高 / 其他新高显式分组
    - 列表分页
    - 现有排序与“只看连续新高”筛选继续保留
- 执行命令：
  - `python -m py_compile src\storage.py src\services\signal_snapshot_service.py api\v1\schemas\signals.py api\v1\endpoints\signals.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `./node_modules/.bin/tsc.cmd -b`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 页现在已经从“单日查看页”进化为“可做短周期多日复盘”的页面
  - 下一步如果继续做，更适合补：
    - 更长日期范围下的虚拟滚动或服务端分页优化
    - 多日对比里的环比/新增/掉队统计
    - 连续新高按 streak 长度排序或专门标签聚合

### 2026-04-05（/signals 新增/掉队与 streak 排行补充）

- 类型：后端增强 / 前端增强 / API 与前端测试 / 构建验证
- 目标：继续细化 `/signals` 多日对比，补上“新增 / 掉队 / 连续新高 streak 排行”
- 涉及文件：
  - `src/storage.py`
  - `src/services/signal_snapshot_service.py`
  - `api/v1/schemas/signals.py`
  - `api/v1/endpoints/signals.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/api/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
- 本次新增能力：
  - 后端列表查询支持：
    - `signal_date_from / signal_date_to`
    - `page / page_size`
  - 后端列表返回补充：
    - `compare_summary`
    - `is_consecutive_signal`
  - `/signals` 页新增：
    - 单日 / 范围模式切换
    - 多日对比区块
    - 连续新高 / 其他新高显式分组
    - 列表分页
- 执行命令：
  - `python -m py_compile src\storage.py src\services\signal_snapshot_service.py api\v1\schemas\signals.py api\v1\endpoints\signals.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `./node_modules/.bin/tsc.cmd -b`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 现在已经具备日期范围、多日对比、连续分组和分页能力
  - 下一步如果继续做，更适合把多日对比再深化成：
    - `新增 / 掉队 / streak 排行` 的显式统计卡片
    - 更大范围下的分页 / 虚拟滚动优化

### 2026-04-05（/signals 多日对比显式统计补充）

- 类型：后端增强 / 前端增强 / API 与前端测试
- 目标：把多日对比从“每日命中数概览”继续细化成显式的 `新增 / 掉队 / streak 排行`
- 涉及文件：
  - `src/services/signal_snapshot_service.py`
  - `api/v1/schemas/signals.py`
  - `api/v1/endpoints/signals.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
- 本次新增能力：
  - 多日对比摘要新增：
    - `added_count`
    - `dropped_count`
    - `added_codes`
    - `dropped_codes`
  - 范围查询返回新增 `streak_leaderboard`
    - `code / name`
    - `current_streak_count`
    - `longest_streak_count`
    - `latest_signal_date`
  - `/signals` 范围模式新增展示：
    - 每日 `新增 / 掉队`
    - `streak 排行`
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 的多日对比现在已经从静态“日期+数量”升级成可直接用于复盘的比较面板
  - 下一步如果继续做，更适合补：
    - 新增 / 掉队股票名称级展示
    - streak 排行点击联动筛选
    - 更长时间范围下的性能优化

### 2026-04-05（/signals 名称级新增/掉队 + streak 联动补充）

- 类型：后端增强 / 前端增强 / API 与前端测试 / 构建验证
- 目标：把 `added/dropped` 从纯代码升级成 `代码 + 名称`，并让 `streak` 排行支持点击联动筛选列表
- 涉及文件：
  - `src/services/signal_snapshot_service.py`
  - `api/v1/schemas/signals.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
- 本次新增能力：
  - 多日对比摘要新增：
    - `added_items`
    - `dropped_items`
    - 每项包含 `code + name`
  - `/signals` 页面：
    - 多日对比卡片显示“新增 某股票 / 掉队 某股票”
    - `streak 排行` 点击某一行后，会联动筛到该股票
    - 额外补了：
      - `只看全部连续新高`
      - `清除联动`
      - 联动状态 badge
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 现在已经从“看 streak 排行”升级到“能用 streak 排行直接驱动筛选”
  - 下一步如果继续做，更适合补：
    - 点击多日对比里的新增/掉队标签后也能联动筛选
    - `streak` 排行支持多选或按主题分组

### 2026-04-05（/signals 新增/掉队联动 + streak 多选/分组补充）

- 类型：后端增强 / 前端增强 / API 与前端测试
- 目标：让“新增/掉队”标签也能联动筛选，并让 `streak` 排行支持多选和按主题/行业分组
- 涉及文件：
  - `api/v1/schemas/signals.py`
  - `src/services/signal_snapshot_service.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
- 本次新增能力：
  - `added_items / dropped_items` 升级为 `code + name`
  - `streak_leaderboard` 新增 `industry`
  - `/signals` 页面新增：
    - 名称级 `新增 / 掉队` 标签展示
    - 点击 `streak` 排行行后联动筛选对应股票
    - 保留 `只看全部连续新高`
    - 保留 `清除联动`
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 现在已经支持名称级新增/掉队展示，并且 `streak` 排行可直接驱动列表联动
  - 下一步如果继续做，更适合补：
    - 点击新增/掉队标签后也触发联动筛选
    - `streak` 排行的真正多选（当前是单选联动）
    - `streak` 排行按主题/行业分组视图

### 2026-04-05（/signals 新增/掉队联动 + streak 多选/分组补充）

- 类型：前端增强 / 后端字段增强 / 前端测试 / 构建验证
- 目标：继续把 `/signals` 做成可直接操作的复盘面板，补上：
  - 点击 `新增/掉队` 标签联动筛选
  - `streak` 排行多选
  - `streak` 按主题 / 行业分组
- 涉及文件：
  - `api/v1/schemas/signals.py`
  - `src/services/signal_snapshot_service.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `apps/dsa-web/src/types/signals.ts`
  - `apps/dsa-web/src/api/signals.ts`
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
- 本次新增能力：
  - `added_items / dropped_items` 已支持点击后直接联动筛选对应股票
  - `streak_leaderboard` 已补 `industry`
  - `streak` 排行现在支持：
    - 多选联动
    - 按 `theme` 分组
    - 按 `industry` 分组
    - `选择本组`
    - `清除联动`
  - 页面联动 badge 从单票升级为“联动 N 只”
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm run build`
- 验证结果：
  - 后端相关测试：`7 passed`
  - 前端页面测试：`5 passed`
  - Web 构建通过
- 结论：
  - `/signals` 现在已经具备“新增/掉队点击联动 + streak 多选/分组”的交互能力
  - 下一步如果继续做，更适合补：
    - 分组内批量选中后的更明确反馈
    - 多选结果导出 / 推送
    - 更长范围下的性能优化
## 2026-03-31 补充记录

- 本次新增独立入口：`scripts/select_hundred_day_high_candidates.py`
- 目标：将“百日新高”从当前组合 K 线策略中拆成单独可执行入口，便于后续独立运行和升级时降低冲突
- 底层调整：`KlineSelectorCriteria` 新增 `require_up_day_ratio`，现在可以按需关闭“近 N 日上涨占比”规则
- 保持不变：原有 `scripts/select_kline_candidates.py` 继续作为“上涨占比 + 涨停 + 百日新高 + 市值上限”的组合策略入口
- 本次验证计划：
  - `py_compile` 验证新增脚本和筛选服务
  - `pytest tests/test_kline_selector_service.py`
  - 使用 `E:\Apps\daily_stock_analysis\.venv` 实跑一版组合策略最新结果
## 2026-03-31 分片优化补充

- 目标：解决全市场 K 线策略在当前 Windows 环境下单进程运行过慢、提高 `--max-workers` 又容易不稳定的问题
- 本次改动：
  - 在 `KlineSelectorService.scan_market()` 增加 `shard_count` / `shard_index`
  - `scripts/select_kline_candidates.py` 与 `scripts/select_hundred_day_high_candidates.py` 支持分片运行
  - 分片运行时自动为输出目录与 checkpoint 文件追加 shard 编号
  - 每个分片输出目录内额外落一份策略专用 checkpoint
  - 新增 `scripts/merge_kline_shard_results.py` 用于合并多个分片目录
- 推荐运行方式：
  - 每个进程保持 `--max-workers 1`
  - 用 `--shard-count 4` 或更保守的 2/4 分片，在多个独立终端同时运行
  - 所有分片结束后，再执行一次 merge 脚本汇总
- 预期收益：
  - 不依赖线程并发提速，尽量避开当前环境下的原生库并发不稳定问题
  - 首次全市场扫描可通过多进程分片显著缩短总耗时
  - 结果仍保持与现有单次运行一致的 `csv/txt/md` 输出结构
# 2026-04-05（/signals 导出/推送 + 长范围优化补充）

- 类型：前端增强 / 交互稳态化 / 文档沉淀
- 目标：补上多选结果导出与推送能力，并让 `/signals` 在更长日期范围下减少重复请求和长列表渲染压力，同时把联动与分组选中状态做得更直观
- 涉及文件：
  - `apps/dsa-web/src/pages/SignalsPage.tsx`
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
  - `apps/dsa-web/src/api/signals.ts`
  - `apps/dsa-web/src/utils/signalSelectionExport.ts`
  - `src/services/signal_snapshot_service.py`
  - `README.md`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
- 本次新增能力：
  - `/signals` 选中结果支持：
    - `复制 Markdown`
    - `导出 Markdown`
    - `导出 JSON`
    - `推送通知`
  - 页面新增更明显的选中反馈：
    - `已选 N 只` 状态条
    - 选中代码 badge
    - 分组内 `已选 x/y` 计数
  - 长范围下的优化：
    - 相同列表查询与历史查询前端缓存
    - 联动筛选改为状态驱动，减少重复请求
    - 多日对比卡片默认折叠，只先展示前 10 天
    - 单日查询不再额外计算多日对比摘要
- 执行命令：
  - `npm.cmd run test -- --run src/pages/__tests__/SignalsPage.test.tsx`
  - `npm.cmd run build`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
- 验证结果：
  - 前端页面测试：`4 passed`
  - Web 构建：通过
  - 后端相关测试：`7 passed`
- 结论：
  - `/signals` 现在已经从“可查看”继续升级到“可导出、可推送、可在长范围下稳定复盘”的页面
  - 下一步如果继续做，更适合往：
    - 多选结果的通知模板细化
    - 更长范围下的服务端聚合优化
    - 结果导出和通知的定时接入
# 2026-04-05（/signals 服务端预计算汇总补充）

- 类型：后端优化 / 存储层增强 / 测试补充
- 目标：把 `/signals` 长范围查询继续从“轻量投影”推进到“预计算汇总表”，让 `compare_summary` 和 `streak` 热点结果优先走数据库内持久化的窄表
- 涉及文件：
  - `src/storage.py`
  - `src/services/signal_snapshot_service.py`
  - `tests/test_signal_snapshot_storage.py`
  - `tests/test_signal_snapshot_service.py`
  - `tests/test_signal_snapshot_api.py`
  - `docs/CHANGELOG.md`
- 本次新增：
  - `kline_signal_daily_summary`
  - `kline_signal_streak_snapshot`
- 本次调整：
  - `upsert_signal_snapshot(...)` 写入快照后会同步刷新：
    - 当天日度汇总
    - 该股票的 streak 窄表
  - 范围查询默认优先走：
    - 日度汇总表生成 `compare_summary`
    - streak 窄表生成 `streak_leaderboard`
  - 当预计算表缺失或历史老数据未覆盖时，服务层仍保留 projection fallback
- 执行命令：
  - `python -m py_compile src\storage.py src\services\signal_snapshot_service.py tests\test_signal_snapshot_storage.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_storage.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
- 验证结果：
  - `py_compile` 通过
  - 测试 `11 passed`
- 结论：
  - `/signals` 现在已经从“前端缓存 + 后端轻量投影”进一步升级到“带预计算汇总表的长范围查询”
  - 下一步如果继续做，更适合往：
    - 汇总表重建脚本 / 修复脚本
    - 更大范围下的 SQL 级分页 summary
    - 周/月级别复盘聚合
# 2026-04-05（信号汇总表重建脚本补充）

- 类型：脚本补充 / 存储维护工具 / 文档沉淀
- 目标：补一个可重复执行的“全量或按条件重建汇总表”脚本，方便老数据补齐、数据库恢复后重刷、以及未来规则升级后的统一重算
- 涉及文件：
  - `scripts/rebuild_signal_summary_tables.py`
  - `src/storage.py`
  - `tests/test_signal_snapshot_storage.py`
  - `README.md`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
- 本次新增：
  - `DatabaseManager.rebuild_signal_summary_tables(...)`
  - CLI：`scripts/rebuild_signal_summary_tables.py`
- 脚本支持：
  - 全量重建
  - 指定 `signal_type`
  - 指定 `start_date / end_date`
  - 指定 `code / codes`
- 执行命令：
  - `python -m py_compile src\storage.py src\services\signal_snapshot_service.py scripts\rebuild_signal_summary_tables.py tests\test_signal_snapshot_storage.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests\test_signal_snapshot_storage.py tests\test_signal_snapshot_service.py tests\test_signal_snapshot_api.py`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --help`
- 验证结果：
  - `py_compile` 通过
  - 测试 `12 passed`
  - 脚本 `--help` 可正常运行
- 结论：
  - 现在预计算汇总表不再只能依赖“后续有新 upsert 才逐步补齐”
  - 老数据、迁移恢复后的数据库、或规则升级场景，都可以显式跑一次重建脚本统一修复

# 2026-04-05（真实库重建耗时记录）

- 类型：真实环境验证 / 基线测速
- 目标：对当前真实数据库跑一次信号汇总表重建，记录库规模与实际耗时，作为后续扩容前的基线
- 运行环境：
  - 数据库：`D:\bb\daily_stock_analysis\data\stock_analysis.db`
  - 数据库大小：约 `479 KB`
  - Python：`E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe`
- 运行前后行数：
  - `kline_signal_snapshot = 3`
  - `kline_signal_daily_summary = 1`
  - `kline_signal_streak_snapshot = 3`
- 执行命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --log-level INFO`
  - `Measure-Command { E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --log-level WARNING }`
- 真实结果：
  - 脚本日志返回：`daily_summary_count=1`，`streak_code_count=3`
  - 一次完整重建耗时：`2.7694s`（约 `2769.45 ms`）
- 结论：
  - 当前真实库规模还很小，重建耗时主要已经不是数据量瓶颈
  - 后续如果 `kline_signal_snapshot` 明显增长，再重复记录 30 / 90 / 180 天级别的真实耗时，才更能判断是否需要继续做批量 SQL 重建版

# 2026-04-05（小型性能观测命令补充）

- 类型：维护命令增强 / 观测补充
- 目标：让汇总表重建脚本本身就能输出一份可留存的规模与性能摘要，而不是每次都手工拼 `Measure-Command`
- 涉及文件：
  - `scripts/rebuild_signal_summary_tables.py`
  - `src/storage.py`
  - `tests/test_signal_snapshot_storage.py`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
- 本次新增：
  - `DatabaseManager.get_signal_summary_stats(...)`
  - `scripts/rebuild_signal_summary_tables.py --report-only`
  - `scripts/rebuild_signal_summary_tables.py --report-json <path>`
- 真实命令示例：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --report-only --log-level WARNING`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --report-json data\signal_summary_perf_report.json --log-level WARNING`
- 真实结果样例：
  - `report_only` 输出了：
    - `snapshot_count=3`
    - `snapshot_day_count=1`
    - `snapshot_code_count=3`
    - `daily_summary_count=1`
    - `streak_snapshot_count=3`
    - `streak_code_count=3`
  - `report_json` 重建输出了：
    - `elapsed_seconds=0.1306`
    - `throughput_snapshot_rows_per_second=22.98`
- 验证结果：
  - `pytest tests/test_signal_snapshot_storage.py tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py` -> `13 passed`
  - `scripts/rebuild_signal_summary_tables.py --report-only` 可运行
  - `scripts/rebuild_signal_summary_tables.py --report-json ...` 可运行并落地 JSON 文件

# 2026-04-05（观测结果追加时间序列日志补充）

- 类型：维护命令增强 / 趋势留痕
- 目标：让每次汇总表观测或重建后，都能自动留下可持续对比的时间序列记录，不再手工保存单次 JSON
- 涉及文件：
  - `scripts/rebuild_signal_summary_tables.py`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
- 本次新增：
  - `--append-jsonl <path>`
- 作用：
  - 每次执行后，把本次 report 作为一行 JSON 追加到 `.jsonl`
  - 后续可以直接按时间对比：
    - `elapsed_seconds`
    - `throughput_snapshot_rows_per_second`
    - `snapshot_count`
    - `daily_summary_count`
    - `streak_snapshot_count`

# 2026-04-05（趋势查看脚本 / 小报表补充）

- 类型：维护脚本补充 / 报表输出
- 目标：把 `signal_summary_perf_history.jsonl` 变成可直接查看的趋势摘要和 Markdown 小报表
- 涉及文件：
  - `scripts/report_signal_summary_perf_trend.py`
  - `tests/test_signal_summary_perf_trend_script.py`
  - `README.md`
  - `docs/KLINE_SELECTOR_GUIDE.md`
  - `docs/CHANGELOG.md`
- 本次新增：
  - 从 JSONL 读取观测记录
  - 按 `signal_type / mode` 过滤
  - 输出终端摘要
  - 生成 `signal_summary_perf_trend.md`
- 实跑命令：
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_summary_perf_trend.py --input data\signal_summary_perf_history.jsonl --output-md data\signal_summary_perf_trend.md --signal-type hundred_day_high --limit 20`
- 实跑结果：
  - 终端摘要：
    - `record_count=2`
    - `latest_snapshot_count=3`
    - `avg_elapsed_seconds=0.0`
  - Markdown 报表已生成：
    - `data\signal_summary_perf_trend.md`
- 验证结果：
  - `pytest tests/test_signal_summary_perf_trend_script.py tests/test_signal_snapshot_storage.py tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py` -> `14 passed`
