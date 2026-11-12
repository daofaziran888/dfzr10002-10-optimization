import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dataset'
const columns = [
  "数据集编号",
  "数据集名称",
  "来源表名",
  "当前代次",
  "字段列表",
  "行数上限",
  "刷新频率",
  "最近刷新",
  "是否启用"
]
const actions = [
  "提交校验",
  "启用配置",
  "停用配置"
]
const statuses = [
  "草稿",
  "待校验",
  "已启用",
  "已停用"
]
const stats = [
  {
    "label": "分析数据集总数",
    "value": 0
  },
  {
    "label": "待校验数据集",
    "value": 0
  },
  {
    "label": "已启用数据集",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type GenerationRecord = {
  id: number
  数据集编号: string
  代次: number | null
  状态: string
  来源表名: string
  字段列表: string[]
  行数上限: number | null
  开始时间: string | null
  最近刷新: string | null
  失败时间: string | null
  失败原因: string | null
}

type CompareResult = {
  数据集编号: string
  字段差异: { 新增: string[]; 移除: string[]; base字段列表: string[]; target字段列表: string[] }
  行数上限差异: { base: number; target: number; 差值: number }
  最近刷新差异: { base: string | null; target: string | null }
}

export default function DatasetView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [compareFor, setCompareFor] = useState<Row | null>(null)
  const [generations, setGenerations] = useState<GenerationRecord[]>([])
  const [currentGeneration, setCurrentGeneration] = useState<number | null>(null)
  const [baseGen, setBaseGen] = useState<number | ''>('')
  const [targetGen, setTargetGen] = useState<number | ''>('')
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null)

  const successGens = useMemo(
    () =>
      generations
        .filter((item) => item.状态 === '成功' && item.代次 !== null)
        .map((item) => Number(item.代次)),
    [generations],
  )

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('数据集列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析数据集列表读取失败')
    }
  }, [filters])

  const reloadGenerations = useCallback(async (row: Row) => {
    const response = await request(`${ENDPOINT}/${row.id}/generations`)
    if (!response.ok) {
      throw new Error('刷新记录读取失败')
    }
    const payload = await response.json()
    const items: GenerationRecord[] = payload.items ?? []
    setGenerations(items)
    setCurrentGeneration(payload.当前代次 ?? null)
    const success = items.filter((item) => item.状态 === '成功' && item.代次 !== null)
    const lastTwo = success.slice(-2).map((item) => Number(item.代次))
    setBaseGen(lastTwo.length >= 2 ? lastTwo[0] : lastTwo[0] ?? '')
    setTargetGen(lastTwo.length >= 2 ? lastTwo[1] : lastTwo[0] ?? '')
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (!compareFor || baseGen === '' || targetGen === '') {
      setCompareResult(null)
      return
    }
    let cancelled = false
    request(`${ENDPOINT}/${compareFor.id}/compare?base=${baseGen}&target=${targetGen}`)
      .then(async (response) => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}))
          throw new Error(payload.detail ?? '代次对比读取失败')
        }
        return response.json()
      })
      .then((payload: CompareResult) => {
        if (!cancelled) {
          setCompareResult(payload)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCompareResult(null)
          setErrorMessage(error instanceof Error ? error.message : '代次对比读取失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [compareFor, baseGen, targetGen])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('分析数据集动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析数据集操作失败')
    }
  }

  async function runRefresh(row: Row, simulate: '成功' | '来源表超时') {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/refresh`, {
        method: 'POST',
        body: JSON.stringify({ values: { 模拟结果: simulate } }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.detail ?? '刷新请求未送达')
      }
      setNotice(payload.message ?? (payload.ok ? '刷新成功' : '刷新失败'))
      await reload()
      if (compareFor && compareFor.id === row.id) {
        await reloadGenerations(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据集刷新失败')
    }
  }

  async function openCompare(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      setCompareFor(row)
      await reloadGenerations(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '刷新记录读取失败')
    }
  }

  return (
    <section className="page" data-module="dataset">
      <header className="page-head">
        <div>
          <h2>分析数据集管理</h2>
          <p className="page-desc">维护分析可用的数据集定义、字段范围与刷新策略，支撑多维度分析。列表只展示当前有效代次。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('数据集登记入口尚未接入审批流')}>
            登记数据集
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分析数据集清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void runRefresh(row, '成功')}>
                  刷新
                </button>
                <button className="link" type="button" onClick={() => void runRefresh(row, '来源表超时')}>
                  模拟来源表超时
                </button>
                <button className="link" type="button" onClick={() => void openCompare(row)}>
                  代次对比
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无分析数据集数据，可先登记数据集</td>
            </tr>
          )}
        </tbody>
      </table>

      {compareFor && (
        <section className="compare-panel">
          <header className="compare-head">
            <h3>
              代次对比：{String(compareFor.数据集编号 ?? '')}
              {currentGeneration !== null && `（当前有效：第${currentGeneration}代）`}
            </h3>
            <button className="btn ghost" type="button" onClick={() => setCompareFor(null)}>收起</button>
          </header>

          <h4 className="compare-subtitle">刷新记录（按代次排列）</h4>
          <table className="data-table">
            <thead>
              <tr>
                <th>代次</th>
                <th>状态</th>
                <th>来源表名</th>
                <th>开始时间</th>
                <th>最近刷新</th>
                <th>失败时间</th>
                <th>失败原因</th>
              </tr>
            </thead>
            <tbody>
              {generations.map((item) => (
                <tr key={item.id} className={item.状态 === '成功' ? '' : 'generation-abnormal'}>
                  <td>{item.代次 !== null ? `第${item.代次}代` : '—（未生效）'}</td>
                  <td>{item.状态}</td>
                  <td>{item.来源表名}</td>
                  <td>{item.开始时间 ?? '—'}</td>
                  <td>{item.最近刷新 ?? '—'}</td>
                  <td>{item.失败时间 ?? '—'}</td>
                  <td>{item.失败原因 ?? '—'}</td>
                </tr>
              ))}
              {generations.length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={7}>暂无刷新记录</td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="compare-picker">
            <span>对比代次：</span>
            <select value={baseGen} onChange={(event) => setBaseGen(Number(event.target.value))}>
              {successGens.map((gen) => <option key={gen} value={gen}>第{gen}代</option>)}
            </select>
            <span>对比</span>
            <select value={targetGen} onChange={(event) => setTargetGen(Number(event.target.value))}>
              {successGens.map((gen) => <option key={gen} value={gen}>第{gen}代</option>)}
            </select>
          </div>

          {compareResult && (
            <table className="data-table compare-table">
              <thead>
                <tr>
                  <th>对比项</th>
                  <th>第{String(baseGen)}代</th>
                  <th>第{String(targetGen)}代</th>
                  <th>差异</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>字段列表</td>
                  <td>{compareResult.字段差异.base字段列表.join('、') || '—'}</td>
                  <td>{compareResult.字段差异.target字段列表.join('、') || '—'}</td>
                  <td>
                    {compareResult.字段差异.新增.length > 0 && (
                      <span className="diff-added">新增：{compareResult.字段差异.新增.join('、')}</span>
                    )}
                    {compareResult.字段差异.移除.length > 0 && (
                      <span className="diff-removed">移除：{compareResult.字段差异.移除.join('、')}</span>
                    )}
                    {compareResult.字段差异.新增.length === 0 && compareResult.字段差异.移除.length === 0 && '无差异'}
                  </td>
                </tr>
                <tr>
                  <td>行数上限</td>
                  <td>{compareResult.行数上限差异.base}</td>
                  <td>{compareResult.行数上限差异.target}</td>
                  <td className={compareResult.行数上限差异.差值 !== 0 ? 'diff-added' : ''}>
                    {compareResult.行数上限差异.差值 > 0 && '+'}
                    {compareResult.行数上限差异.差值}
                  </td>
                </tr>
                <tr>
                  <td>最近刷新</td>
                  <td>{compareResult.最近刷新差异.base ?? '—'}</td>
                  <td>{compareResult.最近刷新差异.target ?? '—'}</td>
                  <td>—</td>
                </tr>
              </tbody>
            </table>
          )}
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条分析数据集记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
