"""Run inside the pinned Hermes image: real HTTP/SSE boundary, synthetic gateway."""
import asyncio
import tempfile
from pathlib import Path

from aiohttp import ClientSession, web
from owner import Owner, application_for
from test_owner import Gateway, SESSION


async def main():
    with tempfile.TemporaryDirectory() as directory:
        gateway = Gateway()
        owner = Owner(gateway, Path(directory) / 'project', Path(directory) / 'state')
        await owner.handle({'action': 'ensure', 'sessionID': SESSION, 'cwd': '/workspace/project'})
        runner = web.AppRunner(application_for(owner, b'Bearer synthetic-test-only'), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f'http://127.0.0.1:{port}/api/native-agents-owner'
        headers = {'Authorization': 'Bearer synthetic-test-only'}
        try:
            async with ClientSession() as client:
                for supplied in ({}, {'Authorization': 'Bearer wrong'},
                                 [('Authorization', 'Bearer synthetic-test-only'), ('Authorization', 'Bearer synthetic-test-only')]):
                    async with client.post(url, json={'action': 'state', 'sessionID': SESSION}, headers=supplied) as response:
                        assert response.status == 401
                async with client.post(url, json={'action': 'state', 'sessionID': 'native-' + 'b' * 64}, headers=headers) as response:
                    assert response.status == 404
                async with client.post(url, json={'action': 'state', 'sessionID': SESSION}, headers=headers) as response:
                    assert response.status == 200
                    assert (await response.json())['state']['isStreaming'] is False
                async with client.post(url + '?other=1', json={}, headers=headers) as response:
                    assert response.status == 404
                async with client.post(url, data='x' * 65537, headers={**headers, 'Content-Type': 'application/json'}) as response:
                    assert response.status == 413
                async with client.post(url, json={'action': 'events', 'sessionID': SESSION}, headers=headers) as response:
                    assert response.status == 200
                    assert response.headers['Content-Type'] == 'text/event-stream'
                    assert await response.content.readline() == b'data: {"type":"connected"}\n'
                    assert await response.content.readline() == b'\n'
                    owner.publish(SESSION, {'type': 'agent_start'})
                    assert await asyncio.wait_for(response.content.readline(), 1) == b'data: {"type":"agent_start"}\n'
                    owner.disconnect(SESSION)
                    await response.read()
                # Fill all ordinary lanes with stalled reads. Stop must still enter.
                entered = 0
                saturated, release = asyncio.Event(), asyncio.Event()
                original = gateway.rpc
                async def stall(method, **params):
                    nonlocal entered
                    if method == 'session.resume' and entered < 14:
                        entered += 1
                        if entered == 14:
                            saturated.set()
                        await release.wait()
                    return await original(method, **params)
                gateway.rpc = stall
                calls = [asyncio.create_task(client.post(url, json={'action': 'state', 'sessionID': SESSION}, headers=headers)) for _ in range(14)]
                try:
                    await asyncio.wait_for(saturated.wait(), 2)
                    async with client.post(url, json={'action': 'state', 'sessionID': SESSION}, headers=headers) as response:
                        assert response.status == 503
                    async with asyncio.timeout(1):
                        async with client.post(url, json={'action': 'abort', 'sessionID': SESSION, 'pendingRequests': []}, headers=headers) as response:
                            assert response.status == 200
                finally:
                    release.set()
                    for response in await asyncio.gather(*calls):
                        await response.read()
                        response.release()
        finally:
            await runner.cleanup()
            owner.close()
    print('HTTP auth, custody, body limit, SSE, and reserved Stop lane: passed')


if __name__ == '__main__':
    asyncio.run(main())
