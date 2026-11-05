#!/usr/bin/env python3
"""HTTP/HTTPS の簡易な受け口。来たリクエストを 1 行 1 件の JSON で記録し、素っ気ない応答を返す。

標準ライブラリだけで動く。実行や転送はせず、ただ記録して 404 を返すだけ。
443 で待ち受けるときは自己署名の証明書を渡す（IP 宛てのスキャンは証明書を確かめずに来ることが多い）。

使い方:
  python3 http_server.py --port 80 --log /opt/honeypot/http/requests.log
  python3 http_server.py --port 443 --cert /opt/honeypot/http/cert.pem --key /opt/honeypot/http/key.pem --log ...
"""
from __future__ import annotations

import argparse
import json
import ssl
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 2048  # 記録する本文の最大バイト数


class Handler(BaseHTTPRequestHandler):
    log_path = "/opt/honeypot/http/requests.log"
    protocol_version = "HTTP/1.1"

    def _record(self) -> None:
        length = min(int(self.headers.get("Content-Length", 0) or 0), MAX_BODY)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        event = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "src_ip": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "host": self.headers.get("Host", ""),
            "ua": self.headers.get("User-Agent", ""),
            "body": body,
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _reply(self) -> None:
        self._record()
        payload = b"Not Found\n"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Server", "nginx")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = _reply

    def log_message(self, *args) -> None:  # 標準エラーへの既定のログは出さない
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--log", default="/opt/honeypot/http/requests.log")
    ap.add_argument("--cert")
    ap.add_argument("--key")
    a = ap.parse_args()
    Handler.log_path = a.log
    httpd = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    if a.cert and a.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
