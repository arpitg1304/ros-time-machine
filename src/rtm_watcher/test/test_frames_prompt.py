import base64

from rtm_watcher.frames import Frame, FrameSampler, FrameWindow, StateBoard, \
    summarize_joint_state
from rtm_watcher.prompt import build_messages, prompt_version, SYSTEM_PROMPT
from rtm_watcher.watchlist import parse_watch_list

S = 1_000_000_000


def config(**overrides):
    watcher = {
        'cameras': ['/cam'],
        'events': [{'label': 'object_dropped', 'describe': 'An object falls.'}],
    }
    watcher.update(overrides)
    return parse_watch_list(watcher)


def test_sampler_rate_per_camera():
    sampler = FrameSampler(2.0)  # every 0.5 s
    stamps = [int(t * S) for t in (0.0, 0.1, 0.4, 0.5, 0.9, 1.0, 1.6)]
    accepted = [t for t in stamps if sampler.accept('/a', t)]
    assert accepted == [0, int(0.5 * S), int(1.0 * S), int(1.6 * S)]
    assert sampler.accept('/b', int(0.1 * S))  # Independent per camera


def test_sampler_accepts_after_clock_reset():
    sampler = FrameSampler(1.0)
    assert sampler.accept('/a', 100 * S)
    assert sampler.accept('/a', 1 * S)


def test_window_keeps_last_n_per_camera_sorted():
    window = FrameWindow(2)
    for t in range(5):
        window.add(Frame('/a', t * S, b'a'))
    window.add(Frame('/b', int(3.5 * S), b'b'))
    frames = window.snapshot()
    assert [(f.camera, f.stamp_ns) for f in frames] == \
        [('/a', 3 * S), ('/b', int(3.5 * S)), ('/a', 4 * S)]


def test_window_unseen_flag():
    window = FrameWindow(3)
    assert not window.has_unseen()
    window.add(Frame('/a', S, b'x'))
    assert window.has_unseen()
    window.snapshot()
    assert not window.has_unseen()


def test_window_resets_on_time_going_backwards():
    window = FrameWindow(4)
    window.add(Frame('/a', 100 * S, b'x'))
    window.add(Frame('/b', 100 * S, b'x'))
    window.add(Frame('/a', 1 * S, b'x'))
    assert [(f.camera, f.stamp_ns) for f in window.snapshot()] == [('/a', 1 * S)]


def test_state_board_summary_and_staleness():
    board = StateBoard(max_chars_per_topic=20)
    board.update('/joints', 10 * S, 'j1=0.10,   j2=0.20')
    board.update('/gripper', 1 * S, 'closed ' * 10)
    text = board.summary(now_ns=10 * S, max_age_s=5)
    assert '/joints: j1=0.10, j2=0.20' in text
    assert '/gripper: (stale) closed' in text
    assert text.splitlines()[0].startswith('/gripper')  # Sorted by topic
    assert len(text.splitlines()[0]) < 40


def test_joint_state_summary():
    assert summarize_joint_state(['a', 'b'], [0.123, -1.0]) == 'a=0.12, b=-1.00'
    assert summarize_joint_state(['a'], [0.0], efforts=[2.5]) == 'a=0.00 (effort 2.50)'
    assert summarize_joint_state(['a'], []) == 'a=?'


def test_messages_contain_numbered_frames_and_events():
    cfg = config(robot_context='SO-101 arm picking a red cup.')
    frames = [Frame('/cam', 1 * S, b'one'), Frame('/cam', 2 * S, b'two')]
    messages = build_messages(cfg, frames, 'gripper: closed')
    assert messages[0] == {'role': 'system', 'content': SYSTEM_PROMPT}
    parts = messages[1]['content']
    intro = parts[0]['text']
    assert 'object_dropped: An object falls.' in intro
    assert 'gripper: closed' in intro
    assert 'SO-101' in intro
    images = [p for p in parts if p['type'] == 'image_url']
    assert len(images) == 2
    assert images[1]['image_url']['url'] == \
        'data:image/jpeg;base64,' + base64.b64encode(b'two').decode()
    labels = [p['text'] for p in parts[1:] if p['type'] == 'text']
    assert labels == ['Frame 0 (/cam, t=-1.0s):', 'Frame 1 (/cam, t=+0.0s):']


def test_prompt_version_tracks_watch_list():
    a = prompt_version(config())
    assert a.startswith('prompt:') and len(a) == len('prompt:') + 6
    assert a == prompt_version(config())
    changed = config(events=[{'label': 'object_dropped', 'describe': 'Something else.'}])
    assert prompt_version(changed) != a
