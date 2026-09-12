"use strict";
// Trusted reverse gateway: TLS terminates here; runners never receive provider credentials.
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const crypto = require('node:crypto');
const raw = fs.readFileSync(process.argv[2]);
const policy = JSON.parse(raw);
const digest = crypto.createHash('sha256').update(raw).digest('hex');
const secret = fs.readFileSync(process.argv[4] || '/policy/credential', 'utf8').trim();
if (!secret || /[\r\n]/.test(secret)) throw Error('Invalid provider credential');
if (!/^[a-f0-9]{32}$/.test(policy.session_id || '')) throw Error('Invalid Pi session identity');
const chosen = policy.targets[0];
const suffix = {'openai-completions':'/chat/completions','openai-responses':'/responses','anthropic-messages':'/messages'}[policy.api];
if (!suffix || policy.targets.length !== 1) throw Error('Unsupported gateway policy');
const path = policy.base_path.replace(/\/$/, '') + suffix;
const inputLimit = 4*1024*1024, outputLimit = 8*1024*1024;
let active = 0;
function reject(res, status=403) { res.writeHead(status); res.end(); }
function remoteReference(value) {
  if (!value || typeof value !== 'object') return false;
  return Object.entries(value).some(([key, item]) => ['image_url','file_url','url','file_id','container'].includes(key) || remoteReference(item));
}
const server = http.createServer({maxHeaderSize:16384}, (req, res) => {
  if (req.url === '/health' && req.method === 'GET') {
    res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify({digest})); return;
  }
  if (req.method !== 'POST' || req.url !== path || active >= 2) { reject(res); return; }
  if (!String(req.headers['content-type'] || '').startsWith('application/json')) { reject(res); return; }
  let bytes = 0, chunks = [];
  req.on('data', chunk => {
    bytes += chunk.length;
    if (bytes > inputLimit) { chunks = []; reject(res,413); req.destroy(); }
    else chunks.push(chunk);
  });
  req.on('end', () => {
    if (res.writableEnded || bytes > inputLimit) return;
    let body;
    try { body = JSON.parse(Buffer.concat(chunks)); } catch { reject(res,400); return; }
    if (!body || Array.isArray(body) || !policy.models.includes(body.model) || typeof body.stream !== 'boolean' || remoteReference(body)) { reject(res); return; }
    const allowed = new Set(['model','messages','input','instructions','system','tools','tool_choice','parallel_tool_calls','temperature','top_p','top_k','stop','stop_sequences','stream','stream_options','max_tokens','max_completion_tokens','max_output_tokens','reasoning','reasoning_effort','thinking','text','response_format','include','store','cache_control']);
    if (Object.keys(body).some(k => !allowed.has(k)) || body.store === true) { reject(res); return; }
    if (body.tools && (!Array.isArray(body.tools) || body.tools.length > 64 || body.tools.some(t => t.type && !['function','custom'].includes(t.type)))) { reject(res); return; }
    for (const key of ['max_tokens','max_completion_tokens','max_output_tokens']) {
      if (key in body && (!Number.isInteger(body[key]) || body[key] < 1 || body[key] > policy.max_tokens)) { reject(res); return; }
    }
    if (!['max_tokens','max_completion_tokens','max_output_tokens'].some(k => k in body)) body[policy.api === 'openai-responses' ? 'max_output_tokens' : 'max_tokens'] = policy.max_tokens;
    const encoded = Buffer.from(JSON.stringify(body));
    const headers = {'content-type':'application/json', 'content-length':encoded.length, host:chosen.hostname + (chosen.port === 443 ? '' : ':' + chosen.port)};
    headers['x-anvil-session-id'] = policy.session_id;
    if (policy.api === 'anthropic-messages') { headers['x-api-key'] = secret; headers['anthropic-version'] = '2023-06-01'; }
    else headers.authorization = 'Bearer ' + secret;
    active++;
    let finished = false;
    const done = () => { if (!finished) { finished = true; active--; } };
    const upstream = (chosen.scheme === 'https' ? https : http).request({hostname:chosen.hostname, servername:chosen.hostname, port:chosen.port, method:'POST', path, headers, rejectUnauthorized:true,
      lookup:(_hostname, options, callback) => options.all ? callback(null,[{address:chosen.addresses[0],family:chosen.addresses[0].includes(':')?6:4}]) : callback(null,chosen.addresses[0],chosen.addresses[0].includes(':')?6:4), timeout:300000}, response => {
      if (response.statusCode >= 300 && response.statusCode < 400) { response.destroy(); reject(res,502); done(); return; }
      res.writeHead(response.statusCode, {'content-type':response.headers['content-type'] === 'text/event-stream' ? 'text/event-stream' : 'application/json','cache-control':'no-store'});
      let output = 0, pending = Buffer.alloc(0);
      const needle = Buffer.from(secret);
      response.on('data', chunk => {
        output += chunk.length;
        if (output > outputLimit) { response.destroy(); res.destroy(); done(); return; }
        pending = Buffer.concat([pending,chunk]);
        // Do not leak credentials even if a provider echoes them across chunks.
        let index;
        while ((index = pending.indexOf(needle)) >= 0) pending = Buffer.concat([pending.subarray(0,index),Buffer.from('[redacted]'),pending.subarray(index+needle.length)]);
        const safe = Math.max(0,pending.length-needle.length+1);
        if (safe) { res.write(pending.subarray(0,safe)); pending=pending.subarray(safe); }
      });
      response.on('end', () => { res.end(pending); done(); });
      response.on('error', () => { res.destroy(); done(); });
    });
    upstream.on('error', () => { if (!res.headersSent) reject(res,502); else res.destroy(); done(); });
    upstream.on('timeout', () => upstream.destroy());
    res.on('close', () => { upstream.destroy(); done(); });
    upstream.end(encoded);
  });
});
server.on('connect', (_req, client) => client.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'));
server.maxConnections = 8;
server.headersTimeout = 15000;
server.requestTimeout = 300000;
server.on('clientError', (_error, socket) => socket.destroy());
server.listen(Number(process.argv[3] || 3128), '0.0.0.0');
