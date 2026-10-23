import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
const url=new URL('../src/visibilityFence.ts',import.meta.url);
const module=existsSync(url)?await import(url.href):{};
function fence(){assert.equal(typeof module.createVisibilityFence,'function');return module.createVisibilityFence();}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}

test('current visibility requests return normally without persisting private data',async()=>{
  const current=fence();assert.deepEqual(await current.guard(async()=>({assets:['visible']})),{assets:['visible']});
});
test('locking or changing privacy invalidates an in-flight successful response',async()=>{
  const current=fence(),pending=deferred();const read=current.guard(()=>pending.promise);
  current.invalidate();pending.resolve({assets:['private-name']});await assert.rejects(read,/视图已更新/);
  assert.deepEqual(await current.guard(async()=>({assets:[]})),{assets:[]});
});
test('old failed writes cannot report success or trigger post-save actions after lock',async()=>{
  const current=fence(),pending=deferred();const read=current.guard(()=>pending.promise);
  current.invalidate();pending.reject(new Error('private-name in an old error'));await assert.rejects(read,error=>/视图已更新/.test(error.message)&&!error.message.includes('private-name'));
});
test('multiple visibility transitions invalidate every older generation',async()=>{
  const current=fence(),old=deferred();const oldRead=current.guard(()=>old.promise);current.invalidate();
  const newer=deferred();const newRead=current.guard(()=>newer.promise);current.invalidate();
  old.resolve('old');newer.resolve('newer');await assert.rejects(oldRead,/视图已更新/);await assert.rejects(newRead,/视图已更新/);
});
test('privacy proof is memory-only and is discarded on every visibility change',()=>{
  const current=fence();assert.equal(typeof current.setPrivacyToken,'function');
  assert.equal(current.privacyToken(),'');current.setPrivacyToken('fixture-per-tab-proof');assert.equal(current.privacyToken(),'fixture-per-tab-proof');
  current.invalidate();assert.equal(current.privacyToken(),'');
});
