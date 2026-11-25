from flask import jsonify

LABELS = {
    'FILE_TOO_LARGE': '文件超过 256 KiB', 'FILE_EMPTY': '文件没有内容', 'FILE_BLANK': '文件只有空白',
    'HEADER_ONLY': '文件只有标题行', 'HEADER_MISMATCH': '列名与字段字典不一致', 'ENCODING_INVALID': '请使用 UTF-8 CSV',
    'CSV_MALFORMED': 'CSV 引号或行格式异常', 'ROW_LIMIT': '文件超过 2000 行', 'FILE_REQUIRED': '请选择 CSV 文件',
    'RUN_NOT_FOUND': '批次不存在', 'REVIEW_FIELDS': '请填写 3 至 200 字复核意见', 'STALE_REVIEW': '批次已更新，请刷新',
    'RUN_FINALIZED': '批次已签发', 'UNRESOLVED_ISSUES': '存在待处理差异', 'SAMPLE_UNKNOWN': '样例不存在', 'STAGE_UNKNOWN': '处理阶段不存在',
    'SUPPLEMENT_NOT_FOUND': '补录批次不存在', 'APPLICATION_REQUIRED': '请提供申请编号',
    'NO_BUSINESS_CASE': '文件中没有可核算的业务组', 'STORE_REQUIRED': '缺少工作台存储',
    'COURSE_CLAIMED': '课程已被其他批次抵扣，需重新核算', 'RECEIPT_CONFLICT': '回执键对应不同内容，请更换回执编号',
    'RECALCULATE': '配对撞课，请按最新已签发课程重新核算',
}

NOT_FOUND = {'RUN_NOT_FOUND', 'SAMPLE_UNKNOWN', 'STAGE_UNKNOWN', 'SUPPLEMENT_NOT_FOUND'}
CONFLICT = {'STALE_REVIEW', 'RUN_FINALIZED', 'UNRESOLVED_ISSUES', 'COURSE_CLAIMED',
            'RECEIPT_CONFLICT', 'RECALCULATE'}


def response(error):
    code = error.code
    status = 404 if code in NOT_FOUND else 409 if code in CONFLICT else 413 if code == 'FILE_TOO_LARGE' else 422
    body = dict(code=str(code), message=LABELS.get(str(code), LABELS.get(code, code)))
    outcome = getattr(error, 'outcome', None)
    if isinstance(outcome, dict):
        # 把签发结论/原因一并带给调用方，便于工作台直接呈现
        body.update(outcome)
        body['code'] = str(code)
        body['message'] = LABELS.get(str(code), body.get('message', str(code)))
    return jsonify(body), status
