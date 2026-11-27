"""Patent-aware reading order and section boundaries for native PDF/OCR text.

A drawing *description* belongs to the specification. A US claim can start halfway
through a two-column page, so classification is also recorded per text segment.
"""
from __future__ import annotations

import re
import statistics

PARSER_VERSION = 4
US_NUMBER = re.compile(r"\bUS\s*0?\d[\d\s,]*\s*[AB]\s*\d?\b", re.I)
CN_NUMBER = re.compile(r"\bCN\s*\d{7,12}\s*[A-Z]\d?\b", re.I)
CLAIM_HEADING = re.compile(r"^(?:(?:what\s+is|the\s+invention)\s+claimed\s+is|we\s+claim|i\s+claim)\s*[:.]?\s*$", re.I)


def jurisdiction(text):
    header = text[:2500]
    cn, us = CN_NUMBER.search(header), US_NUMBER.search(header)
    # Foreign references on a US cover must not override its own publication ID.
    if us and (not cn or us.start() < cn.start()):
        return "US"
    if cn:
        return "CN"
    if re.search(r"United\s+States\s+Patent", header[:1000], re.I):
        return "US"
    return ""


def is_drawing_sheet(text):
    # These are publisher page headings, never a mention in body prose.
    for line in text.splitlines()[:8]:
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"(?:CN\d{7,12}[A-Z]\d?)?说明书附图(?:\d+/\d+页)?", compact):
            return True
        if re.fullmatch(r"DRAWINGS", line.strip(), re.I):
            return True
    return bool(re.search(r"\bSheet\s*\d+\s*of\s*\d+\b", text[:1800], re.I))


def heading_section(line):
    compact = re.sub(r"\s+", "", line)
    if re.fullmatch(r"(?:CN\d{7,12}[A-Z]\d?)?权利要求书(?:\d+/\d+页)?", compact):
        return "claims"
    if re.fullmatch(r"(?:CN\d{7,12}[A-Z]\d?)?说明书(?:\d+/\d+页)?", compact):
        return "description"
    if CLAIM_HEADING.fullmatch(line.strip()) or re.fullmatch(r"CLAIMS\s*[:.]?", line.strip(), re.I):
        return "claims"
    if re.fullmatch(r"(?:BRIEF\s+)?DESCRIPTION\s+OF\s+(?:THE\s+)?DRAWINGS|(?:DETAILED\s+)?DESCRIPTION(?:\s+OF\s+(?:THE\s+)?(?:INVENTION|EMBODIMENTS))?|BACKGROUND(?:\s+OF\s+(?:THE\s+)?INVENTION)?|SUMMARY(?:\s+OF\s+(?:THE\s+)?INVENTION)?|FIELD(?:\s+OF\s+(?:THE\s+)?INVENTION)?|CROSS\s*-?\s*REFERENCE\s+TO\s+RELATED(?:\s+APPLICATIONS)?", line.strip(), re.I):
        return "description"
    return None


def classify_page(text, previous=None):
    if is_drawing_sheet(text):
        return "drawings"
    # Cover-page bibliographic counts are not section headings.
    if re.search(r"\(\s*(?:19|12)\s*\).*(?:国家知识产权局|United\s+States\s+Patent)", text[:1200], re.I):
        return "basic"
    for line in text.splitlines():
        section = heading_section(line)
        if section:
            return section
    # Never propagate a drawing classification into a subsequent text page.
    return previous if previous in {"basic", "claims", "description"} else "basic"


def _rows(words):
    if not words:
        return []
    abstract_heading = next((w for w in words if w[4].upper() == 'ABSTRACT'), None)
    if abstract_heading and all(len(w) > 5 and isinstance(w[5], tuple) for w in words):
        # Preserve OCR baselines in the abstract. Bibliographic tags above it
        # can be separate OCR blocks and still need geometric alignment.
        boundary = abstract_heading[3]
        before = [w for w in words if (w[1] + w[3]) / 2 <= boundary]
        lines = {}
        for word in words:
            if (word[1] + word[3]) / 2 > boundary:
                lines.setdefault(word[5], []).append(word)
        ordered = sorted(lines.values(), key=lambda line: statistics.median((w[1] + w[3]) / 2 for w in line))
        return _rows([w[:5] for w in before]) + [" ".join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in ordered]
    heights = [w[3]-w[1] for w in words if w[3]>w[1]]
    tolerance = max(1.5, min(6, statistics.median(heights)*.45))
    rows = []
    for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
        y = (word[1]+word[3])/2
        if not rows or abs(y-rows[-1][0]) > tolerance:
            rows.append([y, [word]])
        else:
            rows[-1][1].append(word)
    return [" ".join(w[4] for w in sorted(row, key=lambda w: w[0])) for _, row in rows]


def clean_text(text):
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if US_NUMBER.fullmatch(line) or CN_NUMBER.fullmatch(line):
            continue
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"(?:CN\d+[A-Z]\d?)?(?:权利要求书|说明书|说明书附图)\d+/\d+页", compact):
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue  # folios / OCR duplicates, not numbered claims (which have punctuation)
        line = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", line)
        line = re.sub(r"\s+([,;:!?])", r"\1", line)
        line = re.sub(r"([（(])\s*(\d{1,5}[a-zA-Z]?)\s*([)）])", r"\1\2\3", line)
        lines.append(line)
    return "\n".join(lines)


def order_words(words, width, height, country, raw):
    columns = country == "US" and not is_drawing_sheet(raw) and len(words) >= 150
    if columns:
        body = []
        for word in words:
            x0,y0,x1,y1,value = word[:5]
            center = (x0+x1)/2/width
            # Patent-number header and column folios sit above the body.
            if y0 < .095*height and .4 < center < .6:
                continue
            if value.isdigit() and y0 < .1*height and (.27<center<.34 or .64<center<.71):
                continue
            # US printed line counters sit in the center gutter, not in either column.
            if value.isdigit() and int(value) % 5 == 0 and .485<center<.507:
                continue
            body.append(word)
        left = [w for w in body if (w[0]+w[2])/2 < width/2]
        right = [w for w in body if (w[0]+w[2])/2 >= width/2]
        return clean_text("\n".join(_rows(left)+_rows(right))), "two-column"
    # Chinese running headings are removed geometrically, keeping headings in the
    # original raw text available for classification before cleaning.
    return clean_text(raw), "single-column"


def reading_order(page, country):
    return order_words(page.get_text("words"), page.rect.width, page.rect.height,
                       country, page.get_text(sort=True))


def ocr_reading_order(tokens, country, raw):
    # OCR boxes are normalized; the column algorithm is scale-independent.
    words = [(t["box"][0]*1000, t["box"][1]*1000,
              (t["box"][0]+t["box"][2])*1000, (t["box"][1]+t["box"][3])*1000,
              t["text"], tuple(t["line"]) if t.get("line") else None) for t in tokens]
    return order_words(words, 1000, 1000, country, raw)


def split_sections(text, previous, raw=None, page_number=1):
    raw = raw if raw is not None else text
    if is_drawing_sheet(raw):
        return [{"section":"drawings", "text":text}], "drawings"
    initial = classify_page(text, previous)
    for line in raw.splitlines()[:4]:
        native_heading = heading_section(line)
        if native_heading:
            initial = native_heading
            break
    if page_number == 1 and re.search(r"\(\s*54\s*\)", raw):
        initial = "basic"
    # A claim heading late on a specification page changes only the following
    # text. It must not steal the preceding description or the other column.
    claim_heading_index = next((i for i, line in enumerate(text.splitlines()) if heading_section(line)=="claims"), None)
    if previous == "description" and claim_heading_index is not None and claim_heading_index > 0:
        initial = "description"
    current = initial
    segments = []
    buffer = []
    def flush():
        if buffer:
            segments.append({"section":current, "text":"\n".join(buffer)})
            buffer.clear()
    for line in text.splitlines():
        section = heading_section(line)
        if section:
            if section != current:
                flush()
                current = section
            if section == "claims":
                continue
        buffer.append(line)
    flush()
    if not segments:
        segments = [{"section":initial, "text":text}]
    return segments, segments[-1]["section"]


# Publication text may declare several canceled numbers in one heading.
_CANCEL_WORD = r"cancell?ed"
_CANCEL_STATUS = re.compile(rf"(?:\(\s*{_CANCEL_WORD}\s*\)|{_CANCEL_WORD})\.?", re.I)
_CANCEL_HEADING = re.compile(
    rf"(?<![\w.\-])(?P<first>\d{{1,3}})(?:\s*\.?\s*[-‐‑–—−]\s*(?P<last>\d{{1,3}}))?"
    rf"\s*[.．、]?\s*(?P<status>{_CANCEL_STATUS.pattern})(?=\s*(?:$|\d{{1,3}}\b))", re.I | re.M)
_CLAIM_HEADING = re.compile(
    r"(?:^[ \t]*|(?<!\bFIG\.)(?<!\bNO\.)(?<=[.。])[ \t]+)"
    r"(?P<number>\d{1,3})\s*[.．、]\s*(?=[^\d\s])", re.M | re.I)
_CLAIM_START = re.compile(rf"^{_CANCEL_HEADING.pattern}|{_CLAIM_HEADING.pattern}", re.M | re.I)


def parse_claims(text):
    # The entire status declaration is one heading, even in flattened HTML.
    text = _CANCEL_HEADING.sub(r"\n\g<0>\n", clean_text(text))
    starts = []
    expected = None
    for match in _CLAIM_START.finditer(text):
        status = match["status"]
        first = int(match["first"] or match["number"])
        last = int(match["last"] or first)
        if (expected is None or first == expected) and first <= last:
            starts.append((match, first, last, status))
            expected = last + 1
    claims = []
    for index, (match, number, last, status) in enumerate(starts):
        if status:
            claims.extend({"number": n, "text": status, "kind": "canceled", "dependencies": []}
                          for n in range(number, last + 1))
            continue
        body = text[match.end():starts[index+1][0].start() if index+1 < len(starts) else len(text)].strip()
        body = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "-", body)
        body = re.sub(r"\s*\n\s*", " ", body)
        body = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", body)
        body = re.sub(r"(?:\s*\*){3,}\s*$", "", body).strip()
        lead = body[:300]
        reference = re.search(r"权利要求\s*([\d\s、,，及或至到\-—～~]+)|\bclaims?\s+(\d+(?:\s*(?:,|and|or|to|through|[-–])\s*\d+)*)", lead, re.I)
        dependencies = []
        if reference:
            numbers = reference[1] or reference[2]
            for first, last in re.findall(r"(\d+)\s*(?:至|到|[-—～~–]|to|through)\s*(\d+)", numbers, re.I):
                dependencies.extend(range(int(first), min(int(last), int(first)+100)+1))
            dependencies.extend(int(n) for n in re.findall(r"\d+", numbers))
        dependent = bool(reference) or bool(re.search(r"(?:根据|按照|如|引用).*?权利要求|\b(?:preceding|previous)\s+claims?\b", lead, re.I))
        claims.append({"number":number, "text":body, "kind":"dependent" if dependent else "independent",
                       "dependencies":sorted({n for n in dependencies if 0<n<number})})
    return claims


def repair_canceled_claims(result):
    """Recover affected cached entries locally, retaining other claims and their edits."""
    claims = result.get("claims", [])
    fixed = []
    changed = False
    for i, claim in enumerate(claims):
        body = claim["text"]
        if "cancel" not in body.lower() or claim.get("kind") == "canceled":
            fixed.append(claim)
            continue
        if _CANCEL_STATUS.match(body) or _CANCEL_HEADING.search(body):
            repaired = parse_claims(f"{claim['number']}. {body}")
            if repaired and (i + 1 == len(claims) or repaired[-1]["number"] < claims[i+1]["number"]):
                fixed.extend(repaired)
                changed |= repaired != [claim]
                continue
        fixed.append(claim)
    if changed:
        result["claims"] = fixed
    return changed


def normalize_classification(text):
    # Apply OCR digit corrections only within the numeric class positions.
    pattern = r"(?<![A-Z0-9])([A-HY])\s*([0-9OIlZ]{2,3})\s*([A-Z)])\s*(\d{1,4})\s*/\s*(\d{2,6})(?:\s*[（(]\s*(\d{4})\s*\.\s*(\d{2})\s*[)）])?"
    codes = []
    for match in re.finditer(pattern, text):
        section, cls, subclass, group, subgroup, year, month = match.groups()
        cls = cls.translate(str.maketrans({'O': '0', 'I': '1', 'l': '1', 'Z': '2'}))
        if len(cls) == 3:
            if not cls.startswith('00'):
                continue
            cls = cls[1:]
        code = f"{section}{cls}{'J' if subclass == ')' else subclass} {group}/{subgroup}"
        if year:
            code += f" ({year}.{month})"
        if code not in codes:
            codes.append(code)
    return "\n".join(codes) if codes else text.strip()


def normalize_abstract(text):
    # Keep a printed hyphen on compounds but remove it for split word suffixes.
    text = re.sub(r"([A-Za-z]+)-[ \t]*\n[ \t]*([a-z]+)",
                  lambda m: m[1] + ("" if m[2] in {'ing', 'tion', 'tions', 'ment', 'ments', 'ness', 'ly', 'ed', 's', 'es'} else "-") + m[2], text)
    text = re.sub(r"([A-Za-z]+)[ \t]*\n[ \t]*(ing|tion|tions|ment|ments|ness)\b", r"\1\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff，。；：])", "", text)
    text = re.sub(r"([，。；：]) +(?=[\u3400-\u9fff])", r"\1", text)
    text = re.sub(r"(?<=[A-Za-z])\s+-\s+(?=[A-Za-z])", "-", text)
    text = re.sub(r"^[.·•:;\s-]+", "", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def extract_metadata(text):
    """Read INID fields instead of treating the line after every tag as its value."""
    from datetime import datetime
    tags = list(re.finditer(r"[（(]\s*(\d{2})\s*[)）]", text))
    fields = {}
    for i, tag in enumerate(tags):
        fields.setdefault(tag[1], text[tag.end():tags[i+1].start() if i+1<len(tags) else len(text)].strip())
    def field(number, label):
        value = fields.get(number, "")
        value = re.sub(r"^(?:"+label+r")\s*[:：]?\s*", "", value, flags=re.I)
        value = re.split(r"\(\s*\*\s*\)|权利要求书\d+页|\bRelated\s+U", value, maxsplit=1, flags=re.I)[0]
        return value.strip()
    title = field('54', r'发明名称|实用新型名称|外观设计名称|名称')
    title = re.sub(r"\s*\n\s*", "" if jurisdiction(text)=='CN' else " ", title)
    applicant = field('71', r'申请人|Applicant') or field('73', r'专利权人|Assignee')
    inventor = field('72', r'发明人|Inventors?')
    application = field('21', r'申请号|Appl\s*\.\s*No\s*\.')
    match = re.search(r"(?:CN\s*)?\d{12}\s*\.\s*[\dXx]|(?:US\s*)?\d{2}\s*/\s*[\d,\s]+", application.splitlines()[0] if application else '')
    application = re.sub(r"\s+", "", match[0]) if match else ''
    filed = field('22', r'申请日|Filed').splitlines()
    date = filed[0] if filed else ''
    date = re.sub(r"\s+\.", ".", date)
    for fmt in ('%Y.%m.%d','%Y-%m-%d','%b. %d, %Y','%b %d, %Y','%B %d, %Y'):
        try:
            date = datetime.strptime(date.strip(), fmt).date().isoformat()
            break
        except ValueError:
            pass
    publication = CN_NUMBER.search(text) or US_NUMBER.search(text)
    number = re.sub(r"[\s,]", "", publication[0]) if publication else ''
    if number.startswith('US0'):
        number = 'US'+number[2:].lstrip('0')
    classification = field('51', r'Int\s*\.\s*C[il]\s*\.|分类号')
    classification = normalize_classification(classification)
    abstract = field('57', r'摘要|ABSTRACT')
    abstract = re.split(r"\b\d+\s+Claims\b", abstract, flags=re.I)[0]
    abstract = re.sub(r"(?m)^\s*(?:CN|[AB])\s*$", "", abstract).strip()
    return {'title':title[:500], 'applicationNumber':application, 'applicationDate':date,
            'owner':applicant.splitlines()[0][:500] if applicant else '',
            'inventors':re.sub(r'\s*\n\s*', ' ', inventor)[:500],
            'classification':classification[:500], 'abstract':normalize_abstract(abstract)[:4000], 'publicationNumber':number}


def part_references(claims, description):
    refs = {}
    text = '\n'.join(c['text'] for c in claims)+'\n'+description
    def add(name, number):
        name = name.strip(' ,.;:，。；：')
        if len(name)>1:
            refs.setdefault(number, {'number':number,'name':name,'matches':[]})
    # Parenthesized component numerals, including the synthetic and CN styles.
    for m in re.finditer(r'([\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z\s-]{0,70})[（(]\s*(\d{1,5}[a-zA-Z]?)\s*[)）]', text):
        name = re.split(r'[，。；：、\n]|包括|具有|所述|其中|以及|和|与|设有|设置有|\b(?:the|a|an|and|of|to)\b', m[1], flags=re.I)[-1]
        add(name, m[2])
    # Native CN publications often omit parentheses: 第一基板10, 磁性组件200.
    for m in re.finditer(r'([\u3400-\u9fff]{2,20})(\d{1,5}[a-z]?)', text):
        name = re.split(r'所述|包括|其中|以及|对应的|设置在|相邻的|参照|和|与|的', m[1])[-1]
        if not name.endswith(('图','要求','段','年','月','日')) and not re.match(r'\s*(?:％|%|度|伏|毫|页)', text[m.end():]):
            add(name, m[2])
    # US descriptions use phrases such as "transformer 104", without brackets.
    for m in re.finditer(r'\b((?:[A-Za-z][A-Za-z-]*\s+){1,7})(\d{2,4}[a-z]?)\b', text):
        phrase = re.split(r'\b(?:the|a|an|and|or|of|to|in|from|with|by|as|for|that|each|such)\b', m[1], flags=re.I)[-1].strip()
        if not phrase or re.search(r'\b(?:FIG|FIGS|Figure|claim|claims|page|Sheet|between|above|below|than|about|at|over|under|year)\.?$', phrase, re.I):
            continue
        if re.match(r'\s*(?:volts?|kilovolts?|kV|MW|kilowatts?|megawatts?|percent|%)\b', text[m.end():], re.I):
            continue
        add(phrase, m[2])
    return list(refs.values())


def check_declared_counts(basic_text, section_pages, claim_count):
    """Cross-check publisher cover declarations; never invent missing claims."""
    declared_claims = re.search(r'\b(\d+)\s*Claims\b', basic_text, re.I)
    compact = re.sub(r'\s+', '', basic_text)
    expected_pages = {}
    for section, heading in [('claims','权利要求书'),('description','说明书'),('drawings','附图')]:
        match = re.search(heading+r'(\d+)页',compact)
        if match:
            expected_pages[section] = int(match[1])
    drawing_sheets = re.search(r'\b(\d+)\s*Drawing\s+Sheets\b',basic_text,re.I)
    if drawing_sheets:
        expected_pages['drawings'] = int(drawing_sheets[1])
    warnings = []
    if declared_claims and claim_count != int(declared_claims[1]):
        warnings.append(f'首页标注 {declared_claims[1]} 项权利要求，当前提取 {claim_count} 项，请核对。')
    names = {'claims':'权利要求','description':'说明书','drawings':'附图'}
    for section, count in expected_pages.items():
        actual = len(section_pages[section])
        if actual != count:
            warnings.append(f'首页标注 {count} 页{names[section]}，当前识别 {actual} 页，请核对。')
    checked = bool(declared_claims or expected_pages)
    return {'expectedClaimCount':int(declared_claims[1]) if declared_claims else None,
            'expectedSectionPages':expected_pages, 'passed':not warnings if checked else None}, warnings
