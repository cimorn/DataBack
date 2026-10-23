import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {dirname, resolve} from 'node:path';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const bulk = await import('../src/bulkRecords.ts').catch(() => ({}));
const asset = (id, name, parent_id = null, backup_target = false, kind = 'folder') => ({
  id, name, parent_id, backup_target, kind, source_path: '', backup_interval_days: 30,
  backup_enabled: true, target_location_ids: [], notes: '', created_at: '2026-10-01T00:00:00Z',
});
const initial = () => ({
  assets: [asset('root', '资料'), asset('a', '照片', 'root', true), asset('b', '合同', 'root', true, 'file'),
    asset('plain', '普通', 'root'), asset('other', '其他'), asset('same', '照片', 'other', true),
    asset('marked-parent', '有子目录', null, true), asset('child', '子资料', 'marked-parent', true)],
  versions: [], locations: [], records: [],
});
const shared = {time: '2026-10-22T09:30', location: '移动硬盘', notes: '已核对'};
const row = (assetId = 'a', patch = {}) => ({assetId, ...shared, ...patch});
const functions = ['collectBulkTargets', 'createBulkSelectionRows', 'parseBackupTable', 'previewBulkRecords', 'createBulkSubmission'];

test('bulk records exposes selection, table preview and submission operations', () => {
  for (const name of functions) assert.equal(typeof bulk[name], 'function', `${name} is not implemented`);
});

test('parent selection expands to marked leaves and removes overlaps without including ordinary leaves', () => {
  assert.equal(typeof bulk.collectBulkTargets, 'function');
  const state = initial();
  assert.deepEqual(bulk.collectBulkTargets(state, ['a', 'root', 'a', 'marked-parent', 'missing']).map(item => item.id), ['a', 'b', 'child']);
  assert.deepEqual(bulk.createBulkSelectionRows(state, ['root'], shared).map(item => [item.assetId, item.path, item.location]), [
    ['a', '资料/照片', '移动硬盘'], ['b', '资料/合同', '移动硬盘'],
  ]);
});

test('Excel TSV accepts a header, quoted multiline cells and literal quotes', () => {
  assert.equal(typeof bulk.parseBackupTable, 'function');
  const rows = bulk.parseBackupTable('路径\t备份时间\t备份位置\t备注\r\n资料\\照片\t2026-10-22 09:30:10\tNAS\t"第一行\n第二行 ""已核对"""\r\n');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].path, '资料\\照片');
  assert.equal(rows[0].notes, '第一行\n第二行 "已核对"');
  assert.equal(rows[0].rowNumber, 2);
});

test('CSV accepts reordered recognized headers and quoted commas', () => {
  assert.equal(typeof bulk.parseBackupTable, 'function');
  const [parsed] = bulk.parseBackupTable('名称,位置,时间,备注\n资料/合同,"硬盘, A",2026-10-22T09:30:00-07:00,"核对, 完成"');
  assert.deepEqual([parsed.path, parsed.time, parsed.location, parsed.notes], ['资料/合同', '2026-10-22T09:30:00-07:00', '硬盘, A', '核对, 完成']);
  const [withoutHeader] = bulk.parseBackupTable('资料/照片,2026-10-22 09:30,NAS');
  assert.equal(withoutHeader.notes, '');
});

test('leading empty spreadsheet rows do not change delimiter detection or physical row numbers', () => {
  assert.equal(typeof bulk.parseBackupTable, 'function');
  const rows = bulk.parseBackupTable('\n\t\t\n路径\t备份时间\t备份位置\n资料/照片\t2026-10-22 09:30\tNAS\n');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].path, '资料/照片');
  assert.equal(rows[0].rowNumber, 4);
});

test('malformed quotes, missing columns and unsupported headers are visible errors', () => {
  assert.equal(typeof bulk.parseBackupTable, 'function');
  assert.throws(() => bulk.parseBackupTable('资料/照片\t2026-10-22 09:30\t"NAS'), /引号/);
  assert.throws(() => bulk.parseBackupTable('资料/照片\t2026-10-22'), /列|位置/);
  assert.throws(() => bulk.parseBackupTable('资料/照片\t2026-10-22 09:30\t"NAS"多余'), /引号/);
  assert.throws(() => bulk.parseBackupTable('路径\t备份时间\t备份位置\t备注\t未知\n资料/照片\t2026-10-22 09:30\tNAS\t\t值'), /列|表头/);
});

test('preview normalizes registered relative paths and prefers full hierarchy matches', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  const state = initial();
  const result = bulk.previewBulkRecords(state, [
    {path: './资料\\照片/', ...shared}, {path: '合同', ...shared},
    {path: '其他/照片', ...shared, time: '2026-10-22T09:31'},
  ]);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.rows.map(item => [item.assetId, item.path]), [['a', '资料/照片'], ['b', '资料/合同'], ['same', '其他/照片']]);
  assert.equal(result.rows[0].backedUpAt, new Date('2026-10-22T09:30').toISOString());
});

test('preview reports every unknown, ambiguous, unmarked, non-leaf and absolute path before writes', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  const values = ['不存在', '照片', '资料/普通', '有子目录', String.raw`\\server\share\照片`, '/资料/照片', '../资料/照片', 'C:\\资料\\照片'];
  const result = bulk.previewBulkRecords(initial(), values.map(path => ({path, ...shared})));
  assert.equal(result.rows.length, values.length);
  assert.equal(result.errors.length, values.length);
  assert.match(result.errors[0], /不存在|未找到/);
  assert.match(result.errors[1], /重名|不唯一|多个/);
  assert.match(result.errors[2], /备份对象/);
  assert.match(result.errors[3], /末级/);
  for (const message of result.errors.slice(4)) assert.match(message, /相对路径/);
});

test('a dot prefix cannot turn an absolute or UNC path into a relative path', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  const result = bulk.previewBulkRecords(initial(), [{path: String.raw`./\\server\share`, ...shared}]);
  assert.match(result.errors.join('\n'), /相对路径/);
});

test('calendar validation rejects impossible dates and accepts local seconds and explicit timezones', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  for (const time of ['2026-02-30 09:30', '2026-13-01 09:30', '2026-01-01 25:00', '2026-10-22', '10/22/2026 09:30', '2026-02-30T09:30:00Z']) {
    const preview = bulk.previewBulkRecords(initial(), [row('a', {time})]);
    assert.equal(preview.errors.length, 1, time);
    assert.match(preview.errors[0], /时间/);
  }
  const accepted = bulk.previewBulkRecords(initial(), [row('a', {time: '2024-02-29 09:30:15'}), row('b', {time: '2026-10-22T09:30:15+08:00'})]);
  assert.deepEqual(accepted.errors, []);
  assert.equal(accepted.rows[0].backedUpAt, new Date('2024-02-29T09:30:15').toISOString());
  assert.equal(accepted.rows[1].backedUpAt, '2026-10-22T01:30:15.000Z');
});

test('all row limits and exact duplicate records are checked while distinct records remain allowed', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  const duplicate = bulk.previewBulkRecords(initial(), [row(), row('a', {location: ' 移动硬盘 '})]);
  assert.match(duplicate.errors.join('\n'), /重复/);
  const different = bulk.previewBulkRecords(initial(), [row(), row('a', {time: '2026-10-22 09:31'}), row('a', {location: '网盘'})]);
  assert.deepEqual(different.errors, []);
  const invalid = bulk.previewBulkRecords(initial(), [row('a', {location: '文'.repeat(201)}), row('b', {notes: '文'.repeat(2001)})]);
  assert.equal(invalid.errors.length, 2);
  assert.match(invalid.errors[0], /200/);
  assert.match(invalid.errors[1], /2000/);
  const excessive = bulk.previewBulkRecords(initial(), Array.from({length: 21}, (_, index) => row('a', {notes: `${index}`})));
  assert.equal(excessive.rows.length, 21, 'the preview must never silently truncate a batch');
  assert.match(excessive.errors.join('\n'), /20/);
});

test('empty batches and stale selected IDs remain explicit validation errors', () => {
  assert.equal(typeof bulk.previewBulkRecords, 'function');
  assert.match(bulk.previewBulkRecords(initial(), []).errors.join('\n'), /选择|粘贴|记录/);
  assert.match(bulk.previewBulkRecords(initial(), [row('removed')]).errors.join('\n'), /移除|不存在/);
});

function sender(state, calls, override) {
  return async (path, method = 'GET', body) => {
    calls.push({path, method, body});
    if (override) {
      const result = await override(path, method, body);
      if (result !== undefined) return result;
    }
    if (path === 'state') return state;
    if (path === 'versions') {
      const version = {id: `version-${body.asset_id}`, ...body}; state.versions.push(version); return version;
    }
    if (path === 'locations/resolve') {
      const location = {id: `location-${state.locations.length}`, ...body}; state.locations.push(location); return location;
    }
    if (path === 'records/batch') return {records: body.records};
    throw new Error(`unexpected request ${path}`);
  };
}

test('submission revalidates fresh IDs before writes and submits one atomic batch with cached references', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const state = initial(), calls = [];
  const submission = bulk.createBulkSubmission(sender(state, calls));
  let refreshed = 0;
  await submission.submit([row(), row('a', {location: '移动硬盘', time: '2026-10-22T09:31'}), row('b')], async () => {refreshed++;});
  assert.equal(calls[0].path, 'state');
  assert.equal(calls.filter(item => item.path === 'versions').length, 2);
  assert.equal(calls.filter(item => item.path === 'locations/resolve').length, 1);
  const batches = calls.filter(item => item.path === 'records/batch');
  assert.equal(batches.length, 1);
  assert.equal(batches[0].body.records.length, 3);
  assert.equal(batches[0].body.records[0].notes, '已核对');
  assert.equal(refreshed, 1);
  assert.equal(submission.committed, true);
});

test('any invalid fresh target or input prevents every reference write and record write', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const state = initial(), calls = [];
  state.assets.find(item => item.id === 'b').backup_target = false;
  const submission = bulk.createBulkSubmission(sender(state, calls));
  await assert.rejects(submission.submit([row(), row('b')], async () => {}), /备份对象/);
  assert.deepEqual(calls.map(item => item.method), ['GET']);
  assert.equal(submission.committed, false);
  const invalidCalls = [];
  await assert.rejects(bulk.createBulkSubmission(sender(initial(), invalidCalls)).submit([row('a', {notes: '文'.repeat(2001)})], async () => {}), /2000/);
  assert.equal(invalidCalls.some(item => item.method !== 'GET'), false);
});

test('over twenty records never creates references, writes records or automatically splits a batch', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const calls = [], submission = bulk.createBulkSubmission(sender(initial(), calls));
  await assert.rejects(submission.submit(Array.from({length: 21}, (_, index) => row('a', {notes: `${index}`})), async () => {}), /20/);
  assert.equal(calls.some(item => item.method !== 'GET'), false);
});

test('failed refresh can be retried without replaying an acknowledged record batch', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const calls = [], submission = bulk.createBulkSubmission(sender(initial(), calls));
  await assert.rejects(submission.submit([row()], async () => {throw new Error('读取失败');}), /读取失败/);
  assert.equal(submission.committed, true);
  await submission.submit([row()], async () => {});
  assert.equal(calls.filter(item => item.path === 'records/batch').length, 1);
});

test('double submission is ignored while the first request is pending', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const calls = [], state = initial();
  let release;
  const waiting = new Promise(resolve => {release = resolve;});
  const submission = bulk.createBulkSubmission(sender(state, calls, async path => {if (path === 'records/batch') await waiting;}));
  const first = submission.submit([row()], async () => {});
  assert.equal(submission.pending, true);
  assert.equal(await submission.submit([row()], async () => {}), false);
  release(); await first;
  assert.equal(submission.pending, false);
  assert.equal(calls.filter(item => item.path === 'records/batch').length, 1);
});

test('429 preserves retryability and existing references are reused on the next attempt', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const calls = [], state = initial();
  let attempt = 0;
  const submission = bulk.createBulkSubmission(sender(state, calls, async path => {
    if (path === 'records/batch' && attempt++ === 0) throw new Error('请求过于频繁，请稍后重试。');
  }));
  await assert.rejects(submission.submit([row()], async () => {}), /频繁/);
  assert.equal(submission.committed, false);
  assert.equal(submission.pending, false);
  await submission.submit([row()], async () => {});
  assert.equal(calls.filter(item => item.path === 'versions').length, 1);
  assert.equal(calls.filter(item => item.path === 'locations/resolve').length, 1);
  assert.equal(submission.committed, true);
});

test('lost internal version response recovers the saved reference before records are created', async () => {
  assert.equal(typeof bulk.createBulkSubmission, 'function');
  const state = initial(), calls = [];
  const submission = bulk.createBulkSubmission(sender(state, calls, async (path, method, body) => {
    if (path === 'versions') {
      state.versions.push({id: 'recovered', ...body});
      throw new Error('response lost');
    }
  }));
  await submission.submit([row()], async () => {});
  assert.equal(calls.filter(item => item.path === 'state').length, 2);
  assert.equal(calls.find(item => item.path === 'records/batch').body.records[0].version_id, 'recovered');
});

const require = createRequire(import.meta.url);
async function componentModule(file) {
  const source = await readFile(file, 'utf8');
  let output = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext}}).outputText;
  output = output.replace(/^import ['"].*\.css['"];?\s*$/gm, '');
  const imports = [...output.matchAll(/from\s+['"]([^'"]+)['"]/g)];
  for (const match of imports) {
    const specifier = match[1];
    let url;
    if (specifier === '../api') url = 'data:text/javascript,' + encodeURIComponent('export const request = async () => { throw new Error("No requests during render"); };');
    else if (specifier === './ui') url = await componentModule(resolve(dirname(file), 'ui.tsx'));
    else if (specifier.startsWith('.')) url = pathToFileURL(resolve(dirname(file), /\.tsx?$/.test(specifier) ? specifier : `${specifier}.ts`)).href;
    else url = pathToFileURL(require.resolve(specifier)).href;
    output = output.replaceAll(`from "${specifier}"`, `from ${JSON.stringify(url)}`).replaceAll(`from '${specifier}'`, `from ${JSON.stringify(url)}`);
  }
  return 'data:text/javascript;base64,' + Buffer.from(output).toString('base64');
}
let component;
try { component = await import(await componentModule(fileURLToPath(new URL('../src/components/BulkBackupDialog.tsx', import.meta.url)))); }
catch { component = {}; }

test('bulk dialog initially previews each selected marked leaf and shared inputs', () => {
  assert.equal(typeof component.BulkBackupDialog, 'function', 'BulkBackupDialog is not implemented');
  const html = renderToStaticMarkup(React.createElement(component.BulkBackupDialog, {
    state: initial(), initialAssetIds: ['root', 'a'], onClose: () => {}, onSaved: async () => {},
  }));
  assert.match(html, /资料\/照片/);
  assert.match(html, /资料\/合同/);
  assert.match(html, /已选 2 项/);
  assert.match(html, /备份位置/);
  assert.match(html, /粘贴表格/);
  assert.match(html, /待登记 2 条/);
});

test('paste mode provides the column order and an editable table input', () => {
  assert.equal(typeof component.BulkBackupDialog, 'function');
  const html = renderToStaticMarkup(React.createElement(component.BulkBackupDialog, {
    state: initial(), initialMode: 'paste', onClose: () => {}, onSaved: async () => {},
  }));
  assert.match(html, /textarea/);
  assert.match(html, /路径.*备份时间.*备份位置.*备注/);
  assert.match(html, /选择资料/);
});

test('more than twenty selected leaves keeps every row and explains the cap', () => {
  assert.equal(typeof component.BulkBackupDialog, 'function');
  const state = initial();
  state.assets.push(...Array.from({length: 21}, (_, index) => asset(`many-${index}`, `测试资料${index}`, 'many', true)));
  state.assets.push(asset('many', '批量目录'));
  const html = renderToStaticMarkup(React.createElement(component.BulkBackupDialog, {
    state, initialAssetIds: ['many'], onClose: () => {}, onSaved: async () => {},
  }));
  assert.match(html, /已选 21 项/);
  assert.match(html, /最多.*20/);
  assert.match(html, /待登记 21 条/);
  assert.match(html, /测试资料20/);
});
