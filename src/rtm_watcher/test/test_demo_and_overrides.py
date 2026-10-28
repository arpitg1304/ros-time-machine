import pytest

from rtm_watcher.demo_scene import (DROP_AT_S, DropLoop, GRIPPER_CLOSED, GRIPPER_OPEN,
                                    load_frames, StaticScene)
from rtm_watcher.watchlist import load_watch_list, parse_watch_list, WatchListError


def test_demo_frames_ship_with_the_package():
    frames = load_frames()
    assert set(frames) == {'hold', 'fall', 'floor'}
    assert all(f[:3] == b'\xff\xd8\xff' for f in frames.values())


def test_drop_loop_timeline():
    scene = DropLoop(period_s=20.0)
    frames = scene.frames
    hold = scene.at(5.0)
    assert hold.frame == frames['hold'] and hold.gripper == GRIPPER_CLOSED
    assert not hold.drop_started
    fall = scene.at(DROP_AT_S + 0.1)
    assert fall.frame == frames['fall'] and fall.gripper == GRIPPER_OPEN and fall.drop_started
    assert not scene.at(DROP_AT_S + 0.2).drop_started      # Reported once per drop
    assert scene.at(DROP_AT_S + 3).frame == frames['floor']
    nxt = scene.at(20.0 + 1)
    assert nxt.frame == frames['hold'] and nxt.loop == 1
    assert scene.at(20.0 + DROP_AT_S + 0.01).drop_started
    assert scene.drop_time(3) == 3 * 20.0 + DROP_AT_S


def test_drop_loop_validates_period_and_static_scene():
    with pytest.raises(ValueError):
        DropLoop(period_s=10.0)
    assert not StaticScene().at(1e9).drop_started


def test_demo_watch_list_is_valid():
    import os
    path = os.path.join(os.path.dirname(__file__), '..', 'config', 'demo_watch_list.yaml')
    config = load_watch_list(path)
    assert config.labels == ['object_dropped', 'person_in_cell']


def write(tmp_path, endpoint='http://127.0.0.1:8765/v1'):
    path = tmp_path / 'wl.yaml'
    path.write_text(f"""
watcher:
  endpoint: {endpoint}
  model: fake-vlm
  cameras: [/cam]
  events: [{{label: object_dropped, describe: An object falls.}}]
""")
    return str(path)


def test_overrides_replace_endpoint_and_model(tmp_path):
    config = load_watch_list(write(tmp_path), {
        'endpoint': 'http://192.168.1.50:8000/v1', 'model': 'qwen3-vl-4b-instruct',
        'api_key_env': ''})
    assert config.endpoint == 'http://192.168.1.50:8000/v1'
    assert config.model == 'qwen3-vl-4b-instruct'
    assert config.api_key_env == ''


def test_empty_overrides_keep_the_file(tmp_path):
    config = load_watch_list(write(tmp_path), {'endpoint': '', 'model': None})
    assert config.endpoint == 'http://127.0.0.1:8765/v1' and config.model == 'fake-vlm'


def test_overrides_still_get_the_privacy_check(tmp_path):
    with pytest.raises(WatchListError, match='allow_remote'):
        load_watch_list(write(tmp_path), {'endpoint': 'https://api.example.com/v1'})


@pytest.mark.parametrize('endpoint,local', [
    ('http://gpubox:8000/v1', True),            # LAN DNS / /etc/hosts name
    ('http://desktop.local:8000/v1', True),
    ('http://192.168.1.50:8000/v1', True),
    ('http://gpu.example.com:8000/v1', False),
    ('http://8.8.8.8:8000/v1', False),
])
def test_lan_hostnames(endpoint, local):
    raw = {'endpoint': endpoint, 'cameras': ['/cam'],
           'events': [{'label': 'a', 'describe': 'x'}]}
    if local:
        assert parse_watch_list(raw).endpoint == endpoint
    else:
        with pytest.raises(WatchListError):
            parse_watch_list(raw)
