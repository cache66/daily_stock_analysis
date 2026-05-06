# 题材板块池缓存刷新设计

## 1. 背景

当前 `board_cycle_scan` 已具备：

- 指定板块扫描
- 本地 `board_universe_file` 离线输入
- 本地 cache fallback
- 多轮扫描 tracking 输出

但它现在缺少一层更稳定的“板块池输入”来源。

现状问题：

1. `AkShare` 板块名单与板块成分接口稳定性不足，不适合做全量主源。
2. 申万行业更适合标准行业研究，不够贴近热点题材轮动。
3. 用户当前更需要的是“题材板块池”：
   - 不追求实时
   - 板块更新频率不高
   - 可以记录最后更新时间
   - 每 3 天刷新一次即可

因此这轮不再追求“远端实时全量板块扫描”，而是新增一层轻量的本地题材板块池缓存。

## 2. 目标

本轮目标：

1. 使用 `Tushare THS/DC` 题材板块体系作为板块池主源。
2. 将板块池缓存到本地文件。
3. 缓存文件记录：
   - `last_updated_at`
   - `source`
   - `expire_after_days`
4. 默认缓存有效期为 3 天。
5. 当缓存未过期时，`board_cycle_scan` 直接复用本地板块池，不重复刷新。
6. 当缓存过期时，尝试刷新；若刷新失败，继续使用旧缓存并写 warning。

## 3. 非目标

本轮不做：

- 不修改 `board_cycle_scan_service.py` 的评分逻辑
- 不接入 `/signals`
- 不做全市场每日自动调度
- 不把申万行业和通达信行业一起混入首版主链路
- 不要求首版就支持所有板块体系自由切换
- 不做数据库落库

## 4. 推荐方案

推荐采用：

`THS/DC 题材板块池 -> 本地缓存文件 -> board_cycle_scan 消费缓存文件`

原因：

1. 用户当前更看重题材板块，而不是标准行业分类。
2. 题材板块不需要高频更新，文件缓存足够。
3. 这样可以把“不稳定的远端板块名单/成分接口”收敛到单独刷新步骤里，而不是每次扫描都直连远端。
4. 失败时可以 fail-open，继续使用旧缓存，不影响复盘。

## 5. 架构

首版增加两层：

1. `board concept pool refresh layer`
   - 负责从 `Tushare THS/DC` 拉取题材板块池
   - 生成本地缓存
2. `board_cycle_scan input resolver`
   - 负责判断是否使用缓存
   - 判断是否需要刷新
   - 将缓存转成 `board_universe_file` 风格输入

## 6. 文件设计

建议新增：

- `scripts/refresh_board_concept_pool.py`
  - 独立刷新题材板块池
- `data/board_concept_pool/board_concept_pool.csv`
  - 板块池主文件
- `data/board_concept_pool/board_concept_pool_meta.json`
  - 元信息文件

必要时后续可再加：

- `data/board_concept_pool/board_members/<board_name>.csv`
  - 单板块成分缓存

但首版可先不拆到每板块单文件，只先把“板块池清单 + 更新时间”做稳。

## 7. 元信息设计

`board_concept_pool_meta.json` 首版字段建议：

```json
{
  "source": "tushare_ths_or_dc",
  "last_updated_at": "2026-05-02T09:00:00+08:00",
  "expire_after_days": 3,
  "refresh_status": "success",
  "board_count": 128,
  "notes": ""
}
```

其中：

- `last_updated_at`
  - 用于判断是否过期
- `expire_after_days`
  - 首版固定默认 `3`
- `refresh_status`
  - `success / failed / partial`

## 8. 数据流

推荐流程：

1. 运行 `refresh_board_concept_pool.py`
2. 先读取 `board_concept_pool_meta.json`
3. 若不存在，直接全量刷新
4. 若存在且未过期，默认不刷新
5. 若已过期：
   - 尝试从 `Tushare THS/DC` 刷新
   - 刷新成功则覆盖缓存和 metadata
   - 刷新失败则保留旧缓存，并记录 warning
6. `board_cycle_scan` 后续消费该板块池时：
   - 直接从本地缓存选板块
   - 不再把“获取板块池”这个动作绑定到扫描主流程

## 9. CLI 设计

### 9.1 新脚本

```bash
python scripts/refresh_board_concept_pool.py
```

建议参数：

- `--source`
  - `ths | dc | auto`
  - 首版默认 `auto`
- `--expire-after-days`
  - 默认 `3`
- `--force-refresh`
  - 强制刷新，忽略缓存时间
- `--output-dir`
  - 默认 `data/board_concept_pool`

### 9.2 board_cycle_scan 侧

首版不强行把刷新逻辑耦合进 `select_board_cycle_candidates.py`。

更稳的方式是：

1. 先独立刷新板块池
2. 再从板块池中挑要扫描的板块

也就是先把“板块池管理”与“板块扫描”分离。

后续如果稳定，再给 `select_board_cycle_candidates.py` 增加可选参数：

- `--board-pool-file`
- `--refresh-board-pool-if-expired`

## 10. 失败处理

必须 fail-open：

1. 远端刷新失败
   - 不中断
   - 保留旧缓存
   - metadata 写 `refresh_status=failed`
2. 本地有缓存但已过期且刷新失败
   - 继续允许扫描使用旧缓存
   - 在 `run_summary.txt` 或刷新日志中标记“过期缓存回退”
3. 本地无缓存且首次刷新失败
   - 这时才真正 fail

## 11. 测试设计

首版至少覆盖：

1. 首次无缓存时会刷新并写出两份文件
2. 缓存未过期时不会重复刷新
3. 缓存已过期时会触发刷新
4. 刷新失败时保留旧缓存
5. metadata 的 `last_updated_at` 与 `expire_after_days` 正确写入

## 12. 分阶段实施建议

推荐分两步：

### 第一步

先做“板块池清单缓存”：

- 只管理板块名单
- 记录更新时间
- 3 天刷新一次

### 第二步

再决定是否扩到“板块成分缓存”：

- 每个板块成分单独缓存
- 板块成分和板块池分开管理

这样改动更小，也更容易验证。

## 13. 结论

当前最合适的落地方向不是继续追远端实时全量板块接口，而是：

`Tushare THS/DC 题材板块池 + 本地文件缓存 + 3 天刷新 + 失败回退旧缓存`

这条路径最符合当前需求：

- 题材导向
- 更新不频繁
- 可记录最后更新时间
- 可稳定复用
- 不把远端不稳定性直接暴露到扫描主链路
