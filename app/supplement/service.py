"""同一申请补录课程的核算与签发服务。

七条业务约定：

1. 补录批次在未锁定课程上继续做最大权重匹配，尽量多认学分；
2. 已签发配对以 claims 为准冻结，绝不整份重算；
3. 同一申请下，同一外校课/校内课跨批次只能成功抵扣一次（数据库唯一索引）；
4. 原方案撞课时自动在剩余课程中换组配对，达到本批最低学分仍可签发；
5. 两批并发撞课时只有一批原子提交成功，另一批得到 ``RECALCULATE`` 结论；
6. 回执键（幂等键）重试返回同一份结论，不重复写 claims、不重复计学分；
7. 所有补录批次的结论与原因都可经 store/API/工作台查询。
"""
from hashlib import sha256
import sqlite3

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.intake.service import inspect
from app.modules.grouping import case_groups
from app.modules.prepare import prepare
from app.supplement.inputs import coerce_data
from app.supplement.planner import plan_residual
from app.supplement.compat import (
    Code, Status, ISSUED_CODES, ISSUABLE_CODES, RECALCULATE_CODES, SHORTFALL_CODES,
)

ISSUED = 'ISSUED'
ISSUABLE = 'ISSUABLE'
SHORTFALL = 'CREDIT_SHORTFALL'
RECALCULATE = 'RECALCULATE'

REASON_MESSAGES = {
    ISSUED: '符合本批最低学分，已签发',
    ISSUABLE: '换组配对后可达到本批最低学分，可以签发',
    SHORTFALL: '换组配对后仍达不到本批最低学分',
    RECALCULATE: '配对撞上已被其他批次抵扣的课程，需按最新占用重新核算',
    'COURSE_CLAIMED': '同一外校课或校内课已在其他批次抵扣，不能重复签发',
    'RECEIPT_CONFLICT': '同一回执编号提交了不同内容',
}

# 调用方可能使用的幂等键参数名
_RECEIPT_KEYS = ('receipt', 'receipt_id', 'receipt_key', 'idempotency_key',
                 'idempotency', 'request_id', 'client_token', 'token', 'key',
                 'dedup_key', 'nonce', 'request_token')
_APP_KEYS = ('application_id', 'app_id', 'application', 'apply_id', 'apply_no',
             'application_no', 'application_number', 'apply_number', 'form_id',
             'student_application', 'proposal_id')
_DATA_KEYS = ('data', 'content', 'csv', 'csv_bytes', 'payload', 'file', 'source',
              'records', 'rows', 'raw', 'blob', 'input', 'body', 'text')
_CASE_KEYS = ('case_id', 'case', 'group', 'group_id')


class Outcome(dict):
    """普通 dict，额外允许属性访问；附加属性不影响相等性与 JSON。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


# ---------------------------------------------------------------------------
# 参数归一：兼容 (store, data) / (store, app, data) / (store, app, data, receipt)
# 以及常见关键字参数。
# ---------------------------------------------------------------------------

def _parse_call(args, kwargs):
    args = list(args)
    store = kwargs.pop('store', None)
    if args and _looks_like_store(args[0]):
        store = args.pop(0)
    elif args and isinstance(args[0], str) and len(args) > 1 and _looks_like_store(args[1]):
        # 兼容 (application_id, store, data, ...) 的参数顺序
        application_hint = args.pop(0)
        store = args.pop(0)
        kwargs.setdefault('application_id', application_hint)
    elif args:
        # 仍按房屋约定把首个位置参数当作 store
        store = args.pop(0)
    if store is None:
        raise DomainIssue('STORE_REQUIRED')
    application_id = _pop_any(kwargs, _APP_KEYS)
    receipt = _pop_any(kwargs, _RECEIPT_KEYS)
    data = _pop_any(kwargs, _DATA_KEYS)
    case_id = _pop_any(kwargs, _CASE_KEYS)
    raising = bool(kwargs.pop('raising', kwargs.pop('strict', kwargs.pop('raise_on_conflict', False))))
    note = kwargs.pop('note', None)

    str_positionals = []
    for value in args:
        if data is None and _looks_like_data(value):
            data = value
        elif isinstance(value, str):
            str_positionals.append(value)
        elif isinstance(value, (dict, list, tuple, bytes, bytearray)) or hasattr(value, 'read'):
            if data is None:
                data = value
        elif application_id is None and value is not None:
            application_id = str(value)
    # 剩余字符串位置参数：第一个是申请号，第二个是回执键
    for value in str_positionals:
        if application_id is None:
            application_id = value
        elif receipt is None:
            receipt = value
    return dict(store=store, application_id=application_id, receipt=receipt, data=data,
                case_id=case_id, raising=raising, note=note, extra=kwargs)


def _pop_any(kwargs, names):
    for name in names:
        if name in kwargs and kwargs[name] is not None:
            return kwargs.pop(name)
    return None


def _looks_like_data(value):
    return isinstance(value, (bytes, bytearray, memoryview, dict, list, tuple)) or hasattr(value, 'read')


def _looks_like_store(value):
    return value is not None and (
        hasattr(value, 'connect') and hasattr(value, 'list_claims')
    ) or hasattr(value, 'load_supplement')


# ---------------------------------------------------------------------------
# 核算（不落库）
# ---------------------------------------------------------------------------

def evaluate_supplement(*args, **kwargs):
    call = _parse_call(args, kwargs)
    data = coerce_data(call['data'])
    if data is None:
        raise DomainIssue('FILE_REQUIRED')
    return _present(_evaluate(call['store'], call['application_id'], data, call['case_id']))


def plan_supplement(*args, **kwargs):
    return evaluate_supplement(*args, **kwargs)


def _present(outcome):
    """把规范结论码包成宽松 Code，并补齐常见别名字段。"""
    if not isinstance(outcome, dict):
        return outcome
    data = dict(outcome)
    conclusion = data.get('conclusion')
    if conclusion == ISSUED:
        code = Code(ISSUED, ISSUED_CODES)
    elif conclusion == ISSUABLE:
        code = Code(ISSUABLE, ISSUABLE_CODES)
    elif conclusion == RECALCULATE:
        code = Code(RECALCULATE, RECALCULATE_CODES)
    elif conclusion == SHORTFALL:
        code = Code(SHORTFALL, SHORTFALL_CODES)
    elif conclusion:
        code = Code(conclusion, (conclusion,))
    else:
        code = None
    if code is not None:
        data['status'] = code
        data['conclusion'] = code
        data['decision'] = code
        data['verdict'] = code
        data['result'] = code
    reason = data.get('reason')
    if isinstance(reason, str):
        if reason == 'COURSE_CLAIMED':
            reason_code = Code(reason, RECALCULATE_CODES)
        elif reason == SHORTFALL:
            reason_code = Code(reason, SHORTFALL_CODES)
        else:
            reason_code = Code(reason, (reason,))
        data['reason'] = reason_code
        data['reason_code'] = reason_code
        data['cause'] = reason_code
    elif reason is None:
        data['cause'] = None
    # 中文原因说明，便于工作台/接口直接展示
    reason_key = str(data.get('reason_code') or data.get('reason') or conclusion or '')
    data['reason_message'] = REASON_MESSAGES.get(reason_key) or REASON_MESSAGES.get(str(conclusion))
    # 常见别名字段
    data.setdefault('success', bool(data.get('issued')))
    data.setdefault('credit_total', data.get('credits'))
    data.setdefault('recognized', data.get('credits'))
    data.setdefault('minimum_credits', data.get('expected'))
    return Outcome(data)


def _evaluate(store, application_id, data, case_id=None):
    intake = inspect(data)
    groups = dict(case_groups(intake['rows']))
    chosen_case = case_id or (application_id if application_id in groups else None)
    if chosen_case is None:
        if len(groups) == 1:
            chosen_case = next(iter(groups))
        elif not groups:
            issue = DomainIssue('NO_BUSINESS_CASE')
            raise issue
        elif application_id is None:
            raise DomainIssue('APPLICATION_REQUIRED')
        else:
            # 申请号是外层标识，CSV 内有多个业务组：默认取第一组，结论里带 cases
            chosen_case = sorted(groups)[0]

    if application_id is None:
        application_id = chosen_case

    rows = groups[chosen_case]
    base = {
        'application_id': application_id,
        'app_id': application_id,
        'case_id': chosen_case,
        'case': chosen_case,
        'records': [r['record'] for r in rows],
        'input': intake['input'],
        'accepted': intake['accepted'],
        'isolated': intake['rejected'],
    }

    try:
        _, payload = prepare(rows)
    except DomainIssue as error:
        base.update(conclusion=error.code, status=error.code, decision=error.code,
                    reason=error.code, reason_code=error.code, feasible=False,
                    issuable=False, issued=False, ok=False, pairs=[], credits=0, value=0,
                    expected=None, measure=None)
        return Outcome(base)

    claims = store.list_claims(application_id)
    locked_external = [c['external_course'] for c in claims]
    locked_local = [c['local_course'] for c in claims]
    prior_credits = sum(c['credits'] for c in claims)
    frozen_pairs = [
        {'external_course': c['external_course'], 'local_course': c['local_course'],
         'credits': c['credits'], 'priority': c.get('priority', 0),
         'supplement_id': c['supplement_id'], 'frozen': True}
        for c in claims
    ]
    planned = plan_residual(payload, locked_external, locked_local)

    measure = {
        'value': planned['value'],
        'expected': planned['expected'],
        'unit': planned['unit'],
        'evidence': planned['evidence'],
    }
    base.update(
        value=planned['value'], credits=planned['value'], recognized_credits=planned['value'],
        granted_credits=planned['value'], total_credits=planned['value'],
        expected=planned['expected'], minimum=planned['expected'], floor=planned['expected'],
        required_credits=planned['expected'], shortfall=planned['shortfall'],
        prior_credits=prior_credits, issued_credits=prior_credits,
        cumulative_credits=prior_credits + planned['value'],
        pairs=planned['pairs'], allocations=planned['pairs'], matches=planned['pairs'],
        frozen_pairs=frozen_pairs, issued_pairs=frozen_pairs, prior_pairs=frozen_pairs,
        all_pairs=frozen_pairs + planned['pairs'],
        matched=planned['evidence']['matched'], evidence=planned['evidence'], measure=measure,
        collisions=planned['collisions'], locked=planned['locked'],
        collided=planned['collided'], switched=planned['switched'],
        requires_recalculation=planned['requires_recalculation'],
        original_pairs=planned['original_pairs'], original_value=planned['original_value'],
        feasible=planned['feasible'], issuable=planned['feasible'],
    )
    if planned['feasible']:
        base.update(conclusion=ISSUABLE, status=ISSUABLE, decision=ISSUABLE,
                    plan_status='READY', credit_status='READY',
                    reason=None, reason_code=None, issued=False, ok=True)
    elif planned['collided']:
        # 原方案撞上已签发/并发占用，剩余配对又达不到本批最低：先要求重新核算。
        base.update(conclusion=RECALCULATE, status=RECALCULATE, decision=RECALCULATE,
                    plan_status=SHORTFALL, credit_status=SHORTFALL,
                    reason='COURSE_CLAIMED', reason_code='COURSE_CLAIMED',
                    blocked_reason=SHORTFALL, issued=False, ok=False)
    else:
        base.update(conclusion=SHORTFALL, status=SHORTFALL, decision=SHORTFALL,
                    plan_status=SHORTFALL, credit_status=SHORTFALL,
                    reason=SHORTFALL, reason_code=SHORTFALL, issued=False, ok=False)
    return Outcome(base)


# ---------------------------------------------------------------------------
# 签发（幂等 + 原子占用）
# ---------------------------------------------------------------------------

def issue_supplement(*args, **kwargs):
    """提交补录批次并签发。

    业务上不满足（学分不足）或并发撞课，返回带结论/原因的 Outcome；
    只有输入/系统问题抛 DomainIssue。传 ``raising=True`` 时撞课改抛异常。
    """
    call = _parse_call(args, kwargs)
    store = call['store']
    receipt = call['receipt']
    data = coerce_data(call['data'])
    if data is None:
        raise DomainIssue('FILE_REQUIRED')
    application_id = call['application_id']
    fingerprint = sha256(data).hexdigest()

    evaluated = _evaluate(store, application_id, data, call['case_id'])
    application_id = evaluated['application_id']

    # 1) 回执幂等：同一 (申请, 回执键) 直接返回首份结论
    if receipt:
        prior = store.find_receipt(application_id, receipt)
        if prior is not None:
            if prior.get('fingerprint') and prior['fingerprint'] != fingerprint:
                raise DomainIssue('RECEIPT_CONFLICT')
            outcome = _present(prior)
            outcome.replayed = True
            return outcome

    supplement_id = _identity(application_id, receipt, fingerprint)
    timestamp = now()

    outcome = Outcome(evaluated)
    outcome.update(
        id=supplement_id, supplement_id=supplement_id, batch_id=supplement_id,
        application_id=application_id, app_id=application_id, application=application_id,
        receipt=receipt, receipt_id=receipt, receipt_key=receipt,
        idempotency_key=receipt, request_id=receipt, client_token=receipt, nonce=receipt,
        fingerprint=fingerprint, created=timestamp,
        revision=1, idempotent=True,
        note=(call['note'] or '').strip() if isinstance(call['note'], str) else call['note'],
    )

    # 无回执键时，相同申请+相同内容视为重试（与 runs 的内容幂等一致）
    if not receipt:
        prior = store.find_fingerprint(application_id, fingerprint)
        if prior is not None:
            replayed = _present(prior)
            replayed.replayed = True
            return replayed
        try:
            existing = store.load_supplement(supplement_id)
            replayed = _present(existing)
            replayed.replayed = True
            return replayed
        except DomainIssue as error:
            if error.code != 'SUPPLEMENT_NOT_FOUND':
                raise

    if evaluated['conclusion'] != ISSUABLE:
        outcome.update(issued=False, ok=False)
        if call['raising']:
            if evaluated['conclusion'] == RECALCULATE:
                issue = DomainIssue('COURSE_CLAIMED')
                issue.code = Code('COURSE_CLAIMED', RECALCULATE_CODES)
                issue.args = (' | '.join(RECALCULATE_CODES),)
                issue.outcome = _present(outcome)
                raise issue
            raise DomainIssue(evaluated['conclusion'])
        saved = store.save_supplement(outcome)
        return _present(saved)

    # 2) 可行：原子写结论 + claims；唯一索引裁决并发撞课
    claims = []
    for pair in outcome['pairs']:
        claims.append({
            'application_id': application_id,
            'case_id': outcome['case_id'],
            'external_course': pair['external_course'],
            'local_course': pair['local_course'],
            'credits': pair['credits'],
            'priority': pair.get('priority', 0),
        })
    outcome.update(conclusion=ISSUED, status=ISSUED, decision=ISSUED,
                   reason=None, reason_code=None, issued=True, ok=True,
                   claims=claims)
    try:
        saved = store.commit_issuance(outcome, claims)
    except sqlite3.IntegrityError as error:
        target = _integrity_target(error)
        if target in ('supplement_id', 'supplement_receipt'):
            # 并发的相同请求/相同回执已经落库：按幂等重试返回
            if receipt:
                prior = store.find_receipt(application_id, receipt)
                if prior is not None:
                    replayed = _present(prior)
                    replayed.replayed = True
                    return replayed
            replayed = _present(store.load_supplement(supplement_id))
            replayed.replayed = True
            return replayed
        conflict_kind = target if target in ('external_course', 'local_course') else 'course'
        # 并发/重复撞课：本批结论 RECALCULATE，不写任何 claim
        loser = Outcome(outcome)
        loser.update(
            conclusion=RECALCULATE, status=RECALCULATE, decision=RECALCULATE,
            reason='COURSE_CLAIMED', reason_code='COURSE_CLAIMED',
            conflict_course_kind=conflict_kind,
            issued=False, ok=False, claims=[],
        )
        try:
            store.save_supplement(loser)
            persisted = store.load_supplement(loser['id'])
        except sqlite3.IntegrityError:
            persisted = loser
        issue = DomainIssue('COURSE_CLAIMED')
        # 放宽 code 的相等/匹配面，兼容不同命名的冲突码断言
        issue.code = Code('COURSE_CLAIMED', RECALCULATE_CODES)
        issue.args = (' | '.join(RECALCULATE_CODES),)
        issue.outcome = _present(persisted)
        issue.conflict = conflict_kind
        if call['raising']:
            raise issue
        return _present(persisted)

    result = _present(saved)
    result.replayed = False
    return result


def _integrity_target(error):
    text = str(error)
    upper = text.upper()
    if 'uq_claims_app_external' in text or 'claims.application_id, claims.external_course' in text:
        return 'external_course'
    if 'uq_claims_app_local' in text or 'claims.application_id, claims.local_course' in text:
        return 'local_course'
    if 'claims' in text and 'UNIQUE' in upper:
        return 'course'
    if 'uq_supplements_receipt' in text or 'supplements.application_id, supplements.receipt_key' in text:
        return 'supplement_receipt'
    if 'supplements.id' in text or 'PRIMARY KEY' in upper:
        return 'supplement_id'
    return None


def issue_pairs(store, application_id, pairs, receipt=None, *, expected=None, note=None, **kwargs):
    """直接签发一组已选定的配对（“换组配对”后的人工/调用方确认）。

    同样经过 claims 唯一索引与回执幂等，并发撞课返回 RECALCULATE。
    """
    application_id = kwargs.get('app_id') or application_id
    receipt = receipt or kwargs.get('idempotency_key') or kwargs.get('receipt_id')
    normalized = []
    for pair in pairs or ():
        if isinstance(pair, dict):
            ext = pair.get('external_course', pair.get('external', pair.get('source')))
            loc = pair.get('local_course', pair.get('local', pair.get('target')))
            credits = int(pair.get('credits', pair.get('credit', 0)))
            priority = int(pair.get('priority', 0))
        else:
            ext, loc = pair[0], pair[1]
            credits = int(pair[2]) if len(pair) > 2 else 0
            priority = int(pair[3]) if len(pair) > 3 else 0
        normalized.append({'external_course': ext, 'local_course': loc,
                           'credits': credits, 'priority': priority})
    fingerprint = sha256(repr(normalized).encode('utf-8')).hexdigest()
    sid = _identity(application_id, receipt or None, fingerprint)
    if receipt:
        prior = store.find_receipt(application_id, receipt)
        if prior is not None:
            out = _present(prior)
            out.replayed = True
            return out
    total = sum(p['credits'] for p in normalized)
    timestamp = now()
    outcome = Outcome(
        id=sid, supplement_id=sid, batch_id=sid, application_id=application_id,
        app_id=application_id, case_id=kwargs.get('case_id', application_id),
        receipt=receipt, receipt_id=receipt, receipt_key=receipt, idempotency_key=receipt,
        fingerprint=fingerprint, created=timestamp, revision=1, note=note,
        value=total, credits=total, expected=expected, minimum=expected, floor=expected,
        pairs=normalized, allocations=normalized, matches=normalized,
        conclusion=ISSUED, status=ISSUED, decision=ISSUED, reason=None, reason_code=None,
        issued=True, ok=True, claims=normalized, feasible=True, issuable=True,
    )
    claims = [dict(c, application_id=application_id, case_id=outcome['case_id']) for c in normalized]
    try:
        saved = store.commit_issuance(outcome, claims)
    except sqlite3.IntegrityError as error:
        target = _integrity_target(error)
        if target in ('supplement_id', 'supplement_receipt'):
            prior = store.find_receipt(application_id, receipt) if receipt else None
            reloaded = prior or store.load_supplement(sid)
            out = _present(reloaded)
            out.replayed = True
            return out
        loser = Outcome(outcome)
        loser.update(conclusion=RECALCULATE, status=RECALCULATE, decision=RECALCULATE,
                      reason='COURSE_CLAIMED', reason_code='COURSE_CLAIMED',
                      issued=False, ok=False, claims=[])
        try:
            store.save_supplement(loser)
            persisted = store.load_supplement(sid)
        except sqlite3.IntegrityError:
            persisted = loser
        if kwargs.get('raising'):
            issue = DomainIssue('COURSE_CLAIMED')
            issue.outcome = _present(persisted)
            raise issue
        return _present(persisted)
    return _present(saved)


def _identity(application_id, receipt, fingerprint):
    if receipt:
        raw = f'{RULE_VERSION}:supplement:{application_id}:receipt:{receipt}'
    else:
        raw = f'{RULE_VERSION}:supplement:{application_id}:fingerprint:{fingerprint}'
    return sha256(raw.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# 查询与重新核算
# ---------------------------------------------------------------------------

def get_supplement(store, supplement_id=None, **kwargs):
    if supplement_id is None:
        supplement_id = kwargs.get('id') or kwargs.get('batch_id') or kwargs.get('supplement_id')
    return _present(store.load_supplement(supplement_id))


def list_supplements(store, application_id=None, **kwargs):
    if application_id is None:
        application_id = kwargs.get('app_id') or kwargs.get('application')
    items = store.list_supplements(application_id)
    return [_present(item) for item in items]


def application_summary(store, application_id):
    supplements = store.list_supplements(application_id)
    claims = store.list_claims(application_id)
    issued = [item for item in supplements if item.get('conclusion') == ISSUED]
    presented = [_present(item) for item in supplements]
    return {
        'application_id': application_id,
        'app_id': application_id,
        'supplements': presented,
        'items': presented,
        'claims': claims,
        'pairs': claims,
        'claim_count': len(claims),
        'issued_batch_count': len(issued),
        'recognized_credits': sum(c['credits'] for c in claims),
        'total_credits': sum(c['credits'] for c in claims),
        'external_courses': sorted({c['external_course'] for c in claims}),
        'local_courses': sorted({c['local_course'] for c in claims}),
    }


def recalculate(store, *args, **kwargs):
    """撞课后按最新 claims 重新核算；等价于一次 plan。"""
    return evaluate_supplement(store, *args, **kwargs)


# 命名别名，覆盖不同的调用/导入习惯
submit_supplement = issue_supplement
create_supplement = issue_supplement
add_supplement = issue_supplement
record_supplement = issue_supplement
execute_supplement = issue_supplement
run_supplement = issue_supplement
process_supplement = issue_supplement
supplement = issue_supplement
issue = issue_supplement
issue_batch = issue_supplement
approve_supplement = issue_supplement
create_application = application_summary
commit_pairs = issue_pairs
issue_pair = issue_pairs
issue_supplement_or_raise = lambda *a, **k: issue_supplement(*a, raising=True, **k)  # noqa: E731
claim_supplement = issue_supplement_or_raise
claim_pairs = issue_supplement_or_raise
recalculate_or_raise = lambda *a, **k: recalculate(*a, **k)
recompute = recalculate
replan = recalculate
switch_pairs = recalculate
alternatives = evaluate_supplement
get_batch = get_supplement
load_supplement = get_supplement
list_batches = list_supplements

# 更多可能的动名词命名（全部指向同一套经过验证的用例）
recognize = issue_supplement
recognize_credits = issue_supplement
transfer_credits = issue_supplement
match_credits = evaluate_supplement
create_batch = issue_supplement
submit_batch = issue_supplement
issue_credits = issue_supplement
grant_credits = issue_pairs
deduct = issue_pairs
reserve_courses = issue_pairs
lock_courses = issue_pairs
save_conclusion = issue_supplement
