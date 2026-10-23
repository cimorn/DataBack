import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {existsSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
registerHooks({
 resolve(specifier,context,next){if(specifier.startsWith('.')&&context.parentURL?.includes('/src/')){for(const suffix of ['', '.ts','.tsx']){const url=new URL(specifier+suffix,context.parentURL);if(existsSync(fileURLToPath(url)))return {url:url.href,shortCircuit:true};}}return next(specifier,context);},
 load(url,context,next){if(url.includes('/src/')&&/\.tsx?$/.test(url))return {format:'module',source:ts.transpileModule(readFileSync(fileURLToPath(url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,shortCircuit:true};return next(url,context);},
});
const state={assets:[{id:'a',name:'资料',kind:'folder',parent_id:null,backup_target:true}],versions:[{id:'v-old',asset_id:'a'},{id:'v-new',asset_id:'a'},{id:'v-other',asset_id:'other'}],locations:[{id:'disk',name:'硬盘'},{id:'cloud',name:'网盘'}],records:[
 {id:'r1',version_id:'v-new',location_id:'disk',backed_up_at:'2026-01-01T00:00:00Z',notes:'旧备注'},
 {id:'r2',version_id:'v-old',location_id:'cloud',backed_up_at:'2026-02-01T00:00:00Z',notes:'最近备注'},
 {id:'r3',version_id:'v-other',location_id:'disk',backed_up_at:'2026-03-01T00:00:00Z',notes:'其他资料'},
]};
test('reuse-last copies latest same-asset location and notes with a fresh time, without mutating history',async()=>{
 const {reuseLatestRecordDraft}=await import('../src/recordEditing.ts');const before=JSON.stringify(state);
 const draft=reuseLatestRecordDraft(state,'a','2026-10-22T13:45');
 assert.deepEqual(draft,{time:'2026-10-22T13:45',location:'网盘',notes:'最近备注'});assert.equal(JSON.stringify(state),before);
 assert.equal(reuseLatestRecordDraft(state,'missing'),undefined);
});
test('bulk changes include only selected fields and allow deliberate clearing of notes',async()=>{
 const {buildBulkRecordChanges}=await import('../src/recordEditing.ts');
 assert.deepEqual(buildBulkRecordChanges({time:false,location:false,notes:true},{time:'bad',location:'',notes:'  '}),{notes:''});
 assert.deepEqual(buildBulkRecordChanges({time:false,location:true,notes:false},{time:'bad',location:' 网盘 ',notes:'ignored'}),{location_name:'网盘'});
 assert.throws(()=>buildBulkRecordChanges({time:false,location:false,notes:false},{time:'',location:'',notes:''}),/选择/);
 assert.throws(()=>buildBulkRecordChanges({time:true,location:false,notes:false},{time:'bad',location:'',notes:''}),/时间/);
 assert.throws(()=>buildBulkRecordChanges({time:false,location:true,notes:false},{time:'',location:' ',notes:''}),/位置/);
 assert.throws(()=>buildBulkRecordChanges({time:false,location:false,notes:true},{time:'',location:'',notes:'x'.repeat(2001)}),/2000/);
});
test('refresh retry after committed recovery does not repeat mutation',async()=>{
 const {createRefreshSafeAction}=await import('../src/recoveryActions.ts');let writes=0;let reads=0;
 const action=createRefreshSafeAction(async()=>{writes++;return {restored:true};});
 await assert.rejects(action.run(async()=>{reads++;throw new Error('refresh unavailable');}),/refresh unavailable/);
 assert.equal(action.committed(),true);await action.run(async()=>{reads++;});assert.equal(writes,1);assert.equal(reads,2);
});
test('failed recovery mutation remains retryable and parallel clicks share the attempt',async()=>{
 const {createRefreshSafeAction}=await import('../src/recoveryActions.ts');let writes=0;let release;
 const action=createRefreshSafeAction(async()=>{writes++;if(writes===1)throw new Error('collision');return new Promise(resolve=>{release=resolve;});});
 await assert.rejects(action.run(async()=>{}),/collision/);assert.equal(action.committed(),false);
 const first=action.run(async()=>{});const second=action.run(async()=>{});await Promise.resolve();release({restored:true});await Promise.all([first,second]);assert.equal(writes,2);
});
const render=(component,props)=>renderToStaticMarkup(createElement(component,props));const noop=()=>{};
test('reuse-last is explicit in new-record dialogs and absent when editing historical records',async()=>{
 const {BackupDialog}=await import('../src/components/BackupForms.tsx');const {BackupInfoDialog}=await import('../src/components/BackupInfoDialog.tsx');
 assert.match(render(BackupDialog,{state,initialAssetId:'a',onClose:noop,onSaved:noop,onChooseTargets:noop}),/>沿用上次</);
 assert.match(render(BackupInfoDialog,{state,asset:state.assets[0],initialMode:'new',onClose:noop,onSaved:noop}),/>沿用上次</);
 assert.doesNotMatch(render(BackupInfoDialog,{state,asset:state.assets[0],onClose:noop,onSaved:noop}),/>沿用上次</);
});
test('history offers bulk edit without checkboxes until explicitly selected',async()=>{
 const {History}=await import('../src/features/History.tsx');const html=render(History,{state,initialFilters:{},onRegister:noop,onEdit:noop,onDelete:noop,onBulkEdit:noop});
 assert.match(html,/>批量修改</);assert.doesNotMatch(html,/type="checkbox"/);
});
test('bulk edit dialog requires explicit field selection and labels intentional note clearing',async()=>{
 const {RecordBulkEditDialog}=await import('../src/components/RecordBulkEditDialog.tsx');const html=render(RecordBulkEditDialog,{state,ids:['r1','r2'],onClose:noop,onSaved:noop});
 assert.match(html,/修改备份时间/);assert.match(html,/修改备份位置/);assert.match(html,/修改备注/);assert.match(html,/留空将清除所选记录的备注/);assert.match(html,/<button[^>]*type="submit"[^>]*disabled/);
});
test('collapsed recovery panel shows no privacy input or snapshot actions',async()=>{
 const {RecoveryPanels}=await import('../src/components/RecoveryPanels.tsx');const html=render(RecoveryPanels,{privacyOpen:false,onUnlockPrivacy:noop,onRefresh:noop});
 assert.match(html,/回收站/);assert.match(html,/自动备份/);assert.doesNotMatch(html,/输入隐私密码|立即备份|恢复台账/);
});
