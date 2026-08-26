interface Props {
  databaseReady: boolean
  onCreateAccount: () => void
}

export default function TopBar({ databaseReady, onCreateAccount }: Props) {
  const today = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  })

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-glyph" aria-hidden="true">
          焰
        </span>
        <span className="brand-title">焰之契约者</span>
        <span className="brand-tagline">每日以一言续之，焰不可熄</span>
      </div>
      <div className="topbar-right">
        <span className="today">{today}</span>
        <span className="health-chip">
          <span className={`health-dot${databaseReady ? '' : ' degraded'}`} />
          {databaseReady ? '本机控制面 · 正常' : '控制面未连接'}
        </span>
        <button className="btn primary" type="button" onClick={onCreateAccount}>
          新建账号
        </button>
      </div>
    </header>
  )
}
