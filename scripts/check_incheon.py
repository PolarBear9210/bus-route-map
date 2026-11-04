"""인천광역시_버스노선 조회 API가 이 컴퓨터의 Python에서 응답하는지 확인한다. 키 값은 출력하지 않는다.

사용법: uv run python scripts/check_incheon.py
"""
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = (os.getenv("INCHEON_BUS_API_KEY") or os.getenv("SEOUL_BUS_API_KEY") or "").strip()
if not key:
    sys.exit(".env에 INCHEON_BUS_API_KEY가 없습니다.")

res = requests.get("https://apis.data.go.kr/6280000/busRouteService/getBusRouteId",
                   params={"serviceKey": key, "routeId": "165000111"}, timeout=30)
msg = re.search(r"<(errMsg|resultMsg)>([^<]*)", res.text)
print("HTTP", res.status_code, "→", msg.group(2) if msg else res.text[:200])
no = re.search(r"<ROUTENO>([^<]*)", res.text)
if no:
    print("노선번호:", no.group(1), "(정상 응답)")
