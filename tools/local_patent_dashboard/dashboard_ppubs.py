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


def publication_number(value):
    value = str(value or '').upper().strip()
    if value.startswith(('HTTPS://', 'HTTP://')):
        match = re.search(r'/PATENT/([^/?#]+)', value)
        value = match[1] if match else ''
    value = re.sub(r'[\s,-]', '', value)
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
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip += 1
        elif not self.skip and tag in {'br', 'p', 'div', 'claim', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in {'p', 'div', 'claim', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


def html_text(value):
    parser = TextHTML()
    parser.feed(str(value or ''))
    # Source HTML whitespace is not a semantic paragraph break.
    return '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in ''.join(parser.parts).splitlines() if line.strip())


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
    description = html_text(raw.get('descriptionHtml'))
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

    def request(self, path, payload=None, method='GET'):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('连接 PPUBS 超时，请稍后重试。')
        data = json.dumps(payload).encode() if method == 'POST' else None
        headers = {**self.headers, 'Content-Type': 'application/json'}
        request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=min(12, remaining)) as response:
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ValueError('PPUBS 返回的数据过大，请使用原始文献核对。')
            if response.headers.get('X-Access-Token'):
                self.headers['X-Access-Token'] = response.headers['X-Access-Token']
            if path == '/pubwebapp/':
                return None
            result = json.loads(body)
            if not isinstance(result, dict) or result.get('error'):
                raise ValueError('PPUBS 未返回有效的专利数据，请稍后重试。')
            return result

    def session(self):
        self.headers.pop('X-Access-Token', None)
        self.request('/pubwebapp/')
        result = self.request('/api/users/me/session', -1, 'POST')
        self.case_id = result['userCase']['caseId']

    def fetch(self, number):
        requested = publication_number(number)
        for attempt in range(2):
            try:
                self.session()
                digits = re.search(r"\d+", requested)[0]
                query = f'"{digits}".pn.'
                sources = ['US-PGPUB'] if requested.endswith(('A1', 'A2', 'A9')) else ['USPAT', 'USOCR']
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
                found = None
                for hit in result.get('patents', []):
                    try:
                        matches = publication_number(hit.get('guid')) == requested
                    except ValueError:
                        matches = False
                    if matches and hit.get('type') in sources:
                        found = hit
                        break
                if not found:
                    raise ValueError('PPUBS 未找到完全匹配的公开号和文献类型，请核对号码或使用 PDF。')
                params = urllib.parse.urlencode({'queryId': 1, 'source': found['type'], 'includeSections': 'true'})
                path = '/api/patents/highlight/' + urllib.parse.quote(found['guid'], safe='') + '?' + params
                return convert_document(self.request(path), requested)
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
