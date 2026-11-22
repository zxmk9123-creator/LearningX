"""Deterministic question-level rubric.

The model's question_level can drift between calls (sampling, provider routing
of an `auto` model). For questions that carry an explicit cue of the thinking
they ask for, the level tier is decided here, so the same question always gets
the same tier. Questions without a cue keep the model's judgment.

Tiers follow the platform's three display levels:
  shallow (clarify)          definition / fact / meaning recall
  mid     (apply, analyze)   reason, cause, effect, comparison, relation, application
  deep    (evaluate, create) judging by criteria, choosing between options, designing

Cues are checked deep -> mid -> shallow, so "이유는 무엇인가요" is mid (reason)
even though it also ends with "무엇인가요".
"""
import re
import unicodedata

RUBRIC_VERSION = 'level-rubric-v1'

TIER = {'clarify': 'shallow', 'apply': 'mid', 'analyze': 'mid', 'evaluate': 'deep', 'create': 'deep'}
CANONICAL = {'shallow': 'clarify', 'mid': 'analyze', 'deep': 'evaluate'}

_RULES = (
    ('deep', 'compare_options', r'어느\s*(?:쪽|것|방법|전략|방식|안)[이가]?\s*더'),
    ('deep', 'better_than', r'더\s*(?:효과적|적절|타당|나은|낫|좋은|바람직|유리)'),
    ('deep', 'judge_by_criteria', r'(?:기준|근거)으로\s*(?:판단|평가|선택|결정)'),
    ('deep', 'evaluate', r'(?:평가|판단)(?:하|할|해|한다면|하려면)|타당(?:한가|한지|성)|장단점|바람직한가|옳은가|옳은지|정당(?:한가|한지|화)'),
    ('deep', 'design', r'설계|고안|대안|개선(?:하려면|할\s*수|방안|한다면)|새로운\s*\S+\s*(?:만들|제안)'),
    ('mid', 'reason', r'왜|이유|원인|까닭'),
    ('mid', 'effect', r'영향|효과(?:가|는)?\s*(?:있|나타|생기|달라)|결과(?:는|가)\s*(?:무엇|어떻)'),
    ('mid', 'compare', r'차이|비교|공통점|다른\s*점|구분(?:되|하|할)'),
    ('mid', 'relation', r'관계|연관|상관|연결(?:되|하)'),
    ('mid', 'mechanism', r'어떻게\s*(?:작용|작동|일어나|발생|달라|변하)|과정은'),
    ('mid', 'apply', r'적용|활용|사례에\s*(?:서|대입)|실제로\s*\S*\s*어떻게'),
    ('shallow', 'definition', r'정의|뜻|의미|(?:이|가)?란\s*무엇|(?:이|가)?란\s*뭐|개념이\s*(?:뭐|무엇)'),
    ('shallow', 'reference', r'가리키|지칭|무엇을\s*말하|뭘\s*말하'),
    ('shallow', 'composition', r'(?:요소|무엇)으?로\s*(?:구성|이루어)|구성\s*요소'),
    ('shallow', 'fact', r'(?:무엇|뭐)(?:인가요|예요|이에요|인지|일까요|죠|야)|누구|언제|어디|몇\s*(?:개|가지|년|명)'),
)
_COMPILED = tuple((tier, name, re.compile(pattern)) for tier, name, pattern in _RULES)


def normalize(question):
    """Canonical form for comparing the same question (spacing/width/trailing marks)."""
    text = unicodedata.normalize('NFKC', str(question or ''))
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'[\s?？!.。…~]+$', '', text)


def rule_tier(question):
    """Return (tier, cue) for a question with an explicit level cue, else (None, None)."""
    text = normalize(question)
    for tier, name, pattern in _COMPILED:
        if pattern.search(text):
            return tier, name
    return None, None


def resolve_level(question, model_level):
    """Final question_level and the basis it was decided on."""
    tier, cue = rule_tier(question)
    if tier is None:
        return model_level, 'model'
    if TIER.get(model_level) == tier:
        return model_level, 'rule:' + cue
    return CANONICAL[tier], 'rule:' + cue
