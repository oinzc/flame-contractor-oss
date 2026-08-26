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

export default function TemplatesPanel({ templates, onChanged }: Props) {
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
    <section className="panel">
      <h2 className="panel-title">文案模板</h2>
      <p className="panel-sub">
        只允许四个受控变量，其余代码或语法会被拒绝。
      </p>

      <div className="variable-doc">
        {VARIABLE_DOC.map((item) => (
          <span className="variable-chip" key={item.token}>
            <code>{item.token}</code> {item.meaning}
          </span>
        ))}
      </div>

      {templates.length > 0 && (
        <div className="row-list" style={{ margin: '10px 0 14px' }}>
          {templates.map((template) => (
            <div className="row-item" key={template.id}>
              <div className="row-main">
                <div className="row-title">
                  {template.name}
                  <span className="stage-chip">v{template.version}</span>
                </div>
                <div className="row-sub">{template.content}</div>
              </div>
            </div>
          ))}
        </div>
      )}

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
            placeholder="{{target_name}}，今天也要开心。第 {{streak_days}} 天，焰不可熄。"
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
  )
}
