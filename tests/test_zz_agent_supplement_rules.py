"""补录课程：冻结已签发配对、跨批次唯一抵扣、撞课换组、并发裁决、回执幂等与结论查询。"""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.business import DomainIssue
from app.intake.schema import HEADER
from app.main import create_app
from app.review.service import execute, review
from app.supplement.service import (
    application_summary, evaluate_supplement, get_supplement,
    issue_supplement, list_supplements, recalculate,
)
from conftest import csv_data


def row(record, case, kind, ext=0, local=0, credits=0, priority=0, note=''):
    return [record, case, kind, ext, local, credits, priority, note]


def first_batch(case='APP1'):
    # 与 ready.csv 的 0001 同构：最优 1->2(6) + 2->1(4) = 10，达到最低 10
    return [
        row('a1', case, 'external', ext=1, credits=6),
        row('a2', case, 'external', ext=2, credits=4),
        row('a3', case, 'required', local=1, credits=4),
        row('a4', case, 'required', local=2, credits=6),
        row('a5', case, 'equivalence', 1, 1, 4, 1),
        row('a6', case, 'equivalence', 1, 2, 6, 1),
        row('a7', case, 'equivalence', 2, 1, 4, 1),
        row('a8', case, 'authorization', credits=10),
    ]


def reroute_supplement(case='APP1'):
    # 原最优会用到被锁的 1/2；剩余 3->3=5 正好达到本批最低 5
    return [
        row('b1', case, 'external', ext=1, credits=5),
        row('b2', case, 'external', ext=3, credits=5),
        row('b3', case, 'required', local=2, credits=5),
        row('b4', case, 'required', local=3, credits=5),
        row('b5', case, 'equivalence', 1, 2, 5),
        row('b6', case, 'equivalence', 1, 3, 5),
        row('b7', case, 'equivalence', 3, 2, 5),
        row('b8', case, 'equivalence', 3, 3, 5),
        row('b9', case, 'authorization', credits=5),
    ]


def single_pair_batch(case, course=9, floor=3, tag='x'):
    return [
        row(f'{tag}1', case, 'external', ext=course, credits=3),
        row(f'{tag}2', case, 'required', local=course, credits=3),
        row(f'{tag}3', case, 'equivalence', course, course, 3),
        row(f'{tag}4', case, 'authorization', credits=floor),
    ]


# ---------------------------------------------------------------------------
# 1) 尽量多认学分 + 首次签发
# ---------------------------------------------------------------------------

def test_first_batch_maximizes_and_issues(store):
    out = issue_supplement(store, 'APP1', csv_data(first_batch()), 'r1')
    assert str(out['status']) == 'ISSUED'
    assert out['issued'] is True
    assert out['credits'] == 10
    assert sorted((p['external_course'], p['local_course']) for p in out['pairs']) == [(1, 2), (2, 1)]
    assert len(store.list_claims('APP1')) == 2


# ---------------------------------------------------------------------------
# 2) 已签发配对继续有效，不被补录重算改掉
# ---------------------------------------------------------------------------

def test_issued_pairs_are_frozen(store):
    first = issue_supplement(store, 'APP1', csv_data(first_batch()), 'r1')
    before = sorted(store.list_claims('APP1'), key=lambda c: c['id'])
    issue_supplement(store, 'APP1', csv_data(reroute_supplement()), 'r2')
    after = sorted(store.list_claims('APP1'), key=lambda c: c['id'])
    # 最早的两条签发配对原样保留（学分、课程、归属批次都不变）
    assert [(c['external_course'], c['local_course'], c['credits'], c['supplement_id']) for c in after[:2]] == \
           [(c['external_course'], c['local_course'], c['credits'], c['supplement_id']) for c in before]
    assert after[0]['supplement_id'] == first['id']


def test_existing_run_approval_materializes_claims(store, clean):
    run = execute(store, clean)
    review(store, run['id'], dict(revision=0, status='APPROVED', note='整批复核通过'))
    claims = store.list_claims('0001')
    assert len(claims) == 2
    assert sum(c['credits'] for c in claims) == 10
    # 之后同 case 的补录必须避开已签发课程
    plan = evaluate_supplement(store, '0001', csv_data(reroute_supplement('0001')))
    assert 1 in [int(x) for x in plan['locked']['external_course']]


# ---------------------------------------------------------------------------
# 3) 同一外校课/校内课跨批次只能抵扣一次
# ---------------------------------------------------------------------------

def test_external_and_local_course_unique_across_batches(store):
    issue_supplement(store, 'APP', csv_data(single_pair_batch('APP', 9, tag='a')), 'ka')
    second = issue_supplement(store, 'APP', csv_data(single_pair_batch('APP', 9, tag='b')), 'kb')
    assert str(second['status']) == 'RECALCULATE'
    # 数据库层面该外校课/校内课都只有一条抵扣
    externals = [c for c in store.list_claims('APP') if c['external_course'] == '9']
    locals_ = [c for c in store.list_claims('APP') if c['local_course'] == '9']
    assert len(externals) == 1 and len(locals_) == 1


# ---------------------------------------------------------------------------
# 4) 撞已签发课程，但换组配对达到本批最低，仍可签发
# ---------------------------------------------------------------------------

def test_reroute_around_issued_courses_still_issues(store):
    issue_supplement(store, 'APP1', csv_data(first_batch()), 'r1')
    out = issue_supplement(store, 'APP1', csv_data(reroute_supplement()), 'r2')
    assert str(out['status']) == 'ISSUED'
    assert out['switched'] is True
    assert out['credits'] == 5 and out['expected'] == 5
    assert [(p['external_course'], p['local_course']) for p in out['pairs']] == [(3, 3)]
    # 原方案确实撞上了已签发课程
    assert out['collided'] is True and out['collisions']


def test_plan_shows_alternative_before_issue(store):
    issue_supplement(store, 'APP1', csv_data(first_batch()), 'r1')
    plan = plan = recalculate(store, 'APP1', csv_data(reroute_supplement()))
    assert plan['feasible'] is True and plan['value'] == 5


# ---------------------------------------------------------------------------
# 5) 两批并发撞同一门课：只有一批签成，另一批重新核算
# ---------------------------------------------------------------------------

def test_parallel_batches_one_winner_one_recalculate(store):
    start = Barrier(2)

    def attempt(i):
        payload = csv_data([
            *single_pair_batch('APP', 9, floor=6, tag=f'p{i}'),
            row(f'x{i}2', 'APP', 'external', ext=20 + i, credits=3),
            row(f'x{i}3', 'APP', 'required', local=20 + i, credits=3),
            row(f'x{i}4', 'APP', 'equivalence', 20 + i, 20 + i, 3),
        ])
        start.wait(3)
        return str(issue_supplement(store, 'APP', payload, f'parallel-{i}')['status'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ['ISSUED', 'RECALCULATE']
    contested = [c for c in store.list_claims('APP') if c['external_course'] == '9']
    assert len(contested) == 1


def test_parallel_raising_variant_has_domain_issue(store):
    start = Barrier(2)

    def attempt(i):
        start.wait(3)
        try:
            issue_supplement(store, 'RAPP', csv_data(single_pair_batch('RAPP', 5, tag=f'r{i}')),
                             f'rr-{i}', raising=True)
            return 'ISSUED'
        except DomainIssue as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(map(str, results)) == ['COURSE_CLAIMED', 'ISSUED']


# ---------------------------------------------------------------------------
# 6) 回执中断重试：同一回执返回同一结论，不重复计学分
# ---------------------------------------------------------------------------

def test_receipt_retry_does_not_double_count(store):
    payload = csv_data(first_batch())
    first = issue_supplement(store, 'APP1', payload, 'same-receipt')
    second = issue_supplement(store, 'APP1', payload, 'same-receipt')
    assert first == second
    assert second['id'] == first['id']
    assert getattr(second, 'replayed', False) is True
    claims = store.list_claims('APP1')
    assert len(claims) == 2
    summary = application_summary(store, 'APP1')
    assert summary['recognized_credits'] == 10
    assert len(list_supplements(store, 'APP1')) == 1


def test_same_content_without_receipt_is_idempotent(store):
    payload = csv_data(first_batch())
    first = issue_supplement(store, 'APP1', payload)
    second = issue_supplement(store, 'APP1', payload)
    assert first['id'] == second['id']
    assert len(store.list_supplements('APP1')) == 1
    assert len(store.list_claims('APP1')) == 2


def test_parallel_same_receipt_commits_once(store):
    payload = csv_data(first_batch('P1'))
    start = Barrier(4)

    def load():
        start.wait(3)
        return issue_supplement(store, 'P1', payload, 'one-receipt')

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: load(), range(4)))
    assert all(r['id'] == results[0]['id'] for r in results)
    assert len(store.list_supplements('P1')) == 1
    assert len(store.list_claims('P1')) == 2


# ---------------------------------------------------------------------------
# 7) 工作台可查补录批次的签发结论和原因
# ---------------------------------------------------------------------------

def test_supplement_conclusion_and_reason_are_queryable(store):
    issue_supplement(store, 'APP1', csv_data(first_batch()), 'r1')
    bad = csv_data(single_pair_batch('APP1', 50, floor=8))  # 只有 3 学分，最低 8
    out = issue_supplement(store, 'APP1', bad, 'r2')
    assert str(out['conclusion']) == 'CREDIT_SHORTFALL'
    assert str(out['reason']) == 'CREDIT_SHORTFALL'
    loaded = get_supplement(store, out['id'])
    assert str(loaded['conclusion']) == 'CREDIT_SHORTFALL' and loaded['reason']
    items = list_supplements(store, 'APP1')
    assert {str(i['conclusion']) for i in items} >= {'ISSUED', 'CREDIT_SHORTFALL'}


def test_collision_conclusion_has_recalculate_reason(store):
    issue_supplement(store, 'APP', csv_data(single_pair_batch('APP', 9, tag='a')), 'ka')
    out = issue_supplement(store, 'APP', csv_data(single_pair_batch('APP', 9, tag='b')), 'kb')
    assert str(out['conclusion']) == 'RECALCULATE'
    assert 'CLAIM' in str(out['reason']) or str(out['reason']) == 'COURSE_CLAIMED'
    assert out['requires_recalculation'] is True or out['collided'] is True


# ---------------------------------------------------------------------------
# HTTP / 工作台接口
# ---------------------------------------------------------------------------

@pytest.fixture
def web_client(tmp_path):
    return create_app(tmp_path / 'web.db').test_client()


def test_http_issue_and_query_application(web_client):
    payload = csv_data(reroute_supplement('HTTP1'))
    # 先签发基础批次
    first = csv_data(first_batch('HTTP1'))
    resp = web_client.post('/api/applications/HTTP1/supplements',
                           data={'receipt': 'r1', 'file': (io.BytesIO(first), 'a.csv')},
                           content_type='multipart/form-data')
    assert resp.status_code == 200 and resp.json['status'] == 'ISSUED'
    # 补录撞课换组成功
    resp = web_client.post('/api/supplements',
                           data={'application_id': 'HTTP1', 'receipt': 'r2',
                                 'file': (io.BytesIO(payload), 'b.csv')},
                           content_type='multipart/form-data')
    assert resp.status_code == 200 and resp.json['status'] == 'ISSUED'
    detail = web_client.get('/api/applications/HTTP1').json
    assert detail['claim_count'] == 3
    assert detail['recognized_credits'] == 15
    listing = web_client.get('/api/supplements?application_id=HTTP1')
    assert listing.status_code == 200 and len(listing.json['items']) == 2


def test_http_receipt_retry_returns_same_id(web_client):
    raw = csv_data(first_batch('H2'))

    def post():
        return web_client.post('/api/applications/H2/supplements',
                               data={'receipt': 'dup', 'file': (io.BytesIO(raw), 'a.csv')},
                               content_type='multipart/form-data')

    a = post()
    b = post()
    assert a.json['id'] == b.json['id']
    assert web_client.get('/api/applications/H2').json['claim_count'] == 2


def test_http_supplement_detail_404(web_client):
    assert web_client.get('/api/supplements/nope').status_code == 404


def test_http_json_records_payload(web_client):
    records = first_batch('J1')
    resp = web_client.post('/api/supplements', json={
        'application_id': 'J1', 'receipt': 'j1', 'records': records})
    assert resp.status_code == 200, resp.get_json()
    assert resp.json['status'] == 'ISSUED' and resp.json['credits'] == 10
