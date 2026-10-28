from rtm_watcher.decision import parse_response

LABELS = ['object_dropped', 'person_in_cell']


def test_plain_json():
    result = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": 2, "confidence": 0.82, '
        '"description": "Box slips."}]}', LABELS, 4)
    assert result.valid_json
    assert len(result.detections) == 1
    d = result.detections[0]
    assert (d.label, d.frame_index, d.confidence, d.description) == \
        ('object_dropped', 2, 0.82, 'Box slips.')


def test_empty_events():
    result = parse_response('{"events": []}', LABELS, 4)
    assert result.valid_json and not result.detections and not result.problems


def test_fenced_and_chatty_output():
    text = 'Sure! Here you go:\n```json\n{"events": [{"label": "person_in_cell", ' \
        '"frame_index": 0, "confidence": 0.7}]}\n```\nHope that helps.'
    result = parse_response(text, LABELS, 4)
    assert [d.label for d in result.detections] == ['person_in_cell']


def test_garbage_is_invalid_not_fatal():
    for text in ['', 'no json here', '{"events": ', None]:
        result = parse_response(text, LABELS, 4)
        assert not result.valid_json
        assert not result.detections


def test_unknown_label_dropped():
    result = parse_response(
        '{"events": [{"label": "robot_danced", "frame_index": 0, "confidence": 0.9}]}',
        LABELS, 4)
    assert result.valid_json
    assert not result.detections
    assert 'unknown label' in result.problems[0]


def test_frame_index_defaults_and_bounds():
    missing = parse_response('{"events": [{"label": "object_dropped", "confidence": 0.9}]}',
                             LABELS, 4)
    assert missing.detections[0].frame_index == 3
    negative = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": -1, "confidence": 0.9}]}',
        LABELS, 4)
    assert negative.detections[0].frame_index == 3
    out_of_range = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": 9, "confidence": 0.9}]}',
        LABELS, 4)
    assert not out_of_range.detections


def test_confidence_normalized():
    percent = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": 0, "confidence": 85}]}',
        LABELS, 1)
    assert percent.detections[0].confidence == 0.85
    clamped = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": 0, "confidence": -2}]}',
        LABELS, 1)
    assert clamped.detections[0].confidence == 0.0
    bad = parse_response(
        '{"events": [{"label": "object_dropped", "frame_index": 0, "confidence": "high"}]}',
        LABELS, 1)
    assert not bad.detections


def test_bare_list_and_single_object():
    as_list = parse_response('[{"label": "object_dropped", "confidence": 0.9}]', LABELS, 2)
    assert len(as_list.detections) == 1
    single = parse_response('{"label": "object_dropped", "confidence": 0.9}', LABELS, 2)
    assert len(single.detections) == 1


def test_partial_batch_keeps_good_entries():
    result = parse_response(
        '{"events": [{"label": "object_dropped", "confidence": 0.9}, "oops", '
        '{"label": "nope", "confidence": 1}]}', LABELS, 2)
    assert len(result.detections) == 1
    assert len(result.problems) == 2
