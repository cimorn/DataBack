import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export interface MenuAction {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
}

export function ActionMenu({label, actions}: {label: string; actions: MenuAction[]}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({top: 0, left: 0});
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = (focus = false) => {
    setOpen(false);
    if (focus) trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const rect = trigger.current!.getBoundingClientRect();
    const height = Math.min(menu.current!.getBoundingClientRect().height, window.innerHeight - 16);
    setPosition({
      top: Math.max(8, Math.min(rect.bottom + 5, window.innerHeight - height - 8)),
      left: Math.max(8, Math.min(rect.right - 190, window.innerWidth - 198)),
    });
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    };
    const dismiss = () => setOpen(false);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('scroll', dismiss, true);
    };
  }, [open]);

  return <div ref={root} className="action-menu" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
  }}>
    <button ref={trigger} type="button" className="text-button action-menu-trigger" aria-label={label}
      aria-haspopup="menu" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}
      onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          setOpen(true);
        }
      }}>编辑<ChevronDown size={14}/></button>
    <div ref={menu} id={id} role="menu" aria-label={label} hidden={!open} className="action-menu-options"
      style={{top: position.top, left: position.left}} onKeyDown={event => {
        if (event.key === 'Tab') { close(); return; }
        const buttons = Array.from(menu.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
          buttons[next]?.focus();
        }
      }}>
      {actions.map(action => <button key={action.label} type="button" role="menuitem"
        className={action.danger ? 'danger' : ''} disabled={action.disabled} onClick={() => {
          close(true);
          action.onSelect();
        }}>{action.label}</button>)}
    </div>
  </div>;
}
