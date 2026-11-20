"""Shared value normalization; no database or HTTP dependencies."""
from __future__ import annotations

import re
from datetime import datetime, timedelta


def clean_text(value, default="") -> str:
    return str(value or default).strip()



def optional_int(value):
    text = clean_text(value)
    if not text:
        return None
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return None



def normalized_patent(payload: dict) -> dict:
    application_number = clean_text(payload.get("applicationNumber"))
    row = {
        "applicationNumber": application_number,
        "title": clean_text(payload.get("title")),
        "countryRegion": clean_text(payload.get("countryRegion")) or infer_country(application_number),
        "patentType": clean_text(payload.get("patentType"), "发明"),
        "applicationDate": clean_text(payload.get("applicationDate")),
        "publicationDate": clean_text(payload.get("publicationDate")),
        "publicationNumber": clean_text(payload.get("publicationNumber")),
        "grantDate": clean_text(payload.get("grantDate")),
        "expirationDate": clean_text(payload.get("expirationDate")),
        "legalStatus": clean_text(payload.get("legalStatus"), "审中"),
        "department": clean_text(payload.get("department"), "研发部"),
        "productLine": clean_text(payload.get("productLine"), "未归类"),
        "familyNumber": clean_text(payload.get("familyNumber")),
        "familyName": clean_text(payload.get("familyName")),
        "inventors": clean_text(payload.get("inventors")),
        "owner": clean_text(payload.get("owner"), "本公司"),
        "agentReference": clean_text(payload.get("agentReference")),
        "claimCount": optional_int(payload.get("claimCount")),
        "independentClaimCount": optional_int(payload.get("independentClaimCount")),
        "pageCount": optional_int(payload.get("pageCount")),
        "cnDraftStatus": clean_text(payload.get("cnDraftStatus")),
        "foreignDraftStatus": clean_text(payload.get("foreignDraftStatus")),
        "relatedNumbers": clean_text(payload.get("relatedNumbers")),
        "caseNotes": clean_text(payload.get("caseNotes")),
    }
    if not row["applicationNumber"] or not row["title"]:
        raise ValueError("申请号和专利名称不能为空。")
    if row["patentType"] not in {"发明", "实用新型", "外观设计"}:
        raise ValueError("专利类型不正确。")
    return row



def excel_date(value) -> str:
    text = clean_text(value)
    if not text or text == "/":
        return ""
    if text.replace(".", "", 1).isdigit():
        number = float(text)
        if 20000 <= number <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=number)).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""



def split_lines(value) -> list[str]:
    return [part.strip() for part in clean_text(value).replace("\r", "\n").split("\n") if part.strip() and part.strip() != "/"]



def simple_identifier(value) -> str:
    text = clean_text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text



def infer_country(application_number: str) -> str:
    upper = application_number.upper().replace(" ", "")
    if upper.startswith("CN"): return "中国"
    if upper.startswith("EP"): return "欧洲"
    if upper.startswith("IN"): return "印度"
    if upper.startswith("PCT") or upper.startswith("WO"): return "PCT"
    if upper.startswith("US") or "/" in upper: return "美国"
    return "其他"



def looks_like_application_number(value: str) -> bool:
    compact = clean_text(value).upper().replace(" ", "")
    return bool(re.match(r"^(CN|US|EP|IN|PCT|WO)[A-Z0-9/.,-]*\d[A-Z0-9/.,-]*$", compact)
                or re.match(r"^\d{2}/+[\d,]+$", compact))



def normalize_patent_type(value, application_number="") -> str:
    text = clean_text(value)
    if "实用" in text or application_number.upper().rstrip().endswith("U"):
        return "实用新型"
    if "外观" in text or application_number.upper().rstrip().endswith("D"):
        return "外观设计"
    return "发明"



def normalize_legal_status(*values) -> str:
    text = " ".join(clean_text(value) for value in values if clean_text(value) and not excel_date(value))
    if any(word in text for word in ("获证", "授权", "核准")): return "已授权"
    if "复审" in text: return "复审"
    if "实审" in text: return "实审"
    if "OA" in text.upper(): return "OA"
    if "撤回" in text: return "撤回"
    if "驳回" in text: return "驳回"
    if "终止" in text: return "终止"
    if "未公开" in text: return "未公开"
    if "公开" in text: return "已公开"
    if any(word in text for word in ("递交", "申请")): return "已申请"
    return "状态待补"



def parse_claims(value) -> tuple[int | None, int | None]:
    parts = clean_text(value).replace("／", "/").split("/")
    total = optional_int(parts[0]) if parts else None
    independent = optional_int(parts[1]) if len(parts) > 1 else None
    return total, independent
