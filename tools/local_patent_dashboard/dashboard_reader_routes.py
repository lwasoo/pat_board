"""HTTP endpoints for the standalone reading workspace."""
import sqlite3
import urllib.parse
import dashboard_reader as reader


class ReaderRoutes:
    def handle_reader_route(self, method):
        route = self.route()
        if not route.path.startswith('/api/reader/') or route.path.startswith('/api/reader/document'):
            return False
        try:
            query = urllib.parse.parse_qs(route.query)
            item_id = int(query.get('id', ['0'])[0])
            library = query.get('scope', ['library'])[0] == 'library'
            if route.path == '/api/reader/items':
                if method == 'GET':
                    self.json_response({'items': reader.list_items()})
                elif method == 'POST':
                    self.json_response({'item': reader.create_item(self.read_json())}, 201)
                elif method == 'DELETE' and item_id > 0:
                    self.json_response(reader.delete_item(item_id))
                else:
                    raise ValueError('请求方式或条目编号无效。')
            elif route.path == '/api/reader/item' and method == 'GET' and item_id > 0:
                self.json_response({'item': reader.item(item_id, library)})
            elif route.path == '/api/reader/prepare' and method == 'POST' and item_id > 0:
                self.json_response(reader.prepare_reading(item_id, library))
            elif route.path == '/api/reader/pdf' and item_id > 0:
                if method == 'GET':
                    self.json_response(reader.pdf_status(item_id, library))
                elif method == 'POST':
                    self.json_response(reader.fetch_pdf(item_id, library), 202)
                else:
                    raise ValueError('请求方式无效。')
            elif route.path == '/api/reader/fulltext' and item_id > 0:
                if method == 'GET':
                    self.json_response(reader.fulltext_status(item_id, library))
                elif method == 'POST':
                    body = self.read_json()
                    self.json_response(reader.fetch_fulltext(item_id, library, body.get('publicationNumber'),
                                                             include_pdf=body.get('includePdf') is True), 202)
                else:
                    raise ValueError('请求方式无效。')
            else:
                self.json_response({'error': '阅读工作台接口不存在。'}, 404)
        except FileNotFoundError as exc:
            self.json_response({'error': str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            self.json_response({'error': str(exc)}, 400)
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            self.json_response({'error': '阅读条目发生变化，请刷新后重试。'}, 409)
        return True
