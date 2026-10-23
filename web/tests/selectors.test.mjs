import test from 'node:test';
import assert from 'node:assert/strict';
import { latestVersion, latestRecordsByLocation, latestRecordsForAsset, latestBackupForAsset, filterRecords, toDatetimeLocal, toBackupTimestamp } from '../src/selectors.ts';
import { createLatestImportReader, parseImportPreview } from '../src/importData.ts';
import { buildRegisteredTree } from '../src/pathTree.ts';

process.env.TZ = 'America/Los_Angeles';
const versions = [
  { id: 'old', asset_id: 'a', label: 'v1', notes: '', created_at: '2026-01-01T00:00:00Z' },
  { id: 'new', asset_id: 'a', label: 'v2', notes: '', created_at: '2026-01-02T00:00:00Z' },
  { id: 'other', asset_id: 'b', label: 'v3', notes: '', created_at: '2026-01-03T00:00:00Z' },
];
const record = (id, version_id, location_id, backed_up_at, notes = '') => ({
  id, version_id, location_id, backed_up_at, stored_path: '/archive', notes, created_at: '2026-01-05T00:00:00Z',
});
const state = {
  assets: [{ id: 'a', name: '合同', kind: 'file', source_path: 'D:/合同.pdf', notes: '', created_at: '' }],
  versions,
  locations: [{ id: 'disk', name: '移动硬盘', kind: 'external', base_path: 'E:/', notes: '', created_at: '' }],
  records: [
    record('before', 'old', 'disk', '2026-01-02T07:59:59Z'),
    record('start', 'new', 'disk', '2026-01-02T08:00:00Z', '签字版'),
    record('end', 'old', 'disk', '2026-01-03T07:59:59.999Z'),
    record('after', 'old', 'disk', '2026-01-03T08:00:00Z'),
  ],
};

test('latest version follows creation order even when an older version is backed up later', () => {
  assert.equal(latestVersion(versions, 'a')?.id, 'new');
  assert.equal(latestVersion(versions, 'missing'), undefined);
});
test('latest registration is selected independently for each location and never crosses versions', () => {
  const records = [record('first', 'new', 'disk', '2026-01-01T00:00:00Z'), record('last', 'new', 'disk', '2026-01-03T00:00:00Z'), record('cloud', 'new', 'cloud', '2026-01-02T00:00:00Z'), record('old', 'old', 'disk', '2026-01-04T00:00:00Z')];
  const latest = latestRecordsByLocation(records, 'new');
  assert.equal(latest.get('disk')?.id, 'last');
  assert.equal(latest.get('cloud')?.id, 'cloud');
  assert.equal(records.length, 4, 'the history is preserved');
});
test('date boundaries include the complete local date and exclude either neighboring day', () => {
  assert.deepEqual(filterRecords(state, { dateFrom: '2026-01-02', dateTo: '2026-01-02' }).map(r => r.id), ['end', 'start']);
});
test('history combines asset, location, and related text filters without a version filter', () => {
  assert.deepEqual(filterRecords(state, { assetId: 'a', locationId: 'disk', keyword: '签字' }).map(r => r.id), ['start']);
  assert.equal(filterRecords(state, { keyword: '移动硬盘' }).length, 4);
  assert.equal(filterRecords(state, { keyword: 'not-found' }).length, 0);
});

test('history directory scopes include all descendants while record details stay exact', () => {
 const scoped={...state,assets:[{id:'root',name:'个人文件夹',kind:'folder',parent_id:null},{id:'photos',name:'Photos',kind:'folder',parent_id:'root'},{...state.assets[0],parent_id:'photos'},{id:'b',name:'其他',kind:'file',parent_id:'root'}],records:[...state.records,record('other-asset','other','disk','2026-01-05T00:00:00Z')]};
 assert.equal(filterRecords(scoped,{assetId:'photos',includeDescendants:true}).length,4);
 assert.equal(filterRecords(scoped,{assetId:'root',includeDescendants:true}).length,5);
 assert.equal(filterRecords(scoped,{assetId:'photos'}).length,0);
 assert.equal(filterRecords(scoped,{keyword:'v1'}).length,0,'retired version labels are not searched');
});
test('datetime-local retains the browser timezone instead of slicing a UTC timestamp', () => {
  assert.equal(toDatetimeLocal('2026-01-02T08:15:00Z'), '2026-01-02T00:15');
});
test('asset overview advances when an older version is backed up later', () => {
  assert.equal(latestBackupForAsset(state, 'a')?.id, 'after');
  assert.equal(latestBackupForAsset(state, 'b'), undefined);
});
test('asset overview aggregates each location across all versions without merging their history', () => {
  const all = { ...state, records: [...state.records, record('cloud-new', 'new', 'cloud', '2026-01-01T00:00:00Z'), record('cloud-old', 'old', 'cloud', '2026-01-04T00:00:00Z')] };
  const latest = latestRecordsForAsset(all, 'a');
  assert.equal(latest.get('disk')?.id, 'after');
  assert.equal(latest.get('cloud')?.id, 'cloud-old');
  assert.equal(latestRecordsByLocation(all.records, 'new').get('cloud')?.id, 'cloud-new');
});
test('date filtering includes the complete 23-hour local day at the spring daylight transition', () => {
  const spring = { ...state, records: [record('start', 'new', 'disk', '2026-03-08T08:00:00Z'), record('end', 'new', 'disk', '2026-03-09T06:59:59Z'), record('next', 'new', 'disk', '2026-03-09T07:00:00Z')] };
  assert.deepEqual(filterRecords(spring, { dateFrom: '2026-03-08', dateTo: '2026-03-08' }).map(item => item.id), ['end', 'start']);
});
test('editing a record without changing its minute retains original seconds', () => {
  assert.equal(toBackupTimestamp('2026-01-02T00:15', '2026-01-02T08:15:49.500Z'), '2026-01-02T08:15:49.500Z');
  assert.equal(toBackupTimestamp('2026-01-02T00:16', '2026-01-02T08:15:49.500Z'), '2026-01-02T08:16:00.000Z');
});
test('invalid import shape is rejected before confirmation without leaking a TypeError', () => {
  assert.throws(() => parseImportPreview('null'), /支持的备份台账格式/);
  assert.throws(() => parseImportPreview('{'), /JSON 内容无法解析/);
  assert.throws(() => parseImportPreview(JSON.stringify({ format: 'filebackup-ledger', schema_version: 1, ...state, records: null })), /支持的备份台账格式/);
  assert.equal(parseImportPreview(JSON.stringify({ format: 'filebackup-ledger', schema_version: 1, ...state })).records.length, 4);
});
test('directory view retains every accepted asset including empty and slash-only source paths', () => {
  const summaries = ['', '   ', '/', '\\\\', 'D:/Photos', 'D:/Photos/Me'].map((source_path, index) => ({
    asset: { id: String(index), name: `资料${index}`, kind: 'folder', source_path, notes: '', created_at: '' }, locations: new Map(),
  }));
  const root = buildRegisteredTree(summaries);
  const ids = [];
  function visit(node) { ids.push(...node.assets.map(item => item.asset.id)); for (const child of node.children.values()) visit(child); }
  for (const child of root.children.values()) visit(child);
  assert.deepEqual(ids.sort(), summaries.map(item => item.asset.id).sort());
  assert.equal(root.children.get('unassigned')?.label, '未设置路径');
  const drive = root.children.get('d:');
  assert.equal(drive.children.get('photos').assets[0].asset.id, '4');
  assert.equal(drive.children.get('photos').children.get('me').assets[0].asset.id, '5');
});
test('a slow previous file selection cannot replace the latest import preview', async () => {
  const read = createLatestImportReader();
  let resolveOld;
  const old = read({ size: 10, text: () => new Promise(resolve => { resolveOld = resolve; }) });
  const latest = await read({ size: 10, text: async () => JSON.stringify({ format: 'filebackup-ledger', schema_version: 1, ...state, records: [] }) });
  resolveOld(JSON.stringify({ format: 'filebackup-ledger', schema_version: 1, ...state }));
  assert.equal(latest.records.length, 0);
  assert.equal(await old, undefined);
});
test('a stale parse error cannot replace a newer valid file selection', async () => {
  const read = createLatestImportReader();
  let resolveOld;
  const old = read({ size: 10, text: () => new Promise(resolve => { resolveOld = resolve; }) });
  await read({ size: 10, text: async () => JSON.stringify({ format: 'filebackup-ledger', schema_version: 1, ...state }) });
  resolveOld('null');
  assert.equal(await old, undefined);
});

test('retired paths remain stored without participating in visible history searches', () => {
 const legacy={...state,assets:[{...state.assets[0],source_path:String.raw`\\fixture\personal_folder`}],records:[{...state.records[0],stored_path:String.raw`D:\Photos`}]};
 assert.equal(filterRecords(legacy,{keyword:'D:/Photos'}).length,0);
 assert.equal(filterRecords(legacy,{keyword:'//fixture/personal_folder'}).length,0);
 assert.equal(filterRecords(legacy,{keyword:String.raw`D:\Photos`}).length,0);
 assert.equal(legacy.records[0].stored_path,String.raw`D:\Photos`);
});
