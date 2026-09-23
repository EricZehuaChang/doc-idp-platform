"""Authorization contract: users, assignments, same-name keys and aggregates."""
import pytest
from httpx import ASGITransport, AsyncClient
from app.auth import security
from app.models import ApiKey, FileRecord, Transaction, FileArtifact, StudioRun, CreditLedger
from tests.test_delete_task import _client, _seed_user, _headers


@pytest.fixture
async def env(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        from app.db import session_factory
        from app.storage import get_storage
        users = {name: await _seed_user(f'{name}@example.com', role) for name, role in
                 [('admin', 'admin'), ('a', 'operator'), ('b', 'operator'), ('v', 'viewer')]}
        headers = {name: _headers(u) for name, u in users.items()}
        async with session_factory()() as s:
            for name, owner, kind in [('ka', 'a', 'agent'), ('kb', 'b', 'agent'), ('app', None, 'application')]:
                token, prefix, hashed = security.generate_api_key()
                s.add(ApiKey(id=name, tenant_id='default', name='same-name', key_hash=hashed,
                             prefix=prefix, key_type=kind, owner_user_id=users[owner]['id'] if owner else None))
                headers[name] = {'Authorization': f'Bearer {token}'}
            for name, owner, key in [('a', 'a', None), ('b', 'b', None), ('ka', None, 'ka'),
                                     ('kb', None, 'kb'), ('app', None, 'app'), ('legacy', None, None)]:
                s.add(Transaction(id='t'+name, tenant_id='default', skill_code='test', skill_version=1,
                    status='completed', purpose='production', initiator_user_id=users[owner]['id'] if owner else None,
                    api_key_id=key, initiator_type='api_key' if key else 'user' if owner else 'unknown',
                    initiator_label='same-name' if key else users[owner]['email'] if owner else None))
                await s.flush()
                storage = get_storage().put_bytes('visibility/'+name+'.pdf', b'%PDF-test')
                s.add(FileRecord(id=name, tenant_id='default', transaction_id='t'+name, file_name=name+'.pdf',
                    storage_path=storage, status='completed', page_count=1, result={'x': {'$value': name, '$confidence': 3}}))
                await s.flush()
                s.add(FileArtifact(id='ar'+name, tenant_id='default', file_id=name, config_hash='test',
                    action='rename', storage_key=storage, display_name=name+'.pdf', status='ready'))
                s.add(CreditLedger(tenant_id='default', transaction_id='t'+name, kind='shadow_meter', amount=1))
            # Two independent uploads share a transaction; assignment exposes only one root.
            s.add(FileRecord(id='assigned', tenant_id='default', transaction_id='tb', file_name='assigned.pdf',
                            storage_path=storage, status='pending_verification', page_count=1, assignee=users['a']['email']))
            s.add(StudioRun(id='runb', tenant_id='default', transaction_id='tb', file_id='b',
                            skill_code='test', skill_version=1, sample_id='sample', package_hash='h', created_by=users['b']['email']))
            # A Playground run made through the application key (skill-building integration)
            s.add(Transaction(id='tapptest', tenant_id='default', skill_code='test', skill_version=1,
                status='completed', purpose='test', api_key_id='app', initiator_type='user',
                initiator_label='apikey:same-name'))
            await s.flush()
            s.add(FileRecord(id='apptest', tenant_id='default', transaction_id='tapptest', file_name='s.pdf',
                             storage_path=storage, status='completed', page_count=1))
            await s.flush()
            s.add(StudioRun(id='runapp', tenant_id='default', transaction_id='tapptest', file_id='apptest',
                            skill_code='test', skill_version=1, sample_id='sample2', package_hash='h', created_by='apikey:same-name'))
            await s.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            yield c, headers, users


async def test_visibility_matrix(env):
    c, headers, _ = env
    expected = {'admin': {'a', 'b', 'ka', 'kb', 'app', 'legacy', 'assigned'},
                'a': {'a', 'ka', 'assigned'}, 'b': {'b', 'kb', 'assigned'}, 'v': set(),
                'app': {'app'}}
    for who, ids in expected.items():
        h = headers[who]
        r = await c.get('/api/v1/files', headers=h)
        assert r.status_code == 200, r.text
        assert {f['file_id'] for f in r.json()['data']} == ids, who
        stats = (await c.get('/api/v1/stats/home', headers=h)).json()
        assert stats['passed_docs'] == len(ids - {'assigned'}), (who, stats)
        cabinet = (await c.get('/api/v1/cabinet/test', headers=h)).json()
        assert {f['file_id'] for f in cabinet['rows']} == ids - {'assigned'}
        for file in ('a', 'b', 'ka', 'kb', 'app', 'legacy'):
            for url in (f'/api/v1/review/{file}', f'/api/v1/files/{file}/download',
                        f'/api/v1/files/{file}/preview', f'/api/v1/files/{file}/artifacts',
                        f'/api/v1/artifacts/ar{file}/download'):
                r = await c.get(url, headers=h)
                assert r.status_code == (200 if file in ids else 404), (who, url, r.text)
    # Same display name never grants cross-key reads; both key types are scoped.
    for key in ('ka', 'kb', 'app'):
        for target in ('ka', 'kb', 'app', 'legacy'):
            for suffix in (f'status/t{target}', f'transactions/t{target}/documents', f'transactions/t{target}/artifacts'):
                r = await c.get('/api/v1/'+suffix, headers=headers[key])
                assert r.status_code == (200 if target == key else 404), (key, suffix, r.text)
    # An assigned upload must not expose other uploads or their Playground run.
    for suffix in ('status/tb', 'transactions/tb/documents'):
        r = await c.get('/api/v1/'+suffix, headers=headers['a'])
        assert r.status_code == 200, r.text
        assert {f['file_id'] for f in r.json()['files']} == {'assigned'}
    assert (await c.get('/api/v1/studio/runs/runb', headers=headers['a'])).status_code == 404
    assert (await c.get('/api/v1/studio/runs', headers=headers['a'])).json()['runs'] == []
    # An application key keeps reading its own Playground runs; users do not see them
    assert (await c.get('/api/v1/studio/runs/runapp', headers=headers['app'])).status_code == 200
    assert [r['run_id'] for r in (await c.get('/api/v1/studio/runs', headers=headers['app'])).json()['runs']] == ['runapp']
    assert (await c.get('/api/v1/studio/runs/runapp', headers=headers['a'])).status_code == 404
    assert (await c.get('/api/v1/transactions/tapptest/documents', headers=headers['ka'])).status_code == 404


async def test_role_write_gates(env):
    c, headers, _ = env
    for who in ('a', 'b', 'v', 'app'):
        assert (await c.post('/api/v1/review/a/assign', headers=headers[who], json={'assignee': 'a@example.com'})).status_code == 403
        for method, url, body in [('GET', '/api/v1/webhooks', None), ('POST', '/api/v1/webhooks', {'url': 'https://example.com'}), ('DELETE', '/api/v1/webhooks/x', None)]:
            assert (await c.request(method, url, headers=headers[who], json=body)).status_code == 403
    for suffix in ('lock', 'unlock', 'confirm', 'reject'):
        assert (await c.post('/api/v1/review/assigned/'+suffix, headers=headers['v'])).status_code == 403
    assert (await c.patch('/api/v1/review/assigned/fields', headers=headers['v'], json={'edits': []})).status_code == 403
    assert (await c.post('/api/v1/process', headers=headers['v'], files={'files': ('x.pdf', b'x')}, data={'skill_code': 'test'})).status_code == 403
    assert (await c.post('/api/v1/review/b/lock', headers=headers['a'])).status_code == 404
    assert (await c.post('/api/v1/review/assigned/lock', headers=headers['a'])).status_code == 200


async def test_live_grants_and_call_history(env):
    c, h, users = env
    create = {'name': 'locate', 'groups': ['process', 'locate']}
    assert (await c.post('/api/v1/me/api-keys', headers=h['a'], json=create)).status_code == 403
    url = '/api/v1/auth/users/'+users['a']['id']
    grants = {'allow_create': True, 'groups': ['process', 'locate'], 'allowed_skill_codes': ['test']}
    assert (await c.patch(url, headers=h['admin'], json={'api_grants': grants})).status_code == 200
    assert (await c.post('/api/v1/me/api-keys', headers=h['a'], json=create)).status_code == 403
    r = await c.post('/api/v1/me/api-keys', headers=h['a'], json={**create, 'allowed_skill_codes': ['test']})
    assert r.status_code == 201, r.text
    key = {'Authorization': 'Bearer '+r.json()['key']}
    r = await c.post('/api/v1/locate', headers=key, files={'file': ('private-name.png', b'private-content')}, data={'terms': '["x"]'})
    assert r.status_code == 422, r.text
    records = (await c.get('/api/v1/api-calls', headers=h['a'])).json()
    assert records['total'] == 1
    assert records['data'][0]['status_code'] == 422
    assert records['data'][0]['created_at'].endswith('+00:00')
    assert 'private' not in str(records)
    assert (await c.get('/api/v1/api-calls', headers=h['b'])).json()['total'] == 0
    grants['groups'] = ['process']
    await c.patch(url, headers=h['admin'], json={'api_grants': grants})
    assert (await c.post('/api/v1/locate', headers=key)).status_code == 403
    await c.patch(url, headers=h['admin'], json={'active': False})
    assert (await c.get('/api/v1/agent/ping', headers=key)).status_code == 401


async def test_source_views_and_audit(env):
    c, h, _ = env
    for source, expected in [('manual', {'a', 'b', 'legacy', 'assigned'}), ('api', {'ka', 'kb', 'app'})]:
        r = await c.get('/api/v1/files', params={'source': source}, headers=h['admin'])
        assert {f['file_id'] for f in r.json()['data']} == expected
        stats = (await c.get('/api/v1/stats/home', params={'source': source}, headers=h['admin'])).json()
        assert stats['passed_docs'] == len(expected - {'assigned'})
        assert stats['used_credits'] == 3
    view = await c.get('/api/v1/files/a/download', headers=h['a'])
    assert 'immutable' in view.headers['cache-control'] and view.headers['vary'] == 'Authorization'
    await c.get('/api/v1/files/a/download', headers=h['a'], params={'save': 1})
    await c.get('/api/v1/artifacts/ara/download', headers=h['a'])
    await c.get('/api/v1/cabinet/test/export.csv', headers=h['a'])
    await c.post('/api/v1/auth/login', json={'email': 'a@example.com', 'password': 'sensitive-password'})
    rows = (await c.get('/api/v1/audit/logs', headers=h['admin'], params={'actor': 'a@example.com'})).json()['data']
    assert {r['action'] for r in rows} >= {'files.downloaded', 'artifacts.downloaded', 'cabinet.exported', 'auth.login_failed'}
    assert 'sensitive-password' not in str(rows)
    # viewing (review pane, hover prefetch) is not a download: exactly one row
    assert sum(r['action'] == 'files.downloaded' for r in rows) == 1
    assert all(r['actor'] == 'a@example.com' for r in rows)
    # SQLite drops tzinfo: timestamps must still go out as explicit UTC
    assert all(r['created_at'].endswith('+00:00') for r in rows)
    assert (await c.get('/api/v1/audit/logs', headers=h['a'])).status_code == 403
