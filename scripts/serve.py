"""web 폴더를 로컬에서 띄우는 개발용 서버. 파일을 고친 뒤 새로고침만 해도 바로 반영되도록 캐시를 끈다.

사용법: python scripts/serve.py [포트(기본 8765)]
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # 브라우저가 매번 서버에 바뀌었는지 묻게 한다 (안 바뀌었으면 304로 가볍게 끝남)
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://localhost:{port}/  ({WEB})")
    ThreadingHTTPServer(("", port), partial(NoCacheHandler, directory=str(WEB))).serve_forever()
