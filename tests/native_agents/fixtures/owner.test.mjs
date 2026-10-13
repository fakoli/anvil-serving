import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtempSync,writeFileSync,rmSync,realpathSync,mkdirSync,symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {nativeAgentsOwner} from './owner.ts';
import {nativeAgentsOwnerAuthorized} from './auth.ts';
import {state} from './owner-stub.ts';
process.env.PI_WEB_NATIVE_AGENTS_TOKEN='fixture-private-owner-token';
process.env.PI_WEB_PASSWORD='different-browser-password';
const sessionID='native-'+'a'.repeat(64);
async function call(action,extra={},token=process.env.PI_WEB_NATIVE_AGENTS_TOKEN) {
  return nativeAgentsOwner(new Request('http://127.0.0.1:9001/api/native-agents-owner',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({action,sessionID,...extra})}));
}
async function catalog(){return (await call('catalog')).json()}
const tick=()=>new Promise(r=>setTimeout(r,5));

test('native token is separate from browser password',async()=>{
  assert.equal(nativeAgentsOwnerAuthorized('Bearer '+process.env.PI_WEB_PASSWORD),false);
  assert.equal(nativeAgentsOwnerAuthorized('Bearer '+process.env.PI_WEB_NATIVE_AGENTS_TOKEN),true);
  assert.equal((await call('state',{},process.env.PI_WEB_PASSWORD)).status,404);
});
test('already pending typed interaction is visible and answerable exactly once',async()=>{
  state.pending=[{type:'extension_ui_request',id:'dialog',method:'select',title:'Choose',options:['yes','no']}];
  const snapshot=await (await call('interactions')).json();assert.equal(snapshot.interactions[0].id,'dialog');
  assert.equal((await call('respond',{ownerInstance:snapshot.ownerInstance,interactionID:'dialog',answer:{value:'other'}})).status,409);
  assert.equal((await call('respond',{ownerInstance:snapshot.ownerInstance,interactionID:'dialog',answer:{value:'yes'}})).status,200);
  assert.equal((await call('respond',{ownerInstance:snapshot.ownerInstance,interactionID:'dialog',answer:{value:'yes'}})).status,409);
});
test('catalog change before dispatch rejects selected descriptor',async()=>{
  const old=await catalog();state.commands=[{name:'review',source:'extension',description:'Different behavior'}];
  const before=state.calls.filter(c=>c.type==='prompt').length;
  assert.equal((await call('invoke_command',{requestID:randomUUID(),ownerInstance:old.ownerInstance,commandID:old.commands[0].id,catalogRevision:old.revision,arguments:''})).status,409);
  assert.equal(state.calls.filter(c=>c.type==='prompt').length,before);
});
test('human preflight waits return receipt immediately and settle without repost',async()=>{
  let release;state.gate=new Promise(r=>release=r);const current=await catalog();const requestID=randomUUID();
  const response=await call('invoke_command',{requestID,ownerInstance:current.ownerInstance,commandID:current.commands[0].id,catalogRevision:current.revision,arguments:'carefully'});
  assert.equal(response.status,202);assert.equal((await response.json()).state,'dispatched');
  assert.equal((await (await call('operation',{requestID})).json()).state,'dispatched');
  release();state.gate=undefined;await tick();
  assert.equal((await (await call('operation',{requestID})).json()).state,'accepted');
  assert.equal(state.calls.filter(c=>c.type==='prompt'&&c.message==='/review carefully').length,1);
});
test('Stop overtaking a delayed prompt cancels that request only',async()=>{
  const current=await catalog();const requestID=randomUUID();
  assert.equal((await call('abort',{pendingRequests:[requestID]})).status,200);
  const before=state.calls.filter(c=>c.type==='prompt').length;
  assert.equal((await call('invoke',{requestID,ownerInstance:current.ownerInstance,message:'must not execute'})).status,409);
  assert.equal(state.calls.filter(c=>c.type==='prompt').length,before);
  assert.equal((await call('invoke',{requestID:randomUUID(),ownerInstance:current.ownerInstance,message:'new work'})).status,202);
});
test('owner generation fences old invocations',async()=>{
 assert.equal((await call('invoke',{requestID:randomUUID(),ownerInstance:'old-owner',message:'stale'})).status,409);
});


test('saved history delegates using a fresh bodyless GET',async()=>{
  const root=realpathSync(mkdtempSync(join(tmpdir(),'owner-history-')));
  state.path=join(root,'saved.jsonl');writeFileSync(state.path,JSON.stringify({cwd:root}));
  try {
    for(const before of [null,'entry-1']) {
      const response=await call('history',{before});
      assert.equal(response.status,200);
      assert.deepEqual((await response.json()).context.messages,[]);
      const delegated=state.historyRequests.at(-1);
      assert.equal(delegated.method,'GET');assert.equal(delegated.body,null);
      assert.equal(delegated.headers.get('authorization'),null);
      assert.equal(new URL(delegated.url).searchParams.get('before'),before);
    }
  } finally {state.path=null;rmSync(root,{recursive:true,force:true});}
});


test('expired empty session ignores a cached unwritten path and keeps native ID and allowed cwd',async()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'owner-empty-')));const cached=join(root,'not-written.jsonl');
 process.env.PI_WEB_NATIVE_AGENTS_DIRECTORIES=JSON.stringify([root]);state.path=cached;state.alive=false;
 const starts=state.starts.length,opens=state.opens.length,prompts=state.calls.filter(c=>c.type==='prompt').length;
 try {
  const response=await call('ensure',{cwd:root});assert.equal(response.status,200);
  assert.deepEqual(await response.json(),{sessionID});assert.equal(state.cwd,root);
  assert.deepEqual(state.starts.at(-1),{id:sessionID,path:'',cwd:root,options:{sessionId:sessionID}});
  assert.equal(state.starts.length,starts+1);assert.equal(state.opens.length,opens);
  const delegated=state.historyRequests.length;const history=await call('history',{before:null});assert.equal(history.status,200);
  assert.deepEqual((await history.json()).context.messages,[]);assert.equal(state.historyRequests.length,delegated);
  assert.equal((await call('ensure',{cwd:root})).status,200);assert.equal(state.starts.length,starts+1);
  assert.equal(state.calls.filter(c=>c.type==='prompt').length,prompts);
 } finally {state.path=null;state.alive=true;rmSync(root,{recursive:true,force:true});}
});

test('read without ensure cannot revive a stale deferred path in the default cwd',async()=>{
 state.alive=false;state.path='/fixture/missing-transient.jsonl';const starts=state.starts.length;
 try {
  for(const action of ['state','history'])assert.equal((await call(action,action==='history'?{before:null}:{})).status,404);
  assert.equal(state.starts.length,starts);
 } finally {state.alive=true;state.path=null;}
});

test('real saved directory mismatch stays rejected and an allowed saved session is reused',async()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'owner-saved-')));const other=join(root,'other');mkdirSync(other);
 process.env.PI_WEB_NATIVE_AGENTS_DIRECTORIES=JSON.stringify([root]);state.alive=false;state.path=join(root,'saved.jsonl');
 const starts=state.starts.length;
 try {
  writeFileSync(state.path,JSON.stringify({cwd:other}));assert.equal((await call('ensure',{cwd:root})).status,409);assert.equal(state.starts.length,starts);
  writeFileSync(state.path,JSON.stringify({cwd:root}));assert.equal((await call('ensure',{cwd:root})).status,200);assert.equal(state.starts.length,starts+1);assert.equal(state.starts.at(-1).path,state.path);
 } finally {state.alive=true;state.path=null;rmSync(root,{recursive:true,force:true});}
});

test('cached paths that are symlinks or directories fail closed without starting a session',async()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'owner-irregular-')));const saved=join(root,'saved.jsonl');writeFileSync(saved,JSON.stringify({cwd:root}));
 const link=join(root,'link.jsonl');symlinkSync(saved,link);process.env.PI_WEB_NATIVE_AGENTS_DIRECTORIES=JSON.stringify([root]);
 state.alive=false;const starts=state.starts.length,opens=state.opens.length;
 try {
  for(const path of [link,root]) {
   state.path=path;assert.equal((await call('ensure',{cwd:root})).status,503);
   assert.equal((await call('state')).status,503);
  }
  assert.equal(state.starts.length,starts);assert.equal(state.opens.length,opens);
 } finally {state.alive=true;state.path=null;rmSync(root,{recursive:true,force:true});}
});
