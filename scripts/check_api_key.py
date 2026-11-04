""".env의 서울 버스 API 키가 동작하는지 노선 검색 한 번으로 확인한다. 키 값은 출력하지 않는다.
headerCd 4(결과 없음)는 키 문제가 아니라 검색어에 맞는 노선이 없다는 뜻이다.

사용법: uv run python scripts/check_api_key.py
"""
import os
import sys
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

key = os.getenv("SEOUL_BUS_API_KEY", "").strip().strip('"').strip("'")
if not key:
    sys.exit(".env의 SEOUL_BUS_API_KEY가 비어 있습니다.")
if "%" in key:
    # Encoding 키를 넣었으면 Decoding 키로 바꿔 쓴다 (requests가 다시 인코딩함)
    print("Encoding 키로 보여 디코딩해서 사용합니다.")
    key = unquote(key)

url = "http://ws.bus.go.kr/api/rest/busRouteInfo/getBusRouteList"
res = requests.get(url, params={"serviceKey": key, "strSrch": "472", "resultType": "json"}, timeout=15)
print("HTTP", res.status_code)

try:
    body = res.json()
except ValueError:
    # 키 오류는 게이트웨이가 XML로 응답한다
    print(res.text[:500])
    sys.exit(1)

header = body.get("msgHeader", {})
print("headerCd", header.get("headerCd"), "/", header.get("headerMsg"))
items = (body.get("msgBody") or {}).get("itemList") or []
for it in items[:5]:
    print(f"  {it.get('busRouteNm')}  {it.get('stStationNm')} ↔ {it.get('edStationNm')}  (routeId {it.get('busRouteId')})")
sys.exit(0 if header.get("headerCd") == "0" else 1)
