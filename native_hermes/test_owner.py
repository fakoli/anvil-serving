"""Deterministic adapter checks; live Hermes/OpenShell acceptance is separate."""
import asyncio
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from owner import Owner, Rejected

SESSION = 'native-' + 'a' * 64
REQUEST = '00000000-0000-4000-8000-000000000001'


class Gateway:
    def __init__(self):
        self.calls = []
        self.events = asyncio.Queue()
        self.snapshot = {'stored_session_id': 'hermes-session', 'running': False,
                         'execution_generation': 1, 'pending': [], 'messages': []}
        self.uncertain = False

    async def rpc(self, method, **params):
        self.calls.append((method, params))
        if method in ('session.create', 'session.resume'):
            return self.snapshot
        if method == 'prompt.cancel':
            self.snapshot['pending'] = []
        if method == 'session.interrupt':
            self.snapshot['running'] = False
        if method == 'prompt.submit':
            if self.uncertain:
                raise TimeoutError('uncertain')
            return {'admission_id': 'admission', 'status': 'queued'}
        return {}


class OwnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = Gateway()
        self.owner = Owner(self.client, self.root / 'project', self.root / 'state')
        await self.owner.handle({'action': 'ensure', 'sessionID': SESSION, 'cwd': '/workspace/project'})

    async def asyncTearDown(self):
        self.owner.close()
        self.temporary.cleanup()

    def prompt(self):
        return {'action': 'invoke', 'sessionID': SESSION, 'requestID': REQUEST,
                'ownerInstance': self.owner.instance, 'message': 'test'}

    async def test_identity_and_receipts_survive_restart_without_replay(self):
        data = self.prompt()
        self.assertEqual((await self.owner.handle(data))['state'], 'accepted')
        self.assertEqual((await self.owner.handle(data))['state'], 'accepted')
        with self.assertRaises(Rejected):
            await self.owner.handle({**data, 'message': 'different'})
        self.owner.close()
        self.owner = Owner(self.client, self.root / 'project', self.root / 'state')
        await self.owner.handle({'action': 'ensure', 'sessionID': SESSION, 'cwd': '/workspace/project'})
        self.assertEqual((await self.owner.handle(self.prompt()))['state'], 'accepted')
        self.assertEqual([method for method, _ in self.client.calls].count('session.create'), 1)
        self.assertEqual([method for method, _ in self.client.calls].count('prompt.submit'), 1)
        with self.assertRaises(Rejected):
            await self.owner.handle(data)  # stale owner instance

    async def test_uncertain_dispatch_is_not_replayed_and_stop_is_honest(self):
        self.client.uncertain = True
        with self.assertRaises(TimeoutError):
            await self.owner.handle(self.prompt())
        self.assertEqual((await self.owner.handle(self.prompt()))['state'], 'dispatched')
        self.client.snapshot.update(running=True, pending=[{'status': 'queued', 'admission_id': 'queued'}])
        with self.assertRaises(Rejected) as caught:
            await self.owner.handle({'action': 'abort', 'sessionID': SESSION, 'pendingRequests': [REQUEST]})
        self.assertEqual(caught.exception.status, 503)  # a lost submit remains uncertain
        self.assertEqual((await self.owner.handle({'action': 'operation', 'sessionID': SESSION, 'requestID': REQUEST}))['state'], 'dispatched')
        with self.assertRaises(Rejected):
            await self.owner.handle(self.prompt())
        self.assertEqual([method for method, _ in self.client.calls].count('prompt.submit'), 1)
        self.assertEqual([method for method, _ in self.client.calls][-3:], ['prompt.cancel', 'session.interrupt', 'session.resume'])

    async def test_unknown_stop_tombstone_and_input_boundaries(self):
        await self.owner.handle({'action': 'abort', 'sessionID': SESSION, 'pendingRequests': [REQUEST]})
        with self.assertRaises(Rejected):
            await self.owner.handle(self.prompt())
        for invalid in (
            {'action': 'ensure', 'sessionID': SESSION, 'cwd': '/etc'},
            {'action': 'ensure', 'sessionID': '../other', 'cwd': '/workspace/project'},
            {'action': 'state', 'sessionID': SESSION, 'extra': True},
            {'action': 'state', 'sessionID': 'native-' + 'b' * 64},
        ):
            with self.assertRaises(Rejected):
                await self.owner.handle(invalid)

    async def test_history_uses_stable_native_row_ids(self):
        self.client.snapshot['messages'] = [{'row_id': n, 'role': 'assistant', 'content': str(n)} for n in range(1, 61)]
        result = await self.owner.handle({'action': 'history', 'sessionID': SESSION, 'before': None})
        self.assertEqual(result['context']['entryIds'], [str(n) for n in range(11, 61)])
        self.assertTrue(result['context']['hasMore'])
        result = await self.owner.handle({'action': 'history', 'sessionID': SESSION, 'before': '11'})
        self.assertEqual(result['context']['entryIds'], [str(n) for n in range(1, 11)])

    async def test_slow_stream_fails_closed_instead_of_dropping_frames(self):
        queue = asyncio.Queue(maxsize=1)
        self.owner.subscribers[SESSION] = {queue}
        self.owner.publish(SESSION, {'type': 'agent_start'})
        self.owner.publish(SESSION, {'type': 'agent_end'})
        self.assertIsNone(queue.get_nowait())
        self.assertNotIn(queue, self.owner.subscribers[SESSION])

    async def test_stop_does_not_wait_for_stalled_submit(self):
        started, finish = asyncio.Event(), asyncio.Event()
        original = self.client.rpc
        async def rpc(method, **params):
            if method == 'prompt.submit':
                started.set()
                await finish.wait()
            return await original(method, **params)
        self.client.rpc = rpc
        task = asyncio.create_task(self.owner.handle(self.prompt()))
        await started.wait()
        try:
            with self.assertRaises(Rejected) as caught:
                await asyncio.wait_for(self.owner.handle({'action': 'abort', 'sessionID': SESSION,
                                                         'pendingRequests': [REQUEST]}), 0.5)
            self.assertEqual(caught.exception.status, 503)  # control ran, but dispatch is still uncertain
            self.assertEqual(self.owner.receipt(SESSION, REQUEST)[3], 1)
            finish.set()
            await task
            self.assertGreaterEqual([name for name, _ in self.client.calls].count('session.resume'), 3)
        finally:
            finish.set()
            await task

    async def test_create_response_loss_reuses_canonical_request(self):
        another = 'native-' + 'b' * 64
        original = self.client.rpc
        creates = []
        async def rpc(method, **params):
            if method == 'session.create':
                creates.append(params)
                if len(creates) == 1:
                    raise TimeoutError()
            return await original(method, **params)
        self.client.rpc = rpc
        ensure = {'action': 'ensure', 'sessionID': another, 'cwd': '/workspace/project'}
        with self.assertRaises(TimeoutError):
            await self.owner.handle(ensure)
        await self.owner.handle(ensure)
        self.assertEqual(creates[0], creates[1])
        self.assertEqual(creates[0]['request_id'], another)

    async def test_replay_gap_and_oversize_cannot_poison_other_streams(self):
        task = asyncio.create_task(self.owner.events())
        def frame(kind, text=None):
            self.client.events.put_nowait({'params': {'session_id': 'hermes-session',
                'type': kind, 'payload': {'text': text}}})
        try:
            queue = asyncio.Queue(maxsize=16)
            self.owner.subscribers[SESSION] = {queue}
            frame('message.delta', 'old')
            await asyncio.sleep(0)
            self.assertIn(b'message_start', queue.get_nowait())
            self.assertIn(b'old', queue.get_nowait())
            frame('session.replay_gap')
            await asyncio.sleep(0)
            self.assertIsNone(queue.get_nowait())
            self.owner.subscribers[SESSION] = {queue}
            frame('tool.start')
            frame('tool.complete')
            frame('message.delta', 'must not splice')
            await asyncio.sleep(0)
            self.assertTrue(queue.empty())
            frame('message.complete', 'canonical final')
            await asyncio.sleep(0)
            self.assertIsNone(queue.get_nowait())
            self.owner.subscribers[SESSION] = {queue}
            frame('message.delta', 'new')
            await asyncio.sleep(0)
            self.assertIn(b'message_start', queue.get_nowait())
            rendered = json.loads(queue.get_nowait())['message']['content'][0]['text']
            self.assertEqual(rendered, 'new')
            frame('message.delta', 'x' * 65537)
            await asyncio.sleep(0)
            self.assertIsNone(queue.get_nowait())
            self.assertFalse(task.done())
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_history_bound_rejects_without_serializing_every_row(self):
        self.client.snapshot['messages'] = [{}] * 4097
        with self.assertRaises(Rejected):
            await self.owner.handle({'action': 'history', 'sessionID': SESSION, 'before': None})

    async def test_stop_unknown_is_not_reported_complete_after_restart(self):
        self.client.snapshot.update(pending=[{'status': 'unknown', 'admission_id': 'uncertain'}])
        with self.assertRaises(Rejected) as caught:
            await self.owner.handle({'action': 'abort', 'sessionID': SESSION, 'pendingRequests': []})
        self.assertEqual(caught.exception.status, 503)

    async def test_history_size_does_not_disable_stop(self):
        self.client.snapshot.update(messages=[{}] * 4097, running=True,
                                    pending=[{'status': 'queued', 'admission_id': 'queued'}])
        self.assertEqual(await self.owner.handle({'action': 'abort', 'sessionID': SESSION, 'pendingRequests': []}), {'accepted': True})
        self.assertIn('session.interrupt', [method for method, _ in self.client.calls])


if __name__ == '__main__':
    unittest.main()
