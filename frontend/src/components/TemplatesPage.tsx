import { useState } from 'react'
import { api, ApiError } from '../api'
import type { MessageTemplate } from '../types'

interface Props {
  templates: MessageTemplate[]
  onChanged: () => void
}

const VARIABLE_DOC = [
  { token: '{{target_name}}', meaning: '好友昵称' },
  { token: '{{streak_days}}', meaning: '火花天数' },
  { token: '{{weekday}}', meaning: '星期几' },
  { token: '{{date}}', meaning: '今天日期' },
]

function previewContent(content: string): string {
  const today = new Date()
  const weekday = ['日', '一', '二', '三', '四', '五', '六'][today.getDay()]
  return content
    .replaceAll('{{target_name}}', '示例好友')
    .replaceAll('{{streak_days}}', '7')
    .replaceAll('{{weekday}}', `星期${weekday}`)
    .replaceAll('{{date}}', today.toLocaleDateString('zh-CN'))
}

export default function TemplatesPage({ templates, onChanged }: Props) {
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await api.createTemplate({ name: name.trim(), content: content.trim() })
      setName('')
      setContent('')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="main-col">
      <div className="page-head">
        <div>
          <h1>文案模板</h1>
          <p className="sub">只支持四个安全变量，其余代码或语法会被拒绝。</p>
        </div>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>模板列表（{templates.length}）</h2>
        </div>
        {templates.length === 0 ? (
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            还没有模板，用下面的表单创建第一个。
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>名称</th>
                <th>内容</th>
                <th>版本</th>
              </tr>
            </thead>
            <tbody>
              {templates.map((template) => (
                <tr key={template.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>{template.name}</td>
                  <td style={{ maxWidth: 0, width: '100%' }}>
                    <div
                      style={{
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        color: 'var(--text-2)',
                      }}
                    >
                      {template.content}
                    </div>
                  </td>
                  <td className="mono">v{template.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>新建模板</h2>
        </div>
        <div className="variable-doc">
          {VARIABLE_DOC.map((item) => (
            <span className="variable-chip" key={item.token}>
              <code>{item.token}</code> {item.meaning}
            </span>
          ))}
        </div>
        <form className="form-grid" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="template-name">模板名称</label>
            <input
              id="template-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：每日一言"
              maxLength={60}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="template-content">文案内容</label>
            <textarea
              id="template-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="{{target_name}}，今天也要开心。第 {{streak_days}} 天。"
              maxLength={1000}
              rows={3}
              required
            />
          </div>
          {content.trim() && (
            <div className="preview-box">
              <div className="preview-label">发送预览</div>
              <div className="preview-text">{previewContent(content)}</div>
            </div>
          )}
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn primary small" type="submit" disabled={submitting}>
              {submitting ? '保存中…' : '保存模板'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
