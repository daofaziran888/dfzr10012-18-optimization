import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/jobops'
const STATE_ENDPOINT = '/api/jobstate'
const columns = [
  "实例编号",
  "作业名称",
  "运行节点",
  "失败次数",
  "重启次数",
  "下一重试时间",
  "恢复位置",
  "实例状态"
]
const actions = [
  "记录失败",
  "恢复实例"
]

type Row = Record<string, string | number | null>
type Recovery = {
  阶段?: string
  说明?: string
  下一重试时间?: string | null
  剩余重启次数?: number | null
  恢复位置?: string | null
  存档版本?: number
  来源标识?: string
}

export default function JobopsView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [waiting, setWaiting] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [recovery, setRecovery] = useState<Recovery | null>(null)
  const [context, setContext] = useState<Row | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const stats = useMemo(() => ([
    { label: "作业实例总数", value: total },
    { label: "运行中作业", value: rows.filter((row) => row["实例状态"] === "运行中").length },
    { label: "等待重试作业", value: waiting },
  ]), [total, rows, waiting])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('作业实例列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      const stateResponse = await request(`${STATE_ENDPOINT}?status=${encodeURIComponent('等待重试')}`)
      if (stateResponse.ok) {
        const statePayload = await stateResponse.json()
        setWaiting(Number(statePayload.total ?? 0))
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流作业运维列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function loadRecovery(row: Row) {
    setErrorMessage('')
    setContext(row)
    try {
      const response = await request(`${ENDPOINT}/${row.id}/recovery`)
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.detail ?? '恢复判定读取失败')
      }
      setRecovery(payload)
    } catch (error) {
      setRecovery(null)
      setErrorMessage(error instanceof Error ? error.message : '恢复判定读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const body = action === '记录失败'
        ? { 运行节点: row["运行节点"], 失败原因: '页面登记失败', 请求标识: `page-${row.id}-${Date.now()}` }
        : { 请求标识: `recover-${row.id}-${Date.now()}` }
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action, ...body }),
      })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.message ?? '流作业运维动作未生效，请稍后重试')
      }
      setErrorMessage(payload.message ?? '')
      if (payload.result) {
        setRecovery(payload.result)
        setContext(row)
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流作业运维操作失败')
    }
  }

  return (
    <section className="page" data-module="jobops">
      <header className="page-head">
        <div>
          <h2>流作业运维管理</h2>
          <p className="page-desc">维护流作业的运行节点、失败记录与恢复位置，连续失败进入退避窗口后只等待，窗口结束再从剩余重启次数与最后检查点继续。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('作业实例登记入口尚未接入审批流')}>
            登记作业实例
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出流作业运维清单
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
                <button className="link" type="button" onClick={() => void loadRecovery(row)}>恢复判定</button>
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无流作业运维数据，可先登记作业实例</td>
            </tr>
          )}
        </tbody>
      </table>

      {recovery && (
        <article className="stat-card" style={{ marginTop: 12 }}>
          <span className="stat-label">
            作业实例状态数据存档 · {context?.["实例编号"] ?? ''} · 版本 {recovery.存档版本 ?? '—'}
          </span>
          <p className="page-desc">
            阶段：{recovery.阶段 ?? '—'}；下一重试时间：{recovery.下一重试时间 ?? '—'}；
            剩余重启次数：{recovery.剩余重启次数 ?? '—'}；恢复位置：{recovery.恢复位置 ?? '—'}
          </p>
          <p className="page-desc">来源标识：{recovery.来源标识 ?? '—'}；{recovery.说明 ?? ''}</p>
        </article>
      )}

      <footer className="page-foot">
        <span>共 {total} 条流作业运维记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
