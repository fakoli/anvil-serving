// UI acceptance against the production Home assets and a bounded API fixture.
// The Go/Python suites separately exercise the real authority and broker seams.
import {test,expect} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';

let server,base,state,operations,unavailable;
const path='/_anvil-connect/home';
const owner='human:'+'a'.repeat(64);
const account=()=>({owner,status:'approved',revision:2,models:['llm.primary'],paths:['/v1/models','/v1/chat/completions'],rpm:60,expires_days:30});

test.beforeAll(async()=>{
  const root=new URL('../internal/httpedge/portal/',import.meta.url);
  const assets={};
  for(const name of ['home.html','home.js','home.css']) assets[name]=await readFile(new URL(name,root),'utf8');
  server=createServer(async(req,res)=>{
    res.setHeader('Cache-Control','no-store');
    if(req.url===path){res.setHeader('Content-Type','text/html');res.end(assets['home.html'].replaceAll('{{.}}',path));return;}
    const asset=req.url.slice(path.length+1);
    if(asset==='logo.png'){res.setHeader('Content-Type','image/png');res.end(await readFile(new URL(asset,root)));return;}
    if(['home.js','home.css'].includes(asset)){res.setHeader('Content-Type',asset.endsWith('js')?'text/javascript':'text/css');res.end(assets[asset]);return;}
    if(req.url===path+'/data'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({services:[],router_keys:true,account_url:'#',passkeys_url:'#',logout_path:'/logout'}));return;}
    if(req.url==='/logout'){res.writeHead(204);res.end();return;}
    if(req.url===path+'/router-keys'){
      if(unavailable){res.writeHead(503);res.end('{}');return;}
      res.setHeader('Content-Type','application/json');res.setHeader('X-CSRF-Token','fixture-session-csrf');
      if(req.method==='GET'){res.end(JSON.stringify(state));return;}
      if(req.headers['x-csrf-token']!=='fixture-session-csrf'){res.writeHead(403);res.end('{}');return;}
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const operation=JSON.parse(Buffer.concat(chunks));operations.push(operation);
      if(operation.action==='request')state.account={...account(),status:'pending',revision:1};
      if(operation.action==='approve'){state.accounts[0]={...state.accounts[0],...operation,revision:state.accounts[0].revision+1};}
      if(operation.action==='forget')state.accounts=state.accounts.filter(account=>account.owner!==operation.owner);
      if(operation.action==='create'){
        const key={key_id:'key_fixture',name:operation.name,models:operation.models,paths:operation.paths,rpm:operation.rpm,expires_at:Math.floor(Date.now()/1000)+86400,revoked_at:null};
        state.keys.push(key);res.end(JSON.stringify({key,secret:'ask_'+randomBytes(32).toString('base64url')}));return;
      }
      if(operation.action==='revoke')state.keys.find(key=>key.key_id===operation.key_id).revoked_at=Math.floor(Date.now()/1000);
      res.end('{"ok":true}');return;
    }
    res.writeHead(404);res.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base=`http://127.0.0.1:${server.address().port}${path}`;
});
test.afterAll(async()=>{await new Promise(resolve=>server.close(resolve));});
test.beforeEach(()=>{state={account:null,keys:[],usage:[],usage_totals:{requests:0,errors:0,rate_limited:0},usage_window:'Retained metadata log; HTTP status does not prove stream completion.',accounts:null,models:[],paths:[]};operations=[];unavailable=false;});

test('member requests access, creates and saves one key, then reviews and revokes it',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await expect(page.locator('#key-workspace')).toBeHidden();await expect(page.locator('#router-approvals')).toBeHidden();
  await page.getByRole('button',{name:'Request router access'}).click();
  await expect(page.locator('#key-status')).toHaveText('Awaiting approval');await expect(page.locator('#request-api-access')).toBeHidden();
  await page.screenshot({path:test.info().outputPath('member-pending.png'),fullPage:true});
  state.account=account();await page.locator('#refresh-keys').click();await expect(page.locator('#key-list')).toContainText('No keys yet');
  await expect(page.locator('#create-key-panel')).toBeHidden();await page.getByRole('button',{name:'Create API key',exact:true}).click();
  await expect(page.getByLabel('Device or app name')).toBeFocused();await expect(page.locator('#key-advanced')).not.toHaveAttribute('open','');
  await page.getByLabel('Device or app name').fill('Work laptop');await page.getByRole('button',{name:'Create key',exact:true}).click();
  await expect(page.locator('#new-key-heading')).toBeFocused();await expect(page.locator('#key-secret')).toHaveValue(/^ask_/);await expect(page.locator('#open-create-key')).toBeDisabled();
  expect(operations.find(op=>op.action==='create')).toMatchObject({models:['llm.primary'],paths:['/v1/chat/completions'],rpm:60,expires_days:30});
  await page.getByRole('button',{name:'Show key',exact:true}).click();await expect(page.locator('#key-secret')).toHaveAttribute('type','text');
  await page.getByRole('button',{name:'Hide key',exact:true}).click();await expect(page.locator('#key-secret')).toHaveAttribute('type','password');
  await page.getByRole('button',{name:'I saved it'}).click();await expect(page.locator('#key-secret')).toHaveValue('');
  state.usage_totals={requests:7,errors:2,rate_limited:1};state.usage=[{key_id:'key_fixture',requests:7,errors:2,rate_limited:1,last_used:Math.floor(Date.now()/1000),average_ms:12}];
  await page.locator('#refresh-keys').click();await expect(page.locator('#key-summary strong')).toHaveText(['7','2','1']);
  await page.screenshot({path:test.info().outputPath('member-keys-desktop.png'),fullPage:true});
  await page.reload();await expect(page.locator('#new-key')).toBeHidden();
  await page.getByRole('button',{name:'Revoke key for Work laptop'}).click();await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button',{name:'Cancel'}).click();expect(operations.filter(op=>op.action==='revoke')).toHaveLength(0);
  await page.getByRole('button',{name:'Revoke key for Work laptop'}).click();await page.getByRole('dialog').getByRole('button',{name:'Revoke key',exact:true}).click();
  await expect(page.locator('#key-list')).toContainText('Revoked');await expect(page.locator('#refresh-keys')).toBeFocused();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('member-keys-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'Sign out of Connect'}).click();await expect(page.locator('#router-keys')).toBeHidden();expect(errors).toEqual([]);
});

test('operator reviews named requests and confirms changes that revoke keys',async({page})=>{
  state.accounts=[{...account(),username:'Example member',available:true,status:'pending',models:[],paths:[],rpm:0,expires_days:0}];state.models=['llm.primary','llm.heavy'];state.paths=['/v1/chat/completions'];
  await page.goto(base);await expect(page.locator('#approval-count')).toHaveText('1 pending');await expect(page.locator('.router-account')).toHaveAttribute('open','');
  await expect(page.locator('.router-account>summary')).toContainText('Example member');
  await page.locator('#router-accounts').getByLabel('llm.primary',{exact:true}).check();
  await page.locator('#router-accounts').getByLabel('Chat · OpenAI (/v1/chat/completions)',{exact:true}).check();
  await page.screenshot({path:test.info().outputPath('operator-review.png'),fullPage:true});
  await page.getByRole('button',{name:'Approve access',exact:true}).click();await expect(page.locator('#approval-count')).toHaveText('0 pending');
  expect(operations[0]).toMatchObject({action:'approve',owner,models:['llm.primary'],paths:['/v1/chat/completions'],rpm:60,expires_days:30});
  await page.locator('.router-account>summary').click();await page.getByLabel('Shared requests per minute').fill('30');
  await page.getByRole('button',{name:'Save limits and revoke keys',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('All existing router keys');
  await page.getByRole('dialog').getByRole('button',{name:'Cancel'}).click();expect(operations).toHaveLength(1);
  await page.getByRole('button',{name:'Save limits and revoke keys',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Save limits and revoke keys',exact:true}).click();
  await expect.poll(()=>operations.length).toBe(2);expect(operations[1]).toMatchObject({rpm:30,revision:3});
});

test('denied, unavailable, and changed-account states give a safe next action',async({page})=>{
  state.account={...account(),status:'denied'};state.accounts=[{...account(),available:false,username:''}];
  await page.goto(base);await expect(page.locator('#key-status')).toHaveText('Access not approved');await expect(page.locator('#open-create-key')).toBeHidden();await expect(page.locator('#request-api-access')).toBeHidden();
  await page.locator('.router-account>summary').click();await expect(page.locator('#router-accounts')).toContainText('must request access again');await expect(page.getByRole('button',{name:'Approve access',exact:true})).toHaveCount(0);
  unavailable=true;await page.locator('#refresh-keys').click();await expect(page.locator('#key-notice')).toHaveAttribute('role','alert');await expect(page.locator('#key-notice')).toContainText('temporarily unavailable');
  unavailable=false;await page.locator('#refresh-keys').click();await expect(page.locator('#key-notice')).toContainText('up to date');
});

test('a changed account cannot present old keys as active',async({page})=>{
  state.keys=[{key_id:'key_old',name:'Old laptop',models:['llm.primary'],paths:['/v1/chat/completions'],rpm:60,expires_at:Math.floor(Date.now()/1000)+86400,revoked_at:null}];
  await page.goto(base);await expect(page.locator('#key-list')).toContainText('Access inactive');await expect(page.locator('#key-count')).toHaveText('0 active');await expect(page.locator('#open-create-key')).toBeHidden();
  await page.getByRole('button',{name:'Request router access'}).click();await expect(page.locator('#key-status')).toHaveText('Awaiting approval');await expect(page.locator('#key-list')).toContainText('Access inactive');
});
