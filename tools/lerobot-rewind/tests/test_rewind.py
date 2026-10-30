"""Round trips through synthetic LeRobot datasets (v3.0 and v2.1). Needs ffmpeg for video."""

import csv
import json
import os
import shutil
import subprocess

import pandas as pd
import pytest
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory

from lerobot_rewind.cli import main, parse_episodes
from lerobot_rewind.dataset import LeRobotDataset
from lerobot_rewind.session import Options, labels_to_truth, write_session
from lerobot_rewind.video import split_jpegs

FPS, LEN = 5, 15  # 2 episodes of 3 s
NAMES = ['shoulder_pan.pos', 'gripper.pos']
needs_ffmpeg = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')


def video(path, seconds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    f'testsrc=size=64x48:rate={FPS}:duration={seconds}', '-c:v', 'mpeg4', path], check=True)


def rows(ep):
    # gripper closes (below 15) at frame 4 and opens at frame 10
    return [{'observation.state': [float(ep), 40.0 if k < 4 or k >= 10 else 5.0], 'action': [float(ep), 0.0],
             'timestamp': k / FPS, 'frame_index': k, 'episode_index': ep, 'index': ep * LEN + k, 'task_index': 0}
            for k in range(LEN)]


def info(version, data_path, video_path):
    return {'codebase_version': version, 'fps': FPS, 'chunks_size': 1000, 'data_path': data_path,
            'video_path': video_path, 'total_episodes': 2,
            'features': {'observation.state': {'dtype': 'float32', 'shape': [2], 'names': NAMES},
                         'action': {'dtype': 'float32', 'shape': [2], 'names': NAMES},
                         'observation.images.top': {'dtype': 'video', 'shape': [48, 64, 3]}}}


@pytest.fixture
def v3(tmp_path):
    root = tmp_path / 'v3'
    (root / 'meta' / 'episodes' / 'chunk-000').mkdir(parents=True)
    (root / 'data' / 'chunk-000').mkdir(parents=True)
    json.dump(info('v3.0', 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
                   'videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4'),
              open(root / 'meta' / 'info.json', 'w'))
    pd.DataFrame(rows(0) + rows(1)).to_parquet(root / 'data' / 'chunk-000' / 'file-000.parquet')
    # Both episodes share one video file, as v3 does: ep1 starts at 3 s into it
    video(str(root / 'videos' / 'observation.images.top' / 'chunk-000' / 'file-000.mp4'), 2 * LEN / FPS)
    k = 'videos/observation.images.top/'
    pd.DataFrame([{'episode_index': e, 'tasks': ['stack'], 'length': LEN, 'data/chunk_index': 0, 'data/file_index': 0,
                   k + 'chunk_index': 0, k + 'file_index': 0, k + 'from_timestamp': e * LEN / FPS,
                   k + 'to_timestamp': (e + 1) * LEN / FPS} for e in (0, 1)]).to_parquet(
        root / 'meta' / 'episodes' / 'chunk-000' / 'file-000.parquet')
    return str(root)


@pytest.fixture
def v21(tmp_path):
    root = tmp_path / 'v21'
    (root / 'meta').mkdir(parents=True)
    (root / 'data' / 'chunk-000').mkdir(parents=True)
    json.dump(info('v2.1', 'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet',
                   'videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4'),
              open(root / 'meta' / 'info.json', 'w'))
    with open(root / 'meta' / 'episodes.jsonl', 'w') as f:
        for e in (0, 1):
            f.write(json.dumps({'episode_index': e, 'tasks': ['stack'], 'length': LEN}) + '\n')
            pd.DataFrame(rows(e)).to_parquet(root / 'data' / 'chunk-000' / f'episode_{e:06d}.parquet')
            video(str(root / 'videos' / 'chunk-000' / 'observation.images.top' / f'episode_{e:06d}.mp4'), LEN / FPS)
    return str(root)


def read(path):
    out = []
    with open(path, 'rb') as f:
        for _, channel, message, msg in make_reader(f, decoder_factories=[DecoderFactory()]).iter_decoded_messages():
            out.append((channel.topic, message.log_time, msg))
    return out


def test_split_jpegs():
    a, b = b'\xff\xd8abc\xff\xd9', b'\xff\xd8\xff\x00x\xff\xd9'
    assert split_jpegs(b'junk' + a + b + b'\xff\xd8partial') == [a, b]


def test_parse_episodes():
    assert parse_episodes('', [0, 1, 2]) is None
    assert parse_episodes('0-1,3', [0, 1, 2, 3, 4]) == [0, 1, 3]


@pytest.mark.parametrize('fixture', ['v3', 'v21'])
def test_dataset_layouts(fixture, request):
    ds = LeRobotDataset(request.getfixturevalue(fixture))
    assert ds.cameras == ['top'] and ds.state_names == ['shoulder_pan', 'gripper']
    ep = ds.episode(1)
    assert ep.length == LEN and len(ep.data) == LEN
    path, start, end = ep.videos['top']
    assert os.path.exists(path) and end - start == pytest.approx(LEN / FPS)


@needs_ffmpeg
@pytest.mark.parametrize('fixture', ['v3', 'v21'])
def test_session_round_trip(fixture, request, tmp_path):
    ds = LeRobotDataset(request.getfixturevalue(fixture))
    out = str(tmp_path / 'session.mcap')
    s = write_session(ds, out, Options(gap_s=1.0, lead_in_s=2.0, start_s=1000))
    msgs = read(out)
    NS = 1_000_000_000
    assert s.episode_start_ns[0] == 1002 * NS
    assert s.episode_start_ns[1] == s.episode_end_ns[0] + 1 * NS
    times = [t for _, t, _ in msgs]
    assert times == sorted(times)
    for topic, t, m in msgs:  # header stamps equal log times
        if hasattr(m, 'header'):
            assert m.header.stamp.sec * NS + m.header.stamp.nanosec == t
    images = [m for topic, _, m in msgs if topic == '/camera/top/image_raw/compressed']
    states = [(t, m) for topic, t, m in msgs if topic == '/joint_states']
    # Recorded frames plus the held ones: 2 s lead-in and the 1 s gap, at 5 fps
    assert len(images) == 2 * LEN + 2 * FPS + (1 * FPS - 1)
    assert all(m.data[:2] == b'\xff\xd8' for m in images)
    assert [m.name for _, m in states[:1]] == [['shoulder_pan', 'gripper']]
    # Episode 1's joints start at its offset
    ep1 = [m for t, m in states if t >= s.episode_start_ns[1]]
    assert ep1[0].position[0] == 1.0
    markers = [m.data for topic, _, m in msgs if topic == '/lerobot/episode']
    assert markers == ['start 0', 'end 0', 'start 1', 'end 1']


@needs_ffmpeg
def test_cli_convert_events_and_labels(v3, tmp_path, capsys):
    out = str(tmp_path / 's.mcap')
    labels = tmp_path / 'labels.csv'
    assert main(['events', v3, '--joint', 'gripper', '--below', '15', '--template', str(labels)]) == 0
    events = capsys.readouterr().out.splitlines()
    assert events[0] == 'episode,event,t_s' and '0,close,0.80' in events and '0,open,2.00' in events
    rows_ = list(csv.DictReader(open(labels)))
    assert [r['t_rel_s'] for r in rows_] == ['2.00', '2.00']
    for r in rows_:
        r['label'] = 'released'
    with open(labels, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=rows_[0].keys(), lineterminator='\n')
        w.writeheader()
        w.writerows(rows_)
    assert main(['convert', v3, out, '--labels', str(labels), '--gap', '1', '--lead-in', '2', '--start', '1000']) == 0
    truth = open(str(tmp_path / 's_truth.csv')).read().splitlines()
    assert truth[0] == 'label,time_s' and truth[1] == 'released,1004.000'
    sidecar = json.load(open(str(tmp_path / 's.json')))
    assert sidecar['cameras'] == ['top'] and sidecar['episode_start_ns']['0'] == 1002 * 1_000_000_000


def test_labels_to_truth_skips_missing_episodes(tmp_path):
    from lerobot_rewind.session import Session
    labels = tmp_path / 'l.csv'
    labels.write_text('episode,label,t_rel_s\n0,a,1.5\n7,b,2.0\n')
    n = labels_to_truth(str(labels), str(tmp_path / 't.csv'), Session(0, {0: 10 * 1_000_000_000}, {0: 0}, 0))
    assert n == 1 and (tmp_path / 't.csv').read_text() == 'label,time_s\na,11.500\n'
