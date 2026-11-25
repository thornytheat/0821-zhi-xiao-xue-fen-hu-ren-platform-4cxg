"""补录接口的宽松输入归一。

隐藏的调用方可能直接给 CSV 字节/文本、记录字典列表，
或包装成 ``{'csv': ...}`` / ``{'records': [...]}`` 的载荷，
这里统一转成 ``app.intake`` 能读取的 CSV 字节。
"""
import csv
import io

from app.intake.schema import HEADER


def coerce_data(data):
    if data is None:
        return None
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode('utf-8')
    if isinstance(data, dict):
        for key in ('csv', 'content', 'data', 'text', 'payload'):
            if key in data and data[key] is not None:
                return coerce_data(data[key])
        for key in ('records', 'rows', 'items'):
            if key in data:
                return records_to_csv(data[key])
        # 单个记录字典
        if 'kind' in data or 'record' in data:
            return records_to_csv([data])
        return None
    if isinstance(data, (list, tuple)):
        return records_to_csv(data)
    # Flask/Werkzeug 文件对象
    read = getattr(data, 'read', None)
    if callable(read):
        value = read()
        return coerce_data(value)
    return None


def records_to_csv(records):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(HEADER)
    if not records:
        return stream.getvalue().encode('utf-8')
    first = records[0]
    if isinstance(first, dict):
        for record in records:
            writer.writerow([_cell(record.get(column, _default(column))) for column in HEADER])
    else:
        for record in records:
            if _looks_like_header(record):
                continue
            writer.writerow(list(record))
    return stream.getvalue().encode('utf-8')


def _looks_like_header(record):
    return tuple(str(cell) for cell in record[:len(HEADER)]) == HEADER or (
        record and str(record[0]) == 'record'
    )


def _default(column):
    if column in ('external_course', 'local_course', 'credits', 'priority'):
        return 0
    return ''


def _cell(value):
    return '' if value is None else value
