import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
const moduleURL = new URL('../src/request.ts', import.meta.url);
const requests = existsSync(moduleURL) ? await import(moduleURL.href) : {};
const controllerURL = new URL('../src/session.ts', import.meta.url);
const sessions = existsSync(controllerURL) ? await import(controllerURL.href) : {};
const nas = {authenticated:false,auth_required:true,storage:'mongodb'};
function deferred() {let resolve; const promise=new Promise(yes => {resolve=yes;}); return {promise,resolve};}
function client() {assert.equal(typeof sessions.createSessionController, 'function'); return sessions.createSessionController(async path => ({...nas,authenticated:path !== 'logout'}));}
function request(...args) {assert.equal(typeof requests.privateRequest, 'function'); return requests.privateRequest(...args);}

test('private requests never reach fetch before session authentication', async () => {
 const session=client(); let calls=0;
 await assert.rejects(request(session,'state','GET',undefined,async () => {calls++; return Response.json({});}), /登录|会话/);
 assert.equal(calls,0);
});

test('same-origin private 401 expires the active session, while wrong login is just a request error', async () => {
 const session=client(); await session.bootstrap();
 await assert.rejects(request(session,'state','GET',undefined,async (_url, options) => {assert.equal(options.credentials,'same-origin'); return Response.json({error:'会话已过期'},{status:401});}), /过期/);
 assert.equal(session.getSnapshot().status,'anonymous');
 assert.equal(typeof requests.publicRequest,'function');
 await assert.rejects(requests.publicRequest('login','POST',{password:'wrong'},async (url,options) => {assert.equal(url,'/api/login'); assert.equal(options.body,JSON.stringify({password:'wrong'})); return Response.json({error:'密码错误'},{status:401});}), /密码错误/);
});

test('older success and older 401 cannot leak private data or expire a newer session', async () => {
 for (const status of [200,401]) {
  const session=client(); await session.bootstrap(); const pending=deferred();
  const old=request(session,'state','GET',undefined,() => pending.promise);
  await session.logout(); await session.login('fixture');
  pending.resolve(Response.json(status===200 ? {assets:[{name:'private'}]} : {error:'old unauthorized'},{status}));
  await assert.rejects(old,/会话/);
  assert.equal(session.getSnapshot().status,'authenticated');
 }
});

test('429 exposes bounded server retry delay without expiring an authenticated session',async()=>{
 const session=client();await session.bootstrap();
 await assert.rejects(request(session,'records','POST',{},async()=>Response.json({error:'稍后重试',retry_after:12},{status:429,headers:{'Retry-After':'12'}})),error=>error.status===429&&error.retryAfter===12);
 assert.equal(session.getSnapshot().status,'authenticated');
 await assert.rejects(requests.publicRequest('login','POST',{},async()=>Response.json({error:'稍后重试',retry_after:999999},{status:429})),error=>error.retryAfter===3600);
});
test('private requests can send an ephemeral tab proof without changing credentials or origin',async()=>{
 const session=client();await session.bootstrap();
 await request(session,'state','GET',undefined,async(url,options)=>{
  assert.equal(url,'/api/state');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-Databack-Privacy'],'fixture-proof');return Response.json({assets:[]});
 },{'X-Databack-Privacy':'fixture-proof'});
});
