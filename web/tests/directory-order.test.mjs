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

test('column clicks start ascending and toggle only the active column',()=>{
 assert.equal(typeof order.directoryColumnSort,'function');
 for(const [column,ascending,descending] of [['name','name-asc','name-desc'],['backup','oldest','newest'],['notes','notes-asc','notes-desc']]){
  assert.deepEqual(order.directoryColumnSort('manual',column),{direction:'none',next:ascending});
  assert.deepEqual(order.directoryColumnSort(ascending,column),{direction:'ascending',next:descending});
  assert.deepEqual(order.directoryColumnSort(descending,column),{direction:'descending',next:ascending});
 }
 assert.deepEqual(order.directoryColumnSort('notes-desc','name'),{direction:'none',next:'name-asc'});
});

test('backup notes sort naturally using only the latest visible description and keep blanks last',()=>{
 const items=[asset('a','A','folder',{backup_target:true}),asset('b','B','folder',{backup_target:true}),asset('c','C','folder',{backup_target:true}),asset('d','D'),asset('p','Parent','folder',{backup_target:true}),asset('child','Child','file',{parent_id:'p',backup_target:true})];
 const versions=items.map(item=>({id:'v-'+item.id,asset_id:item.id}));
 const records=items.map((item,index)=>({id:'r-'+item.id,version_id:'v-'+item.id,location_id:'disk',backed_up_at:'2026-10-24T00:00:00Z',notes:['2','10','   ','1','0','0'][index]}));
 records.push({id:'older',version_id:'v-a',location_id:'disk',backed_up_at:'2026-10-23T00:00:00Z',notes:'999'});
 const noteState={assets:items,versions,records,locations:[]};
 const roots=items.filter(item=>!item.parent_id);
 assert.deepEqual(order.sortDirectoryAssets(noteState,roots,'notes-asc').map(x=>x.id),['a','b','c','d','p']);
 assert.deepEqual(order.sortDirectoryAssets(noteState,roots,'notes-desc').map(x=>x.id),['b','a','c','d','p']);
 assert.deepEqual(roots.map(x=>x.id),['a','b','c','d','p']);
});

test('backup time sorting matches parent rows that display the latest marked descendant backup',()=>{
 const items=[asset('pa','A','folder'),asset('pb','B','folder'),asset('single','C'),asset('ca','CA','file',{parent_id:'pa',backup_target:true}),asset('cb','CB','file',{parent_id:'pb',backup_target:true}),asset('ignored','Ignored','file',{parent_id:'pa'})];
 const versions=items.map(item=>({id:'v-'+item.id,asset_id:item.id}));
 const records=[['pa','2026-12-01'],['ca','2026-10-01'],['cb','2026-10-20'],['single','2026-10-10'],['ignored','2026-11-01']].map(([id,date])=>({id:'r-'+id,version_id:'v-'+id,location_id:'disk',backed_up_at:date+'T00:00:00Z'}));
 const grouped={assets:items,versions,records,locations:[]};
 const roots=items.filter(item=>!item.parent_id);
 assert.deepEqual(order.sortDirectoryAssets(grouped,roots,'newest').map(x=>x.id),['pb','single','pa']);
 assert.deepEqual(order.sortDirectoryAssets(grouped,roots,'oldest').map(x=>x.id),['pa','single','pb']);
});
test('move produces complete sibling order without mutating input and rejects foreign ids',()=>{assert.equal(typeof order.moveDirectoryAsset,'function');const ids=['a','b','c'];assert.deepEqual(order.moveDirectoryAsset(ids,'c','a'),['c','a','b']);assert.deepEqual(ids,['a','b','c']);assert.equal(order.moveDirectoryAsset(ids,'foreign','a'),null);assert.equal(order.moveDirectoryAsset(ids,'a','a'),null);});

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
