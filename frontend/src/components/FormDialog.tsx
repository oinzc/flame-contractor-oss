import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

interface Props {
  title: string
  description?: string
  dirty: boolean
  busy: boolean
  formId: string
  submitLabel: string
  onClose: () => void
  children: ReactNode
}

export default function FormDialog({ title, description, dirty, busy, formId, submitLabel, onClose, children }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const [confirmClose, setConfirmClose] = useState(false)

  useEffect(() => {
    const dialog = dialogRef.current!
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.showModal()
    dialog.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), select:not([disabled])')?.focus()
    return () => {
      dialog.close()
      document.body.style.overflow = previousOverflow
      trigger?.focus()
    }
  }, [])

  useEffect(() => {
    if (confirmClose) dialogRef.current?.querySelector<HTMLButtonElement>('[data-continue-editing]')?.focus()
    else dialogRef.current?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled]), select:not([disabled])')?.focus()
  }, [confirmClose])

  function requestClose() {
    if (busy) return
    if (dirty) {
      setConfirmClose(true)
      return
    }
    onClose()
  }

  return (
    <dialog
      ref={dialogRef}
      className="modal form-dialog"
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault()
        if (confirmClose) setConfirmClose(false)
        else requestClose()
      }}
    >
      <div className="modal-head">
        <h2 id={titleId} className="modal-title">{title}</h2>
        <button className="btn ghost small" type="button" disabled={busy} onClick={requestClose}>关闭</button>
      </div>
      {description && <p className="modal-sub">{description}</p>}
      {confirmClose && (
        <div className="form-error" role="alert">
          <p>有未保存的内容，确定放弃并关闭？</p>
          <div className="form-actions">
            <button className="btn small" type="button" data-continue-editing onClick={() => setConfirmClose(false)}>继续编辑</button>
            <button className="btn ghost small" type="button" onClick={onClose}>放弃并关闭</button>
          </div>
        </div>
      )}
      <fieldset className="dialog-fields" disabled={busy} hidden={confirmClose}>{children}</fieldset>
      <div className="form-actions dialog-actions" hidden={confirmClose}>
        <button className="btn ghost small" type="button" disabled={busy} onClick={requestClose}>取消</button>
        <button className="btn primary small" type="submit" form={formId} disabled={busy}>{submitLabel}</button>
      </div>
    </dialog>
  )
}
