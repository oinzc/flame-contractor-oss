import { Fragment } from 'react'
import type { ContractTarget } from '../types'

interface Props {
  targets: ContractTarget[]
  succeeded: number
}

export default function FlameLine({ targets, succeeded }: Props) {
  const enabledTargets = targets.filter((target) => target.enabled)

  if (enabledTargets.length === 0) {
    return (
      <div className="flame-empty">
        尚无契约。先在账号下绑定续火好友，焰线将从这里逐节点点亮。
      </div>
    )
  }

  return (
    <>
      <div className="flame-summary">
        <span className="flame-count">
          <span className="lit">今日已续 {succeeded}</span>
          <span className="total"> / {enabledTargets.length} 焰</span>
        </span>
        <span className="panel-sub" style={{ margin: 0 }}>
          执行器未接入，节点按契约映射示意
        </span>
      </div>
      <div className="flame-track">
        {enabledTargets.map((target, index) => {
          const tone = index < succeeded ? 'lit' : 'unlit'
          return (
            <Fragment key={target.id}>
              {index > 0 && <span className="flame-link" aria-hidden="true" />}
              <div className="flame-node-wrap">
                <span
                  className={`flame-node ${tone}`}
                  title={target.nicknameSnapshot}
                  aria-label={`${target.nicknameSnapshot}：${tone === 'lit' ? '已续焰' : '待续焰'}`}
                />
                <span className="flame-label">{target.nicknameSnapshot}</span>
              </div>
            </Fragment>
          )
        })}
      </div>
    </>
  )
}
