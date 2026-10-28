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

"""Minimal client for OpenAI-compatible chat endpoints (vLLM, llama.cpp, Ollama, SGLang).

Standard library only, so the watcher installs anywhere ROS does.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request


class VlmError(RuntimeError):
    """The endpoint was unreachable or returned something unusable."""


class OpenAICompatibleClient:
    """POSTs chat completions and returns the assistant's text.

    Providers disagree on optional parameters (reasoning models reject
    temperature, newer OpenAI models want max_completion_tokens, some servers
    have no JSON mode). When a 400 names a parameter we sent, the client
    adapts once and remembers, so one watch list works against any backend.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str = '',
        timeout_s: float = 10.0,
        max_tokens: int = 300,
        temperature: Optional[float] = None,
        json_mode: bool = True,
        extra_body: Optional[Dict[str, Any]] = None,
    ):
        self.url = endpoint.rstrip('/') + '/chat/completions'
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.temperature = temperature      # None = provider default (not sent)
        self.json_mode = json_mode          # Turned off if the server rejects response_format
        self.extra_body = dict(extra_body or {})
        self.max_tokens_param = 'max_tokens'

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:500]
            raise VlmError(f'HTTP {e.code} from {self.url}: {detail}') from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise VlmError(f'cannot reach {self.url}: {e}') from e
        except ValueError as e:
            raise VlmError(f'non-JSON reply from {self.url}: {e}') from e

    def _body(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        body: Dict[str, Any] = {'model': self.model, 'messages': messages}
        body[self.max_tokens_param] = self.max_tokens
        if self.temperature is not None:
            body['temperature'] = self.temperature
        if self.json_mode:
            body['response_format'] = {'type': 'json_object'}
        body.update(self.extra_body)
        return body

    def _adapt(self, error: VlmError) -> bool:
        """Change the request shape the server just rejected; False if nothing to change."""
        text = str(error)
        if not text.startswith('HTTP 400') and not text.startswith('HTTP 422'):
            return False
        if self.json_mode and 'response_format' in text:
            self.json_mode = False
            return True
        if self.temperature is not None and 'temperature' in text:
            self.temperature = None
            return True
        if self.max_tokens_param == 'max_tokens' and 'max_completion_tokens' in text:
            self.max_tokens_param = 'max_completion_tokens'
            return True
        return False

    def complete(self, messages: List[Dict[str, Any]]) -> str:
        for _ in range(4):  # At most one adaptation per optional parameter
            try:
                return self.extract_text(self._post(self._body(messages)))
            except VlmError as e:
                if not self._adapt(e):
                    raise
        raise VlmError(f'{self.url} kept rejecting the request shape')

    @staticmethod
    def extract_text(reply: Dict[str, Any]) -> str:
        try:
            content = reply['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError) as e:
            raise VlmError(f'unexpected completion shape: {str(reply)[:300]}') from e
        if isinstance(content, list):  # Content parts
            content = ''.join(
                part.get('text', '') for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise VlmError(f'completion content is not text: {content!r}')
        return content
