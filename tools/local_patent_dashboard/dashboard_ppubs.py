"""Small, Python 3.11-compatible PPUBS client using its public browser session.

Protocol and search template adapted from patent-client-agents (Apache-2.0),
commit b06f807. See third_party/PATENT-CLIENT-AGENTS-NOTICE.txt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from dashboard_patent_text import normalize_abstract, normalize_classification, parse_claims, part_references

BASE = 'https://ppubs.uspto.gov'
MAX_RESPONSE = 16 * 1024 * 1024


class PublicationChoices(ValueError):
    def __init__(self, candidates):
        super().__init__('查到多个文献版本，请选择完整公开号。')
        self.candidates = candidates


def publication_number(value, allow_partial=False):
    value = str(value or '').upper().strip()
    if value.startswith(('HTTPS://', 'HTTP://')):
        match = re.search(r'/PATENT/([^/?#]+)', value)
        value = match[1] if match else ''
    value = re.sub(r'[\s,-]', '', value)
    partial = re.fullmatch(r'(?:US)?(\d{6,11})', value)
    if allow_partial and partial:
        digits = partial[1]
        if not int(digits):
            raise ValueError('专利号码不能全为零。')
        return 'US' + (digits if len(digits) == 11 else digits.lstrip('0'))
    match = re.fullmatch(r'US(\d{6,11})(A1|A2|A9|B1|B2|A)', value)
    if not match:
        raise ValueError('请输入完整美国公开号或授权号，例如 US20250337340A1、US10211630B1。')
    digits, kind = match.groups()
    if kind in {'A1', 'A2', 'A9'} and len(digits) != 11:
        raise ValueError('美国申请公开号应含 11 位数字和 A1 等文献类型。')
    if kind in {'A', 'B1', 'B2'}:
        digits = digits.lstrip('0') or '0'
    return 'US' + digits + kind


class TextHTML(HTMLParser):
    def __init__(self, paragraphs=False):
        super().__init__(convert_charrefs=True)
        self.paragraphs = paragraphs
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip += 1
        elif not self.skip and tag in {'br', 'p', 'div', 'claim', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n\n' if self.paragraphs else '\n')
            if self.paragraphs:
                number = dict(attrs).get('num') or dict(attrs).get('data-num')
                if number and re.fullmatch(r'\d{1,6}', number):
                    self.parts.append('[' + number.zfill(4) + '] ')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in {'p', 'div', 'claim', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n\n' if self.paragraphs else '\n')

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(re.sub(r'\s+', ' ', text) if self.paragraphs else text)


def html_text(value):
    parser = TextHTML()
    parser.feed(str(value or ''))
    # Source HTML whitespace is not a semantic paragraph break.
    return '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def description_text(value):
    parser = TextHTML(paragraphs=True)
    parser.feed(str(value or ''))
    blocks = []
    for block in re.split(r"\n\s*\n", ''.join(parser.parts)):
        block = re.sub(r"[ \t]+", " ", block).strip()
        # Some publishers supply both a num attribute and a visible marker.
        block = re.sub(r"^\[(\d{4,6})\]\s*\[\1\]", r"[\1]", block)
        if block:
            blocks.append(block)
    return '\n\n'.join(blocks)


def _joined(value):
    if isinstance(value, list):
        return '; '.join(str(v) for v in value if v)
    return str(value or '')


def convert_document(raw, requested):
    try:
        actual = publication_number(raw.get('guid', ''))
    except ValueError as exc:
        raise ValueError('PPUBS 返回内容缺少完整公开号，无法核对文献版本。') from exc
    if actual != requested:
        raise ValueError('PPUBS 返回了其他公开号或文献版本，未保存该结果。')
    claims = parse_claims(html_text(raw.get('claimsHtml')))
    description = description_text(raw.get('descriptionHtml'))
    abstract = normalize_abstract(html_text(raw.get('abstractHtml')))
    if not claims or not description:
        raise ValueError('PPUBS 未返回完整权利要求和说明书，已保留原有阅读内容。')
    metadata = {
        'publicationNumber': requested, 'title': html_text(raw.get('inventionTitle')),
        'abstract': abstract, 'classification': normalize_classification(_joined(raw.get('ipcCodeFlattened'))),
        'applicationNumber': _joined(raw.get('applicationNumber')),
        'applicationDate': _joined(raw.get('applicationFilingDate')),
        'owner': _joined(raw.get('assigneeName') or raw.get('applicantName')),
        'inventors': _joined(raw.get('inventorsName')),
    }
    references = part_references(claims, description)
    for reference in references:
        reference['status'] = 'unmatched'
    return {'status': 'ready', 'metadata': metadata, 'claims': claims, 'description': description,
            'references': references, 'pages': [], 'warnings': [],
            'source': {'name': 'USPTO Public Search', 'publicationNumber': requested,
                       'url': BASE + '/pubwebapp/', 'fetchedAt': datetime.now(timezone.utc).isoformat()}}


class PpubsClient:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.headers = {'User-Agent': 'Mozilla/5.0 PatentReader/1.0', 'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest', 'Origin': BASE,
                        'Referer': BASE + '/pubwebapp/'}
        self.deadline = time.monotonic() + 55
        self.case_id = None

    def request(self, path, payload=None, method='GET', binary=False, text=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('连接 PPUBS 超时，请稍后重试。')
        data = json.dumps(payload).encode() if method == 'POST' else None
        headers = {**self.headers, 'Content-Type': 'application/json'}
        request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=min(12, remaining)) as response:
            limit = 50 * 1024 * 1024 if binary else MAX_RESPONSE
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError('PPUBS 返回的数据过大，请使用原始文献核对。')
            if response.headers.get('X-Access-Token'):
                self.headers['X-Access-Token'] = response.headers['X-Access-Token']
            if binary:
                return body
            if text:
                return body.decode('utf-8').strip()
            if path == '/pubwebapp/':
                return None
            result = json.loads(body)
            if isinstance(result, dict) and result.get('error'):
                raise ValueError('PPUBS 未返回有效的专利数据，请稍后重试。')
            return result

    def session(self):
        self.headers.pop('X-Access-Token', None)
        self.request('/pubwebapp/')
        result = self.request('/api/users/me/session', -1, 'POST')
        self.case_id = result['userCase']['caseId']

    def fetch(self, number):
        requested = publication_number(number, allow_partial=True)
        partial = bool(re.fullmatch(r"US\d+", requested))
        for attempt in range(2):
            try:
                self.session()
                digits = re.search(r"\d+", requested)[0]
                query = f'"{digits}".pn.'
                application = len(digits) == 11 if partial else requested.endswith(('A1', 'A2', 'A9'))
                sources = ['US-PGPUB'] if application else ['USPAT', 'USOCR']
                search_query = {
                    'caseId': self.case_id, 'hl_snippets': '2', 'op': 'OR', 'q': query,
                    'queryName': query, 'userEnteredQuery': query, 'highlights': '1', 'qt': 'brs',
                    'spellCheck': False, 'viewName': 'tile', 'plurals': False, 'britishEquivalents': False,
                    'databaseFilters': [{'databaseName': s, 'countryCodes': []} for s in sources],
                    'searchType': 1, 'ignorePersist': True,
                }
                payload = {'start': 0, 'pageCount': 20, 'sort': 'date_publ desc',
                           'docFamilyFiltering': 'familyIdFiltering', 'searchType': 1,
                           'familyIdEnglishOnly': True, 'familyIdFirstPreferred': 'US-PGPUB',
                           'familyIdSecondPreferred': 'USPAT', 'familyIdThirdPreferred': 'FPRS',
                           'showDocPerFamilyPref': 'showEnglish', 'queryId': 0,
                           'tagDocSearch': False, 'query': search_query}
                self.request('/api/searches/counts', search_query, 'POST')
                result = self.request('/api/searches/searchWithBeFamily', payload, 'POST')
                matches = {}
                for hit in result.get('patents', []):
                    try:
                        canonical = publication_number(hit.get('guid'))
                    except ValueError:
                        continue
                    same = re.search(r"\d+", canonical)[0] == digits if partial else canonical == requested
                    if same and hit.get('type') in sources:
                        matches.setdefault(canonical, hit)
                if not matches:
                    raise ValueError('PPUBS 未找到匹配的专利号码，请核对号码或使用 PDF。')
                if len(matches) > 1:
                    raise PublicationChoices([{'publicationNumber': key, 'title': html_text(hit.get('inventionTitle'))}
                                              for key, hit in matches.items()])
                requested, found = next(iter(matches.items()))
                params = urllib.parse.urlencode({'queryId': 1, 'source': found['type'], 'includeSections': 'true'})
                path = '/api/patents/highlight/' + urllib.parse.quote(found['guid'], safe='') + '?' + params
                self.document = self.request(path)
                self.document.setdefault('type', found['type'])
                return convert_document(self.document, requested)
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403} and attempt == 0:
                    continue
                if exc.code == 429:
                    raise ValueError('PPUBS 请求过于频繁，请稍后重试。') from exc
                raise ValueError(f'PPUBS 请求失败（HTTP {exc.code}），请检查网络和服务状态。') from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ValueError('无法连接 PPUBS，可能被网络或代理拦截；本地文件和已缓存全文仍可阅读。') from exc
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError('PPUBS 返回格式发生变化，未替换原有内容。') from exc
        raise ValueError('PPUBS 暂时无法建立公开检索会话。')


    def download_pdf(self, number):
        # Resolve the exact version again and use the same public session for
        # its print job. Text and image retrieval are separate PPUBS operations.
        self.fetch(publication_number(number))
        raw = self.document
        try:
            count = int(raw.get('pageCount') or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError('PPUBS 未返回有效页数，无法生成 PDF。') from exc
        location = raw.get('imageLocation')
        if not isinstance(location, str) or count < 1:
            raise ValueError('该文献未提供可下载的图像位置，请手动上传 PDF。')
        if count > 150:
            raise ValueError('本机阅读最多支持 150 页，请先分割原文 PDF 后上传。')
        location = location.rstrip('/')
        self.deadline = time.monotonic() + 180
        try:
            job = self.request('/api/print/imageviewer', {
                'caseId': self.case_id,
                'pageKeys': [f'{location}/{page:08}.tif' for page in range(1, count + 1)],
                'patentGuid': raw['guid'], 'saveOrPrint': 'save', 'source': raw['type'],
            }, 'POST', text=True)
            if not isinstance(job, str) or not job.strip():
                raise ValueError('PPUBS 未创建 PDF 下载任务，请稍后重试。')
            while time.monotonic() < self.deadline:
                statuses = self.request('/api/print/print-process', [job.strip().strip('"')], 'POST')
                if not isinstance(statuses, list) or not statuses or not isinstance(statuses[0], dict):
                    raise ValueError('PPUBS PDF 任务状态格式发生变化。')
                status = statuses[0]
                if not isinstance(status.get('printStatus'), str):
                    raise ValueError('PPUBS 未返回有效的 PDF 任务状态。')
                if status.get('printStatus') == 'COMPLETED':
                    name = str(status.get('pdfName') or '')
                    if not name:
                        raise ValueError('PPUBS 未返回 PDF 文件名。')
                    data = self.request('/api/print/save/' + urllib.parse.quote(name, safe=''), binary=True)
                    if not data.startswith(b'%PDF-'):
                        raise ValueError('PPUBS 返回的文件不是 PDF，未保存该内容。')
                    return data
                if status.get('printStatus') in {'FAILED', 'ERROR', 'CANCELLED'}:
                    raise ValueError('PPUBS 无法生成该文献的 PDF，请稍后重试或手动上传。')
                time.sleep(1)
            raise ValueError('PPUBS PDF 生成超时，请稍后重试。')
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError('原文 PDF 下载失败，请检查网络后重试；已获取的全文仍可阅读。') from exc
