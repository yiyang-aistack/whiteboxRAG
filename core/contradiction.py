"""Structured contradiction detection between an answer and its retrieved context.

Why this module exists
----------------------
Contradiction checking used to live inside ``core/sentence_tracing.py`` as a set of
substring heuristics:

* thresholds were hard-coded and generous (a percentage had to differ by more than 10
  points, a count or an amount had to differ by a factor of 2), so the flagship example in
  the README - the document states "BM25 weight is 0.4" and the model answers 0.6 - was
  never flagged on the number, because 0.4 vs 0.6 is a ratio of 1.5;
* the patterns only understood Chinese keywords ( ...), so an English
  answer was effectively unchecked;
* it was never part of the normal Q&A path: it was reachable only from the A/B comparison
  endpoint, so a trace could not show "this sentence disagrees with the source".

This module compares *claims* instead of substrings. A claim is a number plus its unit
(duration, length, weight, money, percentage, count, calendar date) or a polarity phrase
(支持 vs 不支持, free vs paid, full refund vs partial refund, ...). Units are normalised
to a common base, so 7 天 and 1 周 compare equal, and a mismatch is only reported when it
cannot be reconciled with any in-scope source claim:

* relative tolerance - ``|a - b| / max(|a|, |b|)`` must exceed
  ``contradiction.relative_tolerance`` (0.2 by default, so 0.4 vs 0.6 is reported);
* absolute tolerance - a difference at or below ``contradiction.absolute_tolerance`` in
  the normalised unit (seconds, days, percentage points, count) is treated as rounding;
* topic scope - the answer sentence and the source sentence must share a token, otherwise
  two unrelated numbers of the same document would be "contradicting" each other;
* reconciliation - if the source states the same value anywhere in scope, the claim is
  considered supported, not contradicted (a document that lists both 0.4 and 0.6 is not a
  contradiction for an answer that says 0.6).

Sentence boundaries come from :mod:`service.text_split`, so the answer sentences reported
here are the very sentences the trace shows, and the source sentence keeps its character
offsets for the UI to highlight.

Known limits (deliberate, so a finding stays defensible):

* Chinese numerals ("五十", "三成") are not parsed - only digits are claims;
* a range ("3-5 天", "3 到 5 天") is skipped rather than guessed, because which bound is the
  claim is ambiguous and guessing produces false positives;
* months are 30 days and years 365 days, so 1 个月 and 30 天 compare equal;
* the polarity table is a curated list of concepts (support, refund scope, cost, completion,
  availability); a concept that is not listed is simply not checked;
* a claim is compared against sentences sharing a topic token, so a mismatch phrased with
  entirely different vocabulary can stay unreported.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from config import config
from service.i18n import _
from service.logger import get_logger
from service.text_split import CITATION_MARKER_RE, split_sentence_spans

logger = get_logger('contradiction')

__all__ = ['Claim', 'ContradictionDetector', 'contradiction_detector']

# ---------------------------------------------------------------------------
# Numeric patterns. A date is consumed before the plain-number rule can steal its digits,
# money before percent ("50% of 100 元") and so on. Spans claimed by an earlier group are
# masked, so one number is never reported twice (the year of a date, for instance, must
# not come back as a plain number claim).
# ---------------------------------------------------------------------------

_NUMBER = r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?'

_MONEY_UNITS = {
    '元': 'CNY', '块': 'CNY', '人民币': 'CNY', 'rmb': 'CNY', 'cny': 'CNY', '¥': 'CNY',
    '美元': 'USD', '美金': 'USD', 'usd': 'USD', '$': 'USD',
    '欧元': 'EUR', 'eur': 'EUR', '€': 'EUR',
    '日元': 'JPY', 'jpy': 'JPY',
}
_MONEY_SCALES = {'百': 1e2, '千': 1e3, '万': 1e4, '亿': 1e8, 'k': 1e3, 'm': 1e6}

_DURATION_UNITS = {
    '秒': 1.0, 's': 1.0, 'sec': 1.0, 'secs': 1.0, 'second': 1.0, 'seconds': 1.0,
    '分钟': 60.0, 'min': 60.0, 'mins': 60.0, 'minute': 60.0, 'minutes': 60.0,
    '小时': 3600.0, 'h': 3600.0, 'hour': 3600.0, 'hours': 3600.0,
    '天': 86400.0, '日': 86400.0, 'day': 86400.0, 'days': 86400.0,
    '工作日': 86400.0, 'weekday': 86400.0, 'weekdays': 86400.0,  # 1 working day ~ 1 day
    '周': 604800.0, '星期': 604800.0, 'week': 604800.0, 'weeks': 604800.0,
    '个月': 2592000.0, '月': 2592000.0, 'month': 2592000.0, 'months': 2592000.0,
    '年': 31536000.0, 'year': 31536000.0, 'years': 31536000.0,
}

_LENGTH_UNITS = {
    '毫米': 0.001, 'mm': 0.001, '厘米': 0.01, 'cm': 0.01, '米': 1.0, 'm': 1.0,
    '千米': 1000.0, '公里': 1000.0, 'km': 1000.0,
    '英寸': 0.0254, 'inch': 0.0254, 'inches': 0.0254,
    '英尺': 0.3048, 'foot': 0.3048, 'feet': 0.3048,
}

_WEIGHT_UNITS = {
    '毫克': 0.001, 'mg': 0.001, '克': 1.0, 'g': 1.0,
    '千克': 1000.0, '公斤': 1000.0, 'kg': 1000.0,
    '斤': 500.0,  # conventional Chinese unit, documented for the zh locale
    '吨': 1e6, 't': 1e6, 'ton': 1e6, 'tons': 1e6,
    '磅': 453.592, 'lb': 453.592, 'lbs': 453.592,
}

_COUNT_UNITS = ('个', '件', '次', '份', '条', '项', '人', '名', '台', '套', '页', '张',
                '只', '位', '款', '种', '遍', '轮', '笔', '单')

# Characters that negate the phrase following them.
_NEGATION_MARKERS = ('不', '未', '无', '没', '非', '别')
_NEGATION_WORDS = ('not', 'no', 'never', 'without', 'unsupported', 'cannot')

_STOPWORDS = frozenset({
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'was', 'were', 'you',
    'your', 'our', 'can', 'will', 'shall', 'not', 'but', 'has', 'have', 'had', 'its',
    'his', 'her', 'their', 'they', 'then', 'than', 'when', 'where', 'which', 'who',
    'how', 'why', 'all', 'any', '每', '请', '您', '的', '了', '是', '在', '和', '与',
    '为', '有', '我', '们', '你', '他', '她', '它', '中', '上', '下', '以', '及',
})

_CJK_RUN_RE = re.compile(r'[\u4e00-\u9fff]+')
_ASCII_WORD_RE = re.compile(r'[a-z][a-z0-9_]{1,}')


@dataclass
class Claim:
    """One numeric (or calendar) assertion found in a sentence."""

    kind: str                     # number | percent | count | duration | length | weight | money | date
    value: float                  # normalised: seconds, metres, grams, percentage points, ...
    raw: str                      # the matched text, e.g. "0.4" or "7 天"
    start: int                    # offset of the match inside its sentence
    end: int
    unit: str = ''                # base unit family: number | percent | count | CNY | ...
    tokens: frozenset = field(default_factory=frozenset)   # topic tokens of the claim context
    year_known: bool = True       # dates only: False for "10月1日" without a year

    @property
    def family(self) -> str:
        """Key deciding whether two claims may be compared at all."""
        if self.kind == 'money':
            return f'money:{self.unit}'
        return self.kind


_DATE_PATTERNS = (
    re.compile(r'(?P<y>\d{4})\s*[-/年.]\s*(?P<m>\d{1,2})\s*[-/月.]\s*(?P<d>\d{1,2})\s*日?'),
    re.compile(r'(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日'),
)
_MONEY_SUFFIX_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?P<scale>[百千万亿])?\s*'
               r'(?P<unit>人民币|美元|美金|欧元|日元|元|块|rmb|cny|usd|eur|jpy|¥|\$|€)', re.IGNORECASE),
)
_MONEY_PREFIX_PATTERNS = (
    re.compile(r'(?P<unit>¥|\$|€)\s*(?P<num>' + _NUMBER + r')\s*(?P<scale>[百千万亿])?'),
)
_PERCENT_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?:%|％|个百分点)'),
    re.compile(r'百分之\s*(?P<num>' + _NUMBER + r')'),
)
_DURATION_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?P<unit>个?工作日|个月|小时|分钟|秒|天|日|周|星期|'
               r'月|年|hours?|minutes?|mins?|seconds?|secs?|days?|weeks?|months?|years?|'
               r'hour|minute|min|sec|day|week|month|year|h|s)(?![a-z0-9])', re.IGNORECASE),
)
_LENGTH_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?P<unit>毫米|厘米|千米|公里|英寸|英尺|米|'
               r'mm|cm|km|inch(?:es)?|feet|foot|m)(?![a-z0-9])', re.IGNORECASE),
)
_WEIGHT_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?P<unit>毫克|千克|公斤|克|吨|斤|磅|'
               r'mg|kgs?|tons?|lbs?|g|t)(?![a-z0-9])', re.IGNORECASE),
)
_COUNT_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')\s*(?P<unit>' + '|'.join(_COUNT_UNITS) + r')'),
)
_NUMBER_PATTERNS = (
    re.compile(r'(?P<num>' + _NUMBER + r')'),
)

# Ordered so that specific units win over the plain-number rule.
_NUMERIC_GROUPS = (
    ('date', _DATE_PATTERNS),
    ('money', _MONEY_SUFFIX_PATTERNS),
    ('money', _MONEY_PREFIX_PATTERNS),
    ('percent', _PERCENT_PATTERNS),
    ('duration', _DURATION_PATTERNS),
    ('length', _LENGTH_PATTERNS),
    ('weight', _WEIGHT_PATTERNS),
    ('count', _COUNT_PATTERNS),
    ('number', _NUMBER_PATTERNS),
)

# kind -> report type. The names on the right are part of the API contract the UI and the
# A/B comparison already consume, so they keep their historical spelling.
_KIND_TO_TYPE = {
    'percent': 'number_percentage',
    'money': 'amount_money',
    'duration': 'time_duration',
    'date': 'date_mismatch',
    'count': 'number_quantity',
    'number': 'number_quantity',
    'length': 'number_quantity',
    'weight': 'number_quantity',
    'status': 'status',
}


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text.replace(',', ''))
    except (TypeError, ValueError):
        return None


def _scope_tokens(text: str, skip: Tuple[int, int] = None) -> frozenset:
    """Topic tokens of a sentence (CJK bigrams + ASCII words), used for scope matching.

    The claim's own number is excluded via ``skip`` so "0.4" cannot be the only reason two
    sentences are considered to be about the same thing.
    """
    if skip:
        text = text[:skip[0]] + ' ' * (skip[1] - skip[0]) + text[skip[1]:]
    tokens = set()
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.add(run)
            continue
        for index in range(len(run) - 1):
            tokens.add(run[index:index + 2])
    for word in _ASCII_WORD_RE.findall(text.lower()):
        if len(word) >= 2:
            tokens.add(word)
    return frozenset(token for token in tokens if token not in _STOPWORDS)


def _overlaps(left: Tuple[int, int], right: Tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _is_negated(text: str, position: int) -> bool:
    """True when the phrase starting at ``position`` is negated by what precedes it."""
    prefix = text[max(0, position - 2):position]
    if any(marker in prefix for marker in _NEGATION_MARKERS):
        return True
    words = _ASCII_WORD_RE.findall(text[max(0, position - 12):position].lower())
    return bool(words) and words[-1] in _NEGATION_WORDS




def _in_a_range(text: str, start: int, end: int) -> bool:
    """True when the match is one bound of a range such as ``3-5 天`` or ``3 到 5 天``.

    A range is ambiguous for a strict comparison ("which bound is the claim?"), and reading one
    bound as a standalone value produces false positives, so range members are skipped: a source
    saying "4-6 天" must not be reported as contradicting an answer that says "3-5 天" just because
    the lower bounds differ.
    """
    separators = ('-', '~', '到', '至', '—')
    before = text[:start].rstrip()
    if before and before[-1] in separators and re.search(r'\d\s*$', before[:-1]):
        return True                                   # upper bound
    after = text[end:].lstrip()
    if after[:1] in separators and re.match(r'[-~到至—]\s*\d', after):
        return True                                   # lower bound
    return False


def _number_is_noise(text: str, start: int, end: int) -> bool:
    """Reject plain numbers that cannot carry a claim (versions, ids, list markers)."""
    before = text[start - 1] if start else ''
    after = text[end] if end < len(text) else ''
    if before in ('.', ':') or after in ('.', ':'):
        return True                      # "v1.2", "10:30", "1. 第一步"
    if before in ('v', 'V'):
        return True                      # version tag
    return False


def _claim_value(kind: str, match, text: str):
    """Return ``(value, unit, extra)`` in normalised base units, or ``(None, '', {})``."""
    groups = match.groupdict()
    if kind == 'date':
        try:
            year = int(groups['y']) if groups.get('y') else 2000
            parsed = date(year, int(groups['m']), int(groups['d']))
        except (TypeError, ValueError):
            return None, '', {}
        return float(parsed.toordinal()), 'date', {'year_known': bool(groups.get('y'))}

    number = _to_float(groups.get('num') or '')
    if number is None:
        return None, '', {}

    unit_text = (groups.get('unit') or '').lower()
    if kind == 'money':
        currency = _MONEY_UNITS.get(unit_text) or _MONEY_UNITS.get(groups.get('unit') or '')
        if not currency:
            return None, '', {}
        scale = _MONEY_SCALES.get((groups.get('scale') or '').lower(), 1.0)
        return number * scale, currency, {}
    if kind == 'percent':
        return number, 'percent', {}
    if kind == 'duration':
        factor = _DURATION_UNITS.get(unit_text)
        return (number * factor, 'duration', {}) if factor else (None, '', {})
    if kind == 'length':
        factor = _LENGTH_UNITS.get(unit_text)
        return (number * factor, 'length', {}) if factor else (None, '', {})
    if kind == 'weight':
        factor = _WEIGHT_UNITS.get(unit_text)
        return (number * factor, 'weight', {}) if factor else (None, '', {})
    if kind == 'count':
        return number, 'count', {}
    if _number_is_noise(text, match.start(), match.end()):
        return None, '', {}
    return number, 'number', {}


def _extract_claims(text: str) -> List[Claim]:
    """Extract every comparable claim from one sentence.

    Groups run in order (date, money, percent, duration, length, weight, count, plain
    number) and matched spans are masked, so the digits of a date never come back as a
    plain number claim.
    """
    if not text:
        return []
    masked = list(text)
    # Citation markers are not values: without this, "[文档1]" in an answer sentence would
    # be reported as the claim "1" and could contradict any number in the source.
    for marker in CITATION_MARKER_RE.finditer(text):
        for index in range(*marker.span()):
            masked[index] = '\x00'
    claims: List[Claim] = []
    for kind, patterns in _NUMERIC_GROUPS:
        for pattern in patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                if '\x00' in ''.join(masked[start:end]):
                    continue
                if _in_a_range(text, start, end):
                    continue
                value, unit, extra = _claim_value(kind, match, text)
                if value is None:
                    continue
                for index in range(start, end):
                    masked[index] = '\x00'
                claims.append(Claim(
                    kind=kind, value=value, raw=match.group(0).strip(),
                    start=start, end=end, unit=unit,
                    tokens=_scope_tokens(text, (start, end)), **extra,
                ))
    claims.sort(key=lambda claim: claim.start)
    return claims


# Polarity concepts: (name, positive phrases, negative phrases). Longer phrases are tested
# first, and a positive phrase swallowed by a negative one ("不支持全额退款") is discarded.
_POLARITY_CONCEPTS = (
    ('support', (
        ('不允许', '支持', '允许', '可以', '能够', 'allowed', 'permitted', 'supported'),
        ('不允许', '不支持', '不可以', '不能', 'not allowed', 'not permitted',
         'unsupported', 'forbidden', 'cannot', 'can not'),
    )),
    ('refund_scope', (
        ('全额退款', '全额', 'full refund', 'full amount'),
        ('部分退款', '不支持全额', '部分', 'partial refund', 'partial'),
    )),
    ('cost', (
        ('免费', 'free of charge', 'for free', 'no charge'),
        ('收费', '付费', 'charged', 'paid', 'fee'),
    )),
    ('completion', (
        ('已完成', '已处理', '已发货', '已退款', '已生效', 'completed', 'shipped', 'refunded'),
        ('未完成', '未处理', '未发货', '未退款', '尚未', 'pending', 'not shipped', 'not refunded'),
    )),
    ('availability', (
        ('无限', 'unlimited'),
        ('有限', 'limited'),
    )),
)


def _concept_polarity(text: str, concept) -> Optional[Tuple[str, str]]:
    """Return ``(polarity, matched_phrase)`` for one concept, or ``None`` when silent.

    Negative phrases are located first, because they swallow positive phrases they contain
    ("不支持全额退款" is negative for refund_scope, not positive because of "全额"), and a
    positive phrase introduced by a negation marker ("不可以") does not count either.
    """
    name, phrases = concept
    positives, negatives = phrases
    lowered = (text or '').lower()

    negative_spans: List[Tuple[int, int]] = []
    negative_matches: List[str] = []
    for phrase in negatives:
        start = lowered.find(phrase.lower())
        while start != -1:
            negative_spans.append((start, start + len(phrase)))
            negative_matches.append(phrase)
            start = lowered.find(phrase.lower(), start + 1)

    for phrase in sorted(positives, key=len, reverse=True):
        start = lowered.find(phrase.lower())
        if start == -1:
            continue
        span = (start, start + len(phrase))
        if _is_negated(lowered, start) or any(_overlaps(span, neg) for neg in negative_spans):
            continue
        return 'positive', phrase

    if negative_spans:
        return 'negative', max(negative_matches, key=len)
    return None


class ContradictionDetector:
    """Compare the claims of an answer against the retrieved context.

    Usage::

        contradictions = contradiction_detector.detect(answer, context_results, lang)
        contradiction_detector.attach_to_sentences(sentence_tracing, contradictions)
    """

    def __init__(self):
        self.reload_config()

    def reload_config(self):
        """(Re)read the ``contradiction:`` block, so thresholds stay in settings.yaml."""
        self._enabled = bool(config.get('contradiction.enabled', True))
        self._relative_tolerance = float(config.get('contradiction.relative_tolerance', 0.2))
        self._absolute_tolerance = float(config.get('contradiction.absolute_tolerance', 0.0))
        self._date_tolerance_days = float(config.get('contradiction.date_tolerance_days', 1))
        self._percent_point_tolerance = float(config.get('contradiction.percent_point_tolerance', 5.0))
        self._high_severity_ratio = float(config.get('contradiction.high_severity_ratio', 0.5))
        self._medium_severity_ratio = float(config.get('contradiction.medium_severity_ratio', 0.2))
        self._max_results = int(config.get('contradiction.max_results', 20))
        self._require_shared_topic = bool(config.get('contradiction.require_shared_topic', True))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, answer: str, context_results: List[Dict],
               lang: Optional[str] = None) -> List[Dict]:
        """Return the contradictions found between ``answer`` and the context.

        Args:
            answer: Final answer text.
            context_results: Chunks that were placed in the prompt.
            lang: i18n language for the explanation strings.

        Returns:
            Contradiction dicts (see the module docstring), most severe first, capped at
            ``contradiction.max_results``.
        """
        if not self._enabled or not answer or not context_results:
            return []

        sources = self._source_sentences(context_results)
        if not sources:
            return []

        found: List[Dict] = []
        for span in split_sentence_spans(answer):
            answer_tokens = _scope_tokens(span.text)
            claims = _extract_claims(span.text)
            if claims:
                found.extend(self._numeric_contradictions(span, claims, sources, lang))
            found.extend(self._polarity_contradictions(span, answer_tokens, sources, lang))

        return self._rank_and_cap(found)

    @staticmethod
    def attach_to_sentences(sentence_tracing: List[Dict],
                            contradictions: List[Dict]) -> int:
        """Flag the trace items whose sentence has a contradiction.

        The detector and the tracer share the splitter, so the sentence text matches
        exactly; the flag lets the UI mark the sentence itself instead of only listing the
        findings elsewhere. Returns the number of flagged sentences.
        """
        if not sentence_tracing or not contradictions:
            return 0
        by_sentence: Dict[str, List[Dict]] = {}
        for item in contradictions:
            by_sentence.setdefault(item.get('sentence', ''), []).append(item)

        flagged = 0
        for trace_item in sentence_tracing:
            hits = by_sentence.get(trace_item.get('sentence', ''))
            if hits:
                trace_item['contradictions'] = hits
                trace_item['has_contradiction'] = True
                flagged += 1
        return flagged


    def _source_sentences(self, context_results: List[Dict]) -> List[Dict]:
        """Flatten the context into sentences carrying their chunk id and offsets."""
        units: List[Dict] = []
        for result in context_results or []:
            text = result.get('text') or ''
            if not text:
                continue
            chunk_id = result.get('id')
            for span in split_sentence_spans(text):
                units.append({
                    'chunk_id': chunk_id,
                    'text': span.text,
                    'start': span.start,
                    'end': span.end,
                    'tokens': _scope_tokens(span.text),
                    'claims': _extract_claims(span.text),
                })
        return units

    def _is_consistent(self, claim: Claim, candidate: Claim) -> bool:
        """True when both claims can be read as the same statement."""
        delta = abs(claim.value - candidate.value)
        if claim.kind == 'date':
            return delta <= self._date_tolerance_days
        if delta <= self._absolute_tolerance:
            return True
        # Percentages also use an absolute floor in percentage points: a relative ratio
        # hides material changes on a small base, so 50% vs 60% would only be a 17%
        # relative difference and slip through the relative tolerance alone.
        if claim.kind == 'percent' and delta >= self._percent_point_tolerance:
            return False
        scale = max(abs(claim.value), abs(candidate.value), 1e-9)
        return (delta / scale) <= self._relative_tolerance

    def _numeric_contradictions(self, span, claims: List[Claim], sources: List[Dict],
                                lang: Optional[str]) -> List[Dict]:
        """Report the claims that no in-scope source claim can reconcile."""
        found: List[Dict] = []
        for claim in claims:
            candidates: List[Tuple[Dict, Claim]] = []
            for unit in sources:
                if self._require_shared_topic and not (claim.tokens & unit['tokens']):
                    continue
                for candidate in unit['claims']:
                    if candidate.family == claim.family:
                        candidates.append((unit, candidate))
            if not candidates:
                continue
            # Reconciliation: the source stating the same value anywhere in scope means the
            # claim is supported, not contradicted - a chunk listing both 0.4 and 0.6 is not
            # a contradiction for an answer that says 0.6.
            if any(self._is_consistent(claim, candidate) for _unit, candidate in candidates):
                continue
            unit, closest = min(candidates, key=lambda pair: abs(claim.value - pair[1].value))
            found.append(self._build_numeric_item(span, claim, unit, closest, lang))
        return found

    def _polarity_contradictions(self, span, answer_tokens: frozenset,
                                 sources: List[Dict], lang: Optional[str]) -> List[Dict]:
        """Report polarity phrases that flip between the answer and the source."""
        found: List[Dict] = []
        for unit in sources:
            if self._require_shared_topic and not (answer_tokens & unit['tokens']):
                continue
            for concept in _POLARITY_CONCEPTS:
                answer_side = _concept_polarity(span.text, concept)
                if not answer_side:
                    continue
                source_side = _concept_polarity(unit['text'], concept)
                if not source_side or answer_side[0] == source_side[0]:
                    continue
                found.append({
                    'sentence': span.text,
                    'sentence_start': span.start,
                    'sentence_end': span.end,
                    'type': 'status',
                    'kind': 'status',
                    'severity': 'high',
                    'concept': concept[0],
                    'answer_value': answer_side[1],
                    'source_value': source_side[1],
                    'answer_text': answer_side[1],
                    'source_text': source_side[1],
                    'unit': '',
                    'delta': 0.0,
                    'relative_delta': 1.0,
                    'source_chunk_id': unit['chunk_id'],
                    'source_sentence': unit['text'],
                    'source_start': None,
                    'source_end': None,
                    'matched_tokens': sorted(answer_tokens & unit['tokens'])[:8],
                    'detector': 'polarity',
                    'explanation': _('contradiction.status_mismatch', lang,
                                     answer_side[1], source_side[1]),
                })
        return found


    def _build_numeric_item(self, span, claim: Claim, unit: Dict, closest: Claim,
                            lang: Optional[str]) -> Dict:
        """Assemble one numeric contradiction report."""
        delta = abs(claim.value - closest.value)
        scale = max(abs(claim.value), abs(closest.value), 1e-9)
        # For dates the ordinals are huge, so a ratio would read as ~0; day deltas are what
        # a human compares, and that is what this field carries for date claims.
        relative = delta if claim.kind == 'date' else (delta / scale if scale else 0.0)
        if claim.kind == 'date':
            explanation = _('contradiction.date_mismatch', lang, claim.raw, closest.raw,
                            int(round(delta)))
        else:
            explanation = _('contradiction.value_mismatch', lang, claim.raw, closest.raw,
                            relative)
        return {
            'sentence': span.text,
            'sentence_start': span.start,
            'sentence_end': span.end,
            'type': _KIND_TO_TYPE.get(claim.kind, 'other'),
            'kind': claim.kind,
            'severity': self._severity(claim, delta, relative),
            'answer_value': claim.value,
            'source_value': closest.value,
            'answer_text': claim.raw,
            'source_text': closest.raw,
            'unit': claim.unit,
            'delta': round(delta, 6),
            'relative_delta': round(relative, 4),
            'source_chunk_id': unit['chunk_id'],
            'source_sentence': unit['text'],
            'source_start': unit['start'] + closest.start,
            'source_end': unit['start'] + closest.end,
            'matched_tokens': sorted(claim.tokens & unit['tokens'])[:8],
            'detector': 'structured',
            'explanation': explanation,
        }

    def _severity(self, claim: Claim, delta: float, relative: float) -> str:
        """Map the size of a mismatch onto low/medium/high."""
        if claim.kind == 'date':
            if delta >= 7:
                return 'high'
            return 'medium' if delta >= 2 else 'low'
        # A percentage can look small relatively while being material in points, so its
        # point delta is scored on the same 0..1 scale (25 points -> 0.5).
        score = max(relative, delta / 50.0) if claim.kind == 'percent' else relative
        if score >= self._high_severity_ratio:
            return 'high'
        return 'medium' if score >= self._medium_severity_ratio else 'low'

    def _rank_and_cap(self, found: List[Dict]) -> List[Dict]:
        """De-duplicate, order by severity then size, and apply ``max_results``."""
        severity_rank = {'high': 0, 'medium': 1, 'low': 2}
        unique: Dict[Tuple, Dict] = {}
        for item in found:
            key = (item.get('sentence', ''), item.get('source_chunk_id'),
                   item.get('kind'), str(item.get('answer_value')),
                   str(item.get('source_value')))
            if key not in unique:
                unique[key] = item
        ordered = sorted(
            unique.values(),
            key=lambda item: (severity_rank.get(item.get('severity'), 3),
                              -float(item.get('relative_delta') or 0.0)),
        )
        return ordered[:max(1, self._max_results)]


contradiction_detector = ContradictionDetector()

