"""Root guardrails on a model's follow-up question, independent of which model wrote it.

Every check here is deterministic. A proposal that fails one is rejected and the
student gets the fixed fallback (judgment pending), never the model's text.
Bump GUARDRAIL_VERSION whenever a check changes: it is part of the root policy
version that keys the decision ledger.
"""
import re
import unicodedata

GUARDRAIL_VERSION = 'guardrails-v1'

_HANGUL = re.compile(r'[가-힣]')
_LETTER = re.compile(r'[^\W\d_]')
_ANSWER_LEAK = re.compile(r'정답|답은|답:|해설|결론은|이므로|때문입니다|입니다\.|라는\s*뜻입니다')
_META = re.compile(r'(?i)\b(?:as an ai|language model|chatgpt|gpt|claude|qwen|llama|gemini)\b|인공지능으로서|언어\s*모델|저는\s*AI')
_COMMAND = re.compile(r'(?:하세요|하십시오|해라|하라)\s*\?$')


def _squash(text):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(text or ''))).lower()


def followup_violation(follow_up, student_question):
    """Return the violated guardrail code, or None when the follow-up is acceptable."""
    letters = _LETTER.findall(follow_up)
    if not letters or len(_HANGUL.findall(follow_up)) / len(letters) < 0.6:
        return 'guardrail_not_korean'
    if _META.search(follow_up):
        return 'guardrail_model_meta'
    if _ANSWER_LEAK.search(follow_up):
        return 'guardrail_answer_leak'
    if _COMMAND.search(follow_up.strip()):
        return 'guardrail_not_a_question'
    if _squash(follow_up) == _squash(student_question):
        return 'guardrail_echoes_student'
    return None
