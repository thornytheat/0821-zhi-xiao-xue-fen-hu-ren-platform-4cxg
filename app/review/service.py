from datetime import datetime, timezone
from hashlib import sha256
from time import monotonic

from app.business import DomainIssue
from app.config import RULE_VERSION
from app.modules.pipeline import run


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def execute(store, data):
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    report = run(data)
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic()-start)*1000,3), bytes=len(data), run_id=run_id)
    return store.register(run_id,report)


def review(store, run_id, values):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision','status','note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED','HELD') or not isinstance(note,str) or not (3<=len(note.strip())<=200):
        raise DomainIssue('REVIEW_FIELDS')
    current = store.load(run_id)
    if revision != current['revision']:
        raise DomainIssue('STALE_REVIEW')
    if current['status'] == 'APPROVED':
        raise DomainIssue('RUN_FINALIZED')
    if status == 'APPROVED' and not current['report']['balance']['releasable']:
        raise DomainIssue('UNRESOLVED_ISSUES')
    claims = _approval_claims(current) if status == 'APPROVED' else ()
    return store.revise(run_id,revision,status,note.strip(),now(),claims=claims)


def _approval_claims(run):
    """把签发 run 中各业务组的获胜配对物化为跨批次占用。

    业务组（case）即同一申请的补录作用域；已签发配对此后冻结。
    """
    claims = []
    for case in run['report'].get('cases', []):
        measure = case.get('measure') or {}
        pairs = case.get('pairs') or measure.get('pairs', [])
        for pair in pairs:
            claims.append({
                'application_id': str(case['case']),
                'supplement_id': run['id'],
                'case_id': str(case['case']),
                'external_course': pair['external_course'],
                'local_course': pair['local_course'],
                'credits': pair.get('credits', 0),
                'priority': pair.get('priority', 0),
            })
    return claims


# 兼容从 app.review.service 直接导入补录签发用例的测试/调用方
from app.supplement.service import (  # noqa: E402,F401
    application_summary, evaluate_supplement, get_supplement, issue_supplement,
    list_supplements, plan_supplement, recalculate,
)
