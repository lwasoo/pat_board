"""Document-specific HTTP routes, kept separate from ledger routes."""
import sqlite3
import urllib.parse

import dashboard_documents as documents


class DocumentRoutes:
    def handle_document_route(self, method):
        route = self.route()
        library = route.path.startswith("/api/reader/document")
        base = "/api/reader/document" if library else "/api/patents/document"
        if route.path != base and not route.path.startswith(base + "/"):
            return False
        try:
            if method == "GET" and route.path == base + "/capabilities":
                self.json_response(documents.capabilities())
                return True
            query = urllib.parse.parse_qs(route.query)
            patent_id = int(query.get("id", ["0"])[0])
            if patent_id <= 0:
                raise ValueError("请选择专利。")
            if method == "GET" and route.path == base:
                self.json_response(documents.document_status(patent_id, library))
            elif method == "GET" and route.path in {base + "/file", base + "/page"}:
                page = int(query.get("page", ["1"])[0]) if route.path.endswith("/page") else None
                data, content_type, name = documents.document_file(patent_id, page, query.get("v", [None])[0], library)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(name))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            elif method == "POST" and route.path == base:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= documents.MAX_PDF_SIZE:
                    raise ValueError("PDF 为空或超过 50 MB。")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("PDF 上传不完整，请重试。")
                self.json_response(documents.upload_document(patent_id, data,
                    query.get("filename", ["专利.pdf"])[0], library), 202)
            elif method == "POST" and route.path == base + "/retry":
                self.json_response(documents.retry_document(patent_id, library), 202)
            elif method == "PATCH" and route.path == base:
                self.json_response(documents.correct_analysis(patent_id, self.read_json(), library))
            elif method == "DELETE" and route.path == base:
                self.json_response(documents.delete_document(patent_id, library))
            else:
                self.json_response({"error": "文件接口不存在。"}, 404)
        except FileNotFoundError as exc:
            self.json_response({"error": str(exc)}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except sqlite3.IntegrityError:
            self.json_response({"error": "关联专利已变更，请刷新后重试。"}, 409)
        except OSError:
            self.json_response({"error": "无法读写专利文件，请检查 data 目录权限。"}, 500)
        return True
