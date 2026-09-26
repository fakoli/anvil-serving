// UI acceptance against the production Home assets and a bounded API fixture.
// The Go/Python suites separately exercise the real authority and broker seams.
import {test,expect} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';

let server,base,state,operations;
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
    if(['home.js','home.css'].includes(asset)){res.setHeader('Content-Type',asset.endsWith('js')?'text/javascript':'text/css');res.end(assets[asset]);return;}
    if(req.url===path+'/data'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({services:[],router_keys:true,account_url:'#',passkeys_url:'#',logout_path:'/logout'}));return;}
    if(req.url==='/logout'){res.writeHead(204);res.end();return;}
    if(req.url===path+'/router-keys'){
      res.setHeader('Content-Type','application/json');res.setHeader('X-CSRF-Token','fixture-session-csrf');
      if(req.method==='GET'){res.end(JSON.stringify(state));return;}
      if(req.headers['x-csrf-token']!=='fixture-session-csrf'){res.writeHead(403);res.end('{}');return;}
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const operation=JSON.parse(Buffer.concat(chunks));operations.push(operation);
      if(operation.action==='request')state.account={...account(),status:'pending',revision:1};
      if(operation.action==='approve'){state.accounts[0]={...state.accounts[0],...operation};}
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
test.beforeEach(()=>{state={account:null,keys:[],usage:[],usage_totals:{requests:0,errors:0,rate_limited:0},usage_window:'Retained metadata log; HTTP status does not prove stream completion.',accounts:null,models:[],paths:[]};operations=[];});

test('request, create once, usage, revoke and logout on Home',async({page})=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.getByRole('button',{name:'Request router access'}).click();
  await expect(page.locator('#key-access')).toContainText('waiting for operator approval');
  state.account=account();await page.getByRole('button',{name:'Refresh keys and usage'}).click();
  await page.getByLabel('Device or key name').fill('My laptop');await page.getByRole('button',{name:'Create API key'}).click();
  await expect(page.locator('#new-key')).toBeVisible();await expect(page.locator('#key-secret')).toHaveValue(/^ask_/);
  expect(operations.find(op=>op.action==='create')).toMatchObject({models:['llm.primary'],paths:['/v1/chat/completions'],rpm:60,expires_days:30});
  await page.getByRole('button',{name:'I saved it'}).click();await expect(page.locator('#key-secret')).toHaveValue('');
  state.usage_totals={requests:7,errors:2,rate_limited:1};
  state.usage=[{key_id:'key_fixture',requests:7,errors:2,rate_limited:1,last_used:Math.floor(Date.now()/1000),average_ms:12}];
  await page.getByRole('button',{name:'Refresh keys and usage'}).click();await expect(page.locator('#key-summary')).toContainText('7 requests');
  await page.screenshot({path:test.info().outputPath('router-keys-desktop.png'),fullPage:true});
  await page.reload();await expect(page.locator('#new-key')).toBeHidden();
  page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Revoke key',exact:true}).click();
  await expect(page.locator('#key-list')).toContainText('Revoked');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('router-keys-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'Sign out of Connect'}).click();await expect(page.locator('#router-keys')).toBeHidden();
  expect(errors).toEqual([]);
});

test('operator approval chooses explicit models and endpoints',async({page})=>{
  state.accounts=[{...account(),status:'pending',models:[],paths:[],rpm:0,expires_days:0}];state.models=['llm.primary','llm.heavy'];state.paths=['/v1/chat/completions'];
  await page.goto(base);
  await page.locator('#router-accounts').getByLabel('llm.primary',{exact:true}).check();
  await page.locator('#router-accounts').getByLabel('/v1/chat/completions',{exact:true}).check();
  await page.getByRole('button',{name:'Approve access',exact:true}).click();
  await expect(page.locator('#router-accounts')).toContainText('Status: approved');
  expect(operations[0]).toMatchObject({action:'approve',owner,models:['llm.primary'],paths:['/v1/chat/completions'],rpm:60,expires_days:30});
});
