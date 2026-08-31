import type { ReactNode } from 'react'
import { formatDateTime } from '../lib/format'

export interface SparkBadgeProps {
  days: number | null
  kind?: string | null
  label?: string | null
  scannedAt?: string | null
  className?: string
  children?: ReactNode
}

function toneFor(kind: string | null | undefined, label: string | null): string {
  if (label?.includes('后消失')) return 'gold'
  if (kind === 'couple') return 'gold'
  if (kind === 'family') return 'cyan'
  return 'ember'
}

function glyphFor(label: string | null): string {
  return label?.includes('后消失') ? '⌛' : '🔥'
}

/**
 * 统一展示本地火花天数与抖音官方状态。
 * 官方 label 优先，但永远不会覆盖本地 streakDays 的业务值。
 */
export default function SparkBadge({
  days,
  kind,
  label,
  scannedAt,
  className = '',
  children,
}: SparkBadgeProps) {
  const officialLabel = label?.trim() || null
  const text = officialLabel ?? (days == null ? null : `${days}天`)
  if (text === null) return null

  const tone = toneFor(kind, officialLabel)
  const title = officialLabel
    ? `抖音官方状态：${officialLabel}${scannedAt ? ` · 扫描于 ${formatDateTime(scannedAt)}` : ''}`
    : `本地累计火花：${days}天`

  return (
    <span className={`badge ${tone} spark-badge ${className}`.trim()} title={title}>
      <span className="spark-badge-glyph" aria-hidden="true">
        {glyphFor(officialLabel)}
      </span>
      <span className="spark-badge-text">{text}</span>
      {children}
    </span>
  )
}
