import { Folder, History, Plus } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { Page } from '../types';
import { BrandIcon } from './BrandIcon';
import { mountScrollMotion } from '../scrollMotion';

const navigation = [
  { id: 'overview', label: '资料目录', Icon: Folder },
  { id: 'history', label: '历史记录', Icon: History },
] as const;
export function Shell({ page, onNavigate, children, privacyOpen=false, onTogglePrivacy }: { page: Page; onNavigate: (page: Page) => void; children?: ReactNode; authRequired?: boolean; onLogout?: () => void; privacyOpen?:boolean; onTogglePrivacy?:()=>void }) {
  const content = useRef<HTMLElement>(null);
  const background = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (content.current && background.current) return mountScrollMotion(content.current, background.current);
  }, []);
  return <div className="app-shell"><div ref={background} className="parallax-background" aria-hidden="true"><div className="parallax-layer parallax-layer-far" /><div className="parallax-layer parallax-layer-near" /></div><aside className="sidebar"><button type="button" className="brand brand-button" aria-label="DataBack" aria-pressed={privacyOpen} disabled={!onTogglePrivacy} onClick={onTogglePrivacy}><BrandIcon /><strong>DataBack</strong></button>
    <nav aria-label="主导航">{navigation.map(({ id, label, Icon }) => <button key={id} type="button" className={`nav-item ${id === page ? 'active' : ''}`} aria-current={id === page ? 'page' : undefined} onClick={() => onNavigate(id)}><Icon size={21} strokeWidth={1.8} /><span>{label}</span></button>)}</nav>
    </aside><main ref={content} className="main-content">{children}</main></div>;
}
export function RegisterButton({ onClick }: { onClick: () => void }) {
  return <button type="button" className="button primary" onClick={onClick}><Plus size={19} />登记备份</button>;
}
