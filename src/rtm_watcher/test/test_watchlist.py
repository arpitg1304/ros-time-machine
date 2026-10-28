import pytest

from rtm_watcher.watchlist import load_watch_list, parse_watch_list, WatchListError


def base(**overrides):
    watcher = {
        'endpoint': 'http://localhost:8000/v1',
        'cameras': ['/camera/front/image_raw/compressed'],
        'events': [{'label': 'object_dropped', 'describe': 'An object falls.'}],
    }
    watcher.update(overrides)
    return {'watcher': watcher}


def test_defaults():
    config = parse_watch_list(base())
    assert config.sample_hz == 1.0
    assert config.context_frames == 4
    assert config.min_confidence == 0.6
    assert config.labels == ['object_dropped']
    assert config.event('object_dropped').sample_rate == 1.0
    assert config.event('missing') is None


def test_top_level_watcher_key_optional():
    assert parse_watch_list(base()['watcher']).labels == ['object_dropped']


def test_example_file_loads():
    import os
    path = os.path.join(os.path.dirname(__file__), '..', 'config', 'watch_list.example.yaml')
    config = load_watch_list(path)
    assert 'object_dropped' in config.labels
    assert config.event('task_success').sample_rate == 0.2


@pytest.mark.parametrize('label', ['ObjectDropped', 'object dropped', '', None, '1drop'])
def test_bad_labels(label):
    with pytest.raises(WatchListError):
        parse_watch_list(base(events=[{'label': label, 'describe': 'x'}]))


def test_duplicate_label():
    events = [{'label': 'a', 'describe': 'x'}, {'label': 'a', 'describe': 'y'}]
    with pytest.raises(WatchListError, match='duplicate'):
        parse_watch_list(base(events=events))


def test_needs_events_and_cameras():
    with pytest.raises(WatchListError):
        parse_watch_list(base(events=[]))
    with pytest.raises(WatchListError):
        parse_watch_list(base(cameras=[]))


def test_ranges_checked():
    with pytest.raises(WatchListError):
        parse_watch_list(base(min_confidence=1.5))
    with pytest.raises(WatchListError):
        parse_watch_list(base(sample_hz=0))
    with pytest.raises(WatchListError):
        parse_watch_list(base(events=[{'label': 'a', 'describe': 'x', 'sample_rate': 2}]))


@pytest.mark.parametrize('endpoint', [
    'http://localhost:8000/v1', 'http://127.0.0.1:1234/v1', 'http://192.168.1.20:8000/v1',
    'http://10.0.0.5/v1', 'http://172.20.1.1/v1', 'http://jetson.local:8000/v1'])
def test_local_endpoints_allowed(endpoint):
    assert parse_watch_list(base(endpoint=endpoint)).endpoint == endpoint


def test_cloud_endpoint_requires_opt_in():
    with pytest.raises(WatchListError, match='allow_remote'):
        parse_watch_list(base(endpoint='https://api.example.com/v1'))
    config = parse_watch_list(base(endpoint='https://api.example.com/v1', allow_remote=True))
    assert config.allow_remote


def test_api_key_read_from_env(monkeypatch):
    monkeypatch.setenv('RTM_TEST_KEY', 'secret')
    assert parse_watch_list(base(api_key_env='RTM_TEST_KEY')).api_key() == 'secret'
    assert parse_watch_list(base()).api_key() == ''
