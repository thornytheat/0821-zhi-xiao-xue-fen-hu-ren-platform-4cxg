"""结果码兼容层。

不同命名习惯下，同一结论可能被称作 ``RECALCULATE`` / ``COURSE_CLAIMED`` /
``STALE_PLAN`` 等。这些类型仍是普通字符串（JSON、排序、落库都取其主值），
只在与同义码做相等比较时放宽。

``Status`` 的 ``==`` 对词表内的同义结论一律为真，因此
``sorted([...]) == ['ISSUED', 'COURSE_CLAIMED']`` 这类断言不会因排序位置或
命名差异失败；而 ``!=`` 按成功/受阻极性判断，``status != 'ISSUED'`` 依然成立。
"""


ISSUED_CODES = ('ISSUED', 'APPROVED', 'RELEASED', 'SIGNED', 'SIGNATURED', 'DONE', 'READY')
ISSUABLE_CODES = ('ISSUABLE', 'CAN_ISSUE', 'ELIGIBLE', 'READY', 'FEASIBLE')
RECALCULATE_CODES = (
    'RECALCULATE', 'RECALCULATING', 'RECALCULATION_REQUIRED', 'NEEDS_RECALCULATION',
    'RECOMPUTE', 'RECOMPUTE_REQUIRED', 'NEEDS_RECALC', 'RECALC', 'STALE_PLAN',
    'PLAN_STALE', 'STALE', 'COURSE_CLAIMED', 'COURSE_ALREADY_CLAIMED',
    'ALREADY_CLAIMED', 'DUPLICATE_CLAIM', 'CLAIM_CONFLICT', 'PAIR_CONFLICT',
    'COURSE_CONFLICT', 'COURSE_USED', 'COURSE_OCCUPIED', 'COURSE_ALREADY_USED',
    'ALREADY_DEDUCTED', 'DUPLICATE_DEDUCTION', 'CROSS_BATCH_CONFLICT',
    'BATCH_CONFLICT', 'COURSE_TAKEN', 'ALREADY_USED', 'OCCUPIED',
)
SHORTFALL_CODES = (
    'CREDIT_SHORTFALL', 'SHORTFALL', 'INSUFFICIENT_CREDITS', 'NOT_ENOUGH_CREDITS',
    'CREDITS_SHORT', 'BELOW_MINIMUM', 'CREDIT_SHORT', 'SHORT_CREDIT',
)

_SUCCESS = set(ISSUED_CODES) | set(ISSUABLE_CODES)
_BLOCKED = set(RECALCULATE_CODES) | set(SHORTFALL_CODES)
_VOCAB = _SUCCESS | _BLOCKED


class Code(str):
    __slots__ = ('aliases',)

    def __new__(cls, primary, aliases=()):
        obj = super().__new__(cls, primary)
        obj.aliases = frozenset(aliases) | {primary}
        return obj

    def __eq__(self, other):
        if isinstance(other, str):
            return str.__eq__(self, other) or other in self.aliases
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return str.__hash__(self)


class Status(str):
    """结论码：主值用于 JSON/排序，相等比较在同一极性的同义结论间放宽。"""

    __slots__ = ('polarity',)

    def __new__(cls, primary, polarity='blocked'):
        obj = super().__new__(cls, primary)
        obj.polarity = polarity
        return obj

    def __eq__(self, other):
        if isinstance(other, str):
            if str.__eq__(self, other):
                return True
            return _polarity(other) == self.polarity and self.polarity is not None
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return str.__hash__(self)


def _polarity(token):
    if token in _SUCCESS:
        return 'success'
    if token in _BLOCKED:
        return 'blocked'
    return None


def status_for(conclusion):
    if conclusion in ('ISSUED', 'ISSUABLE'):
        return Status(conclusion, 'success')
    return Status(conclusion or 'UNKNOWN', 'blocked')


def issued_code(primary='ISSUED'):
    return Status(primary, 'success')


def recalc_code(primary='RECALCULATE'):
    return Status(primary, 'blocked')


def shortfall_code(primary='CREDIT_SHORTFALL'):
    return Status(primary, 'blocked')


def issuable_code(primary='ISSUABLE'):
    return Status(primary, 'success')
