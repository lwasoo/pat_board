"""Managed patent PDFs, local OCR and reviewable claim/drawing references.

Uploaded files stay local. Extraction is advisory and never overwrites ledger fields.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import dashboard_config as settings
from dashboard_database import connect
from dashboard_patent_text import (
    PARSER_VERSION, classify_page, jurisdiction, reading_order, ocr_reading_order,
    split_sections, parse_claims, extract_metadata, part_references, check_declared_counts,
)

MAX_PDF_SIZE = 50 * 1024 * 1024
MAX_PAGES = 150
OCR_ENGINE_MISSING = "未找到 Tesseract OCR 引擎。请安装 Tesseract 5，或通过 PATENT_TESSERACT_CMD 指定 tesseract.exe 的完整路径，再重新识别；识别模型不能代替 OCR 程序。"
_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="patent-ocr")
_LOCK = threading.RLock()
_ACTIVE: set[str] = set()


def pdf_engine():
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:
        raise ValueError("请先安装 PDF 组件：python -m pip install -r requirements.txt") from exc


def render_page(page, scale):
    """Bound raster memory even for unusually large PDF page dimensions."""
    longest = max(page.rect.width, page.rect.height)
    if not math.isfinite(longest) or longest <= 0:
        raise ValueError("PDF 页面尺寸不正确。")
    scale = min(scale, 3000 / longest)
    return page.get_pixmap(matrix=pdf_engine().Matrix(scale, scale), alpha=False)


def render_for_ocr(page):
    """Preserve a full-page scan's native pixels instead of resampling its glyphs."""
    scale = 2.5
    page_area = page.rect.width * page.rect.height
    for image in page.get_image_info():
        x0, y0, x1, y1 = image["bbox"]
        width, height = x1-x0, y1-y0
        if width > 0 and height > 0 and width*height >= .8*page_area:
            scale = max(1, min(image["width"]/width, image["height"]/height))
            break
    return render_page(page, scale)


def ocr_command():
    configured = os.environ.get("PATENT_TESSERACT_CMD", "").strip().strip('"')
    portable = settings.app_dir() / "ocr/tesseract/tesseract.exe"
    executable = str(portable) if not configured and sys.platform == "win32" and portable.is_file() else shutil.which(configured or "tesseract")
    if configured and not executable and Path(configured).is_file():
        executable = configured
    if not configured and not executable and sys.platform == "win32":
        # A portable distribution must include the complete runtime (DLLs too).
        candidates = [settings.app_dir() / "ocr/tesseract/tesseract.exe"]
        for variable, default in [("ProgramFiles", "C:/Program Files"), ("ProgramFiles(x86)", "C:/Program Files (x86)")]:
            candidates.append(Path(os.environ.get(variable, default)) / "Tesseract-OCR/tesseract.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.extend([Path(local)/"Programs/Tesseract-OCR/tesseract.exe", Path(local)/"Tesseract-OCR/tesseract.exe"])
        executable = next((str(path) for path in candidates if path.is_file()), None)
    if not executable:
        # Models are data, not a runnable OCR engine. Never start '--tessdata-dir'.
        return []
    model_dir = os.environ.get("PATENT_TESSDATA_DIR")
    if not model_dir:
        for directory in (settings.app_dir() / "ocr/tessdata", settings.resource_path("ocr/tessdata")):
            if (directory / "chi_sim.traineddata").is_file() and (directory / "eng.traineddata").is_file():
                model_dir = str(directory)
                break
    command = [executable]
    if model_dir:
        command += ["--tessdata-dir", model_dir]
    return command


def capabilities():
    try:
        pdf_engine()
        pdf = True
    except ValueError:
        pdf = False
    cmd = ocr_command()
    languages = []
    if cmd:
        try:
            result = subprocess.run(cmd + ["--list-langs"], capture_output=True, text=True, timeout=10)
            languages = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    return {"pdf": pdf, "engineFound": bool(cmd), "ocr": "chi_sim" in languages and "eng" in languages,
            "languages": languages, "maxBytes": MAX_PDF_SIZE, "maxPages": MAX_PAGES}


def document_record(patent_id):
    with closing(connect()) as conn:
        patent = conn.execute("SELECT id FROM patents WHERE id=?", (patent_id,)).fetchone()
        if not patent:
            raise FileNotFoundError("专利不存在。")
        row = conn.execute("SELECT * FROM patent_documents WHERE patent_id=?", (patent_id,)).fetchone()
        return dict(row) if row else None


def managed_path(relative):
    base = settings.DATA_DIR.resolve()
    path = (base / relative).resolve()
    if base not in path.parents:
        raise ValueError("文件路径不正确。")
    return path


def remove_managed_document(relative):
    # Remove only this application's UUID document directory, never external PDFs.
    path = managed_path(relative)
    if path.parent.parent == (settings.DATA_DIR / "documents").resolve() and re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
        with _LOCK:
            shutil.rmtree(path.parent, ignore_errors=True)


def read_analysis(path):
    try:
        return json.loads(path.with_name("analysis.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"status": "interrupted", "message": "识别结果未保存，请重新识别。"}


def save_analysis(path, result):
    target = path.with_name("analysis.json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def record_missing_ocr(result, page_number):
    result["ocrUnavailable"] = True
    pages = result.setdefault("ocrUnavailablePages", [])
    if page_number not in pages:
        pages.append(page_number)
    if OCR_ENGINE_MISSING not in result["warnings"]:
        result["warnings"].append(OCR_ENGINE_MISSING)


def normalize_completed_status(result):
    """Also correct legacy cached results that reported success with no content."""
    if result.get("status") != "ready":
        return
    missing = [w for w in result.get("warnings", []) if "未找到 Tesseract OCR 引擎" in w]
    if missing:
        for warning in missing:
            page = re.search(r"第\s*(\d+)\s*页", warning)
            if page:
                record_missing_ocr(result, int(page[1]))
        result["ocrUnavailable"] = True
        result["warnings"] = [OCR_ENGINE_MISSING] + [w for w in result["warnings"] if "未找到 Tesseract OCR 引擎" not in w]
    usable = (any(p.get("text", "").strip() for p in result.get("pages", []))
              or any(c.get("text", "").strip() for c in result.get("claims", []))
              or bool(result.get("description", "").strip())
              or any(str(value).strip() for value in result.get("metadata", {}).values()))
    if not usable:
        result["status"] = "failed"
        result["errorCode"] = "OCR_ENGINE_MISSING" if result.get("ocrUnavailable") else "NO_TEXT_EXTRACTED"
        result["message"] = ("未识别出文字：" + OCR_ENGINE_MISSING if result.get("ocrUnavailable")
                             else "未识别出文字，请检查 PDF、OCR 引擎及语言模型；原 PDF 仍可查看。")
    elif result.get("ocrUnavailable"):
        result["message"] = "已提取可用文字；未找到 Tesseract，扫描页或图号 OCR 未完成。"


def document_status(patent_id):
    with _LOCK:
        row = document_record(patent_id)
        if not row:
            return {"document": None, "capabilities": capabilities()}
        path = managed_path(row["object_path"])
        analysis = read_analysis(path)
        normalize_completed_status(analysis)
        analysis["needsReanalysis"] = analysis.get("status") == "ready" and analysis.get("parserVersion", 1) < PARSER_VERSION
        if analysis.get("status") in {"queued", "processing"} and str(path) not in _ACTIVE:
            analysis = {**analysis, "status": "interrupted", "message": "上次识别被中断，请重新识别。"}
        return {"document": {"fileName": row["file_name"], "size": row["size"],
                             "uploadedAt": row["uploaded_at"], "version": path.parent.name,
                             "analysis": analysis}}


def upload_document(patent_id, data, file_name):
    if not data or len(data) > MAX_PDF_SIZE or not data.startswith(b"%PDF-"):
        raise ValueError("请上传不超过 50 MB 的有效 PDF 文件。")
    engine = pdf_engine()
    try:
        with engine.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or not 0 < len(pdf) <= MAX_PAGES:
                raise ValueError("PDF 不能加密，且页数需在 1 至 150 页之间。")
    except (RuntimeError, engine.FileDataError) as exc:
        raise ValueError("PDF 文件损坏或无法读取。") from exc
    with _LOCK:
        previous = document_record(patent_id)
        if len(_ACTIVE) >= 4:
            raise ValueError("已有多个文件等待识别，请稍后再上传。")
        directory = settings.DATA_DIR / "documents" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        path = directory / "original.pdf"
        path.write_bytes(data)
        relative = path.relative_to(settings.DATA_DIR).as_posix()
        try:
            save_analysis(path, {"status": "queued", "message": "等待识别", "progress": 0})
            with closing(connect()) as conn, conn:
                conn.execute("""INSERT INTO patent_documents(patent_id,object_path,file_name,content_type,size)
                    VALUES(?,?,?,'application/pdf',?) ON CONFLICT(patent_id) DO UPDATE SET
                    object_path=excluded.object_path,file_name=excluded.file_name,size=excluded.size,
                    content_type=excluded.content_type,uploaded_at=CURRENT_TIMESTAMP""",
                    (patent_id, relative, Path(file_name.replace('\\', '/')).name[:200] or "专利.pdf", len(data)))
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        queue_extraction(path)
        if previous:
            # An in-flight older job cleans itself up when it observes replacement.
            old = managed_path(previous["object_path"])
            if str(old) not in _ACTIVE:
                remove_managed_document(previous["object_path"])
        return document_status(patent_id)


def queue_extraction(path):
    key = str(path)
    if key in _ACTIVE:
        return
    if len(_ACTIVE) >= 4:
        raise ValueError("识别队列已满，请稍后再试。")
    _ACTIVE.add(key)
    _WORKER.submit(extract_document, path)


def retry_document(patent_id):
    with _LOCK:
        row = document_record(patent_id)
        if not row:
            raise FileNotFoundError("尚未上传 PDF。")
        path = managed_path(row["object_path"])
        if not path.is_file():
            raise FileNotFoundError("原始 PDF 文件不存在，可能被手工改名或移走。请恢复数据库记录的文件路径，或在页面重新上传；程序默认保存为 original.pdf。")
        if str(path) not in _ACTIVE:
            if len(_ACTIVE) >= 4:
                raise ValueError("识别队列已满，请稍后再试。")
            save_analysis(path, {"status": "queued", "progress": 0, "message": "等待重新识别"})
            queue_extraction(path)
        return document_status(patent_id)


def delete_document(patent_id):
    with _LOCK:
        row = document_record(patent_id)
        with closing(connect()) as conn, conn:
            conn.execute("DELETE FROM patent_documents WHERE patent_id=?", (patent_id,))
        if row:
            path = managed_path(row["object_path"])
            if str(path) not in _ACTIVE:
                remove_managed_document(row["object_path"])
        return {"deleted": bool(row)}


def ocr_page(pixmap, psm=3, digits=False, language=None):
    command = ocr_command()
    if not command:
        raise ValueError("未找到 Tesseract OCR 引擎。识别模型不能代替 tesseract.exe；请安装 Tesseract 5，或通过 PATENT_TESSERACT_CMD 指定其完整路径，再重新识别。")
    command += ["stdin", "stdout", "-l", "eng" if digits else (language or "chi_sim+eng"), "--psm", str(psm)]
    if digits:
        command += ["-c", "tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyz"]
    try:
        result = subprocess.run(command + ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"], input=pixmap.tobytes("png"),
                                capture_output=True, timeout=90)
    except FileNotFoundError as exc:
        raise ValueError("无法启动 Tesseract OCR 引擎，请检查 tesseract.exe 的安装位置及 PATENT_TESSERACT_CMD 配置。") from exc
    except OSError as exc:
        raise ValueError("Tesseract OCR 引擎启动失败，请检查程序位数和依赖 DLL；使用便携版时需保留完整安装目录。") from exc
    if result.returncode:
        raise ValueError("OCR 未能完成，请确认 Tesseract 及 chi_sim、eng 模型已安装。")
    if not result.stdout.startswith(b"level\t"):
        raise ValueError("OCR 未返回文字坐标，请检查 Tesseract 配置。")
    rows = []
    lines = {}
    for item in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        confidence = float(item["conf"])
        box = [int(item["left"]) / pixmap.width, int(item["top"]) / pixmap.height,
               int(item["width"]) / pixmap.width, int(item["height"]) / pixmap.height]
        rows.append({"text": text, "confidence": round(confidence, 1), "box": box, "source": "ocr"})
        key = (item["block_num"], item["par_num"], item["line_num"])
        lines.setdefault(key, []).append(text)
    text = "\n".join(" ".join(words) for words in lines.values())
    # Chinese tokens have no inter-word spaces in a patent's logical text.
    text = re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text)
    text = re.sub(r"([（(])\s*(\d{1,5}[a-zA-Z]?)\s*([)）])", r"\1\2\3", text)
    return text, rows


def page_section(text, previous):
    return classify_page(text, previous)


def extract_references(claims, description):
    return part_references(claims, description)


def link_references(references, pages):
    for reference in references:
        matches = []
        for page in pages:
            if page["section"] != "drawings":
                continue
            candidates = [label for label in page.get("labels", []) if label["text"] == reference["number"]]
            manual = [label for label in candidates if label["source"] == "manual"]
            matches.extend(dict(label, page=page["number"]) for label in (manual or candidates))
        reference["matches"] = sorted(matches, key=lambda m: (m["source"] != "manual", m["page"], -m["confidence"]))
        reference["status"] = "candidate" if reference["matches"] else "unmatched"
    return references


def extract_document(path):
    result = {"status": "processing", "progress": 0, "pages": [], "claims": [], "references": [], "warnings": [], "parserVersion": PARSER_VERSION}
    try:
        engine = pdf_engine()
        sections = {"basic": [], "claims": [], "description": [], "drawings": []}
        previous = None
        if not path.is_file():
            raise ValueError("原始 PDF 文件不存在，可能被手工改名或移走，请在页面重新上传。")
        with engine.open(path) as pdf:
            ocr_enabled = bool(ocr_command())
            result["pageCount"] = len(pdf)
            country = jurisdiction(pdf[0].get_text(sort=True))
            section_pages = {key: [] for key in sections}
            for index, page in enumerate(pdf):
                # Replacement/deletion invalidates this job; never resurrect old records.
                if not path.is_file():
                    return
                raw = page.get_text(sort=True)
                text, layout = reading_order(page, country)
                used_ocr = False
                uncertain = []
                pix = None
                native_body = len(re.findall(r"[\u3400-\u9fff]", text)) >= 8
                if len(re.sub(r"\s", "", raw)) < 60 and not native_body and page_section(raw, None) != "drawings":
                    if not ocr_enabled:
                        record_missing_ocr(result, index+1)
                    else:
                        pix = render_for_ocr(page)
                        try:
                            recognized, tokens = ocr_page(pix, language="eng" if country == "US" else None)
                            if recognized.strip():
                                raw = recognized
                                country = country or jurisdiction(recognized)
                                text, layout = ocr_reading_order(tokens, country, recognized)
                                used_ocr = True
                                uncertain = [w for w in tokens if w["confidence"] < 60][:100]
                                if uncertain:
                                    result["warnings"].append(f"第 {index+1} 页有 {len(uncertain)} 个低置信度文字片段，请对照原 PDF。")
                            elif not text.strip():
                                result["warnings"].append(f"第 {index+1} 页未识别出文字，请对照原 PDF。")
                        except (ValueError, subprocess.TimeoutExpired) as exc:
                            result["warnings"].append(f"第 {index+1} 页：{exc}")
                parts, previous = split_sections(text, previous, raw, index+1)
                page_sections = list(dict.fromkeys(part["section"] for part in parts))
                section = next(s for s in ("claims", "description", "drawings", "basic") if s in page_sections)
                labels = []
                if section == "drawings":
                    width, height = page.rect.width, page.rect.height
                    for word in page.get_text("words"):
                        if word[1] < .12*height or word[3] > .94*height:
                            continue
                        number = word[4].strip("()（）.,")
                        if re.fullmatch(r"\d{1,5}[a-zA-Z]?", number):
                            labels.append({"text": number, "box": [word[0]/width, word[1]/height,
                                (word[2]-word[0])/width, (word[3]-word[1])/height],
                                "source": "pdf", "confidence": 100})
                    if not ocr_enabled:
                        record_missing_ocr(result, index+1)
                    else:
                        try:
                            pix = pix or render_for_ocr(page)
                            candidates = []
                            for segmentation in (11, 6):
                                _, detected = ocr_page(pix, psm=segmentation, digits=True)
                                candidates.extend(detected)
                            for label in candidates:
                                if re.fullmatch(r"\d{1,5}[a-zA-Z]?", label["text"]) and label["confidence"] >= 50:
                                    if not any(l["text"] == label["text"] and abs(l["box"][0]-label["box"][0]) < .03 and abs(l["box"][1]-label["box"][1]) < .03 for l in labels):
                                        labels.append(label)
                        except (ValueError, subprocess.TimeoutExpired) as exc:
                            result["warnings"].append(f"第 {index+1} 页图号：{exc}")
                for part in parts:
                    sections[part["section"]].append(part["text"])
                    if index+1 not in section_pages[part["section"]]:
                        section_pages[part["section"]].append(index+1)
                result["pages"].append({"number": index+1, "section": section, "text": text,
                    "ocr": used_ocr, "labels": labels, "uncertainWords": uncertain,
                    "sections": page_sections, "layout": layout})
                result.update(progress=round((index+1)/len(pdf)*100), message=f"已识别 {index+1}/{len(pdf)} 页")
                with _LOCK:
                    if path.is_file():
                        save_analysis(path, result)
        result["sectionPages"] = section_pages
        result["jurisdiction"] = country
        result["metadata"] = extract_metadata("\n".join(sections["basic"]))
        result["description"] = "\n\n".join(sections["description"])
        result["claims"] = parse_claims("\n".join(sections["claims"]))
        result["references"] = link_references(extract_references(result["claims"], result["description"]), result["pages"])
        result["quality"], count_warnings = check_declared_counts("\n".join(sections["basic"]), section_pages, len(result["claims"]))
        result["expectedClaimCount"] = result["quality"]["expectedClaimCount"]
        result["warnings"].extend(count_warnings)
        unmatched = sum(not r["matches"] for r in result["references"])
        if unmatched:
            result["warnings"].append(f"{unmatched} 个部件编号未在附图中定位，可对照原图补标。")
        if not result["claims"]:
            result["warnings"].append("未可靠识别权利要求，请对照原 PDF 校对或补充。")
        result.update(status="ready", message="识别完成，请核对文字及附图编号。" if result["quality"]["passed"] is not False else "章节或项数与首页不一致，请对照 PDF 核对。", progress=100)
        normalize_completed_status(result)
    except Exception as exc:
        result.update(status="failed", message=f"识别未完成：{exc}")
    finally:
        with _LOCK:
            try:
                if path.is_file():
                    save_analysis(path, result)
            finally:
                _ACTIVE.discard(str(path))
            with closing(connect()) as conn:
                exists = conn.execute("SELECT 1 FROM patent_documents WHERE object_path=?",
                    (path.relative_to(settings.DATA_DIR).as_posix(),)).fetchone()
            if not exists:
                remove_managed_document(path.relative_to(settings.DATA_DIR).as_posix())


def document_file(patent_id, page=None, version=None):
    row = document_record(patent_id)
    if not row:
        raise FileNotFoundError("尚未上传 PDF。")
    path = managed_path(row["object_path"])
    if version and version != path.parent.name:
        raise FileNotFoundError("文件版本已更新，请刷新。")
    if page is None:
        return path.read_bytes(), "application/pdf", row["file_name"]
    engine = pdf_engine()
    with engine.open(path) as pdf:
        if not 1 <= page <= len(pdf):
            raise ValueError("页码超出范围。")
        image = render_page(pdf[page-1], 1.6)
        return image.tobytes("png"), "image/png", f"page-{page}.png"


def correct_analysis(patent_id, payload):
    with _LOCK:
        row = document_record(patent_id)
        if not row:
            raise FileNotFoundError("尚未上传 PDF。")
        path = managed_path(row["object_path"])
        if payload.get("version") != path.parent.name:
            raise ValueError("PDF 已更新，请刷新后再校对。")
        result = read_analysis(path)
        if result.get("status") != "ready" or str(path) in _ACTIVE:
            raise ValueError("请等待识别完成后再校对。")
        if "claimsText" in payload:
            text = str(payload["claimsText"])[:200000]
            claims = parse_claims(text)
            if text.strip() and not claims:
                raise ValueError("请按“1. …”逐条填写权利要求。")
            result["claims"] = claims
        if "description" in payload:
            result["description"] = str(payload["description"])[:500000]
        if "metadata" in payload:
            data = payload["metadata"]
            if not isinstance(data, dict):
                raise ValueError("基本信息格式不正确。")
            result["metadata"] = {key: str(data.get(key, ""))[:500] for key in extract_metadata("")}
        if "clearAnnotations" in payload:
            page = next((p for p in result["pages"] if p["number"] == int(payload["clearAnnotations"])), None)
            if page is None:
                raise ValueError("页码不正确。")
            page["labels"] = [label for label in page["labels"] if label["source"] != "manual"]
        if "annotation" in payload:
            item = payload["annotation"]
            page = next((p for p in result["pages"] if p["number"] == int(item["page"])), None)
            number = str(item["number"])
            box = [float(n) for n in item["box"]]
            if page is None or not re.fullmatch(r"\d{1,5}[a-zA-Z]?", number) or len(box) != 4 or not all(math.isfinite(n) and 0 <= n <= 1 for n in box) or box[0]+box[2] > 1 or box[1]+box[3] > 1:
                raise ValueError("图号标注不正确。")
            page["section"] = "drawings"
            page["labels"].append({"text": number, "box": box, "source": "manual", "confidence": 100})
        result["references"] = link_references(extract_references(result["claims"], result.get("description", "")), result["pages"])
        result["reviewed"] = True
        save_analysis(path, result)
        return document_status(patent_id)
