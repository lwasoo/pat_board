"""Excel workbook parsing and export; preserves existing import rules."""
from __future__ import annotations

import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dashboard_validation import (
    clean_text, optional_int, excel_date, split_lines, simple_identifier,
    infer_country, looks_like_application_number, normalize_patent_type,
    normalize_legal_status, parse_claims,
)


PATENT_COLUMNS = [
    ("申请号", "applicationNumber"), ("专利名称", "title"), ("国家/地区", "countryRegion"),
    ("专利类型", "patentType"), ("申请日", "applicationDate"), ("公开日", "publicationDate"),
    ("公开号/授权号", "publicationNumber"), ("授权日", "grantDate"), ("到期日", "expirationDate"),
    ("法律状态", "legalStatus"), ("专利族编号", "familyNumber"), ("专利族名称", "familyName"),
    ("发明人/设计人", "inventors"), ("申请人/权利人", "owner"), ("代理编号", "agentReference"),
    ("权利要求项数", "claimCount"), ("独立权利要求项数", "independentClaimCount"),
    ("说明书页数", "pageCount"), ("CN稿状态", "cnDraftStatus"),
    ("境外稿状态", "foreignDraftStatus"), ("部门", "department"), ("产品线", "productLine"),
    ("关联国家编号", "relatedNumbers"), ("备注", "caseNotes"),
]



FEE_COLUMNS = [
    ("关联申请号", "applicationNumber"), ("专利名称", "patentTitle"), ("国家/地区", "feeRegion"),
    ("费用类型", "feeType"), ("缴费期限", "dueDate"), ("金额", "amount"),
    ("原始值", "rawValue"), ("币种", "currency"), ("缴费状态", "status"),
    ("实际缴费日", "paidDate"), ("备注", "note"),
]



FEE_SUMMARY_COLUMNS = [
    ("关联申请号", "applicationNumber"), ("专利名称", "patentTitle"),
    ("中国费用明细", "chinaFees"), ("美国费用明细", "usFees"),
    ("欧洲费用明细", "europeFees"), ("印度费用明细", "indiaFees"),
    ("最近待缴项目", "nextFeeType"), ("最近缴费期限", "nextDueDate"),
    ("最近待缴金额", "nextAmount"), ("币种", "nextCurrency"),
    ("缴费状态", "nextStatus"), ("实际缴费日", "paidDate"), ("备注", "note"),
]



FEE_REGION_SUMMARY_KEYS = {
    "中国": "chinaFees", "美国": "usFees", "欧洲": "europeFees", "印度": "indiaFees",
}



def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result



def worksheet_xml(headers: list[str], rows: list[list], widths: list[int] | None = None) -> str:
    all_rows = [headers] + rows
    row_xml = []
    for row_number, row in enumerate(all_rows, 1):
        cells = []
        for col_number, value in enumerate(row, 1):
            ref = f"{column_name(col_number)}{row_number}"
            style = 1 if row_number == 1 else 2
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                text = html.escape("" if value is None else str(value))
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        row_xml.append(f'<row r="{row_number}" ht="{28 if row_number == 1 else 54}" customHeight="1">{"".join(cells)}</row>')
    widths = widths or [18] * len(headers)
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    end_ref = f"{column_name(len(headers))}{max(1, len(all_rows))}"
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<dimension ref="A1:{end_ref}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>{"".join(row_xml)}</sheetData>
<autoFilter ref="A1:{column_name(len(headers))}{max(1, len(all_rows))}"/></worksheet>'''



def fee_display_value(row: dict) -> str:
    raw = clean_text(row.get("rawValue"))
    if raw:
        return raw
    amount = row.get("amount")
    if amount in (None, ""):
        return ""
    if isinstance(amount, float) and amount.is_integer():
        return str(int(amount))
    return str(amount)



def build_fee_summary_rows(patents: list[dict], fees: list[dict]) -> list[list]:
    patent_by_number = {clean_text(row.get("applicationNumber")): row for row in patents}
    grouped: dict[str, list[dict]] = {}
    for fee in fees:
        number = clean_text(fee.get("applicationNumber"))
        if number:
            grouped.setdefault(number, []).append(fee)
    numbers = list(patent_by_number)
    numbers.extend(number for number in grouped if number not in patent_by_number)
    result = []
    for number in numbers:
        patent = patent_by_number.get(number, {})
        items = grouped.get(number, [])
        summary = {key: "" for _, key in FEE_SUMMARY_COLUMNS}
        summary["applicationNumber"] = number
        summary["patentTitle"] = clean_text(patent.get("title")) or next(
            (clean_text(item.get("patentTitle")) for item in items if clean_text(item.get("patentTitle"))), "")
        region_lines = {key: [] for key in FEE_REGION_SUMMARY_KEYS.values()}
        for item in items:
            # A dated payment/reminder is represented by the dedicated "最近" columns below.
            # Keeping it in both places would create a duplicate when the exported file is re-imported.
            if clean_text(item.get("dueDate")):
                continue
            key = FEE_REGION_SUMMARY_KEYS.get(clean_text(item.get("feeRegion")))
            if not key:
                continue
            value = fee_display_value(item)
            currency = clean_text(item.get("currency"))
            suffix = f" {currency}" if currency and currency not in value.upper() else ""
            label = clean_text(item.get("feeType")) or "费用"
            region_lines[key].append(f"{label}={value}{suffix}" if value else label)
        for key, lines in region_lines.items():
            summary[key] = "\n".join(lines)
        open_items = [item for item in items if clean_text(item.get("dueDate")) and clean_text(item.get("status")) not in {"已缴费", "无需缴费"}]
        candidates = open_items or [item for item in items if clean_text(item.get("dueDate"))]
        if candidates:
            next_item = min(candidates, key=lambda item: clean_text(item.get("dueDate")) or "9999-12-31")
            summary.update({
                "nextFeeType": clean_text(next_item.get("feeType")),
                "nextDueDate": clean_text(next_item.get("dueDate")),
                "nextAmount": next_item.get("amount") if next_item.get("amount") is not None else "",
                "nextCurrency": clean_text(next_item.get("currency")),
                "nextStatus": clean_text(next_item.get("status")),
                "paidDate": clean_text(next_item.get("paidDate")),
                "note": clean_text(next_item.get("note")),
            })
        result.append([summary.get(key, "") for _, key in FEE_SUMMARY_COLUMNS])
    return result



def build_xlsx(patents: list[dict], fees: list[dict], template=False) -> bytes:
    patent_rows = [] if template else [[row.get(key, "") for _, key in PATENT_COLUMNS] for row in patents]
    fee_rows = [[
        "示例（导入时自动忽略）", "一种示例装置",
        "申请费=135 CNY\n实审费=2500 CNY",
        "申请费=1820 USD\n年费=800 USD",
        "申请费=1350 EUR", "申请费=1600 INR",
        "美国第3年年费", "2027-03-31", 800, "USD", "待缴费", "", "此行为填写示例",
    ]] if template else build_fee_summary_rows(patents, fees)
    notes = [
        ["填写说明"],
        ["专利台账工作表用于导入；申请号和专利名称为必填，其他字段可后续补充。"],
        ["日期请使用 yyyy-mm-dd；专利类型填写发明、实用新型或外观设计。"],
        ["相同专利族请填写相同专利族编号；多位发明人可用顿号或逗号分隔。权项和页数请填写数字。"],
        ["也支持直接导入既有的“专利家族”格式；其中合并单元格信息会自动向下继承。"],
        ["费用汇总按一个专利一行填写。中国、美国、欧洲、印度费用明细可写多项，每项一行，格式为：申请费=135 CNY。示例行导入时会自动忽略。"],
        ["最近待缴项目、最近缴费期限和缴费状态用于临期提醒；没有缴费期限的费用明细不会触发提醒。"],
    ]
    sheets = [
        ("专利台账", worksheet_xml([h for h, _ in PATENT_COLUMNS], patent_rows,
         [20,34,12,14,14,14,22,14,14,14,18,28,26,24,18,14,18,14,18,18,16,16,32,42])),
        ("费用汇总", worksheet_xml([h for h, _ in FEE_SUMMARY_COLUMNS], fee_rows,
         [20,34,28,28,28,28,24,16,16,10,14,14,32])),
    ]
    if template:
        sheets.append(("填写说明", worksheet_xml(["说明"], notes[1:], [85])))
    content_types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>','<Default Extension="xml" ContentType="application/xml"/>','<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>','<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    content_types += [f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1)]
    workbook_sheets = "".join(f'<sheet name="{html.escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))
    workbook_rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="10"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF0D766E"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border/><border><bottom style="thin"><color rgb="FFDCE8E5"/></bottom></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'+"".join(content_types)+"</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{workbook_sheets}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{workbook_rels}</Relationships>')
        z.writestr("xl/styles.xml", styles)
        for i,(_,xml) in enumerate(sheets,1): z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buffer.getvalue()



def xlsx_matrices(data: bytes) -> dict[str, list[list]]:
    ns = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "p":"http://schemas.openxmlformats.org/package/2006/relationships"}
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                      for item in root.findall("m:si", ns)]
        date_styles = set()
        if "xl/styles.xml" in z.namelist():
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom_formats = {int(node.attrib["numFmtId"]): node.attrib.get("formatCode", "")
                              for node in styles.findall("m:numFmts/m:numFmt", ns)}
            cell_xfs = styles.find("m:cellXfs", ns)
            if cell_xfs is not None:
                for style_index, xf in enumerate(cell_xfs):
                    number_format = int(xf.attrib.get("numFmtId", "0"))
                    format_code = custom_formats.get(number_format, "").lower()
                    if number_format in set(range(14, 23)) | {45, 46, 47} or (
                            "y" in format_code and "d" in format_code):
                        date_styles.add(style_index)
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relation = {node.attrib["Id"]: node.attrib["Target"] for node in rels.findall("p:Relationship", ns)}
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            target = relation[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
            sheet_path = target.lstrip("/") if target.startswith("/") else target if target.startswith("xl/") else "xl/" + target
            root = ET.fromstring(z.read(sheet_path)); matrix = []
            for xml_row in root.findall(".//m:sheetData/m:row", ns):
                values = {}
                for cell in xml_row.findall("m:c", ns):
                    ref = cell.attrib.get("r", ""); letters = "".join(c for c in ref if c.isalpha()); index = 0
                    for char in letters:
                        index = index * 26 + ord(char.upper()) - 64
                    cell_type = cell.attrib.get("t"); value = ""
                    if cell_type == "inlineStr":
                        value = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                    else:
                        node = cell.find("m:v", ns); raw = node.text if node is not None else ""
                        value = shared[int(raw)] if cell_type == "s" and raw else raw
                        style_index = int(cell.attrib.get("s", "0"))
                        if raw and cell_type != "s" and style_index in date_styles:
                            try:
                                value = (datetime(1899, 12, 30) + timedelta(days=float(raw))).strftime("%Y-%m-%d")
                            except ValueError:
                                pass
                    values[index - 1] = value
                if values:
                    matrix.append([values.get(i, "") for i in range(max(values) + 1)])
            result[sheet.attrib.get("name", "")] = matrix
    return result



def parse_standard_patents(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value).replace("／", "/") for value in matrix[0]]
    aliases = {name: key for name, key in PATENT_COLUMNS}
    aliases.update({"名称":"title", "发明人":"inventors", "设计人":"inventors",
                    "发明人/设计人":"inventors", "权利人":"owner", "申请人":"owner",
                    "申请人/权利人":"owner", "美国代理编号":"agentReference",
                    "US代理编号":"agentReference", "专利家族":"familyNumber",
                    "CN/US权项/独权":"claimSummary"})
    date_keys = {"applicationDate", "publicationDate", "grantDate", "expirationDate"}
    rows = []
    for values in matrix[1:]:
        row = {}
        for index, header in enumerate(headers):
            key = aliases.get(header)
            if not key: continue
            value = values[index] if index < len(values) else ""
            row[key] = excel_date(value) if key in date_keys else clean_text(value)
        if row.get("claimSummary"):
            row["claimCount"], row["independentClaimCount"] = parse_claims(row.pop("claimSummary"))
        if row.get("applicationNumber") or row.get("title"):
            rows.append(row)
    return rows



def parse_family_patents(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value).replace("\n", "").replace("／", "/") for value in matrix[0]]
    def col(name, default=-1):
        return headers.index(name) if name in headers else default
    positions = {name: col(name) for name in ("美国代理编号", "专利家族", "名称", "申请号", "申请日",
        "到期日/公开日/授权日", "发明人", "申请人", "CN/US权项/独权", "CN稿", "US稿",
        "CN状态/授权日", "CN到期日", "授权号/公开号", "EP", "IN", "CN")}
    value_at = lambda values, index: values[index] if 0 <= index < len(values) else ""
    rows = []; current_family = ""; current_family_name = ""; current_inventors = ""; current_owner = ""; current_agent = ""
    for values in matrix[1:]:
        raw_family = simple_identifier(value_at(values, positions["专利家族"]))
        raw_title = clean_text(value_at(values, positions["名称"]))
        if raw_family:
            current_family = raw_family
            current_family_name = raw_title or current_family_name
            current_inventors = ""; current_owner = ""; current_agent = ""
        elif raw_title and not current_family_name:
            current_family_name = raw_title
        inventor_value = clean_text(value_at(values, positions["发明人"]))
        owner_value = clean_text(value_at(values, positions["申请人"]))
        agent_value = clean_text(value_at(values, positions["美国代理编号"]))
        if inventor_value not in {"", "/"}: current_inventors = inventor_value
        if owner_value not in {"", "/"}: current_owner = owner_value
        if agent_value not in {"", "/"}: current_agent = agent_value
        number_lines = split_lines(value_at(values, positions["申请号"]))
        if not number_lines or not looks_like_application_number(number_lines[0]):
            continue
        application_number = number_lines[0]
        title = raw_title or current_family_name
        if not title: continue
        date_lines = split_lines(value_at(values, positions["申请日"]))
        dates = [excel_date(value) for value in date_lines]
        dates = [value for value in dates if value]
        claim_count, independent_count = parse_claims(value_at(values, positions["CN/US权项/独权"]))
        cn_status = clean_text(value_at(values, positions["CN稿"]))
        foreign_status = clean_text(value_at(values, positions["US稿"]))
        if cn_status == "/": cn_status = ""
        if foreign_status == "/": foreign_status = ""
        status_or_grant = value_at(values, positions["CN状态/授权日"])
        grant_date = excel_date(status_or_grant)
        publication_number = clean_text(value_at(values, positions["授权号/公开号"]))
        if not publication_number and len(number_lines) > 1:
            publication_number = "\n".join(number_lines[1:])
        related = []
        for heading in ("EP", "IN", "CN"):
            related_value = clean_text(value_at(values, positions[heading]))
            if related_value:
                related.append(f"{heading}: {related_value}")
        notes = []
        milestone = clean_text(value_at(values, positions["到期日/公开日/授权日"]))
        if milestone and milestone != "/": notes.append(f"原表重要日期：{milestone}")
        for index in (11, 12):
            extra = clean_text(value_at(values, index))
            if extra and extra != "/": notes.append(extra)
        rows.append({
            "applicationNumber": application_number, "title": title,
            "countryRegion": infer_country(application_number),
            "patentType": normalize_patent_type("", publication_number or application_number),
            "applicationDate": dates[0] if dates else "", "publicationDate": dates[1] if len(dates) > 1 else "",
            "publicationNumber": publication_number, "grantDate": grant_date,
            "expirationDate": excel_date(value_at(values, positions["CN到期日"])),
            "legalStatus": normalize_legal_status(cn_status, foreign_status, status_or_grant, "授权" if grant_date else ""),
            "familyNumber": current_family or application_number,
            "familyName": current_family_name or title, "inventors": current_inventors,
            "owner": current_owner, "agentReference": current_agent,
            "claimCount": claim_count, "independentClaimCount": independent_count,
            "cnDraftStatus": cn_status, "foreignDraftStatus": foreign_status,
            "relatedNumbers": "\n".join(related), "caseNotes": "\n".join(notes),
        })
    return rows



def enrich_from_fee_matrix(rows: list[dict], matrix: list[list]) -> None:
    if not matrix: return
    by_number = {clean_text(row.get("applicationNumber")): row for row in rows}
    headers = [clean_text(value).replace("\n", "") for value in matrix[0]]
    for values in matrix[1:]:
        application_lines = split_lines(values[3] if len(values) > 3 else "")
        if not application_lines or not looks_like_application_number(application_lines[0]): continue
        number = application_lines[0]
        row = by_number.get(number)
        if row is None:
            title = clean_text(values[2] if len(values) > 2 else "")
            if not title: continue
            dates = [excel_date(value) for value in split_lines(values[4] if len(values) > 4 else "")]
            row = {"applicationNumber": number, "title": title, "countryRegion": infer_country(number),
                   "patentType": normalize_patent_type(values[5] if len(values) > 5 else "", number),
                   "applicationDate": next((value for value in dates if value), ""),
                   "legalStatus": normalize_legal_status(values[6] if len(values) > 6 else ""),
                   "familyNumber": simple_identifier(values[1] if len(values) > 1 else "") or number,
                   "familyName": title}
            rows.append(row); by_number[number] = row
        row["pageCount"] = row.get("pageCount") or optional_int(values[7] if len(values) > 7 else "")
        row["claimCount"] = row.get("claimCount") or optional_int(values[8] if len(values) > 8 else "")
        related = split_lines(row.get("relatedNumbers"))
        for index, header in enumerate(headers):
            value = values[index] if index < len(values) else ""
            if header == "权利要求项" and optional_int(value) is not None:
                row["claimCount"] = optional_int(value)
            elif header == "独权项" and optional_int(value) is not None:
                row["independentClaimCount"] = optional_int(value)
            elif header in {"US编号", "EP编号", "IN编号"} and clean_text(value):
                prefix = header[:2]
                entry = f"{prefix}: {clean_text(value)}"
                if entry not in related: related.append(entry)
        if related: row["relatedNumbers"] = "\n".join(related)
        if len(values) > 5 and clean_text(values[5]): row["patentType"] = normalize_patent_type(values[5], number)
        if len(values) > 6 and clean_text(values[6]): row["legalStatus"] = normalize_legal_status(values[6], row.get("legalStatus"))



def numeric_amount(value):
    text = clean_text(value).replace(",", "")
    if not text or excel_date(text): return None
    try:
        return float(text)
    except ValueError:
        return None



def fee_currency(region: str, header: str) -> str:
    upper = header.upper()
    if "RMB" in upper or "CNY" in upper or "中代理" in header: return "CNY"
    if "USD" in upper: return "USD"
    if "EUR" in upper: return "EUR"
    return {"中国":"CNY", "美国":"USD", "欧洲":"EUR", "印度":"INR"}.get(region, "CNY")



def parse_legacy_fees(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value) for value in matrix[0]]
    sections = [(9, 32, "中国"), (36, 60, "美国"), (62, 77, "欧洲"), (79, 87, "印度")]
    fees = []
    for row_index, values in enumerate(matrix[1:], 2):
        number_lines = split_lines(values[3] if len(values) > 3 else "")
        if not number_lines or not looks_like_application_number(number_lines[0]): continue
        application_number = number_lines[0]
        for start, end, region in sections:
            previous_header = ""
            for index in range(start, min(end + 1, len(headers))):
                if headers[index]: previous_header = headers[index]
                raw = clean_text(values[index] if index < len(values) else "")
                if not raw or raw == "/": continue
                paid_date = excel_date(raw)
                if headers[index]:
                    header = headers[index]
                elif paid_date and previous_header:
                    header = previous_header + "（缴费日）"
                else:
                    header = f"原表 {column_name(index + 1)} 列（无标题）"
                amount = None if paid_date else numeric_amount(raw)
                fees.append({
                    "applicationNumber": application_number,
                    "feeRegion": region,
                    "feeType": header.replace("\n", " / "),
                    "dueDate": "", "amount": amount, "rawValue": raw,
                    "currency": fee_currency(region, header),
                    "status": "已缴费" if paid_date else "费用记录",
                    "paidDate": paid_date,
                    "note": f"原费用表 {column_name(index + 1)} 列导入",
                    "sourceKey": f"legacy:{application_number}:{row_index}:{column_name(index + 1)}",
                })
    return fees



def parse_standard_fees(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value) for value in matrix[0]]
    mapping = {name: key for name, key in FEE_COLUMNS}
    rows = []
    for row_index, values in enumerate(matrix[1:], 2):
        row = {}
        for index, header in enumerate(headers):
            key = mapping.get(header)
            if not key: continue
            value = values[index] if index < len(values) else ""
            if key in {"dueDate", "paidDate"}: row[key] = excel_date(value)
            elif key == "amount": row[key] = numeric_amount(value)
            else: row[key] = clean_text(value)
        if row.get("applicationNumber") and row.get("feeType"):
            row["sourceKey"] = f"ledger:{row['applicationNumber']}:{row_index}"
            row.setdefault("status", "待缴费" if row.get("dueDate") else "费用记录")
            rows.append(row)
    return rows



def parse_fee_detail_entries(value, region: str) -> list[tuple[str, float | None, str, str]]:
    entries = []
    for entry in re.split(r"[\n；;]+", clean_text(value)):
        entry = entry.strip()
        if not entry:
            continue
        parts = re.split(r"[=＝:：]", entry, maxsplit=1)
        fee_type = clean_text(parts[0]) or "费用"
        raw = clean_text(parts[1]) if len(parts) > 1 else ""
        currency_match = re.search(r"\b(CNY|RMB|USD|EUR|INR)\b", raw, re.IGNORECASE)
        currency = (currency_match.group(1).upper() if currency_match else fee_currency(region, ""))
        if currency == "RMB":
            currency = "CNY"
        amount_text = re.sub(r"\b(CNY|RMB|USD|EUR|INR)\b", "", raw, flags=re.IGNORECASE).strip()
        entries.append((fee_type, numeric_amount(amount_text), raw, currency))
    return entries



def parse_summary_fees(matrix: list[list]) -> list[dict]:
    if not matrix:
        return []
    headers = [clean_text(value) for value in matrix[0]]
    indexes = {header: index for index, header in enumerate(headers) if header}
    rows = []
    region_columns = {"中国费用明细": "中国", "美国费用明细": "美国", "欧洲费用明细": "欧洲", "印度费用明细": "印度"}
    for row_index, values in enumerate(matrix[1:], 2):
        def value(name):
            index = indexes.get(name)
            return values[index] if index is not None and index < len(values) else ""
        number = clean_text(value("关联申请号"))
        if not number or number.startswith("示例"):
            continue
        title = clean_text(value("专利名称"))
        for column, region in region_columns.items():
            for item_index, (fee_type, amount, raw, currency) in enumerate(parse_fee_detail_entries(value(column), region), 1):
                rows.append({
                    "applicationNumber": number, "patentTitle": title, "feeRegion": region,
                    "feeType": fee_type, "dueDate": "", "amount": amount, "rawValue": raw,
                    "currency": currency, "status": "费用记录", "paidDate": "", "note": "",
                    "sourceKey": f"summary:{number}:{row_index}:{region}:{item_index}",
                })
        next_type = clean_text(value("最近待缴项目"))
        next_due = excel_date(value("最近缴费期限"))
        if next_type or next_due:
            currency = clean_text(value("币种")) or fee_currency(infer_country(number), "")
            raw_amount = clean_text(value("最近待缴金额"))
            rows.append({
                "applicationNumber": number, "patentTitle": title,
                "feeRegion": infer_country(number), "feeType": next_type or "待缴费用",
                "dueDate": next_due, "amount": numeric_amount(raw_amount), "rawValue": raw_amount,
                "currency": currency, "status": clean_text(value("缴费状态")) or ("待缴费" if next_due else "费用记录"),
                "paidDate": excel_date(value("实际缴费日")), "note": clean_text(value("备注")),
                "sourceKey": f"summarydue:{number}:{row_index}",
            })
    return rows



def parse_import_xlsx(data: bytes) -> tuple[list[dict], list[dict]]:
    sheets = xlsx_matrices(data)
    if "专利台账" in sheets:
        rows = parse_standard_patents(sheets["专利台账"])
    elif "专利家族" in sheets:
        rows = parse_family_patents(sheets["专利家族"])
    else:
        rows = parse_standard_patents(next(iter(sheets.values()), []))
    enrich_from_fee_matrix(rows, sheets.get("费用", []))
    unique = {}
    for row in rows:
        number = clean_text(row.get("applicationNumber"))
        if number: unique[number] = {**unique.get(number, {}), **row}
    fee_rows = parse_summary_fees(sheets.get("费用汇总", []))
    fee_rows.extend(parse_standard_fees(sheets.get("缴费台账", [])))
    if "费用" in sheets:
        fee_rows.extend(parse_legacy_fees(sheets["费用"]))
    return list(unique.values()), fee_rows



def parse_xlsx(data: bytes) -> list[dict]:
    return parse_import_xlsx(data)[0]
