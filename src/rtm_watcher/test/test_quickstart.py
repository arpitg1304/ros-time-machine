import json
import os

import yaml

from rtm_watcher.quickstart import (
    choose_topics, clip_summary, COMPRESSED, JOINTS, RAW, Selection, time_machine_yaml,
    watch_list_yaml, write_if_missing)
from rtm_watcher.watchlist import parse_watch_list

GRAPH = [
    ('/camera/front/image_raw', [RAW]),
    ('/camera/front/image_raw/compressed', [COMPRESSED]),
    ('/camera/wrist/image_raw', [RAW]),             # raw only: no compressed sibling
    ('/joint_states', [JOINTS]),
    ('/odom', ['nav_msgs/msg/Odometry']),
    ('/rosout', ['rcl_interfaces/msg/Log']),
    ('/time_machine/buffer_stats', ['ros_time_machine_msgs/msg/BufferStats']),
]


def test_prefers_compressed_sibling_and_keeps_raw_only_cameras():
    s = choose_topics(GRAPH)
    assert s.cameras == ['/camera/front/image_raw/compressed', '/camera/wrist/image_raw']
    assert s.state == ['/joint_states']
    assert s.record == ['/camera/front/image_raw/compressed', '/camera/wrist/image_raw',
                        '/joint_states']
    assert s.raw_cameras


def test_ignores_plumbing_and_other_types_by_default():
    s = choose_topics(GRAPH)
    assert '/odom' not in s.record
    assert not any(t.startswith(('/rosout', '/time_machine/')) for t in s.record)


def test_wanted_topics_are_all_recorded():
    s = choose_topics(GRAPH, wanted=['/odom', '/camera/front/image_raw/compressed'])
    assert s.record == ['/odom', '/camera/front/image_raw/compressed']
    assert s.cameras == ['/camera/front/image_raw/compressed']
    assert not s.raw_cameras


def test_watches_at_most_two_cameras():
    graph = [(f'/cam{i}/compressed', [COMPRESSED]) for i in range(4)]
    s = choose_topics(graph)
    assert len(s.cameras) == 2 and len(s.record) == 4


def test_generated_configs_are_valid():
    s = choose_topics(GRAPH)
    tm = yaml.safe_load(time_machine_yaml(s, '/data/clips', 10.0, 2.0))
    profile = tm['snapshots'][0]
    assert profile['name'] == 'quickstart' and 'trigger' not in profile
    assert profile['recording']['topics'] == s.record
    assert profile['recording']['max_buffer_size'] == 1000   # raw camera present
    assert profile['output']['path'] == '/data/clips'
    assert profile['output']['name_pattern'] == '{label}_{timestamp}'

    wl = parse_watch_list(yaml.safe_load(watch_list_yaml(s)))
    assert wl.cameras == s.cameras and wl.state_topics == s.state
    assert {e.profile for e in wl.events} == {'quickstart'}
    assert wl.min_confidence == 0.5 and wl.cooldown_s == 5


def test_topic_names_are_quoted_safely():
    s = Selection(cameras=['/a: b'], record=['/a: b'])
    assert yaml.safe_load(time_machine_yaml(s, '/x', 1, 1))['snapshots'][0][
        'recording']['topics'] == ['/a: b']


def test_never_overwrites_without_fresh(tmp_path):
    path = str(tmp_path / 'watch_list.yaml')
    assert write_if_missing(path, 'first', fresh=False)
    assert not write_if_missing(path, 'second', fresh=False)
    assert open(path).read() == 'first'
    assert write_if_missing(path, 'third', fresh=True)
    assert open(path).read() == 'third'


def test_clip_summary(tmp_path):
    event = {
        'window': {'start_ns': 0, 'end_ns': 12_000_000_000},
        'coverage': {'pre_event_complete': False},
        'topics': [{'messages': 360}, {'messages': 1200}],
    }
    (tmp_path / 'event.json').write_text(json.dumps(event))
    summary = clip_summary(str(tmp_path))
    assert summary.startswith('12.0 s, 2 topics, 1,560 messages')
    assert 'still filling' in summary
    assert clip_summary(os.path.join(str(tmp_path), 'missing')) == ''
