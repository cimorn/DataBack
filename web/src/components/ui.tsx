import { cloneElement, isValidElement, useEffect, useId, useRef } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { X } from 'lucide-react';

export function Modal({ title, onClose, children, wide = false, busy = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; busy?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); };
  }, []);
  return <dialog ref={dialog} className={`modal ${wide ? 'modal-wide' : ''}`} aria-labelledby={headingId}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="modal-header"><h2 id={headingId}>{title}</h2><button type="button" className="icon-button" aria-label="关闭对话框" disabled={busy} onClick={onClose}><X size={20} /></button></div>
    {children}
  </dialog>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const control = isValidElement(children) ? cloneElement(children as ReactElement<{ id?: string; 'aria-describedby'?: string }>, { id: inputId, 'aria-describedby': hint ? hintId : undefined }) : children;
  return <div className="field"><label htmlFor={inputId}>{label}</label>{control}{hint ? <small id={hintId}>{hint}</small> : null}</div>;
}
export function FormError({ message }: { message: string }) {
  return message ? <div className="error-message" role="alert">{message}</div> : null;
}
export function EmptyState({ title, text, action }: { title: string; text?: string; action?: ReactNode }) {
  return <div className="empty-state"><h3>{title}</h3>{text ? <p>{text}</p> : null}{action}</div>;
}
export function PageHeading({ title, action }: { title: string; action?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1></div>{action}</header>;
}
export function FormActions({ onCancel, busy, label = '保存' }: { onCancel: () => void; busy: boolean; label?: string }) {
  return <div className="form-actions"><button type="button" className="button secondary" onClick={onCancel} disabled={busy}>取消</button><button type="submit" className="button primary" disabled={busy}>{busy ? '正在保存…' : label}</button></div>;
}
