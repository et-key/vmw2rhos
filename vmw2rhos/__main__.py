"""Local, dependency-free planning UI. No live migration endpoints exposed."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

from .planner import build_plan
from .comparison import compare, observation_template
from .store import ConflictError, Store

WEB = Path(__file__).parent / 'web'


def make_handler(store, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, payload, content_type='application/json; charset=utf-8'):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            hosts = (f'127.0.0.1:{port}', f'localhost:{port}')
            if self.headers.get('Host') not in hosts:
                self.deny('ローカル接続のみ利用できます。')
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in tuple(f'http://{h}' for h in hosts):
                self.deny('異なるサイトからの操作は許可されていません。')
                return False
            return True

        def deny(self, message):
            # Consume bounded request bodies so Windows does not reset the
            # connection before the client receives the rejection response.
            if self.command == 'POST':
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if 0 < size <= 2_000_000:
                        self.rfile.read(size)
                except (ValueError, OSError):
                    pass
            self.reply(403, {'error': message})

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path == '/api/inventory':
                self.reply(200, store.load())
            elif path == '/api/plan':
                saved = store.load()
                self.reply(200, dict(revision=saved['revision'], **build_plan(saved['inventory'])))
            elif path == '/api/sample':
                self.reply(200, json.loads((WEB / 'sample.json').read_text(encoding='utf-8')))
            elif path in ('/', '/app.js', '/style.css'):
                name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                              '/style.css': ('style.css', 'text/css')}[path]
                self.reply(200, (WEB / name).read_bytes(), mime + '; charset=utf-8')
            else:
                self.reply(404, {'error': '見つかりません。'})

        def do_POST(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path not in ('/api/inventory', '/api/check', '/api/compare', '/api/observation-template'):
                self.reply(404, {'error': '見つかりません。'})
                return
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('application/jsonで送信してください。')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2_000_000:
                    raise ValueError('入力は2MB以内で指定してください。')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('JSONオブジェクトを指定してください。')
                if path == '/api/check':
                    self.reply(200, build_plan(data.get('inventory')))
                elif path == '/api/compare':
                    self.reply(200, compare(data.get('inventory'), data.get('observation')))
                elif path == '/api/observation-template':
                    self.reply(200, observation_template(data.get('inventory')))
                else:
                    self.reply(200, store.save(data.get('inventory'), data.get('revision')))
            except ConflictError as error:
                self.reply(409, {'error': str(error)})
            except (ValueError, UnicodeDecodeError) as error:
                self.reply(400, {'error': str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description='vmw2rhos ローカル移行計画ツール')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=Path('.vmw2rhos/plans.sqlite3'))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('portは1〜65535で指定してください。')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(Store(args.data), args.port))
    print(f'http://127.0.0.1:{args.port} をブラウザーで開いてください。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

