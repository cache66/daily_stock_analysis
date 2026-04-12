# 商品涨价传导策略

## 1. 目标

这套策略用于回答一个更具体的问题：

- 某个商品真的在涨价
- 哪些 A 股公司和这轮涨价存在直接关系
- 这些公司里，谁更可能把“价格上涨”真正转成收入、毛利和净利润改善

它不是通用题材追踪策略，也不是单纯技术面突破策略，而是把“商品涨价 -> 产业链映射 -> 利润传导 -> 财报验证 -> 技术确认”串成一条结构化判断链。

## 2. 为什么单看“涨价新闻”不够

实际交易里最容易踩的坑有三类：

1. 商品确实涨价了，但公司只是弱映射，主营收入并不靠这个品类
2. 公司处在下游，原料上涨反而挤压毛利
3. 公司有题材映射，但业绩兑现要等财报，短期更像情绪交易

所以这套策略不把“涨价”直接等同于“业绩释放”，而是要求逐层验证。

## 3. 策略流程

### 第一步：确认涨价事件

先确认新闻是否真实、是否足够新、是否有持续性。

重点看：

- 涨价品类是什么
- 催化来源是什么
- 是否有明确日期
- 是一次性脉冲，还是供需偏紧、去库、扩产滞后导致的阶段景气

如果只有模糊传闻、二手转载、没有日期的旧闻，默认降低结论置信度。

### 第二步：映射到 A 股产业链

把公司放回产业链里，而不是只看题材标签。

常用分类：

- 上游资源/原材料
- 中游制造/加工
- 下游组装/品牌
- 分销/模组/库存重估受益方

对于光纤、内存、硬盘这类题材，要特别警惕“映射看起来很近，实际利润关系很弱”的情况。

### 第三步：判断利润传导

这是策略的核心。

要回答的问题不是“有没有概念”，而是：

- 涨价相关业务是不是公司主线收入
- 公司有没有议价能力
- 是收入受益、毛利受益，还是只是账面题材受益
- 是否存在库存优势、锁价订单、龙头地位、供给约束
- 新闻里有没有“成本压力”“采购涨价”“价格传导不顺”等反向证据

### 第四步：验证财报和经营数据

如果已经能从财报、预告、快报看到营收或净利润改善，那么“业绩释放”可信度会明显上升。

建议重点关注：

- 营收同比
- 净利润同比
- 最近财报/预告/快报日期
- 公司体量是否足够小，能被单一涨价逻辑明显驱动

如果目前还没有财报验证，就只能写成“预期释放”，不能写成“已经兑现”。

### 第五步：最后才看技术确认

技术面只解决两个问题：

- 资金有没有认可这个逻辑
- 现在是不是合适的交易时点

它不能替代利润传导和财报验证。

## 4. v1 评分卡

当前第一版采用 100 分打分：

- 涨价事件强度：25 分
- 产业链位置纯度：25 分
- 利润传导能力：25 分
- 财报/经营验证：15 分
- 技术确认度：10 分

对应解释：

- 80-100：高概率业绩释放候选
- 65-79：有逻辑，但仍需验证
- 50-64：主题映射存在，兑现链条不完整
- 50 以下：更像题材联动或伪受益

## 5. 适用品类示例

### 光纤涨价

优先看：

- 光纤预制棒
- 光纤光缆
- 光通信关键材料

谨慎看：

- 一般通信设备集成
- 仅有弱关联工程属性的公司

### 内存涨价

优先看：

- DRAM / NAND 直接敞口
- 存储模组
- 主控/模组品牌
- 有低价库存重估收益的公司

谨慎看：

- 纯下游整机装配
- 只能被动承受上游涨价的环节

### 硬盘涨价

优先看：

- 企业级存储相关链条
- 关键部件或控制器
- 与存储景气周期绑定更强的配套环节

谨慎看：

- A 股里产业链映射本来就弱的标的
- 只有题材联想、缺少收入证据的公司

## 6. 本次落地过程记录

这次第一版没有直接修改主分析链路，而是优先做成一个内置 Agent 策略，原因有三点：

1. 现有仓库已经具备策略 YAML 加载能力，接入成本低
2. 现有工具已经覆盖新闻、基本面、历史 K 线、实时行情和趋势分析，足够支撑第一版推理链
3. 先把判断框架固化成 prompt 级策略，比直接新增硬编码打分服务更稳，也更便于后续迭代

第一版复用了以下现有能力：

- `search_comprehensive_intel`
- `search_stock_news`
- `get_stock_info`
- `get_daily_history`
- `get_realtime_quote`
- `analyze_trend`

第一版刻意没有做的事情：

- 没新增专门的商品数据库
- 没硬编码 A 股公司与商品的全量映射表
- 没把利润传导做成后台批量评分脚本

这样做是为了先把策略认知框架跑通，再根据真实使用反馈决定第二版是否需要：

- 商品词典和 A 股映射表
- 更细的角色标签（上游/中游/下游/分销/库存受益）
- 面向 `/signals` 或批量扫描脚本的结构化评分服务

## 7. 第二版升级

第二版开始把一部分判断从纯 prompt 推理升级为可复用代码。

新增内容：

- 新增 `CommodityPassThroughService`
- 新增 Agent 分析工具 `analyze_commodity_pass_through`
- 内置小型商品映射规则表
- 增加产业链角色标签：`upstream / midstream / downstream / distribution / weak_proxy`

第二版当前优先覆盖：

- 光纤
- 内存
- 硬盘
- 铜

第二版到第三版之间，专题映射也进一步细化成了 `subtheme`：

- 光纤
  - `preform_and_materials`
  - `fiber_and_cable`
  - `optical_module_and_cpo`
  - `telecom_equipment_and_network`
- 内存
  - `flash_and_memory_design`
  - `module_and_packaging`
  - `authorized_distribution`
  - `server_oem_and_assembly`
- 硬盘
  - `enterprise_storage_system`
  - `hdd_channel_distribution`
  - `surveillance_storage_demand`
  - `server_oem_and_integrator`

第二版的作用不是替代最终判断，而是先把最容易出错的几件事结构化：

- 这只股票到底是不是这个涨价题材
- 它更像产业链的哪一层
- 当前证据更像正向传导还是成本压力
- 业绩释放到底更像已经验证，还是仍停留在预期

策略层现在建议先调用 `analyze_commodity_pass_through`，再结合新闻、财报和技术面做人类可读结论。

## 8. 样例白名单与反例

当前第三版在规则层补了一批真实 A 股样例，用于做精确代码命中时的提示和纠偏。

### 光纤

白名单：

- `601869 长飞光纤`：更接近 `preform_and_materials`
- `600487 亨通光电`：更接近 `fiber_and_cable`

反例：

- `300308 中际旭创`：更偏 `optical_module_and_cpo`
- `000063 中兴通讯`：更偏 `telecom_equipment_and_network`

### 内存

白名单：

- `603986 兆易创新`：更接近 `flash_and_memory_design`
- `688525 佰维存储`：更接近 `module_and_packaging`
- `300475 香农芯创`：更接近 `authorized_distribution`

反例：

- `601138 工业富联`：更接近 `server_oem_and_assembly`

### 硬盘

白名单：

- `300302 同有科技`：更接近 `enterprise_storage_system`
- `300857 协创数据`：更接近 `hdd_channel_distribution`

反例：

- `002415 海康威视`：更接近 `surveillance_storage_demand`
- `601138 工业富联`：更接近 `server_oem_and_integrator`

## 9. 配置外置化与批量扫描

第四版开始，专题映射与样例表已经外置到：

- `config/commodity_pass_through/optical_fiber.json`
- `config/commodity_pass_through/memory.json`
- `config/commodity_pass_through/hard_disk.json`
- `config/commodity_pass_through/copper.json`

同时新增批量扫描脚本：

- `scripts/select_commodity_beneficiaries.py`

它会基于这些配置文件批量扫描 A 股，直接输出“商品涨价受益候选池”。

详细用法见：

- `docs/COMMODITY_BENEFICIARY_SCAN.md`
- `docs/COMMODITY_BENEFICIARY_SNAPSHOTS.md`

## 10. 当前局限

- 仍然依赖新闻和公开信息质量，弱映射标的容易出现证据不足
- 对“主营占比”“毛利率弹性”“库存结构”的判断还偏经验化
- 光纤、内存、硬盘等链条在 A 股里并不总有纯正标的，策略会有保守倾向
- 第二版虽然增加了规则层，但仍然只是小型映射表，不是完整产业数据库。

## 11. 使用建议

适合在 `/chat` 或 `/ask` 中这样使用：

- 用商品涨价传导分析长飞光纤
- 用商品涨价传导分析某只内存概念股是否真的会释放业绩
- 用商品涨价传导策略看某公司更像直接受益还是伪受益

如果后续要做批量化筛选，建议下一步在这个策略框架之上再增加专题脚本，而不是把所有判断都压到一次对话里。

## 12. 组合强化规则

最新一版把“叠加判断”从隐含加分升级成了显式规则。

当一只票同时满足下面几类条件时，会触发额外的 `combo_reinforcement`：

- 商品涨价映射强
  - 商品匹配清晰
  - 产业链位置更接近 `upstream / midstream`
  - 价格传导方向偏 `positive`
- 强逻辑
  - `logic_consensus_score` 足够高
  - 主营、subtheme、白名单样例等证据支持“最先被想到”
- 资金集中
  - `capital_consensus_score` 或 `liquidity_score` 足够高
  - 成交、换手、承接能证明市场在集中关注

这类票不会只被视为“自然叠加加分”，而是会被单独记成一个结构化因子：

- `combo_reinforcement_score`
- `factor_breakdown.combo_reinforcement`

它的作用是：

- 提高最终 `scores.total`
- 强化 `earnings_release_probability` 的总分判断
- 在候选池、快照、导出结果里明确标识“涨价 + 强逻辑 + 资金集中”的组合型机会

这条规则适合你强调的那类票：

- 不只是题材对
- 不只是逻辑顺
- 还要有资金真的集中到核心股上

## 13. 三层分类

最新一版不再只输出“商品映射”这一层，而是显式区分三层：

- `theme_key`
  - 大主题
  - 例如 `optical_communication`、`storage_semiconductor`
- `subtheme_key`
  - 真正驱动交易的子主题
  - 例如 `preform_and_materials`、`optical_module_and_cpo`
- `stock_role`
  - 个股在这个子主题里的角色
  - 例如：
    - `source_beneficiary`
    - `manufacturing_beneficiary`
    - `prosperity_core`
    - `channel_beneficiary`
    - `downstream_cost_pressure`
    - `theme_proxy`

这样做的目的，是把“同属大方向，但不是同一条交易逻辑”的票拆开。

例如在光通信里：

- `长飞光纤`
  - `theme_key = optical_communication`
  - `subtheme_key = preform_and_materials`
  - `stock_role = source_beneficiary`
- `中际旭创`
  - `theme_key = optical_communication`
  - `subtheme_key = optical_module_and_cpo`
  - `stock_role = prosperity_core`

两者都属于光通信，但不是一回事：

- 前者更接近“光纤涨价源头受益”
- 后者更接近“CPO / 光模块景气核心”

这也是系统现在区分“同大主题”与“真子主题核心股”的基础。
