"""ROS 2 message definitions embedded in the MCAP, so no ROS install is needed to write it."""

SEP = '=' * 80


def _with(main: str, *deps: str) -> str:
    return main + ''.join(f'\n{SEP}\nMSG: {name}\n{DEFS[name]}' for name in deps)


DEFS = {
    'builtin_interfaces/Time': 'int32 sec\nuint32 nanosec',
    'std_msgs/Header': 'builtin_interfaces/Time stamp\nstring frame_id',
}
HEADER_DEPS = ('std_msgs/Header', 'builtin_interfaces/Time')

MSGDEFS = {
    'sensor_msgs/msg/CompressedImage': _with('std_msgs/Header header\nstring format\nuint8[] data', *HEADER_DEPS),
    'sensor_msgs/msg/JointState': _with(
        'std_msgs/Header header\nstring[] name\nfloat64[] position\nfloat64[] velocity\nfloat64[] effort',
        *HEADER_DEPS),
    'std_msgs/msg/String': 'string data',
}

NS = 1_000_000_000


def header(ns: int, frame_id: str = '') -> dict:
    return {'stamp': {'sec': ns // NS, 'nanosec': ns % NS}, 'frame_id': frame_id}


def compressed_image(ns: int, jpeg: bytes, frame_id: str) -> dict:
    return {'header': header(ns, frame_id), 'format': 'jpeg', 'data': jpeg}


def joint_state(ns: int, names, positions) -> dict:
    return {'header': header(ns), 'name': list(names), 'position': [float(v) for v in positions],
            'velocity': [], 'effort': []}
