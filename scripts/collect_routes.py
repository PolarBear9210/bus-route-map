"""서울시 버스 API에서 전체 노선 목록과 노선별 경로 좌표·정류장을 받아 저장한다.

노선마다 data/raw/seoul_api/<수집일>/routes/<busRouteId>.json 파일 하나로 저장하고,
이미 받은 노선은 건너뛴다. 호출 한도 초과 등으로 멈추면 같은 명령을 다시 실행하면 이어서 받는다.

사용법: uv run python scripts/collect_routes.py [--date YYYYMMDD] [--limit N]
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

BASE = "http://ws.bus.go.kr/api/rest/busRouteInfo/"
ROOT = Path(__file__).resolve().parent.parent
DELAY = 0.1  # 호출 간 간격(초)


class ApiStop(Exception):
    """더 진행하면 안 되는 응답(키 오류, 호출 한도 초과 등)."""


def load_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("SEOUL_BUS_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        sys.exit(".env의 SEOUL_BUS_API_KEY가 비어 있습니다.")
    return unquote(key) if "%" in key else key


def call(session: requests.Session, key: str, op: str, **params) -> list[dict]:
    for attempt in range(3):
        try:
            res = session.get(BASE + op, params={"serviceKey": key, "resultType": "json", **params}, timeout=30)
            body = res.json()
            break
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
        except ValueError:
            # 키 오류·한도 초과는 게이트웨이가 JSON이 아닌 XML로 응답한다
            raise ApiStop(res.text[:300])
    else:
        raise ApiStop(f"{op} 네트워크 오류가 반복됨")

    header = body.get("msgHeader")
    if header is None:
        raise ApiStop(f"{op} 예상과 다른 응답: {json.dumps(body, ensure_ascii=False)[:300]}")
    code = header.get("headerCd")
    if code == "4":  # 결과 없음
        return []
    if code != "0":
        raise ApiStop(f"{op} headerCd {code}: {header.get('headerMsg')}")
    return (body.get("msgBody") or {}).get("itemList") or []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="수집 폴더 이름(기본: 오늘)")
    ap.add_argument("--limit", type=int, help="이번 실행에서 받을 최대 노선 수(테스트용)")
    args = ap.parse_args()

    out = ROOT / "data" / "raw" / "seoul_api" / args.date
    routes_dir = out / "routes"
    routes_dir.mkdir(parents=True, exist_ok=True)

    key = load_key()
    session = requests.Session()

    list_file = out / "route_list.json"
    if list_file.exists():
        route_list = json.loads(list_file.read_text(encoding="utf-8"))
    else:
        route_list = call(session, key, "getBusRouteList", strSrch="")
        list_file.write_text(json.dumps(route_list, ensure_ascii=False), encoding="utf-8")
    print(f"노선 목록 {len(route_list)}개")

    todo = [r for r in route_list if not (routes_dir / f"{r['busRouteId']}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"남은 노선 {len(todo)}개 수집 시작")

    done = 0
    try:
        for r in todo:
            rid = r["busRouteId"]
            path = call(session, key, "getRoutePath", busRouteId=rid)
            time.sleep(DELAY)
            stations = call(session, key, "getStaionByRoute", busRouteId=rid)
            time.sleep(DELAY)
            record = {"route": r, "path": path, "stations": stations}
            (routes_dir / f"{rid}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  (마지막: {r['busRouteNm']})", flush=True)
    except ApiStop as e:
        print(f"중단: {e}")
        print(f"이번 실행 {done}개 저장. 같은 명령을 다시 실행하면 이어서 받습니다.")
        sys.exit(1)

    total = len(list(routes_dir.glob("*.json")))
    print(f"완료: 이번 실행 {done}개, 누적 {total}/{len(route_list)}개 → {routes_dir}")


if __name__ == "__main__":
    main()
