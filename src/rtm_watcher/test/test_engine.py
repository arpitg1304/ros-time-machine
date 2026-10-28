import json

import pytest

from rtm_watcher.engine import WatcherEngine
from rtm_watcher.fake_vlm import FakeVlmServer, ScriptedResponder
from rtm_watcher.vlm_client import OpenAICompatibleClient, VlmError
from rtm_watcher.watchlist import parse_watch_list

S = 1_000_000_000


def config(**overrides):
    watcher = {
        'model': 'qwen3-vl-4b-instruct',
        'cameras': ['/cam'],
        'sample_hz': 1.0,
        'context_frames': 3,
        'min_confidence': 0.6,
        'cooldown_s': 10,
        'events': [
            {'label': 'object_dropped', 'describe': 'An object falls.', 'profile': 'manip'},
            {'label': 'task_success', 'describe': 'Cup on coaster.', 'sample_rate': 0.5,
             'pre_event': 2.0, 'post_event': 1.0, 'tags': ['episode']},
        ],
    }
    watcher.update(overrides)
    return parse_watch_list(watcher)


class Canned:
    """Client returning queued replies (str) or raising queued exceptions."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.messages = []

    def complete(self, messages):
        self.messages.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def events(*entries):
    return json.dumps({'events': [
        dict(zip(('label', 'frame_index', 'confidence'), e)) for e in entries]})


def feed(engine, times, camera='/cam'):
    for t in times:
        engine.offer_frame(camera, int(t * S), b'jpeg')


def test_detection_becomes_time_addressed_trigger():
    engine = WatcherEngine(config(), Canned(events(('object_dropped', 1, 0.82))))
    feed(engine, [10, 11, 12])
    assert engine.ready()
    result = engine.step()
    assert not engine.ready()
    assert result.frames == 3
    [trigger] = result.triggers
    assert trigger.label == 'object_dropped'
    assert trigger.profile == 'manip'
    assert trigger.event_stamp_ns == 11 * S  # Frame 1, not the time of the answer
    assert (trigger.start_ns, trigger.end_ns) == (0, 0)  # Profile defaults
    assert trigger.source == 'vlm:qwen3-vl-4b-instruct'
    assert trigger.source_version.startswith('prompt:')


def test_per_event_window_override_and_tags():
    engine = WatcherEngine(config(), Canned(events(('task_success', 2, 0.9))), rng=lambda: 0.1)
    feed(engine, [10, 11, 12])
    [trigger] = engine.step().triggers
    assert trigger.start_ns == 10 * S and trigger.end_ns == 13 * S
    assert trigger.tags == ['episode']


def test_unsampled_frames_never_encoded():
    engine = WatcherEngine(config(), Canned())
    calls = []

    def encode():
        calls.append(1)
        return b'jpeg'
    for t in [0.0, 0.2, 0.4, 1.0]:
        engine.offer_frame('/cam', int(t * S), encode)
    assert len(calls) == 2
    assert engine.stats.frames_offered == 4
    assert engine.stats.frames_sampled == 2


def test_low_confidence_suppressed():
    engine = WatcherEngine(config(), Canned(events(('object_dropped', 0, 0.4))))
    feed(engine, [1])
    result = engine.step()
    assert result.detections and not result.triggers
    assert engine.stats.below_confidence == 1


def test_cooldown_dedupes_overlapping_windows():
    replies = [events(('object_dropped', 2, 0.9)),   # Seen at t=12
               events(('object_dropped', 1, 0.9)),   # Same drop in the next window
               events(('object_dropped', 2, 0.9))]   # A new drop much later
    engine = WatcherEngine(config(), Canned(*replies))
    feed(engine, [10, 11, 12])
    assert len(engine.step().triggers) == 1
    feed(engine, [13])
    assert engine.step().triggers == []
    assert engine.stats.in_cooldown == 1
    feed(engine, [30, 31, 32])
    assert len(engine.step().triggers) == 1


def test_sample_rate_still_starts_cooldown():
    rolls = iter([0.9, 0.1])  # First roll loses (>= 0.5), second would win
    replies = [events(('task_success', 0, 0.9)), events(('task_success', 0, 0.9))]
    engine = WatcherEngine(config(), Canned(*replies), rng=lambda: next(rolls))
    feed(engine, [10])
    assert engine.step().triggers == []
    assert engine.stats.sampled_out == 1
    feed(engine, [11])
    assert engine.step().triggers == []  # In cooldown, no re-roll
    assert engine.stats.in_cooldown == 1


def test_errors_and_garbage_counted_not_raised():
    engine = WatcherEngine(config(), Canned(VlmError('down'), RuntimeError('boom'), 'lol'))
    for t in (1, 2, 3):
        feed(engine, [t])
        result = engine.step()
        assert result.triggers == []
    assert engine.stats.errors == 2
    assert engine.stats.invalid_json == 1
    assert engine.stats.calls == 3


def test_step_without_frames_is_noop():
    engine = WatcherEngine(config(), Canned())
    assert engine.step().frames == 0
    assert engine.stats.calls == 0


def test_state_reaches_prompt():
    client = Canned(events())
    engine = WatcherEngine(config(), client)
    engine.update_state('/joint_states', 1 * S, 'j1=0.10')
    feed(engine, [1])
    engine.step()
    assert '/joint_states: j1=0.10' in client.messages[0][1]['content'][0]['text']


def test_latency_measured():
    clock = iter([100.0, 102.5])
    engine = WatcherEngine(config(), Canned(events()), monotonic=lambda: next(clock))
    feed(engine, [1])
    assert engine.step().latency_s == pytest.approx(2.5)
    assert engine.stats.as_dict()['mean_latency_s'] == pytest.approx(2.5)


def test_end_to_end_with_fake_server():
    responder = ScriptedResponder('object_dropped', every=2, confidence=0.9)
    with FakeVlmServer(responder) as server:
        cfg = config(endpoint=server.endpoint)
        client = OpenAICompatibleClient(server.endpoint, cfg.model, timeout_s=5)
        engine = WatcherEngine(cfg, client)
        feed(engine, [10, 11, 12])
        assert engine.step().triggers == []
        feed(engine, [13])
        [trigger] = engine.step().triggers
        assert trigger.event_stamp_ns == 13 * S  # Newest of frames 11, 12, 13
        body = server.requests[-1]
        assert body['model'] == 'qwen3-vl-4b-instruct'
        assert body['response_format'] == {'type': 'json_object'}
        assert sum(p['type'] == 'image_url' for p in body['messages'][1]['content']) == 3


def test_client_reports_unreachable_endpoint():
    client = OpenAICompatibleClient('http://127.0.0.1:9/v1', 'm', timeout_s=0.5)
    with pytest.raises(VlmError):
        client.complete([{'role': 'user', 'content': 'hi'}])


def test_client_extracts_content_parts():
    reply = {'choices': [{'message': {'content': [{'type': 'text', 'text': '{"events"'},
                                                  {'type': 'text', 'text': ': []}'}]}}]}
    assert OpenAICompatibleClient.extract_text(reply) == '{"events": []}'
    with pytest.raises(VlmError):
        OpenAICompatibleClient.extract_text({'choices': []})


def test_client_adapts_to_strict_provider():
    # Like an OpenAI reasoning model: no temperature, max_completion_tokens, no JSON mode
    strict = {
        'temperature': "Unsupported value: 'temperature' does not support 0.0",
        'max_tokens': "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'.",
        'response_format': 'response_format is not supported by this model',
    }
    responder = ScriptedResponder()
    with FakeVlmServer(responder, reject_params=strict) as server:
        client = OpenAICompatibleClient(server.endpoint, 'm', temperature=0.0, timeout_s=5)
        assert client.complete([{'role': 'user', 'content': 'hi'}]) == '{"events": []}'
        final = server.requests[-1]
        assert 'temperature' not in final and 'response_format' not in final
        assert final['max_completion_tokens'] == 300 and 'max_tokens' not in final
        attempts = len(server.requests)
        client.complete([{'role': 'user', 'content': 'again'}])
        assert len(server.requests) == attempts + 1  # Adaptations are remembered


def test_client_does_not_retry_unrelated_errors():
    with FakeVlmServer(ScriptedResponder(), reject_params={'model': 'model not found'}) as s:
        client = OpenAICompatibleClient(s.endpoint, 'missing', timeout_s=5)
        with pytest.raises(VlmError, match='model not found'):
            client.complete([{'role': 'user', 'content': 'hi'}])
        assert len(s.requests) == 1


def test_extra_body_and_defaults_sent():
    with FakeVlmServer(ScriptedResponder()) as server:
        client = OpenAICompatibleClient(
            server.endpoint, 'm', extra_body={'reasoning_effort': 'low', 'cache_prompt': False})
        client.complete([{'role': 'user', 'content': 'hi'}])
        body = server.requests[-1]
        assert body['reasoning_effort'] == 'low' and body['cache_prompt'] is False
        assert 'temperature' not in body  # Not sent unless configured


def test_watch_list_latency_options():
    cfg = config(image_detail='low', extra_body={'reasoning_effort': 'minimal'})
    assert cfg.temperature is None
    from rtm_watcher.frames import Frame
    from rtm_watcher.prompt import build_messages
    image = [p for p in build_messages(cfg, [Frame('/cam', 1, b'x')])[1]['content']
             if p['type'] == 'image_url'][0]
    assert image['image_url']['detail'] == 'low'
    from rtm_watcher.watchlist import WatchListError
    with pytest.raises(WatchListError):
        config(image_detail='tiny')
    with pytest.raises(WatchListError):
        config(extra_body={'model': 'other'})
