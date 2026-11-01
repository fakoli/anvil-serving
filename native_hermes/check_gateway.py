"""Disposable OpenShell check: canonical Hermes tool turn against a local test server.

This checks the integration, not inference-model quality. No real credential is used.
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from aiohttp import web
from owner import Owner


async def main():
    root = Path('/sandbox/canonical-check-' + uuid.uuid4().hex[:8])
    root.mkdir(mode=0o700)  # Refuse to overwrite or replay an earlier run.
    home = root / 'hermes'
    home.mkdir(mode=0o700)
    (home / 'plugins').mkdir()
    (home / 'plugins/holographic').symlink_to('/opt/anvil-plugins/holographic')
    os.environ['HERMES_HOME'] = str(home)
    sys.path.insert(0, '/opt/hermes')
    from hermes_cli.config import DEFAULT_CONFIG

    calls = []
    async def completion(request):
        body = await request.json()
        calls.append({'model': body.get('model'), 'stream': body.get('stream'),
                      'tools': [item['function']['name'] for item in body.get('tools', [])]})
        assert body['model'] == 'qualification-model'
        tool_reply = any(item.get('role') == 'tool' for item in body['messages'])
        if not calls[-1]['tools']:
            message = {'role': 'assistant', 'content': 'Qualification tool check'}
            finish = 'stop'
        elif not tool_reply:
            assert 'terminal' in calls[-1]['tools']
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_probe', 'type': 'function',
                'function': {'name': 'terminal', 'arguments': json.dumps({'command': f"printf HERMES_TOOL_CANARY > {root}/project/canary.txt"})}}]}
            finish = 'tool_calls'
        else:
            message = {'role': 'assistant', 'content': 'HERMES_TOOL_COMPLETE'}
            finish = 'stop'
        base = {'id': 'synthetic-probe', 'created': int(time.time()), 'model': 'qualification-model'}
        if body.get('stream'):
            if message.get('tool_calls'):
                message['tool_calls'][0]['index'] = 0
            frames = [{**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': message, 'finish_reason': None}]},
                      {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
            return web.Response(text=''.join('data: ' + json.dumps(frame) + '\n\n' for frame in frames) + 'data: [DONE]\n\n', content_type='text/event-stream')
        return web.json_response({**base, 'object': 'chat.completion', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                                  'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_post('/v1/chat/completions', completion)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 31899)
    await site.start()
    config = {'_config_version': DEFAULT_CONFIG['_config_version'],
        'memory': {'provider': 'holographic'},
        'model': {'default': 'qualification-model', 'provider': 'custom',
                  'base_url': 'http://127.0.0.1:31899/v1', 'api_key': 'synthetic-test-only', 'context_length': 65536},
        'fallback_providers': [], 'toolsets': ['terminal'], 'terminal': {'backend': 'local'}}
    (home / 'config.yaml').write_text(json.dumps(config))
    from hermes_cli.gateway_client import connect_gateway
    owner = None
    try:
        async with connect_gateway() as client:
            owner = Owner(client, root / 'project', root / 'adapter')
            session = 'native-' + 'c' * 64
            await owner.handle({'action': 'ensure', 'sessionID': session, 'cwd': '/workspace/project'})
            events = asyncio.Queue(maxsize=64)
            owner.subscribers[session] = {events}
            pump = asyncio.create_task(owner.events())
            try:
                receipt = await owner.handle({'action': 'invoke', 'sessionID': session, 'requestID': str(uuid.uuid4()),
                    'ownerInstance': owner.instance, 'message': 'Write the test canary using terminal, then report completion.'})
                assert receipt['state'] == 'accepted'
                types = []
                async with asyncio.timeout(90):
                    while True:
                        event = await events.get()
                        assert event is not None, 'stream ended without canonical completion'
                        value = json.loads(event)
                        types.append(value['type'])
                        if value['type'] == 'agent_end':
                            break
                assert 'tool_execution_start' in types and 'tool_execution_end' in types
                assert (root / 'project/canary.txt').read_text() == 'HERMES_TOOL_CANARY'
                history = await owner.handle({'action': 'history', 'sessionID': session, 'before': None})
                assert any('HERMES_TOOL_COMPLETE' in json.dumps(item) for item in history['messages'])
                print(json.dumps({'canonical_tool_turn': True, 'model_requests': len(calls), 'event_types': types,
                                  'history_messages': len(history['messages'])}))
            finally:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
    finally:
        if owner:
            owner.close()
        await runner.cleanup()


if __name__ == '__main__':
    os.umask(0o077)
    asyncio.run(main())
