import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

interface Props {
  accountId: string
  /** 每次轮询自增；变化即重新拉取最新一帧（端点已设 no-store） */
  tick: number
}

/**
 * 登录二维码截图。
 *
 * 图片端点走 Bearer 鉴权，`<img src>` 无法携带 Authorization 头（浏览器只会
 * 得到 401 裂图），因此必须用 fetch 取回后转成 object URL 渲染。
 * 刷新时保留上一帧直到新帧就绪，避免每轮轮询闪一下空白。
 */
export default function QrCodeImage({ accountId, tick }: Props) {
  const [url, setUrl] = useState<string | null>(null)
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .qrImage(accountId)
      .then((blob) => {
        if (cancelled) return
        const next = URL.createObjectURL(blob)
        const previous = urlRef.current
        urlRef.current = next
        setUrl(next)
        if (previous) URL.revokeObjectURL(previous)
      })
      .catch(() => {
        /* 保留上一帧；下一轮 tick 会自动重试 */
      })
    return () => {
      cancelled = true
    }
  }, [accountId, tick])

  useEffect(
    () => () => {
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current)
        urlRef.current = null
      }
    },
    [],
  )

  if (!url) {
    return <div className="qr-hint">二维码生成中，请稍候…</div>
  }
  return <img src={url} alt="抖音登录二维码（截图实时刷新）" />
}
