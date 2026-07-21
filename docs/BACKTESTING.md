# Market Lens 回测数据说明

回测分成“历史截面采集”和“离线评估”两步。普通 `/api/analyze` 结果不能直接作为历史快照；采集数据必须能够证明成员关系、行业分类和财务公告在分析时点已知。

## 1. 历史股票样本清单

样本清单使用 `stock-universe-1`。`includes_delisted`、`point_in_time_verified` 和 `historical_industry_verified` 必须全部为 `true`，否则采集器拒绝运行。

```json
{
  "schema_version": "stock-universe-1",
  "name": "历史样本名称",
  "source": "可审计的数据来源或文件版本",
  "point_in_time_verified": true,
  "includes_delisted": true,
  "historical_industry_verified": true,
  "entries": [
    {
      "code": "600519",
      "name": "贵州茅台",
      "memberships": [
        {"start": "2001-08-27", "end": null}
      ],
      "industries": [
        {
          "start": "2001-08-27",
          "end": null,
          "em_industry": "酿酒行业",
          "csrc_industry": "酒、饮料和精制茶制造业",
          "source": "历史行业分类来源"
        }
      ]
    }
  ]
}
```

成员区间和行业区间不能重叠。任一调仓日缺少历史行业分类都会使清单验证失败。只包含当前上市股票的清单不满足要求。

## 2. 采集历史截面

```powershell
uv run market-lens collect-stock-backtest .\universe.json `
  --output .\stock-backtest-dataset.json `
  --start 2018-01-01 `
  --end 2025-12-31 `
  --frequency quarterly
```

默认严格模式。任何历史成员快照因价格、估值、成员关系或行业数据缺失而跳过时，采集会失败。`--allow-partial` 仅用于排查数据源，不能据此发布模型参数。

采集规则：

- 使用月末或季末之前最后一个交易日作为 `analysis_as_of`，超过 7 天的旧价格不接受。
- 财务指标必须具有 `notice_date`，且公告日不能晚于 `analysis_as_of`。
- 历史估值只使用分析日及以前的数据。
- 行业横截面使用对应估值交易日和历史板块代码。
- 收益价格使用前复权日线；回测入场仍固定为分析日后的首个交易日。
- 每个快照写入 `backtest_provenance.point_in_time_verified=true` 和采集方法版本。

## 3. 生成离线报告

```powershell
uv run market-lens backtest .\stock-backtest-dataset.json `
  --output .\stock-backtest-report.json
```

报告包含数据指纹、持有期分层收益、信息系数、单调性、最大回撤、换手、评分稳定性，以及行业、模型、市场阶段和置信度分组。综合吸引力候选只在训练日期中选择，再在后续验证日期中评估。

## 4. 当前限制

- 真实样本清单及其历史来源尚未接入项目，当前不能生成可用于调参的正式报告。
- 退市股票在持有期内缺少可靠终止价值时会出现结果截尾，正式报告前必须单独统计和处理。
- 当前未计入交易费用、税费、滑点、停牌成交限制和容量约束。
- 基金持仓报告日不等于实际披露日；历史持仓、费率和规模快照未验证前禁止进行基金参数校准。
- V2-7 只产出研究证据，不会自动修改生产权重或发布 `attractiveness`。
