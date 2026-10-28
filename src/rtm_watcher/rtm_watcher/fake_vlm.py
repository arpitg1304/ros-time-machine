# Copyright 2024 ROS Time Machine Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A scripted OpenAI-compatible server for demos and tests without a GPU.

    ros2 run rtm_watcher fake_vlm --port 8000 --fire object_dropped --every 5

answers {"events": []} to every request except every Nth, where it reports the
event on the newest frame. --delay simulates inference latency.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Callable, Dict, List, Optional


def count_images(messages: List[dict]) -> int:
    count = 0
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            count += sum(1 for part in content if part.get('type') == 'image_url')
    return count


class ScriptedResponder:
    """Every Nth call reports `label` on the newest frame; otherwise no events."""

    def __init__(self, label: str = '', every: int = 0, confidence: float = 0.9,
                 description: str = 'Scripted event from the fake VLM.'):
        self.label = label
        self.every = every
        self.confidence = confidence
        self.description = description
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, messages: List[dict]) -> str:
        with self._lock:
            self.calls += 1
            fire = bool(self.label) and self.every > 0 and self.calls % self.every == 0
        if not fire:
            return json.dumps({'events': []})
        newest = max(count_images(messages) - 1, 0)
        return json.dumps({'events': [{
            'label': self.label,
            'frame_index': newest,
            'confidence': self.confidence,
            'description': self.description,
        }]})


class FakeVlmServer:
    """Runs in a background thread; `requests` records every request body received."""

    def __init__(self, responder: Callable[[List[dict]], str], host: str = '127.0.0.1',
                 port: int = 0, delay_s: float = 0.0, model: str = 'fake-vlm',
                 reject_params: Optional[Dict[str, str]] = None):
        self.responder = responder
        self.delay_s = delay_s
        self.model = model
        # Behave like a strict provider: {param: error text} -> HTTP 400 when sent
        self.reject_params = dict(reject_params or {})
        self.requests: List[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # Keep test output quiet
                pass

            def _send(self, code: int, payload: dict):
                body = json.dumps(payload).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.rstrip('/').endswith('/models'):
                    self._send(200, {'object': 'list', 'data': [{'id': server.model}]})
                else:
                    self._send(404, {'error': 'not found'})

            def do_POST(self):
                if not self.path.rstrip('/').endswith('/chat/completions'):
                    self._send(404, {'error': 'not found'})
                    return
                length = int(self.headers.get('Content-Length', 0))
                try:
                    body = json.loads(self.rfile.read(length).decode('utf-8'))
                except ValueError:
                    self._send(400, {'error': 'invalid JSON'})
                    return
                server.requests.append(body)
                for param, message in server.reject_params.items():
                    if param in body:
                        self._send(400, {'error': {'message': message, 'param': param}})
                        return
                if server.delay_s:
                    time.sleep(server.delay_s)
                text = server.responder(body.get('messages', []))
                self._send(200, {
                    'id': f'fake-{len(server.requests)}',
                    'object': 'chat.completion',
                    'model': body.get('model', server.model),
                    'choices': [{'index': 0, 'finish_reason': 'stop',
                                 'message': {'role': 'assistant', 'content': text}}],
                })

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._thread: Optional[threading.Thread] = None

    @property
    def endpoint(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f'http://{host}:{port}/v1'

    def start(self) -> 'FakeVlmServer':
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--fire', default='', help='label to report')
    parser.add_argument('--every', type=int, default=0, help='report on every Nth request')
    parser.add_argument('--confidence', type=float, default=0.9)
    parser.add_argument('--delay', type=float, default=0.0, help='seconds of fake inference')
    # launch_ros appends --ros-args ...; ignore anything we do not know
    args, _ = parser.parse_known_args(argv)

    responder = ScriptedResponder(args.fire, args.every, args.confidence)
    server = FakeVlmServer(responder, args.host, args.port, args.delay)
    print(f'Fake VLM listening on {server.endpoint}'
          + (f' (reports {args.fire!r} every {args.every} requests)' if args.fire else ''))
    try:
        server._httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server._httpd.server_close()


if __name__ == '__main__':
    main()
