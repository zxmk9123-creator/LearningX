"""Model-independent transport. No claims about provider model capabilities.

Never put secrets in logs or return provider text without schema/policy checks.
"""
import json
import os
import socket
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Protocol


class GatewayFailure(Exception):
    pass


class GatewayTimeout(GatewayFailure):
    """A timeout is not eligible for an automatic retry during student coaching."""
    pass


@dataclass(frozen=True)
class ModelResult:
    text: str
    provider: str
    model: str = 'unknown'


class ModelGateway(Protocol):
    def generate(self, system: str, user: str) -> ModelResult: ...


class OpenAICompatibleGateway:
    """FreeLLMAPI-compatible when configured to its actual OpenAI-compatible base URL.

    URL/model/key are explicit server configuration, not defaults inferred from a brand.
    """
    def __init__(self, base_url=None, api_key=None, model=None, timeout=25, opener=None,
                 max_tokens=650, json_mode=True, seed=None):
        self.base_url = base_url or os.environ.get('NIT_LLM_BASE_URL')
        self.api_key = api_key or os.environ.get('NIT_LLM_API_KEY')
        self.model = model or os.environ.get('NIT_LLM_MODEL')
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.max_tokens = int(max_tokens)
        self.json_mode = bool(json_mode)
        seed = os.environ.get('NIT_LLM_SEED', '7') if seed is None else seed
        self.seed = None if str(seed).strip().lower() in ('', 'off', 'none') else int(seed)
        if not (64 <= self.max_tokens <= 2048):
            raise GatewayFailure('max_tokens must be between 64 and 2048')
        if not (self.base_url and self.api_key and self.model):
            raise GatewayFailure('NIT_LLM_BASE_URL, NIT_LLM_API_KEY, NIT_LLM_MODEL are required')
        if not self.base_url.startswith('https://') and not os.environ.get('NIT_ALLOW_LOCAL_HTTP') == '1':
            raise GatewayFailure('HTTPS base URL required (local HTTP only with explicit development opt-in)')
        self.url = self.base_url.rstrip('/') + '/chat/completions'

    def generate(self, system, user):
        # Judgments must be reproducible: greedy decoding and a fixed seed where supported.
        payload = {'model': self.model, 'messages': [
            {'role':'system','content':system}, {'role':'user','content':user}],
            'temperature': 0, 'max_tokens': self.max_tokens}
        if self.seed is not None:
            payload['seed'] = self.seed
        if self.json_mode:
            payload['response_format'] = {'type': 'json_object'}
        try:
            return self._post(payload)
        except GatewayFailure as exc:
            # A provider that rejects the optional seed field gets one call without it.
            if 'seed' in payload and str(exc) in ('Provider HTTP status: 400', 'Provider HTTP status: 422'):
                payload.pop('seed')
                return self._post(payload)
            raise

    def _post(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.url, data=data, headers={
            'Authorization':'Bearer '+self.api_key, 'Content-Type':'application/json'}, method='POST')
        try:
            with self.opener(req, timeout=self.timeout) as response:
                body = json.loads(response.read(256 * 1024).decode('utf-8'))
            choice = body['choices'][0]['message']['content']
            if not isinstance(choice, str):
                raise GatewayFailure('Non-string model response')
            return ModelResult(choice, 'openai_compatible', str(body.get('model') or 'unknown'))
        except (TimeoutError, socket.timeout) as exc:
            raise GatewayTimeout('Provider deadline exceeded') from exc
        except urllib.error.HTTPError as exc:
            # Never include response body, URL query, or Authorization header in errors.
            raise GatewayFailure('Provider HTTP status: '+str(exc.code)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise GatewayTimeout('Provider deadline exceeded') from exc
            raise GatewayFailure('Provider transport error') from exc
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise GatewayFailure('Provider error: '+type(exc).__name__) from exc


class GeminiGateway:
    """Optional development adapter. Not required by the NIT coaching domain."""
    def __init__(self, client=None, model=None):
        self.model = model or os.environ.get('NIT_GEMINI_MODEL')
        if not self.model:raise GatewayFailure('NIT_GEMINI_MODEL is required')
        if client is None:
            try:
                from google import genai
                client = genai.Client()
            except (ImportError, Exception) as exc:
                raise GatewayFailure('Gemini SDK/config unavailable') from exc
        self.client = client

    def generate(self, system, user):
        try:
            res = self.client.models.generate_content(model=self.model, contents=system+'\n\n'+user)
            return ModelResult(res.text or '', 'gemini', self.model)
        except Exception as exc:
            raise GatewayFailure('Gemini provider failure') from exc


class DeterministicDemoGateway:
    """Offline mock for testing only. NEVER passes as evidence of model quality."""
    def generate(self, system, user):
        return ModelResult(json.dumps({'schema_version':'nit_coaching_v1',
            'question_level':'analyze', 'evidence_ids': [],
            'follow_up_question':'어떤 비교 기준을 먼저 정할 수 있을까요?'},ensure_ascii=False),
            'demo_mock', 'deterministic_fixture')
