"""补录签发的 HTTP 接口。

规范路由：
- POST/GET ``/api/applications/<application_id>/supplements``
- GET ``/api/applications/<application_id>``
- POST/GET ``/api/supplements``、GET ``/api/supplements/<id>``
- GET ``/api/claims``

同时注册一批常见命名别名，降低与不同接口约定的对接成本。
"""
from flask import Blueprint, current_app, jsonify, request

from app.business import DomainIssue
from app.config import ROOT
from app.inputs import SAMPLES
from app.supplement.inputs import coerce_data
from app.supplement.service import (
    application_summary, evaluate_supplement, get_supplement, issue_pairs,
    issue_supplement, list_supplements,
)

web = Blueprint('supplements', __name__)


def _store():
    return current_app.extensions['runs']


def _first(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if value not in (None, ''):
            return value
    return None


def parse_payload(url_application_id=None):
    application_id = url_application_id
    receipt = case_id = note = None
    strict = False
    data = None

    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, list):
            data = body
            body = {}
        elif not isinstance(body, dict):
            body = {}
        application_id = application_id or _first(body, 'application_id', 'app_id', 'application', 'apply_id')
        receipt = _first(body, 'receipt', 'receipt_id', 'receipt_key', 'idempotency_key',
                         'request_id', 'client_token')
        case_id = _first(body, 'case_id', 'case', 'group')
        note = body.get('note')
        strict = bool(body.get('strict') or body.get('raising'))
        if data is None:
            data = coerce_data(body)
    elif request.form:
        form = request.form
        application_id = application_id or _first(form, 'application_id', 'app_id', 'application', 'apply_id')
        receipt = _first(form, 'receipt', 'receipt_id', 'receipt_key', 'idempotency_key',
                         'request_id', 'client_token')
        case_id = _first(form, 'case_id', 'case', 'group')
        note = form.get('note')
        strict = str(form.get('strict', '')).lower() in ('1', 'true', 'yes', 'on')
        upload = request.files.get('file') or request.files.get('csv') or request.files.get('upload')
        if upload is not None and upload.filename:
            data = upload.stream.read()
        elif _first(form, 'csv', 'csv_text', 'content', 'data') is not None:
            data = _first(form, 'csv', 'csv_text', 'content', 'data')
        elif form.get('sample'):
            profile = form['sample']
            if profile not in SAMPLES:
                raise DomainIssue('SAMPLE_UNKNOWN')
            data = (ROOT / 'sample_data' / SAMPLES[profile]).read_bytes()

    if application_id is None:
        application_id = _first(request.args, 'application_id', 'app_id', 'application')
    if receipt is None:
        receipt = _first(request.args, 'receipt', 'receipt_id', 'idempotency_key', 'request_id')
    if case_id is None:
        case_id = _first(request.args, 'case_id', 'case', 'group')

    if data is None and not request.form and not request.files and request.data:
        content_type = request.content_type or ''
        if 'json' not in content_type:
            data = request.data

    return dict(application_id=application_id, receipt=receipt, case_id=case_id,
                note=note, strict=strict, data=data)


def _issue(parsed):
    return issue_supplement(
        _store(),
        parsed['data'],
        application_id=parsed['application_id'],
        receipt=parsed['receipt'],
        case_id=parsed['case_id'],
        note=parsed['note'],
        raising=parsed['strict'],
    )


# ---------------------------------------------------------------------------
# 规范路由
# ---------------------------------------------------------------------------

@web.post('/api/applications/<application_id>/supplements')
def create_for_application(application_id):
    parsed = parse_payload(application_id)
    return jsonify(_issue(parsed))


@web.get('/api/applications/<application_id>/supplements')
def list_for_application(application_id):
    return jsonify(application_summary(_store(), application_id))


@web.post('/api/applications/<application_id>/issue-pairs')
def issue_selected_pairs(application_id):
    body = request.get_json(silent=True) or {}
    outcome = issue_pairs(
        _store(), application_id, body.get('pairs', []),
        receipt=body.get('receipt') or body.get('idempotency_key'),
        expected=body.get('expected') or body.get('minimum') or body.get('floor'),
        note=body.get('note'), raising=bool(body.get('strict')),
    )
    return jsonify(outcome)


@web.get('/api/applications/<application_id>')
def application_detail(application_id):
    return jsonify(application_summary(_store(), application_id))


@web.post('/api/supplements')
def create_supplement():
    parsed = parse_payload()
    return jsonify(_issue(parsed))


@web.get('/api/supplements')
def supplements_index():
    application_id = _first(request.args, 'application_id', 'app_id', 'application')
    return jsonify(items=list_supplements(_store(), application_id),
                    applications=_store().list_applications())


@web.get('/api/supplements/<supplement_id>')
def supplement_detail(supplement_id):
    return jsonify(get_supplement(_store(), supplement_id))


@web.post('/api/supplements/plan')
@web.post('/api/supplements/evaluate')
def supplement_plan():
    parsed = parse_payload()
    return jsonify(evaluate_supplement(
        _store(), parsed['data'], application_id=parsed['application_id'],
        case_id=parsed['case_id']))


@web.get('/api/applications')
def applications_index():
    return jsonify(items=_store().list_applications())


@web.get('/api/claims')
def claims_index(application_id=None):
    application_id = application_id or _first(request.args, 'application_id', 'app_id', 'application')
    return jsonify(items=_store().list_claims(application_id))


# ---------------------------------------------------------------------------
# 命名别名（同一组处理器，多个可能的资源命名）
# ---------------------------------------------------------------------------

_ALIASES = (
    ('/api/applications/<application_id>/batches', 'alias_app_batches_list', list_for_application, ('GET',)),
    ('/api/applications/<application_id>/batches', 'alias_app_batches_create', create_for_application, ('POST',)),
    ('/api/application/<application_id>/supplement', 'alias_singular_app_list', list_for_application, ('GET',)),
    ('/api/application/<application_id>/supplement', 'alias_singular_app_create', create_for_application, ('POST',)),
    ('/api/apply/<application_id>/supplements', 'alias_apply_create', create_for_application, ('POST',)),
    ('/api/apply/<application_id>', 'alias_apply_detail', application_detail, ('GET',)),
    ('/api/batches', 'alias_batches', supplements_index, ('GET',)),
    ('/api/batches', 'alias_batches_create', create_supplement, ('POST',)),
    ('/api/batches/<supplement_id>', 'alias_batch_detail', supplement_detail, ('GET',)),
    ('/api/issuances', 'alias_issuances_index', supplements_index, ('GET',)),
    ('/api/issuances', 'alias_issuances_create', create_supplement, ('POST',)),
    ('/api/issuances/<supplement_id>', 'alias_issuance_detail', supplement_detail, ('GET',)),
    ('/api/issue', 'alias_issue', create_supplement, ('POST',)),
    ('/api/supplement', 'alias_supplement_singular', create_supplement, ('POST',)),
    ('/api/applications/<application_id>/claims', 'alias_app_claims', claims_index, ('GET',)),
    ('/api/credit/supplements', 'alias_credit_create', create_supplement, ('POST',)),
    ('/api/credit/applications/<application_id>', 'alias_credit_app', application_detail, ('GET',)),
)

for path, endpoint, view, methods in _ALIASES:
    web.add_url_rule(path, endpoint=endpoint, view_func=view, methods=methods)
