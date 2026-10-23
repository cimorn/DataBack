import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

process.env.TZ = 'America/Los_Angeles';

// Compile actual React components in memory; do not create build output in the repo.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/')) {
      const url = new URL(specifier, context.parentURL);
      for (const suffix of ['', '.ts', '.tsx']) {
        const candidate = new URL(url.href + suffix);
        if (existsSync(fileURLToPath(candidate))) return {url: candidate.href, shortCircuit: true};
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.includes('/src/') && /\.tsx?$/.test(url)) {
      const source = ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX}}).outputText;
      return {format: 'module', source, shortCircuit: true};
    }
    return nextLoad(url, context);
  },
});
const { Overview } = await import('../src/features/Overview.tsx');
const { AssetDialog } = await import('../src/components/AssetForm.tsx');
const { AssetDetail } = await import('../src/features/AssetDetail.tsx');
const { BackupDialog } = await import('../src/components/BackupForms.tsx');
const asset = (id, parent_id = null, extra = {}) => ({id, name: id, parent_id, kind: 'folder', source_path: '', notes: '', created_at: '', backup_target: false, backup_enabled: true, backup_interval_days: 30, target_location_ids: [], ...extra});
const ordinary = asset('ordinary');
const state = {assets: [ordinary], versions: [], locations: [], records: []};
const noop = () => {};
const props = {state, now: Date.parse('2026-10-12T00:00:00Z'), folderId: null, onFolder: noop, onCreate: noop, onAssetForm: noop, onMark: noop, onAssetDelete: noop, onDetail: noop, onRegister: noop, onClose: noop, onAdd: noop, onEdit: noop, onDelete: noop, onHistory: noop, onLocate: noop};
const render = (component, extra = {}) => renderToStaticMarkup(createElement(component, {...props, ...extra}));

test('ordinary directory row has marker and child controls but no new-registration control or deadline', () => {
  const html = render(Overview);
  assert.doesNotMatch(html, />状态<|data-label="状态"|>文件夹</);
  assert.equal((html.match(/<th>/g)??[]).length,3);
  assert.doesNotMatch(html, /个备份对象|个普通条目|备份对象最新记录/);
  assert.match(html, /设为备份对象/);
  assert.equal((html.match(/aria-haspopup="menu"/g) ?? []).length, 1);
  assert.match(html, /aria-label="编辑 ordinary"/);
  assert.match(html, /添加子文件夹/);
  assert.match(html, /添加子文件/);
  assert.doesNotMatch(html, />登记<|待首次备份/);
});

test('empty folder retains only top-toolbar creation and separate backup controls', () => {
  const html = render(Overview, {folderId: ordinary.id});
  assert.match(html, /设为备份对象/);
  assert.doesNotMatch(html, /add-child-menu|子文件夹|子文件/);
  assert.equal((html.match(/>添加文件</g) ?? []).length, 1);
  assert.equal((html.match(/>新建文件夹</g) ?? []).length, 1);
  assert.doesNotMatch(html, /登记备份/);
});

test('asset form contains only name, type and parent directory', () => {
  const html = render(AssetDialog, {onSave: noop});
  assert.match(html, /资料名称/); assert.match(html, /类型/); assert.match(html, /所属目录/);
  assert.doesNotMatch(html, /原始路径|备注|设为备份对象|type="checkbox"|启用到期提醒|目标备份位置|备份间隔/);
  assert.equal((html.match(/<label/g) ?? []).length, 3);
});

test('parent metadata form cannot change its legacy backup marker', () => {
  const group = {...ordinary, backup_target: true};
  const html = render(AssetDialog, {state: {...state, assets: [group, asset('child', group.id)]}, entry: group, onSave: noop});
  assert.doesNotMatch(html, /backup-target|type="checkbox"/);
  assert.doesNotMatch(html, /备份提醒与位置/);
  assert.match(html, /<option value="file" disabled/);
});

test('ordinary details retain history without exposing retired path fields', () => {
  const history = {...state, versions: [{id: 'v', asset_id: ordinary.id, label: 'archive', created_at: ''}], locations: [{id: 'disk', name: 'Disk'}], records: [{id: 'r', version_id: 'v', location_id: 'disk', backed_up_at: '2026-09-01T00:00:00Z', stored_path: '/kept-history', notes: '', created_at: ''}]};
  const html = render(AssetDetail, {state: history, asset: ordinary});
  assert.match(html, /Disk/);
  assert.doesNotMatch(html, /kept-history|备份路径|下次备份/);
  assert.equal((html.match(/<table/g) ?? []).length, 1);
  assert.doesNotMatch(html, />登记备份<|待首次备份/);
});

test('new-registration chooser excludes ordinary entries and directs the empty case to the directory', () => {
  const html = render(BackupDialog, {onSaved: noop, onNeed: noop, onChooseTargets: noop});
  assert.match(html, /前往资料目录/);
  assert.doesNotMatch(html, /value="ordinary"|新增资料/);
});

test('marked leaf is actionable without a separate legacy reminder switch', () => {
  const chosen = {...ordinary, backup_target: true, backup_enabled: false};
  const html = render(Overview, {state: {...state, assets: [chosen]}});
  assert.doesNotMatch(html, /个备份对象/);
  assert.doesNotMatch(html, /提醒已关闭/);
  assert.match(html, /登记备份/);
});



const { History } = await import('../src/features/History.tsx');
const { RecordEditDialog } = await import('../src/components/BackupForms.tsx');
const { EntityDialog } = await import('../src/components/EntityForms.tsx');
const { Shell } = await import('../src/components/Shell.tsx');
const { default: App } = await import('../src/App.tsx');
const legacy = {assets: [asset('legacy', null, {source_path: String.raw`\\fixture-nas\personal_folder`, backup_target: true})], versions: [{id:'legacy-version',asset_id:'legacy',label:'old',notes:'',created_at:''}], locations: [{id:'legacy-location',name:'NAS fixture',kind:'nas',base_path:String.raw`\\fixture\backup`,notes:'',created_at:''}], records:[{id:'legacy-record',version_id:'legacy-version',location_id:'legacy-location',backed_up_at:'2026-09-01T00:00:00Z',stored_path:String.raw`D:\Photos`,notes:'',created_at:''}]};

test('record displays omit legacy paths while unrelated destination metadata stays compatible', () => {
 assert.equal((render(AssetDetail,{state:legacy,asset:legacy.assets[0]}).match(/<table/g) ?? []).length,1);
 for (const html of [render(AssetDetail,{state:legacy,asset:legacy.assets[0]}),render(History,{state:legacy,initialFilters:{}})]) {
  assert.doesNotMatch(html,/D:\/Photos|D:\\Photos|备份路径|存放路径/);
 }
 const form=render(BackupDialog,{state:legacy,initialAssetId:'legacy',onSaved:noop,onChooseTargets:noop});
 assert.match(form,/备份位置/);
 assert.doesNotMatch(form,/新增位置|目标备份位置/);
});

test('legacy edit inputs present forward slashes without mutating their state', () => {
 assert.doesNotMatch(render(AssetDialog,{state:legacy,entry:legacy.assets[0],onSave:noop}),/原始路径|personal_folder|备注|type="checkbox"/);
 assert.match(render(EntityDialog,{state:legacy,form:{kind:'locations',entry:legacy.locations[0]},onSave:noop}),/value="\/\/fixture\/backup"/);
 assert.doesNotMatch(render(RecordEditDialog,{state:legacy,record:legacy.records[0],onSaved:noop}),/D:\/Photos|备份路径/);
 assert.equal(legacy.records[0].stored_path,String.raw`D:\Photos`);
});

test('databack shell retains navigation without storage footer or visible logout', () => {
 const nasHtml=render(Shell,{page:'overview',onNavigate:noop,storage:'mongodb',authRequired:true,onLogout:noop});
 assert.match(nasHtml,/>DataBack</);
 for (const label of ['资料目录','历史记录','数据管理']) assert.ok(nasHtml.includes(label));
 assert.doesNotMatch(nasHtml,/待备份|到期/);
 assert.doesNotMatch(nasHtml,/备份位置/);
 assert.doesNotMatch(nasHtml,/NAS 保存|退出登录/);
 assert.doesNotMatch(nasHtml,/本机保存|本地记录/);
 const localHtml=render(Shell,{page:'overview',onNavigate:noop,storage:'sqlite',authRequired:false});
 assert.doesNotMatch(localHtml,/本机保存/);
 assert.doesNotMatch(localHtml,/退出登录|NAS 保存/);
});

test('app initial render contains a session check and never mounts private ledger content before bootstrap', () => {
 globalThis.window={location:{hash:''}};
 const html=render(App);
 delete globalThis.window;
 assert.match(html,/>DataBack</);
 assert.match(html,/正在确认会话/);
 assert.doesNotMatch(html,/主导航|资料目录|正在读取本机台账/);
});


test('protected SQLite shell remains minimal independently of its storage engine', () => {
 const html=render(Shell,{page:'overview',onNavigate:noop,storage:'sqlite',authRequired:true,onLogout:noop});
 assert.match(html,/>DataBack</);
 assert.doesNotMatch(html,/NAS 保存|退出登录/);
 assert.doesNotMatch(html,/本机保存|本地记录/);
});

test('brand itself is the privacy button with no separate hidden-folder label',()=>{
 for(const privacyOpen of [false,true]){
  const html=render(Shell,{page:'overview',onNavigate:noop,onTogglePrivacy:noop,privacyOpen});
  assert.match(html,/<button[^>]+aria-label="DataBack"/);
  assert.match(html,new RegExp(`aria-pressed="${privacyOpen}"`));
  assert.doesNotMatch(html,/查看隐藏|收起隐藏|privacy-toggle/);
 }
});

test('directory batch controls do not render checkboxes before batch mode is entered',()=>{
 const marked=asset('marked',null,{backup_target:true});
 const html=render(Overview,{state:{...state,assets:[marked]},onBulk:noop,onPaste:noop});
 assert.match(html,/>批量登记</);
 assert.doesNotMatch(html,/type="checkbox"|登记所选|取消选择/);
});

test('local MongoDB shell remains minimal independently of its storage engine', () => {
 const html=render(Shell,{page:'overview',onNavigate:noop,storage:'mongodb',authRequired:false});
 assert.match(html,/>DataBack</);
 assert.doesNotMatch(html,/本机保存/);
 assert.doesNotMatch(html,/NAS 台账|NAS 保存|退出登录/);
});

test('app loading copy follows serving authentication mode for protected SQLite and local MongoDB', async () => {
 const {sessionController}=await import('../src/sessionClient.ts');
 const previousFetch=globalThis.fetch;
 const previousWindow=globalThis.window;
 try {
  globalThis.window={location:{hash:''}};
  for (const [session,expected,unexpected] of [
   [{authenticated:true,auth_required:true,storage:'sqlite'},'正在读取台账','正在读取本机台账'],
   [{authenticated:true,auth_required:false,storage:'mongodb'},'正在读取本机台账','正在读取台账'],
  ]) {
   globalThis.fetch=async () => Response.json(session);
   await sessionController.bootstrap();
   const html=render(App);
   assert.ok(html.includes(expected),`missing ${expected}`);
   assert.ok(!html.includes(unexpected),`unexpected ${unexpected}`);
  }
 } finally {
  globalThis.fetch=previousFetch;
  if(previousWindow===undefined) delete globalThis.window; else globalThis.window=previousWindow;
 }
});

test('details render complete history with notes and visible edit access', () => {
 const expanded={...legacy,versions:[...legacy.versions,{id:'old-version',asset_id:'legacy',label:'oldest',created_at:'2026-01-01'}],records:[...legacy.records,{...legacy.records[0],id:'r2',version_id:'old-version',backed_up_at:'2026-10-10T00:00:00Z',stored_path:'/newest-on-old-version'}]};
 expanded.records[0].notes='异地归档';
 const html=render(AssetDetail,{state:expanded,asset:expanded.assets[0],onEditRecords:noop});
 assert.equal((html.match(/<table/g)??[]).length,1);
 assert.match(html,/>备份时间</); assert.match(html,/>备份位置</);
 assert.match(html,/>备注</); assert.match(html,/异地归档/);
 assert.match(html,/aria-label="编辑备份信息 legacy"/);
 assert.equal((html.match(/<th>/g)??[]).length,3);
 assert.doesNotMatch(html,/>操作<|编辑资料|编辑备份记录|aria-haspopup="menu"/);
 assert.doesNotMatch(html,/D:\/Photos|newest-on-old-version|备份路径|下次备份|每 30 天/);
 assert.ok(html.indexOf('2026-10-09')<html.indexOf('异地归档'));
 assert.doesNotMatch(html,/各位置最新备份|查看全部历史|version-section|backup-summary/);
});

test('empty backup details retain record editing and exactly three columns', () => {
 const html=render(AssetDetail,{state,asset:ordinary,onEditRecords:noop});
 assert.match(html,/aria-label="编辑备份信息 ordinary"/);
 assert.match(html,/colSpan="3"/i);
 assert.match(html,/>备注</);
 assert.doesNotMatch(html,/>操作<|编辑资料|>登记备份</);
});

test('marked terminal folders immediately display records instead of a directory empty state', () => {
 const chosen={...ordinary,backup_target:true};
 const html=render(Overview,{state:{...state,assets:[chosen]},folderId:chosen.id});
 assert.match(html,/备份信息 · ordinary/);
 assert.match(html,/尚未登记备份/);
 assert.doesNotMatch(html,/暂无下级资料|取消备份对象|>备份信息<|搜索当前目录|目录排序/);
 assert.equal((html.match(/<table/g)??[]).length,1);
 assert.doesNotMatch(html,/add-child-menu/);
});

test('dedicated backup information editor opens first-record inputs without asset metadata', async () => {
 const module=await import('../src/components/BackupInfoDialog.tsx').catch(()=>({}));
 assert.equal(typeof module.BackupInfoDialog,'function','missing dedicated backup record editor');
 const html=render(module.BackupInfoDialog,{asset:ordinary,onSaved:noop});
 assert.match(html,/新增备份记录/);
 assert.match(html,/备份时间/); assert.match(html,/备份位置/); assert.match(html,/备注/);
 assert.doesNotMatch(html,/备份路径/);
 assert.match(html,/type="datetime-local"/);
 assert.doesNotMatch(html,/资料名称|所属目录|原始路径|type="checkbox"/);
});

test('registration works without a separately configured location catalog', () => {
 const chosen={...ordinary,backup_target:true};
 const html=render(BackupDialog,{state:{...state,assets:[chosen]},initialAssetId:chosen.id,onSaved:noop,onChooseTargets:noop});
 assert.match(html,/aria-label="备份位置 1"/);
 assert.match(html,/aria-label="备份时间 1"/);
 assert.doesNotMatch(html,/备份路径/);
 assert.match(html,/aria-label="备注 1"/);
 assert.match(html,/添加一行/);
 assert.doesNotMatch(html,/暂无备份位置|新增位置|已选.*个|版本标记/);
 assert.doesNotMatch(html,/<button[^>]*type="submit"[^>]*disabled/);
});

test('directories have no scheduled dates or due-based status even for old backups', () => {
 const html=render(Overview,{state:legacy});
 assert.doesNotMatch(html,/下次备份|到期|正常|待首次备份/);
 assert.doesNotMatch(html,/>状态<|data-label="状态"/);
 assert.match(html,/2026-08-31/);
});

test('marked files open records and never offer invalid child creation', () => {
 const file={...legacy.assets[0],kind:'file'};
 const html=render(Overview,{state:{...legacy,assets:[file]},folderId:file.id});
 assert.match(html,/备份信息 · legacy/);
 assert.doesNotMatch(html,/暂无下级资料|新建文件夹|添加文件|关闭备份信息/);
});

test('a legacy marked parent still displays children rather than hiding the directory', () => {
 const html=render(Overview,{state:{...state,assets:[{...ordinary,backup_target:true},asset('nested',ordinary.id)]},folderId:ordinary.id});
 assert.match(html,/nested/);
 assert.doesNotMatch(html,/备份信息 · ordinary/);
});

test('history shows names and first-level directory scopes without any version controls', () => {
 const root=asset('root',null,{name:'个人文件夹'});
 const photos=asset('photos',root.id,{name:'Photos'});
 const year=asset('year',photos.id,{name:'2025',backup_target:true});
 const all={...legacy,assets:[root,photos,year],versions:[{...legacy.versions[0],asset_id:year.id}]};
 const html=render(History,{state:all,initialFilters:{}});
 assert.match(html,/>名称</);
 assert.match(html,/<option value=""[^>]*>资料<\/option>/);
 assert.match(html,/<option value="photos">个人文件夹\/Photos<\/option>/);
 assert.doesNotMatch(html,/版本|标记：|未标注|<option value="year">/);
 assert.match(html,/个人文件夹\/Photos\/2025/);
});

test('target directories show latest record notes while ordinary-only directories retain three columns', () => {
 const oldVersion={id:'v-old',asset_id:'legacy',label:'old'};
 const latest={...legacy.records[0],id:'latest',version_id:oldVersion.id,backed_up_at:'2026-10-17T08:00:00Z',notes:'最新备份说明'};
 const all={...legacy,assets:[...legacy.assets,ordinary],versions:[...legacy.versions,oldVersion],records:[{...legacy.records[0],notes:'这条不是最新记录'},latest]};
 const html=render(Overview,{state:all});
 assert.match(html,/>备份说明</);
 assert.match(html,/最新备份说明/);
 assert.doesNotMatch(html,/这条不是最新记录/);
 assert.equal((html.match(/<th>/g)??[]).length,4);
 const plain=render(Overview);
 assert.doesNotMatch(plain,/>备份说明</);
 assert.equal((plain.match(/<th>/g)??[]).length,3);
});

test('latest empty notes never fall back to an older description or an ordinary historical record', () => {
 const all={...legacy,records:[{...legacy.records[0],notes:'过时说明'},{...legacy.records[0],id:'new',backed_up_at:'2026-10-17T08:00:00Z',notes:''}]};
 const html=render(Overview,{state:all});
 assert.match(html,/>备份说明</);
 assert.doesNotMatch(html,/过时说明/);
 assert.match(html,/data-label="备份说明"[^>]*><span[^>]*>—</);
});

test('backup details provide an explicit add entry without adding a row actions column', () => {
 const html=render(AssetDetail,{state:legacy,asset:legacy.assets[0],onEditRecords:noop,onAddRecord:noop});
 assert.match(html,/aria-label="新增备份记录 legacy"/);
 assert.match(html,/>新增记录</);
 assert.equal((html.match(/<th>/g)??[]).length,3);
 assert.doesNotMatch(html,/>操作</);
});

test('backup editor exposes new and delete actions and new mode starts with blank record fields', async () => {
 const {BackupInfoDialog}=await import('../src/components/BackupInfoDialog.tsx');
 const existing=render(BackupInfoDialog,{state:legacy,asset:legacy.assets[0],onSaved:noop,onDeleted:noop});
 assert.match(existing,/>新增记录</);assert.match(existing,/>删除记录</);
 const fresh=render(BackupInfoDialog,{state:legacy,asset:legacy.assets[0],initialMode:'new',onSaved:noop,onDeleted:noop});
 assert.match(fresh,/<option value="" selected="">新增备份记录</);
 assert.doesNotMatch(fresh,/<input[^>]*value="NAS fixture"/);
 assert.doesNotMatch(fresh,/>删除记录</);
});
