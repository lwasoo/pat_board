"""Independent reading library and opt-in, locally cached PPUBS text."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading
import uuid

from dashboard_database import connect, SELECT_SQL
import dashboard_documents as documents
from dashboard_ppubs import PpubsClient, publication_number

_LOCK = threading.RLock()
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ppubs')
_JOBS = {}


def item(item_id, library):
    with closing(connect()) as conn:
        if library:
            row = conn.execute('SELECT id, title, publication_number AS publicationNumber FROM reading_items WHERE id=?', (item_id,)).fetchone()
        else:
            row = conn.execute(SELECT_SQL + ' WHERE p.id=?', (item_id,)).fetchone()
    if not row:
        raise FileNotFoundError('阅读条目不存在或已删除。')
    return dict(row)


def list_items():
    with closing(connect()) as conn:
        rows = [dict(row) for row in conn.execute('''SELECT r.id, r.title, r.publication_number AS publicationNumber,
            d.file_name AS fileName, 'library' AS scope FROM reading_items r
            LEFT JOIN reading_documents d ON d.patent_id=r.id ORDER BY r.id DESC''')]
        rows += [dict(row) for row in conn.execute('''SELECT p.id,p.title,p.publication_number AS publicationNumber,
            d.file_name AS fileName,'ledger' AS scope FROM patents p JOIN patent_documents d ON d.patent_id=p.id
            ORDER BY p.id DESC''')]
    return rows


def create_item(payload):
    title = str(payload.get('title') or '').strip()[:500]
    number = str(payload.get('publicationNumber') or '').strip()[:100]
    if not title and not number:
        raise ValueError('请输入标题或公开号。')
    with closing(connect()) as conn, conn:
        result = conn.execute('INSERT INTO reading_items(title,publication_number) VALUES(?,?)', (title or number, number))
        item_id = result.lastrowid
    return item(item_id, True)


def delete_item(item_id):
    with documents._LOCK:
        item(item_id, True)
        documents.delete_document(item_id, True)
        with _LOCK, closing(connect()) as conn, conn:
            conn.execute('DELETE FROM reading_items WHERE id=?', (item_id,))
            conn.execute('DELETE FROM reader_fulltexts WHERE item_key=?', (f'library:{item_id}',))
            _JOBS.pop(f'library:{item_id}', None)
    return {'deleted': True}


def cache_key(item_id, library):
    item(item_id, library)
    return ('library:' if library else 'ledger:') + str(item_id)


def _read_cache(key):
    with closing(connect()) as conn:
        row = conn.execute('SELECT payload FROM reader_fulltexts WHERE item_key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else {'status': 'empty', 'data': None}


def _save_cache(key, payload):
    with closing(connect()) as conn, conn:
        conn.execute('INSERT INTO reader_fulltexts(item_key,payload) VALUES(?,?) ON CONFLICT(item_key) DO UPDATE SET payload=excluded.payload',
                     (key, json.dumps(payload, ensure_ascii=False)))


def fulltext_status(item_id, library):
    key = cache_key(item_id, library)
    with _LOCK:
        cached = _read_cache(key)
        if cached['status'] == 'fetching' and key not in _JOBS:
            cached.update(status='failed', error='上次全文获取被中断，可以重新获取。')
            _save_cache(key, cached)
        return cached


def _fetch(key, version, requested, previous):
    try:
        payload = {'status': 'ready', 'data': PpubsClient().fetch(requested), 'error': '', 'requested': requested}
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '全文获取失败，请检查网络后重试。'
        payload = {'status': 'failed', 'data': previous, 'error': message, 'requested': requested}
    with _LOCK:
        if _JOBS.get(key) == version:
            _save_cache(key, payload)
            if key.startswith('library:') and payload['status'] == 'ready':
                title = payload['data']['metadata'].get('title')
                if title:
                    with closing(connect()) as conn, conn:
                        conn.execute('UPDATE reading_items SET title=? WHERE id=? AND title=publication_number', (title, int(key.split(':')[1])))
            _JOBS.pop(key, None)


def fetch_fulltext(item_id, library, value):
    requested = publication_number(value)
    record = item(item_id, library)
    known = record.get('publicationNumber') or ''
    document = documents.document_record(item_id, library)
    if not known and document:
        known = documents.read_analysis(documents.managed_path(document['object_path'])).get('metadata', {}).get('publicationNumber', '')
    if known:
        try:
            matches = publication_number(known) == requested
        except ValueError:
            matches = False
        if not matches:
            raise ValueError('输入号码与当前文献公开号不一致，请为另一篇专利新建文献。')
    key = cache_key(item_id, library)
    with _LOCK:
        item(item_id, library)
        if key in _JOBS:
            return _read_cache(key)
        if len(_JOBS) >= 3:
            raise ValueError('已有多个全文请求，请稍后再试。')
        previous = _read_cache(key).get('data')
        payload = {'status': 'fetching', 'data': previous, 'requested': requested, 'error': ''}
        _save_cache(key, payload)
        version = uuid.uuid4().hex
        _JOBS[key] = version
        _WORKER.submit(_fetch, key, version, requested, previous)
        return payload
