from flask import Blueprint, Response, current_app, jsonify, request

from app.business import DomainIssue
from app.evidence.render import artifact
from app.inputs import read_input
from app.modules.pipeline import run
from app.review.service import execute, review
from app.supplement.service import issue_supplement

api = Blueprint('api',__name__,url_prefix='/api')
STAGES = {'intake':1,'prepare':2,'calculate':3,'policy':4,'balance':5}


@api.post('/stages/<stage>')
def preview(stage):
    if stage not in STAGES:
        raise DomainIssue('STAGE_UNKNOWN')
    return jsonify(run(read_input(),STAGES[stage]))


def _supplement_hint():
    """若调用方在 /api/runs 上携带申请/回执信息，则按补录签发处理。"""
    values = request.values
    application_id = values.get('application_id') or values.get('app_id') or values.get('application')
    receipt = (values.get('receipt') or values.get('receipt_id')
               or values.get('idempotency_key') or values.get('request_id'))
    if request.is_json:
        body = request.get_json(silent=True) or {}
        application_id = application_id or body.get('application_id') or body.get('app_id')
        receipt = receipt or body.get('receipt') or body.get('idempotency_key')
    if application_id or receipt:
        return application_id, receipt
    return None


@api.route('/runs',methods=['GET','POST'])
def runs():
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(items=store.list())
    hint = _supplement_hint()
    if hint is not None:
        application_id, receipt = hint
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            data = payload if isinstance(payload, list) else (
                payload.get('records') or payload.get('csv') or payload.get('data') or payload)
        else:
            data = read_input()
        return jsonify(issue_supplement(
            store, data, application_id=application_id, receipt=receipt))
    return jsonify(execute(store,read_input()))


@api.route('/runs/<run_id>', methods=['GET','PATCH'])
def batch(run_id):
    store = current_app.extensions['runs']
    if request.method == 'GET':
        return jsonify(store.load(run_id))
    return jsonify(review(store,run_id,request.get_json(silent=True)))


@api.get('/runs/<run_id>/evidence')
def export(run_id):
    report = current_app.extensions['runs'].load(run_id)
    return Response(artifact(report),mimetype='application/json', headers={
        'Content-Disposition':f'attachment; filename="review-{report["id"][:12]}.json"',
        'Cache-Control':'no-store',
    })
