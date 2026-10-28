import json
import os

import pytest

from rtm_watcher import review

S = 1_000_000_000


def make_clip(root, name, label='object_dropped', confidence=0.8, verdict=None, lag=2.0,
              source='vlm:qwen', pre_ok=True, stamp=0):
    path = os.path.join(root, name)
    os.makedirs(path)
    event = {
        'schema': 'rtm.event/1', 'clip': name, 'event_stamp_ns': stamp,
        'coverage': {'pre_event_complete': pre_ok},
        'trigger': {'label': label, 'confidence': confidence, 'source': source,
                    'detection_lag_s': lag, 'description': f'{label} seen'},
        'review': {'verdict': verdict} if verdict else None,
    }
    with open(os.path.join(path, 'event.json'), 'w') as f:
        json.dump(event, f)
    return path


def test_find_clips_recursive_and_sorted(tmp_path):
    make_clip(tmp_path, 'b', stamp=2)
    os.makedirs(tmp_path / 'sub')
    make_clip(str(tmp_path / 'sub'), 'a', stamp=1)
    (tmp_path / 'not_a_clip').mkdir()
    clips = review.find_clips(str(tmp_path))
    assert [c.name for c in clips] == ['a', 'b']
    assert len(review.find_clips(str(tmp_path / 'b'))) == 1


def test_find_clips_skips_corrupt(tmp_path, capsys):
    bad = tmp_path / 'bad'
    bad.mkdir()
    (bad / 'event.json').write_text('{oops')
    assert review.find_clips(str(tmp_path)) == []
    assert 'skipping' in capsys.readouterr().err


def test_mark_writes_review_and_keeps_fields(tmp_path):
    path = make_clip(tmp_path, 'c')
    review.mark(path, 'good', note='clean drop', by='tester')
    event = json.load(open(os.path.join(path, 'event.json')))
    assert event['review']['verdict'] == 'good'
    assert event['review']['note'] == 'clean drop'
    assert event['review']['by'] == 'tester'
    assert event['review']['at'].endswith('Z')
    assert event['trigger']['label'] == 'object_dropped'
    with pytest.raises(ValueError):
        review.mark(path, 'meh')


def test_group_stats_precision_lag_and_coverage(tmp_path):
    make_clip(tmp_path, '1', verdict='good', lag=1.0)
    make_clip(tmp_path, '2', verdict='bad', lag=3.0, pre_ok=False)
    make_clip(tmp_path, '3', lag=5.0)
    make_clip(tmp_path, '4', label='person_in_cell', verdict='good')
    stats = review.group_stats(review.find_clips(str(tmp_path)), lambda c: c.label)
    drop = stats['object_dropped']
    assert (drop.clips, drop.reviewed, drop.good) == (3, 2, 1)
    assert drop.precision == 0.5
    assert drop.incomplete_pre_event == 1
    assert sorted(drop.lags) == [1.0, 3.0, 5.0]
    assert stats['person_in_cell'].precision == 1.0


def test_suggest_threshold_finds_precise_cutoff(tmp_path):
    # High-confidence detections are right, low-confidence ones are mostly wrong
    for i, (conf, verdict) in enumerate([(0.95, 'good'), (0.9, 'good'), (0.88, 'good'),
                                         (0.85, 'good'), (0.8, 'good'), (0.7, 'bad'),
                                         (0.65, 'bad'), (0.6, 'good'), (0.55, 'bad')]):
        make_clip(tmp_path, f'c{i}', confidence=conf, verdict=verdict)
    clips = review.find_clips(str(tmp_path))
    assert review.suggest_threshold(clips, 0.8) == 0.7   # 5 of 6 good at >= 0.7
    assert review.suggest_threshold(clips, 0.95) == 0.8  # 5 of 5 good at >= 0.8
    assert review.suggest_threshold(clips, 0.5) == 0.55
    assert review.suggest_threshold(clips[:3], 0.8) is None  # Too few reviewed


def test_cli_end_to_end(tmp_path, capsys):
    a = make_clip(tmp_path, 'a', confidence=0.9)
    make_clip(tmp_path, 'b', confidence=0.7, label='wrong_object')
    assert review.main(['mark', a, 'good', '--by', 'me']) == 0
    assert review.main(['list', str(tmp_path), '--unreviewed']) == 0
    out = capsys.readouterr().out
    assert 'wrong_object' in out and '\na ' not in out and '1 clip(s)' in out
    assert review.main(['stats', str(tmp_path), '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['labels']['object_dropped']['precision'] == 1.0
    assert data['labels']['wrong_object']['reviewed'] == 0
    assert review.main(['stats', str(tmp_path), '--ros-args']) == 0
    assert 'min_conf@0.8' in capsys.readouterr().out


def make_windowed(root, name, label, event_s, pre=5, post=3, lag=2.0, pre_ok=True):
    path = make_clip(root, name, label=label, lag=lag, pre_ok=pre_ok, stamp=int(event_s * S))
    with open(os.path.join(path, 'event.json')) as f:
        event = json.load(f)
    event['window'] = {'start_ns': int((event_s - pre) * S), 'end_ns': int((event_s + post) * S)}
    event['trigger']['event_stamp_ns'] = int(event_s * S)
    event['events'] = [event['trigger']]
    with open(os.path.join(path, 'event.json'), 'w') as f:
        json.dump(event, f)
    return path


def test_evaluate_plan_metrics(tmp_path):
    truth_csv = tmp_path / 'truth.csv'
    truth_csv.write_text('label,time_s\nobject_dropped,100\nobject_dropped,200\n'
                         'person_in_cell,300\n')
    clips_dir = tmp_path / 'clips'
    clips_dir.mkdir()
    make_windowed(str(clips_dir), 'hit', 'object_dropped', 101.0, lag=1.5)   # Detected late
    make_windowed(str(clips_dir), 'false_alarm', 'object_dropped', 150.0)
    make_windowed(str(clips_dir), 'wrong_label', 'object_dropped', 300.0, pre_ok=False)

    clips = review.find_clips(str(clips_dir))
    truth = review.load_truth(str(truth_csv))
    strict = review.evaluate(clips, truth)
    assert strict['recall'] == pytest.approx(1 / 3)
    assert strict['precision'] == pytest.approx(1 / 3)
    assert strict['lag_p50_s'] == pytest.approx(2.5)   # 101 + 1.5 - 100
    assert strict['pre_event_coverage'] == pytest.approx(2 / 3)

    loose = review.evaluate(clips, truth, match_label=False)
    assert loose['recall'] == pytest.approx(2 / 3)
    assert loose['precision'] == pytest.approx(2 / 3)

    assert review.main(['eval', str(clips_dir), '--truth', str(truth_csv), '--json']) == 0


def test_evaluate_empty():
    result = review.evaluate([], [])
    assert result['recall'] is None and result['precision'] is None
