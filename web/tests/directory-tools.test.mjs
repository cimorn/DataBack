import test from 'node:test';
import assert from 'node:assert/strict';
import { directoryNavigation, previewDirectoryBatch, searchDirectoryAssets } from '../src/directoryTools.ts';
import { collectBulkTargets } from '../src/bulkRecords.ts';

const asset = (id, name, parent_id = null, kind = 'folder') => ({id, name, parent_id, kind, backup_target:false});
const assets = [asset('root','个人文件夹'),asset('photos','Photos','root'),asset('existing','2025','photos'),asset('file','readme.md','root','file')];

test('batch creation previews intermediate folders and explicit leaf kinds without extension guessing',()=>{
  const preview=previewDirectoryBatch(assets,'root','file','Photos/2026/trip.jpg\nnew/readme\n');
  assert.equal(preview.error,''); assert.equal(preview.created,4); assert.equal(preview.skipped,0);
  assert.deepEqual(preview.paths,['Photos/2026/trip.jpg','new/readme']);
  assert.deepEqual(preview.rows.map(row=>[row.path,row.kind,row.status]),[
    ['Photos/2026','folder','create'],['Photos/2026/trip.jpg','file','create'],['new','folder','create'],['new/readme','file','create'],
  ]);
});

test('batch creation reuses folders, counts duplicate input leaves and trims path segments',()=>{
  const preview=previewDirectoryBatch(assets,'root','folder',' photos / 2025 \nPhotos/2026\nphotos/2026');
  assert.equal(preview.created,1); assert.equal(preview.skipped,2); assert.equal(preview.error,'');
  assert.deepEqual(preview.paths,['photos/2025','Photos/2026','photos/2026']);
  assert.equal(preview.rows.filter(row=>row.status==='skip').length,2);
});

test('file traversal and incompatible leaf kind block the whole preview',()=>{
  for(const [kind,raw] of [['folder','readme.md/child'],['file','Photos'],['folder','new\nreadme.md']]){
    const preview=previewDirectoryBatch(assets,'root',kind,raw);
    assert.match(preview.error,/冲突|文件/); assert.equal(preview.valid,false);
  }
});

test('relative paths reject traversal, absolute paths and empty segments',()=>{
  for(const path of ['/Photos','//NAS/Photos','C:/Photos','a\\b','../Photos','a/./b','a//b','a/','a\u0000b','a:b']){
    const preview=previewDirectoryBatch(assets,'root','folder',path);
    assert.equal(preview.valid,false,path); assert.ok(preview.error,path);
  }
  assert.equal(previewDirectoryBatch(assets,'root','folder','a'.repeat(201)).valid,false);
});

test('batch preview rejects invalid parents, ambiguous existing names and excessive input',()=>{
  assert.equal(previewDirectoryBatch(assets,'file','folder','child').valid,false);
  assert.equal(previewDirectoryBatch(assets,'missing','folder','child').valid,false);
  assert.equal(previewDirectoryBatch([...assets,asset('dupe','PHOTOS','root')],'root','folder','Photos/2026').valid,false);
  assert.equal(previewDirectoryBatch(assets,'root','folder',Array.from({length:201},(_,i)=>`n${i}`).join('\n')).valid,false);
  assert.equal(previewDirectoryBatch(assets,'root','folder',' ').valid,false);
  const paths=Array.from({length:50},(_,i)=>Array.from({length:10},(_,n)=>`n${i}-${n}`).join('/'));
  assert.equal(previewDirectoryBatch([],null,'folder',paths.join('\n')).created,500);
  assert.equal(previewDirectoryBatch([],null,'folder',[...paths,'overflow'].join('\n')).valid,false);
});

test('global search navigates folder and marked-file targets while locating ordinary files in their parent',()=>{
  assert.deepEqual(directoryNavigation(assets[1],false,true),{folderId:'photos'});
  assert.deepEqual(directoryNavigation(assets[3],true,true),{folderId:'file'});
  assert.deepEqual(directoryNavigation(assets[3],false,true),{folderId:'root',detailId:'file'});
  assert.deepEqual(directoryNavigation(assets[3],false,false),{detailId:'file'});
  assert.deepEqual(directoryNavigation(asset('loose','loose',null,'file'),false,true),{folderId:null,detailId:'loose'});
});

test('overlapping global search parent and child selections expand to unique authorized terminal backup objects',()=>{
  const selectedAssets=[...assets.map(item=>({...item,backup_target:item.id==='existing'})),asset('ordinary','ordinary','photos','file'),{...asset('nested','nested','photos'),backup_target:true},{...asset('deep','deep','nested','file'),backup_target:true}];
  const authorized={assets:selectedAssets,versions:[],locations:[],records:[]};
  const results=searchDirectoryAssets(selectedAssets,null,'Photos','all');
  assert.deepEqual(collectBulkTargets(authorized,results.map(item=>item.id)).map(item=>item.id),['existing','deep']);
  assert.deepEqual(collectBulkTargets(authorized,['photos','existing','deep','unavailable']).map(item=>item.id),['existing','deep']);
});

test('global directory search matches relative paths only within supplied authorized assets',()=>{
  assert.deepEqual(searchDirectoryAssets(assets,'root','2025','current'),[]);
  assert.deepEqual(searchDirectoryAssets(assets,'root','photos/2025','all').map(item=>item.id),['existing']);
  assert.deepEqual(searchDirectoryAssets(assets,'root','PHOTOS','all').map(item=>item.id),['photos','existing']);
  assert.deepEqual(searchDirectoryAssets(assets,'root','hidden','all'),[]);
  assert.deepEqual(searchDirectoryAssets(assets,'root','','all').map(item=>item.id),['photos','file']);
});
