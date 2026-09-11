import type { ReactNode } from 'react'
import { formatDateTime } from '../lib/format'

export interface SparkBadgeProps {
  officialDays: number | null
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
 * 只展示抖音官方火花快照；本地 streakDays 不在此组件回退显示。
 */
export default function SparkBadge({
  officialDays,
  kind,
  label,
  scannedAt,
  className = '',
  children,
}: SparkBadgeProps) {
  const officialLabel = label?.trim() || null
  const observedNoSpark = officialLabel === '无火花'
  const text = officialLabel ?? (officialDays == null ? '待扫描' : `${officialDays}天`)
  const unknown = officialLabel === null && officialDays === null

  const tone = observedNoSpark ? 'crimson' : unknown ? '' : toneFor(kind, officialLabel)
  const title = observedNoSpark
    ? `抖音官方状态：未检测到火花${scannedAt ? ` · 更新于 ${formatDateTime(scannedAt)}` : ''}`
    : officialLabel
      ? `抖音官方状态：${officialLabel}${scannedAt ? ` · 更新于 ${formatDateTime(scannedAt)}` : ''}`
      : officialDays != null
        ? `抖音官方状态：${officialDays} 天${scannedAt ? ` · 更新于 ${formatDateTime(scannedAt)}` : ''}`
        : '尚未记录抖音官方状态，请扫描或手动校准'

  return (
    <span className={`badge ${tone} spark-badge ${className}`.trim()} title={title}>
      <span className="spark-badge-glyph" aria-hidden="true">
        {observedNoSpark ? '×' : unknown ? '·' : glyphFor(officialLabel)}
      </span>
      <span className="spark-badge-text">{text}</span>
      {children}
    </span>
  )
}
