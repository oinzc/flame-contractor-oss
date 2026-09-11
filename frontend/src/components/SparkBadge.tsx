import type { ReactNode } from 'react'
import { formatDateTime } from '../lib/format'

export interface SparkBadgeProps {
  days: number | null
  officialDays?: number | null
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
 * 统一展示抖音官方火花状态；没有官方数字快照时才回退到本地累计值。
 * 本地 streakDays 仍保留给发送模板和业务规则，不被扫描覆盖。
 */
export default function SparkBadge({
  days,
  officialDays = null,
  kind,
  label,
  scannedAt,
  className = '',
  children,
}: SparkBadgeProps) {
  const officialLabel = label?.trim() || null
  const observedNoSpark = officialLabel === '无火花'
  const displayDays = officialDays ?? days
  const text = officialLabel ?? (displayDays == null ? null : `${displayDays}天`)
  if (text === null) return null

  const tone = observedNoSpark ? 'crimson' : toneFor(kind, officialLabel)
  const title = observedNoSpark
    ? `抖音官方状态：未检测到火花 · 扫描于 ${formatDateTime(scannedAt ?? null)}${days == null ? '' : ` · 本地累计记录 ${days} 天（仅历史）`}`
    : officialLabel
      ? `抖音官方状态：${officialLabel}${scannedAt ? ` · 扫描于 ${formatDateTime(scannedAt)}` : ''}`
      : officialDays != null
        ? `抖音官方状态：${officialDays} 天${scannedAt ? ` · 扫描于 ${formatDateTime(scannedAt)}` : ''}${days == null ? '' : ` · 本地累计记录 ${days} 天（仅用于发送模板）`}`
        : `本地累计火花：${days}天${scannedAt ? ' · 官方数字待下次扫描' : ''}`

  return (
    <span className={`badge ${tone} spark-badge ${className}`.trim()} title={title}>
      <span className="spark-badge-glyph" aria-hidden="true">
        {observedNoSpark ? '×' : glyphFor(officialLabel)}
      </span>
      <span className="spark-badge-text">{text}</span>
      {children}
    </span>
  )
}
