# 主题核心映射策略

## 1. 目标

这套策略用于把一只股票拆成三层：

- 大主题
- 真子主题
- 个股角色

它解决的问题不是“这票涨不涨”，而是：

- 它和另一只票是不是同一条交易逻辑
- 它是源头受益、景气核心、渠道受益，还是下游承压
- 它是不是这个子主题里的核心股

## 2. 三层结构

`theme_core_mapper` 默认输出：

- `theme_key`
- `subtheme_key`
- `stock_role`

常见理解方式：

- `theme_key`
  - 大主题
  - 例如 `optical_communication`
- `subtheme_key`
  - 真子主题
  - 例如 `preform_and_materials`
  - 或 `optical_module_and_cpo`
- `stock_role`
  - 个股在子主题里的位置
  - 常见包括：
    - `source_beneficiary`
    - `manufacturing_beneficiary`
    - `prosperity_core`
    - `channel_beneficiary`
    - `downstream_cost_pressure`
    - `theme_proxy`

## 3. 为什么这比“同板块”更清楚

很多票同属一个大方向，但不是同一条交易逻辑。

例如：

- `长飞光纤`
  - `theme_key = optical_communication`
  - `subtheme_key = preform_and_materials`
  - `stock_role = source_beneficiary`
- `中际旭创`
  - `theme_key = optical_communication`
  - `subtheme_key = optical_module_and_cpo`
  - `stock_role = prosperity_core`

两者同属光通信，但不是同一逻辑：

- 前者更像“光纤涨价源头受益”
- 后者更像“CPO / 光模块景气核心”

## 4. 核心度

除了三层结构，策略还会输出：

- `theme_core_probability`
- `subtheme_core_probability`
- `core_driver_type`
- `is_direct_beneficiary`

理解方式：

- `subtheme_core_probability`
  - 更重要
  - 它判断这只票是不是某个真子主题里的核心股
- `theme_core_probability`
  - 更宽泛
  - 它判断这只票是不是整个大主题里有代表性的强票
- `core_driver_type`
  - 当前主要包括：
    - `price_pass_through`
    - `subtheme_prosperity`
    - `channel_inventory_repricing`
    - `cost_pressure`
    - `theme_proxy`

## 5. 和现有策略的关系

- `commodity_price_pass_through`
  - 更擅长回答：是不是直接受益、传导顺不顺、业绩能不能释放
- `dragon_head`
  - 更擅长回答：是不是资金核心、是不是高辨识度龙头
- `theme_core_mapper`
  - 更擅长回答：是不是同一条交易逻辑、是不是同一个子主题核心

三者配合时最清楚：

1. `theme_core_mapper`
   先拆清楚大主题 / 子主题 / 个股角色
2. `commodity_price_pass_through`
   再判断是不是直接受益
3. `dragon_head`
   最后判断是不是资金核心

## 6. 批量找出来

如果不是只分析单票，而是想让系统直接找：

- 哪些子主题最强
- 每个子主题里谁是最核心的票

可以用：

`scripts/select_theme_core_candidates.py`

示例：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_theme_core_candidates.py --commodity-hint optical_fiber --limit 100
```

这个脚本会：

- 扫一批股票
- 先做三层分类
- 再按 `theme_key + subtheme_key` 分组
- 最后给每个子主题保留最核心的几只票

## 7. 当前边界

- 当前优先服务于已经有主题配置的方向
  - 如 `optical_fiber / memory / hard_disk / copper`
- 它不是通用行业 ontology
- 但已经足够解决“同大方向但不是同子逻辑”的主要问题
