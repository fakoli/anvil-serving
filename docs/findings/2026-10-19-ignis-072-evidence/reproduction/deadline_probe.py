"""Portable deadline-probe parser and optional HTTP runner.

Fixture mode is offline-only and verifies graceful handling of a malformed
completion response. Live mode requires explicit endpoint, model, and output.
It performs no lifecycle action.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def parse_stream(raw):
    frames=[line[5:].strip() for line in raw.decode('utf-8','replace').splitlines() if line.startswith('data:')]
    chunks=[]
    for frame in frames:
        if frame=='[DONE]': continue
        chunks.append(json.loads(frame))
    return {'done':bool(frames) and frames[-1]=='[DONE]','chunk_count':len(chunks),'finish_reasons':[c['finish_reason'] for x in chunks for c in x.get('choices',[]) if c.get('finish_reason')],'usage':[x['usage'] for x in chunks if x.get('usage')]}
def fixture_mode(fixture, output):
    fixture=json.loads(Path(fixture).read_text())
    # Fixture is deliberately incomplete: a production parser must report failure,
    # not turn missing choices into an uncaught exception.
    response=fixture.get('response', fixture)
    choices=response.get('choices') if isinstance(response,dict) else None
    result={'schema':'anvil-serving.deadline-probe-fixture-result/v1','mode':'offline-fixture','fixture_sha256':digest(args.fixture),'http_contract_passed':False,'validation_error':'missing choices' if not isinstance(choices,list) else None,'passed':not isinstance(choices,list),'scope':'offline malformed-response robustness only; no engine request was sent'}
    Path(output).write_text(json.dumps(result,indent=2)+'\n')
    return result
def request(base_url, payload, timeout):
    req=Request(base_url.rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    started=time.monotonic()
    try:
        with urlopen(req,timeout=timeout) as res: return {'status':res.status,'headers':dict(res.headers.items()),'body':json.loads(res.read()),'elapsed_seconds':time.monotonic()-started}
    except (URLError, ValueError, json.JSONDecodeError) as err: return {'status':None,'error_type':type(err).__name__,'error':str(err),'elapsed_seconds':time.monotonic()-started}
def live_mode(base_url, model, timeout, output):
    payload={'model':model,'messages':[{'role':'user','content':'Reply with exactly OK.'}],'max_tokens':64}
    result={'schema':'anvil-serving.deadline-probe-live-result/v1','mode':'live','request':request(base_url,payload,timeout),'scope':'portable single-request smoke only; it does not reproduce the scaled-deadline diagnostic'}
    Path(output).write_text(json.dumps(result,indent=2)+'\n'); return result
p=argparse.ArgumentParser(); p.add_argument('--fixture'); p.add_argument('--base-url'); p.add_argument('--model'); p.add_argument('--timeout',type=float,default=15); p.add_argument('--output',required=True); args=p.parse_args()
if bool(args.fixture)==bool(args.base_url): p.error('provide exactly one of --fixture or --base-url')
if args.base_url and not args.model: p.error('--model is required with --base-url')
result=fixture_mode(args.fixture,args.output) if args.fixture else live_mode(args.base_url,args.model,args.timeout,args.output)
print(json.dumps({'passed':result['passed'] if 'passed' in result else result['request']['status']==200,'output':args.output}))
