import test from 'node:test';
import assert from 'node:assert/strict';
import * as order from '../src/directoryOrder.ts';
const asset = (id, name, kind = 'file', extra = {}) => ({id, name, kind, parent_id: null, ...extra});
const a=asset('a','A'), b=asset('b','B','folder'), c=asset('c','C','file',{manual_order:0});
const state={assets:[a,b,c], versions:[{id:'v-new',asset_id:'a',created_at:'2026-10-10'},{id:'v-old',asset_id:'a',created_at:'2026-01-01'},{id:'vb',asset_id:'b'}],records:[{id:'new',version_id:'v-new',backed_up_at:'2026-09-01T00:00:00Z'},{id:'old',version_id:'v-old',backed_up_at:'2026-10-01T00:00:00Z'},{id:'b',version_id:'vb',backed_up_at:'2026-09-20T00:00:00Z'}],locations:[]};
test('manual siblings mix kinds and use stable legacy fallback',()=>{assert.equal(typeof order.sortDirectoryAssets,'function');assert.deepEqual(order.sortDirectoryAssets(state,state.assets,'manual').map(x=>x.id),['c','b','a']);assert.deepEqual(order.sortDirectoryAssets(state,[b,a],'manual').map(x=>x.id),['b','a']);});
test('manual legacy fallback uses portable Unicode order for privacy slot preservation',()=>{
 const items=[asset('a','阿','folder'),asset('b','中','folder'),asset('c','李','folder'),asset('d','𠀀','folder'),asset('e','\ue000','folder')];
 assert.deepEqual(order.sortDirectoryAssets({...state,assets:items},items,'manual').map(x=>x.id),['b','c','a','e','d']);
 assert.deepEqual(order.sortDirectoryAssets({...state,assets:items},items.slice(0,3),'name-asc').map(x=>x.id),['a','c','b']);
});
test('name modes and actual backup time modes include all versions and never-backed-up first for oldest',()=>{assert.equal(typeof order.sortDirectoryAssets,'function');for(const [mode, ids] of [['name-asc',['a','b','c']],['name-desc',['c','b','a']],['newest',['a','b','c']],['oldest',['c','b','a']]])assert.deepEqual(order.sortDirectoryAssets(state,state.assets,mode).map(x=>x.id),ids);});
test('move produces complete sibling order without mutating input and rejects foreign ids',()=>{assert.equal(typeof order.moveDirectoryAsset,'function');const ids=['a','b','c'];assert.deepEqual(order.moveDirectoryAsset(ids,'c','a'),['c','a','b']);assert.deepEqual(ids,['a','b','c']);assert.equal(order.moveDirectoryAsset(ids,'foreign','a'),null);assert.equal(order.moveDirectoryAsset(ids,'a','a'),null);});

const {parseImportPreview}=await import('../src/importData.ts');
test('import preview validates optional bounded manual order without requiring legacy field',()=>{
 const payload={format:'filebackup-ledger',schema_version:3,...state};
 assert.equal(parseImportPreview(JSON.stringify(payload)).assets.length,3);
 for(const value of [-1,true,1.5,'1',null,2147483648])assert.throws(()=>parseImportPreview(JSON.stringify({...payload,assets:[{...a,manual_order:value}]})),/手动排序/);
 assert.equal(parseImportPreview(JSON.stringify({...payload,assets:[{...a,manual_order:2147483647}]})).assets[0].manual_order,2147483647);
});

test('never-backed-up items precede even pre-epoch actual backups in oldest mode',()=>{
 const oldState={...state,records:[{id:'ancient',version_id:'v-old',backed_up_at:'1960-01-01T00:00:00Z'}]};
 assert.deepEqual(order.sortDirectoryAssets(oldState,[a,c],'oldest').map(x=>x.id),['c','a']);
 assert.deepEqual(order.sortDirectoryAssets(oldState,[a,c],'newest').map(x=>x.id),['a','c']);
});

test('handle gesture commits only after movement threshold and a same-directory target',()=>{
 assert.equal(typeof order.startDirectoryDrag,'function');
 const ids=['a','b','c'];
 const start=order.startDirectoryDrag('c','parent',ids,10,10);
 assert.equal(order.finishDirectoryDrag(order.advanceDirectoryDrag(start,ids,'parent',12,12,'a'),ids,'parent'),null);
 const active=order.advanceDirectoryDrag(start,ids,'parent',20,20,'a');
 assert.deepEqual(order.finishDirectoryDrag(active,ids,'parent'),['c','a','b']);
 assert.equal(order.finishDirectoryDrag(active,ids,'other'),null);
 assert.equal(order.finishDirectoryDrag(active,['a','c'],'parent'),null);
 assert.equal(order.finishDirectoryDrag(order.advanceDirectoryDrag(active,ids,'parent',30,30,null),ids,'parent'),null);
 assert.equal(order.finishDirectoryDrag(null,ids,'parent'),null);
 assert.equal(order.startDirectoryDrag('foreign','parent',ids,10,10),null);
});
