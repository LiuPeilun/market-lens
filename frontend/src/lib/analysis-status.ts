import type { AnalysisResult, PersistenceStatus, ValuationAssessment } from '@/lib/api'

export type AnalysisStatus = 'complete' | 'degraded' | 'unavailable'

const reasonLabels: Record<string, string> = {
  eastmoney_index_price_history_empty: '东方财富指数行情为空',
  eastmoney_index_price_history_unavailable: '东方财富指数行情不可用',
  exchange_fund_price_history_not_applicable: '该基金不适用场内价格路径',
  exchange_fund_price_history_unavailable: '场内基金价格不可用',
  fund_holdings_route_unavailable: '基金持仓路由不可用',
  fund_index_matrix_selected: '已选择指数估值降级路径',
  fund_nav_data_unavailable: '基金净值和场内价格均不可用',
  fund_nav_history_empty: '基金净值历史为空',
  fund_nav_history_unavailable: '基金净值历史不可用',
  fund_tracking_relationship_unavailable: '基金跟踪关系无法确认',
  fund_valuation_unavailable: '基金没有可验证的估值结果',
  holdings_valuation_unavailable: '持仓数据未通过估值准入条件',
  index_fallback_unavailable: '指数估值降级路径不可用',
  index_price_position_proxy_unavailable: '指数价格位置代理不可用',
  index_valuation_unavailable: '指数估值不可用',
  last_known_good_snapshot: '正在使用经过校验的历史稳定快照',
  limited_holdings_snapshot: '仅有有限持仓披露数据',
  official_index_complete_weights_unavailable: '官方指数完整权重不可用',
  official_index_full_weights_not_published: '官方指数公开源未披露完整权重',
  official_index_fund_mapping_unconfirmed: '官方来源未确认基金与指数的映射',
  official_index_fundamentals_unavailable: '官方指数基本面数据不可用',
  official_index_product_validation_unavailable: '官方指数相关产品校验暂不可用',
  official_index_route_rejected: '官方指数数据未通过身份或完整性校验',
  official_index_scoring_gates_not_met: '官方指数数据未达到评分条件',
  official_index_valuation_unavailable: '官方指数估值历史不可用',
  primary_valuation_unavailable: '主要估值方法不可用，已使用替代方法',
  reit_production_model_unavailable: 'REIT 正式估值模型尚不可用',
  reit_profile_unavailable: 'REIT 详情数据不可用',
  sina_index_price_history_empty: '新浪指数行情为空',
  sina_index_price_history_unavailable: '新浪指数行情不可用',
  stock_price_data_unavailable: '股票价格数据不可用',
  stock_price_history_empty: '股票价格历史为空',
  stock_price_history_unavailable: '股票价格历史不可用',
  stock_valuation_history_empty: '股票估值历史为空',
  stock_valuation_history_unavailable: '股票估值历史不可用',
  stock_valuation_score_unavailable: '股票估值因子未达到评分条件',
  target_etf_nav_history_empty: '目标 ETF 净值历史为空',
  target_etf_nav_history_unavailable: '目标 ETF 净值历史不可用',
  target_etf_relationship_official_mismatch: '目标 ETF 与官方指数产品映射不一致',
  tracked_index_identity_incomplete: '跟踪指数身份信息不完整',
  tracked_index_not_applicable: '该标的不适用指数估值路径',
  valuation_price_projection_unavailable: '无法从估值历史恢复价格序列',
  valuation_score_unavailable: '估值数据未达到评分条件',
}

const methodLabels: Record<string, string> = {
  fundamental_valuation: '基本面估值',
  holdings_valuation: '持仓加权估值',
  index_fundamental_valuation: '指数基本面估值',
  last_known_good: '历史稳定快照',
  price_position_proxy: '价格位置代理',
  unavailable: '暂无可验证估值',
}

export function analysisStatus(result: AnalysisResult | undefined): AnalysisStatus | null {
  if (!result) return null
  const explicit = result.assessment?.status
  if (explicit) return explicit
  return Number.isFinite(result.valuation.score) ? 'complete' : 'unavailable'
}

export function analysisStatusLabel(status: AnalysisStatus | null) {
  if (status === 'complete') return '分析完整'
  if (status === 'degraded') return '降级分析'
  if (status === 'unavailable') return '暂不可估值'
  return '等待分析'
}

export function analysisStatusDescription(status: AnalysisStatus | null) {
  if (status === 'complete') {
    return '主要估值方法已通过数据准入和评分条件。'
  }
  if (status === 'degraded') {
    return '已保留可验证结果，但使用了替代数据、有限披露或代理方法。'
  }
  if (status === 'unavailable') {
    return '当前没有足够的可验证数据生成数值估值；已取得的行情和收益信息仍会保留。'
  }
  return '提交标的后显示数据完整性和估值状态。'
}

export function assessmentMethodLabel(assessment: ValuationAssessment | null | undefined) {
  const method = assessment?.method
  return method ? (methodLabels[method] ?? humanizeCode(method)) : '—'
}

export function fallbackReasonLabel(reason: string) {
  if (reason.startsWith('missing_factor:')) {
    return `缺少估值因子：${humanizeCode(reason.slice('missing_factor:'.length))}`
  }
  return reasonLabels[reason] ?? humanizeCode(reason)
}

export function persistenceMessage(persistence: PersistenceStatus | null | undefined) {
  if (!persistence || persistence.status === 'saved' || persistence.status === 'not_attempted') {
    return null
  }
  const operations = persistence.failed_operations.map(persistenceOperationLabel)
  const suffix = operations.length ? `失败环节：${operations.join('、')}。` : ''
  return persistence.status === 'partial'
    ? `分析结果已返回，但部分记录未保存。${suffix}`
    : `分析结果已返回，但未能保存到历史记录。${suffix}`
}

function persistenceOperationLabel(operation: string) {
  const labels: Record<string, string> = {
    analysis_result: '分析结果',
    assistant_message: '助手消息',
    session_context: '会话上下文',
    user_message: '用户消息',
  }
  return labels[operation] ?? humanizeCode(operation)
}

function humanizeCode(value: string) {
  return value.replaceAll('_', ' ')
}
