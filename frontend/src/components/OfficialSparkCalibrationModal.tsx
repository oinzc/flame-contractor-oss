import { useEffect, useState } from 'react'
import { ApiError } from '../api'
import type { ContractTarget } from '../types'
import SparkBadge from './SparkBadge'

type OfficialSparkMode = 'days' | 'rekindling' | 'expiring' | 'none'

interface OfficialSparkPatch {
  sparkDays: number | null
  sparkLabel: string | null
}

interface Props {
  target: ContractTarget
  onSubmit: (patch: OfficialSparkPatch) => Promise<void>
  onClose: () => void
}

function initialMode(target: ContractTarget): OfficialSparkMode {
  if (target.sparkLabel === '无火花') return 'none'
  if (target.sparkLabel?.includes('重燃中')) return 'rekindling'
  if (target.sparkLabel?.includes('后消失')) return 'expiring'
  return 'days'
}

function firstNumber(value: string | null, fallback: number): string {
  const match = value?.match(/\d+/)
  return String(match ? Number.parseInt(match[0], 10) : fallback)
}

export default function OfficialSparkCalibrationModal({ target, onSubmit, onClose }: Props) {
  const [mode, setMode] = useState<OfficialSparkMode>(() => initialMode(target))
  const [days, setDays] = useState(() => String(target.sparkDays ?? 0))
  const [rekindlingStep, setRekindlingStep] = useState(() => firstNumber(target.sparkLabel, 1))
  const [expiringDays, setExpiringDays] = useState(() => firstNumber(target.sparkLabel, 1))
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const numericDays = Math.max(0, Number.parseInt(days, 10) || 0)
  const numericExpiringDays = Math.max(1, Number.parseInt(expiringDays, 10) || 1)
  const preview: OfficialSparkPatch =
    mode === 'days'
      ? { sparkDays: numericDays, sparkLabel: null }
      : mode === 'rekindling'
        ? { sparkDays: null, sparkLabel: `重燃中 ${rekindlingStep}/3` }
        : mode === 'expiring'
          ? { sparkDays: null, sparkLabel: `${numericExpiringDays} 天后消失` }
          : { sparkDays: null, sparkLabel: '无火花' }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit(preview)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '官方状态校准失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay spark-calibration-overlay">
      <div className="modal spark-calibration-modal" role="dialog" aria-modal="true" aria-label="校准抖音官方状态">
        <div className="modal-head">
          <h2 className="modal-title">校准官方状态 · {target.nicknameSnapshot}</h2>
          <button className="btn ghost small" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="modal-sub">
          按抖音当前显示修正官方快照。这里不会改本地累计，也不会触发扫描或发送。
        </p>
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="official-spark-mode">抖音当前状态</label>
            <select
              id="official-spark-mode"
              value={mode}
              onChange={(event) => setMode(event.target.value as OfficialSparkMode)}
              autoFocus
            >
              <option value="days">正常火花天数</option>
              <option value="rekindling">重燃中</option>
              <option value="expiring">即将消失</option>
              <option value="none">无火花</option>
            </select>
          </div>

          {mode === 'days' && (
            <div className="field">
              <label htmlFor="official-spark-days">官方火花天数</label>
              <input
                id="official-spark-days"
                type="number"
                min={0}
                value={days}
                onChange={(event) => setDays(event.target.value)}
                required
              />
            </div>
          )}

          {mode === 'rekindling' && (
            <div className="field">
              <label htmlFor="official-rekindling-step">重燃进度</label>
              <select
                id="official-rekindling-step"
                value={rekindlingStep}
                onChange={(event) => setRekindlingStep(event.target.value)}
              >
                <option value="1">1/3</option>
                <option value="2">2/3</option>
                <option value="3">3/3</option>
              </select>
            </div>
          )}

          {mode === 'expiring' && (
            <div className="field">
              <label htmlFor="official-expiring-days">距离消失</label>
              <div className="spark-calibration-number">
                <input
                  id="official-expiring-days"
                  type="number"
                  min={1}
                  value={expiringDays}
                  onChange={(event) => setExpiringDays(event.target.value)}
                  required
                />
                <span>天后消失</span>
              </div>
            </div>
          )}

          <div className="spark-calibration-preview" aria-live="polite">
            <span>保存后显示</span>
            <SparkBadge
              officialDays={preview.sparkDays}
              kind={target.sparkKind}
              label={preview.sparkLabel}
            />
          </div>

          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn primary" type="submit" disabled={submitting}>
              {submitting ? '保存中…' : '保存官方状态'}
            </button>
            <button className="btn" type="button" onClick={onClose}>
              取消
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
