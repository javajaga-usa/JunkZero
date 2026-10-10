from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, base_url="http://127.0.0.1")


def test_static_ui_is_served():
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200


def test_file_cannot_be_scan_root(tmp_path):
    file = tmp_path / 'junk.tmp'
    file.write_text('junk')
    assert client.post('/api/scan/start', json={'target_path': str(file)}).status_code == 400


def test_scan_options_are_validated(tmp_path):
    for options in ({'min_size_mb': -1}, {'stale_days': 0}):
        assert client.post('/api/scan/start', json={
            'target_path': str(tmp_path), **options}).status_code == 422


def test_empty_cleanup_rejected():
    assert client.post('/api/clean', json={'items': []}).status_code == 400


def test_replaced_scan_workers_keep_their_own_event_queue(tmp_path, monkeypatch):
    import asyncio
    import importlib
    main = importlib.import_module('app.main')
    workers = []
    original_thread = main.threading.Thread

    class DeferredThread:
        def __init__(self, target, **kwargs):
            workers.append(target)

        def start(self):
            pass

    monkeypatch.setattr(main.threading, 'Thread', DeferredThread)
    monkeypatch.setattr(main, 'current_scanner', None)
    monkeypatch.setattr(main, 'event_queue', asyncio.Queue())
    (tmp_path / 'junk.tmp').write_text('junk')

    async def scenario():
        await main.api_start_scan(main.ScanRequest(target_path=str(tmp_path)))
        first_queue = main.event_queue
        await main.api_start_scan(main.ScanRequest(target_path=str(tmp_path)))
        second_queue = main.event_queue
        monkeypatch.setattr(main.threading, 'Thread', original_thread)
        workers[0]()
        workers[1]()
        await asyncio.sleep(0)
        first_event = first_queue.get_nowait()
        assert first_event['type'] == 'completed'
        assert first_event['stats']['is_cancelled']
        events = []
        while not second_queue.empty():
            events.append(second_queue.get_nowait())
        assert [event['type'] for event in events] == ['item_found', 'completed']
        assert events[-1]['stats']['is_completed']

    asyncio.run(scenario())
