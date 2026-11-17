"""경로 좌표가 없는 구간을 도로망을 따라 잇기 위해, OSRM 공개 서버에서 정류장 사이 도로 경로를 받아 저장한다.

대상: 웹 데이터(region_routes.json)에서 추정 구간이 있는 노선(realPct < 100). 지금은 인천시 경로 파일(2024-08)에 없는
신설·변경 인천 노선이다. 노선마다 정류장 순서(build_region_routes.py가 이어 붙일 때 쓰는 것과 같은 목록)를
경유지로 보내고, 이어진 두 정류장 사이 구간(leg)을 "정류장id|정류장id" 키로 저장한다. 이미 받은 노선은 건너뛴다.

OSRM 공개 서버(router.project-osrm.org) 이용 정책에 맞춰 초당 1회 이하로 보내고, 대상 노선이 바뀔 때만 다시 실행한다.
승용차 기준 도로망이라 버스 전용 도로·고속도로 구간은 실제와 다를 수 있다.

사용법: uv run python scripts/route_osrm.py [--limit N]
출력: data/raw/osrm/legs.json, data/raw/osrm/done.json(받은 노선 목록)
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_region_routes as b  # noqa: E402  (정류장 목록을 같은 방식으로 만들기 위해)

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "osrm"
URL = "https://router.project-osrm.org/route/v1/driving/"
HEADERS = {"User-Agent": "bus-route-map/1.0 (+https://github.com/PolarBear9210/bus-route-map)"}
CHUNK = 50          # 한 번에 보내는 경유지 수 (앞 묶음의 마지막 정류장을 다음 묶음 첫 정류장으로 겹친다)
MAX_SNAP_M = 150    # 정류장이 도로에서 이보다 멀리 붙으면 그 구간은 믿지 않는다
MAX_DETOUR = 4.0    # 도로 거리가 직선거리의 이 배수를 넘고
MIN_DETOUR_M = 1500  # 이 거리보다 길면 엉뚱하게 돌아간 것으로 보고 버린다
DELAY = 1.1


def haversine(a, b_):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b_))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def route_chunk(session, stops):
    coords = ";".join(f"{s['x']:.6f},{s['y']:.6f}" for s in stops)
    for attempt in range(4):
        res = session.get(URL + coords, params={"overview": "false", "steps": "true", "geometries": "geojson"}, headers=HEADERS, timeout=60)
        if res.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        body = res.json()
        if body.get("code") != "Ok":
            raise RuntimeError(f"OSRM 응답 {body.get('code')}: {body.get('message', '')[:200]}")
        return body
    raise RuntimeError("OSRM 요청이 계속 거절됨(429)")


def main(limit: int | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    legs_file, done_file = OUT / "legs.json", OUT / "done.json"
    legs = json.loads(legs_file.read_text(encoding="utf-8")) if legs_file.exists() else {}
    done = set(json.loads(done_file.read_text(encoding="utf-8"))) if done_file.exists() else set()

    meta = json.loads((ROOT / "web" / "data" / "region_routes.json").read_text(encoding="utf-8"))["meta"]
    targets = {rid for rid, pct in meta["realPct"].items() if pct < 100}
    stations = {route["rid"]: st for route, st, _ in b.incheon_records(meta["incheonCollectedOn"]) if route["rid"] in targets}
    todo = [rid for rid in sorted(targets) if rid not in done and rid in stations]
    if limit:
        todo = todo[:limit]
    print(f"추정 구간이 있는 노선 {len(targets)}개 중 남은 {len(todo)}개 요청 (인천 외 노선 {len(targets - set(stations))}개는 대상 아님)")

    session = requests.Session()
    kept = dropped = 0
    for n, rid in enumerate(todo, 1):
        stops = [s for s in sorted(stations[rid], key=lambda s: s["seq"]) if b.in_korea(s["x"], s["y"])]
        for start in range(0, len(stops) - 1, CHUNK - 1):
            chunk = stops[start:start + CHUNK]
            if len(chunk) < 2:
                break
            body = route_chunk(session, chunk)
            snaps = [w.get("distance", 0) for w in body["waypoints"]]
            for i, leg in enumerate(body["routes"][0]["legs"]):
                a, c = chunk[i], chunk[i + 1]
                key = f"{a['sid']}|{c['sid']}"
                line = []
                for step in leg["steps"]:
                    for xy in step["geometry"]["coordinates"]:
                        if not line or xy != line[-1]:
                            line.append([round(xy[0], 6), round(xy[1], 6)])
                straight = haversine((a["x"], a["y"]), (c["x"], c["y"]))
                ok = (len(line) >= 2 and max(snaps[i], snaps[i + 1]) <= MAX_SNAP_M
                      and not (leg["distance"] > MAX_DETOUR * straight and leg["distance"] > MIN_DETOUR_M))
                legs[key] = line if ok else None
                kept += ok
                dropped += not ok
            time.sleep(DELAY)
        done.add(rid)
        if n % 10 == 0 or n == len(todo):
            legs_file.write_text(json.dumps(legs, ensure_ascii=False), encoding="utf-8")
            done_file.write_text(json.dumps(sorted(done)), encoding="utf-8")
            print(f"  {n}/{len(todo)}", flush=True)
    legs_file.write_text(json.dumps(legs, ensure_ascii=False), encoding="utf-8")
    done_file.write_text(json.dumps(sorted(done)), encoding="utf-8")
    print(f"완료: 구간 {kept}개 사용, {dropped}개 버림(도로에서 멀거나 크게 돌아감) → {legs_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    main(ap.parse_args().limit)
