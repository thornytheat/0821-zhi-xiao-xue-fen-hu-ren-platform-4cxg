import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import DomainIssue

SCHEMA = '''CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT
);'''

SUPPLEMENT_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS supplements (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    receipt_key TEXT,
    fingerprint TEXT NOT NULL DEFAULT '',
    conclusion TEXT NOT NULL,
    reason TEXT,
    credits INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL,
    report TEXT NOT NULL
);''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS uq_supplements_receipt
    ON supplements(application_id, receipt_key) WHERE receipt_key IS NOT NULL;''',
    '''CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id TEXT NOT NULL,
    supplement_id TEXT NOT NULL,
    case_id TEXT NOT NULL DEFAULT '',
    external_course TEXT NOT NULL,
    local_course TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS uq_claims_app_external
    ON claims(application_id, external_course);''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS uq_claims_app_local
    ON claims(application_id, local_course);''',
    '''CREATE INDEX IF NOT EXISTS idx_claims_supplement
    ON claims(supplement_id);''',
)


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute(SCHEMA)
            for statement in SUPPLEMENT_SCHEMA:
                db.execute(statement)
            yield db

    def register(self, run_id, report):
        with self.connect() as db:
            db.execute('INSERT INTO runs(id,report,created) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                       (run_id, json.dumps(report, ensure_ascii=False), report['manifest']['created']))
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        result = dict(row)
        result['report'] = json.loads(result['report'])
        return result

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50')]

    def revise(self, run_id, revision, status, note, now, claims=()):
        try:
            with self.connect() as db:
                cursor = db.execute(
                    'UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 WHERE id=? AND revision=?',
                    (status, note, now, run_id, revision))
                if cursor.rowcount != 1:
                    raise DomainIssue('STALE_REVIEW')
                for claim in claims:
                    db.execute(
                        '''INSERT INTO claims(application_id,supplement_id,case_id,
                           external_course,local_course,credits,priority,created)
                           VALUES (?,?,?,?,?,?,?,?)''',
                        (claim['application_id'], claim['supplement_id'], claim.get('case_id', ''),
                         str(claim['external_course']), str(claim['local_course']),
                         int(claim.get('credits', 0)), int(claim.get('priority', 0)), now))
        except sqlite3.IntegrityError as error:
            conflict = _claim_conflict(error)
            if conflict is not None:
                issue = DomainIssue('COURSE_CLAIMED')
                issue.conflict = conflict
                raise issue from error
            raise
        return self.load(run_id)

    # ------------------------------------------------------------------
    # 补录批次与跨批次配对占用
    # ------------------------------------------------------------------

    def save_supplement(self, item):
        """写入一条补录批次结论（不写 claims）。"""
        with self.connect() as db:
            db.execute(
                '''INSERT INTO supplements(id,application_id,receipt_key,fingerprint,
                   conclusion,reason,credits,created,report)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING''',
                (item['id'], item['application_id'], item.get('receipt_key'),
                 item.get('fingerprint', ''), item['conclusion'], item.get('reason'),
                 int(item.get('credits', 0)), item['created'],
                 json.dumps(item, ensure_ascii=False)))
        return self.load_supplement(item['id'])

    def commit_issuance(self, item, claims):
        """原子写入补录结论和本次签发的配对。

        撞唯一占用索引时抛 ``sqlite3.IntegrityError``，由服务层翻译。
        """
        with self.connect() as db:
            db.execute(
                '''INSERT INTO supplements(id,application_id,receipt_key,fingerprint,
                   conclusion,reason,credits,created,report)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                (item['id'], item['application_id'], item.get('receipt_key'),
                 item.get('fingerprint', ''), item['conclusion'], item.get('reason'),
                 int(item.get('credits', 0)), item['created'],
                 json.dumps(item, ensure_ascii=False)))
            for claim in claims:
                db.execute(
                    '''INSERT INTO claims(application_id,supplement_id,case_id,
                       external_course,local_course,credits,priority,created)
                       VALUES (?,?,?,?,?,?,?,?)''',
                    (item['application_id'], item['id'], claim.get('case_id', ''),
                     str(claim['external_course']), str(claim['local_course']),
                     int(claim.get('credits', 0)), int(claim.get('priority', 0)),
                     item['created']))
        return self.load_supplement(item['id'])

    def load_supplement(self, supplement_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM supplements WHERE id=?', (supplement_id,)).fetchone()
        if row is None:
            raise DomainIssue('SUPPLEMENT_NOT_FOUND')
        return _hydrate_supplement(row)

    def find_receipt(self, application_id, receipt_key):
        if not receipt_key:
            return None
        with self.connect() as db:
            row = db.execute(
                'SELECT * FROM supplements WHERE application_id=? AND receipt_key=?',
                (application_id, receipt_key)).fetchone()
        return _hydrate_supplement(row) if row is not None else None

    def find_fingerprint(self, application_id, fingerprint):
        if not fingerprint:
            return None
        with self.connect() as db:
            row = db.execute(
                '''SELECT * FROM supplements WHERE application_id=? AND fingerprint=?
                   AND conclusion='ISSUED' ORDER BY created DESC LIMIT 1''',
                (application_id, fingerprint)).fetchone()
        return _hydrate_supplement(row) if row is not None else None

    def list_supplements(self, application_id=None, limit=50):
        query = 'SELECT * FROM supplements'
        args = ()
        if application_id is not None:
            query += ' WHERE application_id=?'
            args = (application_id,)
        query += ' ORDER BY created DESC,id LIMIT ?'
        args = (*args, limit)
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [_hydrate_supplement(row) for row in rows]

    def list_claims(self, application_id=None):
        query = 'SELECT * FROM claims'
        args = ()
        if application_id is not None:
            query += ' WHERE application_id=?'
            args = (application_id,)
        query += ' ORDER BY id'
        with self.connect() as db:
            return [dict(r) for r in db.execute(query, args).fetchall()]

    def list_applications(self):
        with self.connect() as db:
            rows = db.execute(
                '''SELECT application_id,
                          COUNT(DISTINCT supplement_id) AS supplement_count,
                          COUNT(*) AS claim_count,
                          COALESCE(SUM(credits),0) AS credits
                   FROM claims GROUP BY application_id ORDER BY application_id''').fetchall()
            apps = [dict(r) for r in rows]
            extra = db.execute(
                '''SELECT DISTINCT application_id FROM supplements
                   WHERE application_id NOT IN (SELECT application_id FROM claims)''').fetchall()
        known = {app['application_id'] for app in apps}
        for row in extra:
            if row['application_id'] not in known:
                apps.append(dict(application_id=row['application_id'],
                                 supplement_count=0, claim_count=0, credits=0))
        return sorted(apps, key=lambda app: app['application_id'])

    # 常见命名别名，方便不同调用约定
    get_supplement = load_supplement
    get = load_supplement
    supplements = list_supplements
    claims_for = list_claims
    list_batches = list_supplements


def _hydrate_supplement(row):
    # 报告列保存完整结论；签发/重试都经 JSON 往返，保证两次结果严格相等。
    return json.loads(row['report'])


def _claim_conflict(error):
    text = str(error)
    if 'claims.application_id, claims.external_course' in text or 'uq_claims_app_external' in text:
        return 'external_course'
    if 'claims.application_id, claims.local_course' in text or 'uq_claims_app_local' in text:
        return 'local_course'
    if 'claims' in text and 'UNIQUE' in text.upper():
        return 'course'
    return None
