import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
const moduleURL = new URL('../src/session.ts', import.meta.url);
const sessions = existsSync(moduleURL) ? await import(moduleURL.href) : {};
const nas = {authenticated: false, auth_required: true, storage: 'mongodb'};
const loggedIn = {...nas, authenticated: true};
function deferred() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; }
function harness() {
  assert.equal(typeof sessions.createSessionController, 'function');
  const pending = [];
  const controller = sessions.createSessionController((path, body) => {const operation=deferred(); pending.push({...operation,path,body}); return operation.promise;});
  return {controller,pending};
}
async function authenticate(h) { const task=h.controller.bootstrap(); h.pending.at(-1).resolve(loggedIn); await task; }

test('bootstrap starts closed and only opens private access after an authenticated session', async () => {
  const h=harness();
  assert.equal(h.controller.getSnapshot().status, 'checking');
  assert.throws(() => h.controller.privateToken(), /登录|会话/);
  const load=h.controller.bootstrap(); h.pending[0].resolve(nas); await load;
  assert.equal(h.controller.getSnapshot().status, 'anonymous');
  assert.throws(() => h.controller.privateToken(), /登录|会话/);
  const login=h.controller.login('fixture password');
  assert.deepEqual(h.pending[1].body, {password: 'fixture password'});
  h.pending[1].resolve(loggedIn); await login;
  assert.equal(h.controller.getSnapshot().status, 'authenticated');
  assert.equal(h.controller.isPrivateCurrent(h.controller.privateToken()), true);
});

test('logout closes private access immediately and older bootstrap or login successes cannot reopen it', async () => {
  for (const first of ['bootstrap', 'login']) {
    const h=harness(); const earlier= first==='bootstrap' ? h.controller.bootstrap() : h.controller.login('fixture');
    const logout=h.controller.logout();
    assert.equal(h.controller.getSnapshot().status, 'logging-out');
    assert.throws(() => h.controller.privateToken(), /登录|会话/);
    h.pending[0].resolve(loggedIn); await earlier;
    assert.equal(h.controller.getSnapshot().status, 'logging-out');
    h.pending[1].resolve(nas); await logout;
    assert.equal(h.controller.getSnapshot().status, 'anonymous');
  }
});

test('expiry invalidates pending private responses and stale 401 cannot expire a newer login', async () => {
  const h=harness(); await authenticate(h);
  const older=h.controller.privateToken();
  h.controller.expire(older);
  assert.equal(h.controller.isPrivateCurrent(older), false);
  assert.equal(h.controller.getSnapshot().status, 'anonymous');
  const login=h.controller.login('fixture'); h.pending[1].resolve(loggedIn); await login;
  h.controller.expire(older);
  assert.equal(h.controller.getSnapshot().status, 'authenticated');
});

test('failed logout stays locally closed and clearly reports that server revocation is unconfirmed', async () => {
  const h=harness(); await authenticate(h);
  const token=h.controller.privateToken(); const logout=h.controller.logout();
  h.pending[1].reject(new Error('offline')); await logout;
  const view=h.controller.getSnapshot();
  assert.equal(view.status, 'anonymous');
  assert.equal(view.logoutUnconfirmed, true);
  assert.match(view.error, /未确认|未完成/);
  assert.equal(h.controller.isPrivateCurrent(token), false);
});

test('wrong login and session connection errors remain recoverable without opening private access', async () => {
  const h=harness(); const initial=h.controller.bootstrap(); h.pending[0].reject(new Error('offline')); await initial;
  assert.equal(h.controller.getSnapshot().status, 'error');
  const retry=h.controller.bootstrap(); h.pending[1].resolve(nas); await retry;
  const login=h.controller.login('bad fixture'); h.pending[2].reject(new Error('密码错误')); await login;
  assert.equal(h.controller.getSnapshot().status, 'anonymous');
  assert.equal(h.controller.getSnapshot().error, '密码错误');
});

test('local SQLite bootstrap authenticates without password and unsupported session responses stay closed', async () => {
  const h=harness(); const initial=h.controller.bootstrap(); h.pending[0].resolve({authenticated:true,auth_required:false,storage:'sqlite'}); await initial;
  assert.equal(h.controller.getSnapshot().status, 'authenticated');
  const bad=harness(); const malformed=bad.controller.bootstrap(); bad.pending[0].resolve({authenticated:true,storage:'mongodb'}); await malformed;
  assert.equal(bad.controller.getSnapshot().status, 'error');
});

test('protected SQLite accepts anonymous bootstrap then login and logout without weakening private access', async () => {
 const h=harness(); const protectedSqlite={authenticated:false,auth_required:true,storage:'sqlite'};
 const bootstrap=h.controller.bootstrap(); h.pending[0].resolve(protectedSqlite); await bootstrap;
 assert.equal(h.controller.getSnapshot().status,'anonymous');
 assert.deepEqual(h.controller.getSnapshot().session,protectedSqlite);
 assert.throws(() => h.controller.privateToken(),/登录|会话/);
 const login=h.controller.login('fixture'); h.pending[1].resolve({...protectedSqlite,authenticated:true}); assert.equal(await login,true);
 const token=h.controller.privateToken(); assert.equal(h.controller.isPrivateCurrent(token),true);
 const logout=h.controller.logout(); assert.equal(h.controller.isPrivateCurrent(token),false);
 h.pending[2].resolve(protectedSqlite); await logout;
 assert.equal(h.controller.getSnapshot().status,'anonymous');
 assert.equal(h.controller.getSnapshot().logoutUnconfirmed,false);
});

test('local MongoDB session opens after authenticated bootstrap with no required login', async () => {
 const h=harness(); const localMongo={authenticated:true,auth_required:false,storage:'mongodb'};
 const bootstrap=h.controller.bootstrap(); h.pending[0].resolve(localMongo); await bootstrap;
 assert.equal(h.controller.getSnapshot().status,'authenticated');
 assert.deepEqual(h.controller.getSnapshot().session,localMongo);
 assert.equal(h.controller.isPrivateCurrent(h.controller.privateToken()),true);
 assert.equal(h.pending.length,1,'no login request is needed for local MongoDB');
});

test('login cooldown blocks repeated network attempts until the server delay expires',async()=>{
 let now=100000,calls=0;
 const controller=sessions.createSessionController(async()=>{
  calls++;
  if(calls===1)throw Object.assign(new Error('登录过快'),{status:429,retryAfter:12});
  return loggedIn;
 },()=>now);
 assert.equal(await controller.login('fixture'),false);
 assert.equal(controller.getSnapshot().loginRetryAt,112000);
 assert.equal(await controller.login('fixture'),false);assert.equal(calls,1);
 now=112000;assert.equal(await controller.login('fixture'),true);assert.equal(calls,2);
 assert.equal(controller.getSnapshot().loginRetryAt,undefined);
});
