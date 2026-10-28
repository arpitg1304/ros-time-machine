import json
import random

import pytest

from rtm_watcher.calibration import ConformalThreshold
from rtm_watcher.engine import WatcherEngine
from rtm_watcher.frames import Frame, FrameHistory
from rtm_watcher.prompt import JUDGE_NOTE
from rtm_watcher.watchlist import parse_watch_list, WatchListError

S = 1_000_000_000


# Conformal calibration ------------------------------------------------------

def test_threshold_controls_false_alarm_rate():
    rng = random.Random(0)
    gate = ConformalThreshold(alpha=0.05)
    for _ in range(2000):
        gate.observe(rng.gauss(0, 1))
    assert gate.finalize()
    fresh = [rng.gauss(0, 1) for _ in range(20000)]
    rate = sum(gate.exceeds(x) for x in fresh) / len(fresh)
    assert rate <= 0.05 + 0.01
    assert rate >= 0.03  # Not wildly conservative either


def test_threshold_is_scale_free():
    a, b = ConformalThreshold(alpha=0.1), ConformalThreshold(alpha=0.1)
    for i in range(100):
        a.observe(i)
        b.observe(1000 * i)
    a.finalize()
    b.finalize()
    assert b.threshold == 1000 * a.threshold


def test_exact_quantile_index():
    gate = ConformalThreshold(alpha=0.1, min_samples=1)
    for x in range(1, 20):  # n = 19 -> k = ceil(20 * 0.9) = 18
        gate.observe(x)
    gate.finalize()
    assert gate.threshold == 18
    assert gate.exceeds(18.5) and not gate.exceeds(18)


def test_too_few_samples_not_calibrated():
    gate = ConformalThreshold(alpha=0.01, min_samples=10)
    for x in range(50):  # alpha 0.01 needs >= 99 samples
        gate.observe(x)
    assert gate.required_samples() == 99
    assert not gate.finalize()
    assert not gate.calibrated
    assert not gate.exceeds(1e9)


def test_fixed_threshold_and_non_finite_scores():
    gate = ConformalThreshold.fixed(2.0)
    assert gate.calibrated and gate.exceeds(2.1) and not gate.exceeds(2.0)
    assert not gate.exceeds(float('nan'))
    cal = ConformalThreshold(alpha=0.5, min_samples=1)
    cal.observe(float('inf'))
    assert cal.samples == 0
    with pytest.raises(ValueError):
        ConformalThreshold(alpha=0)


# Frame history ----------------------------------------------------------------

def test_history_prunes_by_time_and_thins_evenly():
    history = FrameHistory(seconds=10)
    for t in range(30):
        history.add(Frame('/cam', t * S, b'x'))
    assert len(history) == 11  # t = 19..29
    frames = history.between(20 * S, 29 * S, max_frames=4)
    assert [f.stamp_ns // S for f in frames] == [20, 23, 26, 29]
    assert [f.stamp_ns // S for f in history.between(0, 21 * S, 8)] == [19, 20, 21]
    assert history.between(0, 21 * S, 1)[0].stamp_ns == 21 * S


def test_history_clears_on_clock_reset():
    history = FrameHistory(seconds=10)
    history.add(Frame('/cam', 100 * S, b'x'))
    history.add(Frame('/cam', 2 * S, b'x'))
    assert len(history) == 1


# Judge ------------------------------------------------------------------------

def config(**overrides):
    watcher = {
        'cameras': ['/cam'],
        'context_frames': 2,
        'history_s': 20,
        'judge_frames': 3,
        'cooldown_s': 10,
        'events': [{'label': 'object_dropped', 'describe': 'An object falls.'}],
    }
    watcher.update(overrides)
    return parse_watch_list(watcher)


class Canned:

    def __init__(self, *replies):
        self.replies = list(replies)
        self.messages = []

    def complete(self, messages):
        self.messages.append(messages)
        return self.replies.pop(0)


def reply(*entries):
    return json.dumps({'events': [
        {'label': label, 'frame_index': i, 'confidence': c} for label, i, c in entries]})


def test_judge_keeps_credible_event_and_uses_history():
    client = Canned(reply(('object_dropped', 1, 0.5), ('object_dropped', 2, 0.9)))
    engine = WatcherEngine(config(), client)
    for t in range(10, 20):
        engine.offer_frame('/cam', t * S, b'x')
    verdict = engine.judge(12 * S, 16 * S)
    assert verdict.keep
    assert verdict.frames == 3  # Thinned from 5 frames in the pin
    assert verdict.detection.confidence == 0.9
    assert verdict.event_stamp_ns == 16 * S
    assert JUDGE_NOTE in client.messages[0][1]['content'][0]['text']
    assert engine.stats.judged == 1 and engine.stats.judge_kept == 1


def test_judge_releases_when_nothing_credible():
    engine = WatcherEngine(config(), Canned(reply(('object_dropped', 0, 0.3))))
    engine.offer_frame('/cam', 1 * S, b'x')
    verdict = engine.judge(0, 2 * S)
    assert not verdict.keep and verdict.detection is None and not verdict.error


def test_judge_ignores_cooldown_but_starts_one():
    client = Canned(reply(('object_dropped', 0, 0.9)), reply(('object_dropped', 0, 0.9)),
                    reply(('object_dropped', 1, 0.9)))
    engine = WatcherEngine(config(), client)
    engine.offer_frame('/cam', 10 * S, b'x')
    assert len(engine.step().triggers) == 1      # Periodic fire at t=10
    engine.offer_frame('/cam', 11 * S, b'x')
    assert engine.judge(9 * S, 12 * S).keep      # Judge is not blocked by that cooldown
    assert engine.step().triggers == []          # Periodic path now in cooldown


def test_judge_without_frames_is_an_error():
    engine = WatcherEngine(config(), Canned())
    verdict = engine.judge(0, S)
    assert not verdict.keep and verdict.error


# Scorer config ----------------------------------------------------------------

def test_scorer_defaults_and_overrides():
    cfg = config(scorers=[
        {'topic': '/scorers/effort', 'profile': 'manipulation'},
        {'topic': '/scorers/fiper', 'threshold': 0.8, 'judge': False, 'pre_event': 8},
    ])
    effort, fiper = cfg.scorers
    assert effort.threshold is None and effort.alpha == 0.01 and effort.judge
    assert fiper.threshold == 0.8 and not fiper.judge and fiper.pre_event == 8


def test_history_must_cover_scorer_pins():
    with pytest.raises(WatchListError, match='history_s'):
        config(history_s=5, scorers=[{'topic': '/s', 'pre_event': 5, 'post_event': 3}])


def test_scorer_requires_topic():
    with pytest.raises(WatchListError):
        config(scorers=[{'profile': 'x'}])


# Built-in effort scorer -----------------------------------------------------

def test_effort_spike_scores_contact_far_above_noise():
    from rtm_watcher.scorers import EffortSpikeScore
    rng = random.Random(1)
    score = EffortSpikeScore(alpha=0.05)
    quiet = [score.update(['a', 'b'], [1.0 + rng.gauss(0, 0.01), rng.gauss(0, 0.01)])
             for _ in range(500)]
    spike = score.update(['a', 'b'], [1.5, 0.0])
    assert spike > 10 * max(quiet[100:])
