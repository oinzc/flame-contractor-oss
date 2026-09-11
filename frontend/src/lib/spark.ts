import type { ScanChatEntry } from '../types'

type ScanSparkSnapshot = Pick<
  ScanChatEntry,
  'preview' | 'streakDays' | 'streakLabel'
>

export function sparkLabelForSnapshot(entry: ScanSparkSnapshot): string | null {
  const label = entry.streakLabel?.trim()
  if (label) return label
  if (entry.streakDays != null) return null

  const preview = entry.preview.trim()
  return preview.includes('火花') ? preview.slice(0, 64) : '无火花'
}
