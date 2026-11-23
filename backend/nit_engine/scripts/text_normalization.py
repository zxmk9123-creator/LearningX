"""Shared Unicode and parser comparison utilities. No factual accuracy inference."""
import re, unicodedata
from collections import Counter

KNOWN_LIST_MARKERS={'\uf0fc','\uf0a7','\uf0d8','\uf0b7','\uf0a0','\uf06c','\uf0b2'}
BAD_SCRIPTS=('ARABIC','HEBREW','CYRILLIC','DEVANAGARI','BENGALI','GURMUKHI','GUJARATI','ORIYA','ODIA','TAMIL','TELUGU','KANNADA','MALAYALAM','SINHALA','THAI','LAO')
# U+F0E0 may encode a Wingdings directional arrow, NOT a list bullet.
# Preserve it and quarantine as ambiguous until a teacher checks the glyph.
AMBIGUOUS_DIRECTION_GLYPHS={'\uf0e0'}

def dangerous_characters(s):
    return [c for c in s if c in ('\x00','\ufffd') or
            (unicodedata.category(c) in ('Co','Cs') and c not in KNOWN_LIST_MARKERS) or
            (unicodedata.category(c)=='Cc' and c not in '\n\t\r\v')]
def normalize_bullets(s):
    return ''.join(('•' if c in KNOWN_LIST_MARKERS else c) for c in str(s or ''))
def clean_compare(s):
    s=unicodedata.normalize('NFKC',normalize_bullets(s))
    return re.sub(r'\s+',' ',s).strip()
def char_bag(s):
    return Counter(c.lower() for c in clean_compare(s) if c.isalnum())
def token_bag(s):
    return Counter(re.findall(r'[\w가-힣]{2,}',clean_compare(s).lower()))
def counter_f1(a,b):
    if not a and not b:return 1.0
    if not a or not b:return 0.0
    common=sum((a&b).values());return 2*common/max(sum(a.values())+sum(b.values()),1)
def numeric_bag(s):
    return Counter(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',clean_compare(s)))


# ---------- repairs of extracted text (no factual inference) ----------
LIGATURES={'\ufb00':'ff','\ufb01':'fi','\ufb02':'fl','\ufb03':'ffi','\ufb04':'ffl','\ufb05':'st','\ufb06':'st'}
_LIGATURE=re.compile('[\ufb00-\ufb06]')
_LATIN1_RUN=re.compile(r'[\x20-\xff]*[\xa1-\xff][\x20-\xff]*[\xa1-\xff][\x20-\xff]*')

def _ks_hangul(ch):
    try:return len(ch.encode('euc-kr'))==2 and '\uac00'<=ch<='\ud7a3'
    except UnicodeEncodeError:return False

def _cp949_run(run):
    """Korean CP949 bytes shown through a Latin-1 font, decoded back; None when it does not fit."""
    try:decoded=run.encode('latin-1').decode('cp949')
    except (UnicodeEncodeError,UnicodeDecodeError):return None
    hangul=[c for c in decoded if '\uac00'<=c<='\ud7a3']
    if len(hangul)<2 or sum(map(_ks_hangul,hangul))<0.95*len(hangul):return None
    if any(unicodedata.category(c) in ('Co','Cs','Cc') and c not in '\n\t' for c in decoded):return None
    return decoded

def repair_text(s):
    """(text, tags): NFC (decomposed Hangul jamo), Latin ligatures, CP949-as-Latin-1 runs."""
    s=str(s or '');tags=[]
    t=unicodedata.normalize('NFC',s)
    if t!=s:tags.append('nfc')
    u=_LIGATURE.sub(lambda m:LIGATURES[m.group(0)],t)
    if u!=t:tags.append('ligature')
    def fix(m):
        out=_cp949_run(m.group(0))
        if out is None:return m.group(0)
        tags.append('cp949');return out
    v=_LATIN1_RUN.sub(fix,u)
    return v,sorted(set(tags))
