import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { request } from '../api';
import { visibilityFence } from '../visibilityFence';
import type { Asset } from '../types';
import { Shell } from './Shell';
import { Field, Modal } from './ui';

interface PrivacyControls {
  privacyOpen: boolean;
  onTogglePrivacy: () => void;
  onSetPrivate: (asset: Asset) => void;
}
type PrivacyCommand = {kind:'unlock'} | {kind:'set'; asset:Asset};
const LOCK_EVENT = 'databack-privacy-lock';

function PasswordDialog({command, onSubmit, onClose}: {command:PrivacyCommand; onSubmit:(password:string)=>Promise<void>; onClose:()=>void}) {
  const [password,setPassword]=useState('');
  const [busy,setBusy]=useState(false);
  const pending=useRef(false);
  async function submit(event:FormEvent) {
    event.preventDefault();
    if(pending.current || !password) return;
    pending.current=true;setBusy(true);
    try {await onSubmit(password);} finally {pending.current=false;setBusy(false);}
  }
  return <Modal title={command.kind==='unlock'?'输入密码':command.asset.private?'取消隐私':'设为隐私'} busy={busy} onClose={onClose}><form className="form-body privacy-password-form" onSubmit={submit}>
    <Field label="隐私密码"><input type="password" autoFocus autoComplete="off" maxLength={1024} value={password} disabled={busy} onChange={event=>setPassword(event.target.value)}/></Field>
    <div className="form-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy||!password}>{busy?'确认中…':'确认'}</button></div>
  </form></Modal>;
}

export function PrivacyWorkspace({children,loadingLabel='读取中…'}:{children:(controls:PrivacyControls)=>ReactNode;loadingLabel?:string}) {
  const [ready,setReady]=useState(false);
  const [privacyOpen,setPrivacyOpen]=useState(false);
  const [epoch,setEpoch]=useState(0);
  const [command,setCommand]=useState<PrivacyCommand|null>(null);
  const [lockError,setLockError]=useState(false);
  const sequence=useRef(0);
  const mounted=useRef(true);
  const pending=useRef(false);
  const hideLocal=useCallback(()=>{
    visibilityFence.invalidate();setReady(false);setPrivacyOpen(false);setCommand(null);setLockError(false);
    window.history.replaceState(null,'','#/');
  },[]);
  const announceLock=useCallback(()=>{try {localStorage.setItem(LOCK_EVENT,crypto.randomUUID());}catch{/* Private mode may disable browser storage. */}},[]);
  const lock=useCallback(async (announce=true)=>{
    const current=++sequence.current;hideLocal();if(announce)announceLock();
    try {
      await request('privacy/lock','POST',{});
      if(mounted.current&&sequence.current===current){setEpoch(value=>value+1);setReady(true);}
    } catch {
      if(mounted.current&&sequence.current===current)setLockError(true);
    }
  },[announceLock,hideLocal]);
  useEffect(()=>{
    mounted.current=true;void lock();
    const changed=(event:StorageEvent)=>{if(event.key===LOCK_EVENT)void lock(false);};
    const restored=(event:PageTransitionEvent)=>{if(event.persisted)void lock();};
    const leaving=()=>{
      visibilityFence.invalidate();announceLock();
      // Same-origin, bounded JSON request; the next page also confirms the lock.
      navigator.sendBeacon('/api/privacy/lock',new Blob(['{}'],{type:'application/json'}));
    };
    window.addEventListener('storage',changed);window.addEventListener('pageshow',restored);window.addEventListener('pagehide',leaving);
    return ()=>{mounted.current=false;sequence.current++;visibilityFence.invalidate();window.removeEventListener('storage',changed);window.removeEventListener('pageshow',restored);window.removeEventListener('pagehide',leaving);};
  },[announceLock,lock]);
  useEffect(()=>{
    if(!privacyOpen)return;
    const timer=setTimeout(()=>void lock(),30*60*1000);
    return ()=>clearTimeout(timer);
  },[privacyOpen,lock]);
  async function submit(password:string) {
    if(pending.current||!command)return;
    pending.current=true;const current=sequence.current;const action=command;
    try {
      if(action.kind==='unlock') {
        const result=await request<{unlocked:boolean;token?:string}>('privacy/unlock','POST',{password});
        if(!mounted.current||sequence.current!==current)return;
        if(result.unlocked&&typeof result.token==='string'&&/^[A-Za-z0-9_-]{43}$/.test(result.token)){visibilityFence.invalidate();visibilityFence.setPrivacyToken(result.token);window.history.replaceState(null,'','#/');setReady(false);setPrivacyOpen(true);setEpoch(value=>value+1);setReady(true);}
      } else {
        const result=await request<{changed:boolean}>(`assets/${action.asset.id}/privacy`,'POST',{password,private:action.asset.private!==true});
        if(!mounted.current||sequence.current!==current)return;
        if(result.changed)await lock();
      }
    } catch {
      // Password errors and throttling deliberately leave the page unchanged.
    } finally {if(mounted.current&&sequence.current===current)setCommand(null);pending.current=false;}
  }
  const controls:PrivacyControls={privacyOpen,onTogglePrivacy:()=>{if(privacyOpen)void lock();else setCommand({kind:'unlock'});},onSetPrivate:asset=>setCommand({kind:'set',asset})};
  return <>{ready?<div key={epoch} className="privacy-workspace">{children(controls)}</div>:<Shell page="overview" onNavigate={()=>{}} privacyOpen={false} onTogglePrivacy={()=>void lock()}><div className="privacy-loading">{lockError?<button className="button secondary" onClick={()=>void lock()}>重新读取</button>:<span>{loadingLabel}</span>}</div></Shell>}
    {command?<PasswordDialog key={command.kind==='set'?command.asset.id:'unlock'} command={command} onSubmit={submit} onClose={()=>setCommand(null)}/>:null}
  </>;
}
