# 业绩线判读手册：`earnings_surprise` 怎么看、怎么筛、怎么复盘

这份文档不重复讲实现细节，而是回答更实战的问题：

- `/signals` 里看到一条 `earnings_surprise`，先看什么
- 哪些票值得继续看，哪些可以先忽略
- `passed_strategy_score` 和 `passed_watch_with_confirmation` 的区别是什么
- `blocked_quality_risk`、`blocked_negative_text` 这类票应该怎么理解
- 业绩线怎么和新高线、题材线一起用

如果你想看底层权重、门槛和快照字段，请先看：

- `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`

如果你想看脚本入口、常用命令和回测方式，请看：

- `docs/EARNINGS_SURPRISE_TRACKING.md`

## 1. 先建立一个简单心智模型

当前 `earnings_surprise` 更像：

- 业绩强势代理信号

不是：

- 严格卖方一致预期差模型
- 直接可下单的交易信号

更适合它的用途是：

- 留样
- 排序
- 二次复盘
- 和趋势/题材主线做交叉验证

所以最好的用法不是“命中就买”，而是：

1. 先用它筛出业绩线候选
2. 再看趋势状态、题材位置和辨识度
3. 最后再决定是不是值得进入观察池

## 2. 看一条票时的推荐顺序

建议固定用下面顺序读：

1. `earnings_strategy_gate_status`
2. `earnings_strategy_score`
3. `earnings_strategy_label`
4. `earnings_quality_verdict / earnings_quality_score`
5. `earnings_quality_cycle_phase`
6. `earnings_event_freshness_score`
7. `reason_summary`

这样做的原因很简单：

- `gate_status` 决定它当前是被放行还是被拦截
- `strategy_score` 决定它是“强通过”还是“观察通过”
- `quality` 和 `cycle` 决定它是不是质量真的在改善
- `freshness` 决定这个信号是不是已经太旧

## 3. 最重要的两类通过票

当前最值得重点看的通过票，基本就两类。

### 3.1 `passed_strategy_score`

含义：

- 混合策略分 `>= 55`
- 不需要靠边缘确认勉强过线

这类票通常意味着：

- 多季度质量画像相对完整
- 周期位置不差
- 风险扣分没把总分打坏

优先级上，这类票一般高于 `passed_watch_with_confirmation`。

更适合继续看的情况：

- `earnings_quality_verdict` 是 `good` 或 `strong`
- `earnings_quality_cycle_phase` 在 `recovering` / `expanding` / `reaccelerating`
- `earnings_event_freshness_score >= 8`

如果再叠加下面任一项，会更值得看：

- 同时命中 `hundred_day_high`
- 属于强主题或主线板块
- 行业内有辨识度和资金共识

### 3.2 `passed_watch_with_confirmation`

含义：

- 分数在 `35 ~ 55`
- 但存在至少一个确认信号，所以放行

这类票通常意味着：

- 基础质量还没强到“自己站得住”
- 但文本、增长阈值或质量信号至少有一块给了额外确认

它不是坏票，但更像：

- 观察票
- 候选票
- 等待走势或后续财报继续确认的票

更适合怎么用：

- 放进观察池
- 等待趋势进一步强化
- 看后续是否叠加题材催化或价格突破

如果这类票本身：

- 没有新高
- 没有题材共振
- 新鲜度也一般

那通常不值得排太前。

## 4. 常见拦截状态怎么读

### 4.1 `blocked_negative_text`

这是最明确的拦截。

含义：

- 财报预告/快报文本命中了明显负向关键词

实战上通常可以理解成：

- 先不看
- 除非你明确是在做反转、困境反转、低预期修复，否则可以直接排除

原因很直接：

- 当前策略里负向文本优先级很高
- 即使其他数字局部不错，也会先被拦下来

### 4.2 `blocked_quality_risk`

这是最需要重视的“结构性风险拦截”。

最常见的理解方式是：

- 看起来像有财报信息
- 但底层质量结构并不支持它成为好业绩线标的

常见触发场景：

- 周期处于 `downcycle`，且没有文本或增长确认
- 经营现金流非正，利润也没有明显转正，同时又缺少文本确认

这类票不要只看“同比看起来还行”，要特别注意：

- 现金流
- 周期位置
- 利润和收入是否同向改善

如果你只做“偏确定性的业绩线”，这类票一般应该直接排除。

### 4.3 `blocked_low_strategy_score`

含义：

- 有一定确认信号
- 但综合分还是不够

这类票通常意味着：

- 有局部亮点
- 但多因子质量不够扎实

更像：

- 题材刺激下的局部改善
- 单季数字还可以，但连续性一般

这类票不建议作为主池，但可以在特殊情形下保留观察：

- 强题材主线
- 板块低位刚启动
- 同时出现了趋势突破

### 4.4 `blocked_missing_confirmation`

含义：

- 分数不高
- 文本、增长、质量三类确认里也没有一项站出来

这种票一般没必要继续花时间。

可以粗暴理解成：

- 既没有强业绩文本
- 也没有清晰增长确认
- 质量画像也没给出正向背书

### 4.5 `blocked_duplicate_event`

这不是“票不好”，而是“同一事件已经记过了”。

这种情况更适合这样理解：

- 它可能依然是好票
- 只是当前这次扫描里，不把它当成新的业绩事件再记一遍

如果你在做历史跟踪：

- 这类票不要误判成策略不认可
- 它只是被事件去重挡住了

## 5. 用四象限快速分层

实战里可以用一个很简单的四象限来分票。

### 第一象限：高分 + 有质量背书

典型特征：

- `earnings_strategy_score >= 55`
- `earnings_quality_verdict in {good, strong}`
- `cycle_phase` 不差

这是最值得优先看的主池票。

### 第二象限：中分 + 有确认

典型特征：

- `35 <= score < 55`
- `passed_watch_with_confirmation`

这是标准观察池。

更适合继续等：

- 趋势突破
- 题材催化
- 后续财报继续验证

### 第三象限：低分 + 仍有一些故事

典型特征：

- `blocked_low_strategy_score`
- 或只有单一文本亮点

这类票更容易是：

- 低基数修饰
- 单季冲高
- 讲法比质量强

一般不建议纳入核心池。

### 第四象限：风险拦截

典型特征：

- `blocked_negative_text`
- `blocked_quality_risk`

这类票默认应直接排除。

## 6. 怎么结合几个关键字段做二次筛选

### 6.1 如果你想要“偏确定性”的业绩线

优先保留：

- `earnings_strategy_gate_status = passed_strategy_score`
- `earnings_quality_verdict = good/strong`
- `earnings_quality_cycle_phase != downcycle`
- `earnings_event_freshness_score >= 8`

再加一条趋势条件会更稳：

- 叠加 `hundred_day_high`

### 6.2 如果你想要“业绩 + 趋势共振”

优先看：

- `earnings_surprise`
- `hundred_day_high_with_earnings`

更理想的组合是：

- 先是 `earnings_surprise` 通过
- 再在同日或随后不久出现新高/强趋势

这种组合通常比单看业绩线更有交易意义。

### 6.3 如果你想要“业绩 + 题材主线共振”

优先把业绩线和这些东西联动看：

- `dragon_head_candidate`
- `theme_core_mapper`
- 板块辨识度

你真正想找的是：

- 不是只有财报改善
- 而是业绩改善恰好落在主线题材核心公司上

## 7. 三种典型票怎么读

下面这三种，是当前最常见的判读范式。

### 7.1 文本和增长都好，但总分还没特别高

典型表现：

- `positive_text_signal = true`
- `growth_signal = true`
- `gate_status = passed_watch_with_confirmation`

怎么理解：

- 市场公开信息已经在说“业绩不错”
- 数字也过了默认门槛
- 但多季度质量画像还没有强到绝对高分

怎么用：

- 这是很标准的观察票
- 如果后面走出趋势强化，价值会快速提升

### 7.2 文本一般，但质量画像很强

典型表现：

- `earnings_quality_signal = true`
- `earnings_quality_verdict = good/strong`
- `gate_status = passed_strategy_score`

怎么理解：

- 不是靠公告话术过关
- 更像是财务结构本身已经连续改善

这类票通常比“纯文本强”的票更值得重视。

### 7.3 文本没有问题，但质量风险太重

典型表现：

- `gate_status = blocked_quality_risk`
- `cycle_phase = downcycle`
- `risk_flags` 里有现金流或利润问题

怎么理解：

- 表面上像有业绩题材
- 但底层财务和周期还没站稳

这类票最容易让人误判，尤其是在题材热的时候。

## 8. 什么时候可以把排序往前提

下面这些情况，可以明显提高你对一条 `earnings_surprise` 的优先级判断：

- `earnings_strategy_score >= 60`
- `earnings_quality_verdict = strong`
- `earnings_event_freshness_score >= 9`
- 同时命中 `hundred_day_high`
- 同时属于热点板块核心股
- 在板块里有较高辨识度

如果同时满足其中 3 项以上，通常已经不是“普通业绩线票”，而是值得重点跟踪的共振票。

## 9. 什么时候应该主动降级

下面这些情况，即使它通过了，也建议主动降级权重：

- `passed_watch_with_confirmation`，但没有趋势共振
- `earnings_event_freshness_score <= 4`
- `earnings_quality_cycle_phase = mixed`
- 风险扣分明显偏高
- 只有文本亮点，没有财务连续性背书

这种票更适合：

- 看看
- 记一下
- 不要过度下注

## 10. 一个简化的实战清单

如果你在 `/signals` 里快速过票，可以直接按这个顺序筛：

1. 先排除 `blocked_negative_text`
2. 再排除 `blocked_quality_risk`
3. 优先看 `passed_strategy_score`
4. 再看 `passed_watch_with_confirmation`
5. 在通过票里优先排序：
   - 分数更高
   - 质量结论更强
   - 新鲜度更高
   - 同时有趋势/题材共振

## 11. 最后一句话总结

`earnings_surprise` 最适合找的，不是“财报里看起来有亮点的票”，而是：

- 业绩改善
- 质量改善
- 周期没坏
- 事件还新
- 最好再叠加趋势或题材共振

这才是当前这条业绩线真正想筛出来的核心对象。

## 12. 相关文档

- `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`
- `docs/EARNINGS_SURPRISE_TRACKING.md`
- `docs/EARNINGS_SURPRISE_QUALITY_SIGNAL.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`
