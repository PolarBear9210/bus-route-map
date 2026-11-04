"""경기도 버스 API에서 노선 목록과 노선별 경로 좌표·정류장을 받아 저장한다.

경기도 API는 전체 목록 조회가 없고 노선번호 앞부분 검색만 되므로, 숫자·영문·시군 이름 등
여러 검색어로 나눠 검색한 결과를 합쳐 목록을 만든다.

  list    : 노선 목록 수집 → data/raw/gyeonggi_api/<수집일>/route_list.json
  routes  : 목록 중 --region(경유 지역명)에 해당하는 노선의 경로·정류장 수집 → routes/<routeId>.json
            이미 받은 노선은 건너뛰므로, 한도 초과로 멈추면 같은 명령을 다시 실행하면 이어서 받는다.

사용법:
  uv run python scripts/collect_gyeonggi.py list [--date YYYYMMDD]
  uv run python scripts/collect_gyeonggi.py routes --region 성남 [--date YYYYMMDD] [--limit N]
  uv run python scripts/collect_gyeonggi.py routes --ids 228000393 232000017 [--date YYYYMMDD]
"""
import argparse
import json
import os
import string
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://apis.data.go.kr/6410000/busrouteservice/v2/"
ROOT = Path(__file__).resolve().parent.parent
DELAY = 0.1

CITIES = ["수원", "성남", "의정부", "안양", "부천", "광명", "평택", "동두천", "안산", "고양", "과천", "구리",
          "남양주", "오산", "시흥", "군포", "의왕", "하남", "용인", "파주", "이천", "안성", "김포", "화성",
          "광주", "양주", "포천", "여주", "연천", "가평", "양평"]
BASE_KEYWORDS = [*string.digits, *string.ascii_uppercase, *CITIES, "순환", "똑버스", "심야", "출근", "급행", "직행"]


def place_keywords() -> list[str]:
    """'초이-01', '감북-01'처럼 동네 이름으로 시작하는 노선을 찾기 위해 경기도 읍면동 이름 앞 두 글자를 검색어로 쓴다."""
    names = set()
    for f in (ROOT / "data" / "raw" / "boundary").glob("HangJeongDong_*.geojson"):
        for feat in json.loads(f.read_text(encoding="utf-8"))["features"]:
            p = feat["properties"]
            if p["sido"] == "41":
                names.add(p["adm_nm"].split()[-1][:2])
    return sorted(names - set(BASE_KEYWORDS))


class ApiStop(Exception):
    """더 진행하면 안 되는 응답(키 오류, 호출 한도 초과 등)."""


def load_key() -> str:
    load_dotenv(ROOT / ".env")
    key = (os.getenv("GYEONGGI_BUS_API_KEY") or os.getenv("SEOUL_BUS_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        sys.exit(".env에 GYEONGGI_BUS_API_KEY 또는 SEOUL_BUS_API_KEY가 필요합니다.")
    return unquote(key) if "%" in key else key


def call(session: requests.Session, key: str, op: str, list_field: str, **params) -> list[dict]:
    for attempt in range(3):
        try:
            res = session.get(BASE + op, params={"serviceKey": key, "format": "json", **params}, timeout=30)
            body = res.json()
            break
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
        except ValueError:
            raise ApiStop(res.text[:300])
    else:
        raise ApiStop(f"{op} 네트워크 오류가 반복됨")

    resp = body.get("response")
    if resp is None:
        # 한도 초과 등은 {"error": ..., "message": ...} 형태로 온다
        raise ApiStop(f"{op} 예상과 다른 응답: {json.dumps(body, ensure_ascii=False)[:300]}")
    header = resp.get("msgHeader") or {}
    code = header.get("resultCode")
    # 결과 없음: 보통 resultCode 4지만, 검색어에 따라 {"comMsgHeader": ""}만 오기도 한다
    if code == 4 or (not header and set(resp) == {"comMsgHeader"}):
        return []
    if code != 0:
        raise ApiStop(f"{op} {params} 응답 이상: {json.dumps(resp, ensure_ascii=False)[:300]}")
    items = (resp.get("msgBody") or {}).get(list_field) or []
    return [items] if isinstance(items, dict) else items  # 결과가 하나면 리스트가 아니라 객체로 온다


def collect_list(session, key, out: Path) -> None:
    routes = {}
    keywords = BASE_KEYWORDS + place_keywords()
    for kw in keywords:
        for r in call(session, key, "getBusRouteListv2", "busRouteList", keyword=kw):
            r["routeId"], r["routeName"] = str(r["routeId"]), str(r["routeName"])
            routes[r["routeId"]] = r
        time.sleep(DELAY)
    items = sorted(routes.values(), key=lambda r: r["routeId"])
    (out / "route_list.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    print(f"검색어 {len(keywords)}개로 노선 {len(items)}개 → {out / 'route_list.json'}")


def collect_routes(session, key, out: Path, region: str | None, ids: list[str] | None, limit: int | None) -> None:
    list_file = out / "route_list.json"
    if not list_file.exists():
        sys.exit(f"{list_file}가 없습니다. 먼저 list를 실행하세요.")
    route_list = json.loads(list_file.read_text(encoding="utf-8"))
    if ids:
        targets = [r for r in route_list if r["routeId"] in ids]
        region = f"지정한 ID {len(ids)}개"
    elif region == "전체":
        targets = route_list
    else:
        targets = [r for r in route_list if region in str(r.get("regionName", "")).split(",")]
    routes_dir = out / "routes"
    routes_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in targets if not (routes_dir / f"{r['routeId']}.json").exists()]
    if limit:
        todo = todo[:limit]
    print(f"'{region}' 경유 노선 {len(targets)}개 중 남은 {len(todo)}개 수집 시작")

    done = 0
    try:
        for r in todo:
            rid = r["routeId"]
            stations = call(session, key, "getBusRouteStationListv2", "busRouteStationList", routeId=rid)
            time.sleep(DELAY)
            path = call(session, key, "getBusRouteLineListv2", "busRouteLineList", routeId=rid)
            time.sleep(DELAY)
            record = {"route": r, "path": path, "stations": stations}
            (routes_dir / f"{rid}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  (마지막: {r['routeName']})", flush=True)
    except ApiStop as e:
        print(f"중단: {e}")
        print(f"이번 실행 {done}개 저장. 같은 명령을 다시 실행하면 이어서 받습니다.")
        sys.exit(1)
    print(f"완료: 이번 실행 {done}개, 누적 {len(list(routes_dir.glob('*.json')))}개 → {routes_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["list", "routes"])
    ap.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--region", help="routes 모드: 경유 지역명(regionName) 예: 성남. '전체'면 목록의 모든 노선")
    ap.add_argument("--ids", nargs="+", help="routes 모드: 지역 대신 노선 ID를 직접 지정")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    out = ROOT / "data" / "raw" / "gyeonggi_api" / args.date
    out.mkdir(parents=True, exist_ok=True)
    session, key = requests.Session(), load_key()
    try:
        if args.mode == "list":
            collect_list(session, key, out)
        else:
            if not (args.region or args.ids):
                sys.exit("routes 모드에는 --region 또는 --ids가 필요합니다.")
            collect_routes(session, key, out, args.region, args.ids, args.limit)
    except ApiStop as e:
        sys.exit(f"중단: {e}")


if __name__ == "__main__":
    main()
