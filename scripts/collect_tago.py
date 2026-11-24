"""국토교통부 TAGO 버스노선정보 API에서 한 도시의 노선 목록과 노선별 경유 정류장·상세 정보를 받아 저장한다.

인천(도시코드 23, 기본값)과 강원 시군(춘천 32010 등)에 쓴다. 강원 노선은 다른 출처가 없어 TAGO가 유일한 노선 자료이고,
경로 좌표가 없어 build_region_routes.py에서 정류장 사이를 도로 경로(OSRM)로 이어 그린다.


인천광역시 버스노선 API가 프로그램 호출에 응답하지 않아(HTTP_ERROR 04) 노선번호·유형·현재 정류장 목록을 TAGO에서 받는다.
경로 모양은 인천시 공개 파일(노선별 경유노드, 노드 좌표)을 쓴다 — build_region_routes.py 참고.

TAGO ID는 'ICB' + 한 자리 + 8자리 형식이고, 끝 8자리 앞에 '1'을 붙이면 서울 API·인천 파일의 ID와 같다
(노선 ICB365000161 = 165000161, 정류장 ICB364000628 = 164000628).

사용법: uv run python scripts/collect_tago.py [--date YYYYMMDD] [--city 도시코드]
출력: 인천 data/raw/tago/<수집일>/incheon_routes.json, stations/<노선ID>.json, info/<노선ID>.json(배차간격)
      그 밖 data/raw/tago/<수집일>/<도시코드>/routes.json, stations/<TAGO 노선ID>.json, info/<TAGO 노선ID>.json
"""
import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService/"
ROOT = Path(__file__).resolve().parent.parent
INCHEON = 23  # TAGO 도시코드
DELAY = 0.1


def load_key() -> str:
    load_dotenv(ROOT / ".env")
    key = (os.getenv("SEOUL_BUS_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        sys.exit(".env의 SEOUL_BUS_API_KEY가 비어 있습니다.")
    return unquote(key) if "%" in key else key


def call(session: requests.Session, key: str, op: str, city: int = INCHEON, **params) -> tuple[list[dict], int]:
    for wait in (5, 15, 30, 60, None):
        res = session.get(BASE + op, params={"serviceKey": key, "_type": "json", "cityCode": city, **params}, timeout=60)
        # 서버 동시 세션이 꽉 찼다는 일시 오류(resultCode 99), 게이트웨이 오류(HTTP_ERROR·5xx)는 잠시 쉬었다가 다시 보낸다
        if wait and ("가용한 세션" in res.text or "HTTP_ERROR" in res.text or res.status_code >= 500):
            time.sleep(wait)
            continue
        break
    try:
        body = res.json()["response"]["body"]
    except (ValueError, KeyError, TypeError):
        body = None
    if not isinstance(body, dict):
        sys.exit(f"중단: {op} 응답 이상 {res.status_code} {res.text[:300]}\n같은 명령을 다시 실행하면 이어서 받습니다.")
    items = body.get("items") or {}
    items = items.get("item") or [] if isinstance(items, dict) else []  # 결과가 없으면 items가 빈 문자열로 온다
    return ([items] if isinstance(items, dict) else items), int(body.get("totalCount") or 0)


def local_id(tago_id: str) -> str:
    return "1" + tago_id[-8:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--city", type=int, default=INCHEON, help="TAGO 도시코드 (인천 23, 춘천 32010 …)")
    args = ap.parse_args()
    city, is_incheon = args.city, args.city == INCHEON
    out = ROOT / "data" / "raw" / "tago" / args.date / ("" if is_incheon else str(city))
    (out / "stations").mkdir(parents=True, exist_ok=True)
    session, key = requests.Session(), load_key()

    list_file = out / ("incheon_routes.json" if is_incheon else "routes.json")
    if list_file.exists():
        routes = json.loads(list_file.read_text(encoding="utf-8"))
    else:
        routes, page = [], 1
        while True:
            items, total = call(session, key, "getRouteNoList", city, numOfRows=1000, pageNo=page)
            routes += items
            if not items or len(routes) >= total:
                break
            page += 1
        for r in routes:
            # 인천은 서울 API·인천시 파일과 같은 ID로 바꾸고, 다른 도시는 TAGO ID를 그대로 쓴다
            r["routeno"], r["localId"] = str(r["routeno"]), local_id(r["routeid"]) if is_incheon else r["routeid"]
        list_file.write_text(json.dumps(routes, ensure_ascii=False), encoding="utf-8")
    print(f"도시 {city} 노선 {len(routes)}개")

    todo = [r for r in routes if not (out / "stations" / f"{r['localId']}.json").exists()]
    print(f"남은 노선 {len(todo)}개 정류장 수집")
    for n, r in enumerate(todo, 1):
        items, _ = call(session, key, "getRouteAcctoThrghSttnList", city, routeId=r["routeid"], numOfRows=1000)
        (out / "stations" / f"{r['localId']}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        if n % 50 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
        time.sleep(DELAY)

    # 노선 상세(배차간격)
    (out / "info").mkdir(exist_ok=True)
    todo = [r for r in routes if not (out / "info" / f"{r['localId']}.json").exists()]
    print(f"남은 노선 {len(todo)}개 상세 정보 수집")
    for n, r in enumerate(todo, 1):
        items, _ = call(session, key, "getRouteInfoIem", city, routeId=r["routeid"])
        (out / "info" / f"{r['localId']}.json").write_text(json.dumps(items[0] if items else {}, ensure_ascii=False), encoding="utf-8")
        if n % 50 == 0:
            print(f"  {n}/{len(todo)}", flush=True)
        time.sleep(DELAY)
    print(f"완료 → {out}")


if __name__ == "__main__":
    main()
