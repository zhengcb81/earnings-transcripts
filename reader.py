#!/usr/bin/env python3
"""
美股电话会议纪要 - 本地Web阅读器
Usage: python3 reader.py [--port 8765]
Then open http://localhost:8765 in browser
"""

import json
import re
import argparse
import webbrowser
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from common import load_companies, parse_transcript_header, load_config, get_path

_BASE_DIR = Path(__file__).parent
__TEMPLATE_FILE = _BASE_DIR / "templates" / "index.html"


class ReaderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/' or parsed.path == '/index.html':
            self.serve_index()
        elif parsed.path == '/api/data':
            self.serve_data()
        else:
            self.send_error(404)

    def serve_index(self):
        data = self.build_data()
        html_template = _TEMPLATE_FILE.read_text(encoding="utf-8")
        html = html_template.replace('/*__DATA__*/{}/*__END__*/', json.dumps(data, ensure_ascii=False))
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_data(self):
        data = self.build_data()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def build_data(self):
        cfg = load_config()
        companies = load_companies(cfg)
        transcripts_dir = get_path(cfg, "transcripts_dir")
        transcripts = {}

        for company in companies:
            ticker = company["ticker"]
            company_dir = transcripts_dir / ticker
            if not company_dir.exists():
                transcripts[ticker] = []
                continue

            files = sorted(company_dir.glob("*earnings_call*.txt"), reverse=True)
            entries = []
            for f in files:
                content = f.read_text(encoding="utf-8")
                # Parse header
                meta = parse_transcript_header(content)

                # Extract body (after the header separator)
                fmt = cfg.get("format", {})
                sep = fmt.get("separator_char", "=") * fmt.get("separator_width", 70)
                parts = content.split(sep)
                body = parts[-1].strip() if len(parts) > 1 else content

                try:
                    char_count = int(meta.get("Characters", len(body)))
                except (ValueError, TypeError):
                    char_count = len(body)

                entry = {
                    "quarter": meta.get("Quarter", f.stem),
                    "company": meta.get("Company", ticker),
                    "url": meta.get("URL", ""),
                    "char_count": char_count,
                    "content": body,
                    "filename": f.name,
                }

                # Load bilingual version if available (JSON format)
                bilingual_path = f.parent / f.name.replace("_earnings_call", "_bilingual").replace(".txt", ".json")
                if not bilingual_path.exists():
                    # Also try .txt for old format
                    bilingual_path = f.parent / f.name.replace("_earnings_call", "_bilingual")
                if bilingual_path.exists():
                    bcontent = bilingual_path.read_text(encoding="utf-8")
                    if bilingual_path.suffix == ".json":
                        try:
                            entry["bilingual_data"] = json.loads(bcontent)
                        except (json.JSONDecodeError, OSError):
                            entry["bilingual_data"] = None
                    else:
                        # Old text format - skip
                        entry["bilingual_data"] = None
                else:
                    entry["bilingual_data"] = None

                entries.append(entry)

            transcripts[ticker] = entries

        return {"companies": companies, "transcripts": transcripts}

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def main():
    parser = argparse.ArgumentParser(description="美股电话会议纪要阅读器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), ReaderHandler)
    url = f"http://localhost:{args.port}"
    print(f"阅读器已启动: {url}")
    print(f"按 Ctrl+C 退出")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()


if __name__ == "__main__":
    main()
