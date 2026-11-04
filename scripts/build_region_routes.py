"""수집한 노선 데이터와 행정구역 경계로 지역별 정차/통과 노선을 계산해 웹용 데이터를 만든다.

- 정차: 지역 안에 실제 정류장이 하나 이상 있는 노선
  (경로 표시용 가상 지점 "(미정차)"·"(가상)"(서울 API)·"(경유)"(경기 API)는 정차로 치지 않음)
- 통과: 정차는 없지만 경로선이 지역 안을 MIN_PASS_M 이상 지나는 노선
- 경로 좌표가 없는 노선은 정류장 좌표를 순서대로 이은 추정 경로를 지도 표시용으로만 만든다(통과 판정에는 쓰지 않음)
- 서울 API와 경기 API 데이터를 노선 ID로 합친다. 같은 노선이 둘 다에 있으면 경기 API의 경로·정류장을 쓴다.
  경기 노선의 이름·유형·면허 시군은 경기 노선 목록(route_list.json) 기준.
받은 노선 파일 전체를 매번 처음부터 다시 계산하므로, 수집이 늘어나면 다시 실행하면 된다.

사용법: uv run python scripts/build_region_routes.py <서울 수집일 YYYYMMDD> [--gyeonggi <경기 수집일>] [--incheon <TAGO 인천 수집일>]
출력: web/data/{region_routes.json, regions_si.geojson, regions_gu.geojson, regions_dong.geojson, bundles/<시군구코드>.json}
      data/processed/{routes.geojson, stations.json} (분석용 전체 파일)
"""
import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, MultiLineString, Point

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
BUNDLES = OUT / "bundles"
ROUTE_STOPS = OUT / "route_stops"  # 노선별 전체 정류장. 노선 ID 끝 두 자리로 나눈 파일(웹이 노선 상세를 열 때 읽음)
PROCESSED = ROOT / "data" / "processed"
METRIC = "EPSG:5179"
MIN_PASS_M = 50  # 경계를 스치는 정도의 교차는 통과로 치지 않는다
SIMPLIFY_M = 5  # 웹 표시용 경로 단순화 허용 오차
EXCLUDE_TYPES = {"14"}  # 한강버스(선박)
TM_TO_WGS = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)
WGS_TO_M = Transformer.from_crs("EPSG:4326", METRIC, always_xy=True)
# 경로 품질 검사 (주로 서울 API가 주는 인천 노선에서 문제가 있다. 경기 API 경로는 모두 정상)
JUMP_M = 3000  # 연속한 경로 점이 이보다 멀면 순서가 뒤섞인 것으로 보고 선을 끊는다
FAR_STOP_M = 500  # 정류장이 경로에서 이보다 멀면 경로가 그 구간을 빠뜨린 것
MAX_FAR_RATIO = 0.1  # 이런 정류장이 이 비율 이상이면 정류장 순서를 뼈대로 경로를 다시 잇는다(stitch)
SNAP_M = 300  # stitch: 정류장이 경로 조각에서 이 거리 안이면 그 조각 위에 있는 것으로 본다
M_TO_WGS = Transformer.from_crs(METRIC, "EPSG:4326", always_xy=True)
# 정류장 목록에 섞여 있는, 버스가 서지 않는 경로 표시용 지점. 이런 지점은 정류장 번호(ARS)도 거의 없다
VIRTUAL_STOP = re.compile(r"\((미정차|가상|경유)\)")

ROUTE_TYPES = {
    "1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환", "6": "광역",
    "7": "인천", "8": "경기", "10": "투어", "15": "심야",
}
# 경기 API routeTypeCd → 화면 유형
GG_ROUTE_TYPES = {
    11: "경기좌석", 12: "경기좌석", 13: "경기일반", 14: "광역급행", 15: "경기일반", 16: "경기좌석",
    21: "경기좌석", 22: "경기좌석", 23: "경기일반", 30: "경기마을",
    50: "똑버스",  # 수요응답형(DRT). API가 유형 이름을 비워서 준다
    41: "시외", 42: "시외", 43: "시외", 51: "공항", 52: "공항", 53: "공항",
}
# TAGO 인천 노선 유형(routetp) → 화면 유형
INCHEON_ROUTE_TYPES = {
    "간선버스": "인천일반", "지선버스": "인천일반", "순환버스": "인천일반", "급행버스": "인천일반",
    "좌석버스": "인천좌석", "광역버스": "인천광역", "마을버스": "인천마을", "공항버스": "공항",
}
INCHEON_FILE = ROOT / "data" / "raw" / "incheon_file"
TM_MID_TO_WGS = Transformer.from_crs("EPSG:5181", "EPSG:4326", always_xy=True)  # 인천 노드 좌표계(중부원점 TM)


def refine_type(route: dict) -> str:
    """API 유형 코드로는 안 나뉘는 노선을 이름으로 구분한다.
    통근버스(P)(광역 프리미엄 P버스, 'P9301(출근)')는 경기 API에서 직행좌석(11)으로 오고,
    예약 버스('3000(예약)', 'M4102(예약)', '9100-1(출근예약)')는 직행좌석·광역급행에 섞여 있다."""
    if re.match(r"서울\d+(출근|퇴근)", route["name"]):  # 서울시 출퇴근 전용 노선 '서울01출근' ~ (API상 간선·광역)
        return "통근버스(서울)"
    if re.match(r"P\d", route["name"]):
        return "통근버스(P)"
    if "예약" in route["name"]:
        return "예약"
    if re.match(r"M\d", route["name"]):  # 인천 M버스는 TAGO에서 '광역버스'로 온다. 경기 M버스와 같은 유형으로 묶는다
        return "광역급행"
    return route["typeName"]


def in_korea(x: float, y: float) -> bool:
    # API가 좌표 대신 0이나 이상한 값을 주는 경우가 있다
    return 124 < x < 132 and 33 < y < 39


def split_at_jumps(pts: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """경로 점 순서가 뒤섞여 몇 km씩 튀는 곳에서 선을 끊는다. 끊긴 조각 하나하나는 실제 도로 모양이다."""
    parts, cur = [], [pts[0]]
    mx, my = WGS_TO_M.transform(*zip(*pts))
    for i in range(1, len(pts)):
        if math.hypot(mx[i] - mx[i - 1], my[i] - my[i - 1]) > JUMP_M:
            parts.append(cur)
            cur = []
        cur.append(pts[i])
    parts.append(cur)
    return [p for p in parts if len(p) >= 2]


def far_stop_ratio(parts: list, stations: list[dict]) -> float:
    """실제 정류장 중 경로에서 FAR_STOP_M보다 먼 것의 비율"""
    real = [(s["x"], s["y"]) for s in stations if not VIRTUAL_STOP.search(s["name"]) and in_korea(s["x"], s["y"])]
    if not real:
        return 0.0
    line = to_metric(MultiLineString(parts))
    far = sum(line.distance(Point(*WGS_TO_M.transform(x, y))) > FAR_STOP_M for x, y in real)
    return far / len(real)


def to_metric(geom):
    return shapely.transform(geom, lambda c: np.column_stack(WGS_TO_M.transform(c[:, 0], c[:, 1])))


def to_wgs(geom):
    return shapely.transform(geom, lambda c: np.column_stack(M_TO_WGS.transform(c[:, 0], c[:, 1])))


def reassemble(pieces: list[list[tuple]], tol: float = SNAP_M) -> list[list[tuple]]:
    """순서가 뒤섞여 끊긴 경로 조각들을, 끝점끼리 가장 가까운 것부터 tol 안이면 이어 붙여 원래 순서로 되돌린다(미터 좌표)."""
    pieces = [list(p) for p in pieces]
    while len(pieces) > 1:
        best = None
        for i, a in enumerate(pieces):
            for j, b in enumerate(pieces):
                if i == j:
                    continue
                # a의 끝 → b의 시작 (b를 뒤집는 경우 포함)
                for rev in (False, True):
                    bb = b[::-1] if rev else b
                    d = math.dist(a[-1], bb[0])
                    if best is None or d < best[0]:
                        best = (d, i, j, rev)
        d, i, j, rev = best
        if d > tol:
            break
        joined = pieces[i] + (pieces[j][::-1] if rev else pieces[j])
        pieces = [p for k, p in enumerate(pieces) if k not in (i, j)] + [joined]
    return pieces


def stitch(parts: list, stations: list[dict]):
    """정류장 순서(가상 지점 포함)를 뼈대로 경로를 다시 잇는다.
    이어진 두 정류장이 같은 경로 조각 위에 있으면 그 사이의 실제 경로 구간을, 아니면 직선을 쓴다.
    반환: (지도용 구간 목록 [(LineString, 실제 경로 여부)], 판정용 실제 구간 MultiLineString 또는 None,
          실제 구간 길이 비율 0~1). 모두 경위도. 지도용 구간은 실제/추정이 바뀌는 곳마다 나뉜다."""
    metric_parts = [list(to_metric(LineString(p)).coords) for p in parts]
    pieces = [LineString(p) for p in reassemble(metric_parts) if len(p) >= 2]
    pts = [Point(*WGS_TO_M.transform(s["x"], s["y"])) for s in sorted(stations, key=lambda s: s["seq"]) if in_korea(s["x"], s["y"])]
    if len(pts) < 2:
        return None, None, 0.0
    snaps = []
    for p in pts:
        d, i = min(((ln.distance(p), i) for i, ln in enumerate(pieces)), default=(math.inf, -1))
        snaps.append((i, pieces[i].project(p)) if d <= SNAP_M else None)

    runs, real, real_len, total_len = [], [], 0.0, 0.0  # runs: [실제 여부, 좌표 목록]
    for k in range(1, len(pts)):
        a, b, pa, pb = snaps[k - 1], snaps[k], pts[k - 1], pts[k]
        seg = None
        # 같은 조각 위의 두 정류장이고, 조각을 따라간 거리가 직선거리에 비해 터무니없이 길지 않으면 실제 경로 구간을 쓴다
        if a and b and a[0] == b[0] and abs(b[1] - a[1]) <= 3 * pa.distance(pb) + 1000:
            seg = shapely.ops.substring(pieces[a[0]], a[1], b[1])  # a > b 이면 거꾸로 된 구간이 나온다
        is_real = seg is not None and seg.geom_type == "LineString" and seg.length > 0
        c = list(seg.coords) if is_real else [(pa.x, pa.y), (pb.x, pb.y)]
        if c[0] == c[-1]:
            continue
        if is_real:
            real.append(seg)
            real_len += seg.length
        total_len += LineString(c).length
        if runs and runs[-1][0] == is_real:
            runs[-1][1].extend(xy for xy in c if xy != runs[-1][1][-1])
        else:
            # 구간이 바뀌어도 선이 끊기지 않도록 앞 구간의 끝점에서 시작한다
            runs.append([is_real, ([runs[-1][1][-1]] if runs else []) + c])
    if not runs:
        return None, None, 0.0
    drawn = [(to_wgs(LineString(c)), r) for r, c in runs if len(c) >= 2]
    real_geom = to_wgs(MultiLineString([list(s.coords) for s in real])) if real else None
    return drawn, real_geom, (real_len / total_len if total_len else 0.0)


def gg_city(admin_name: str) -> str:
    """'경기도 성남시' → '성남', '경기도' → '경기'"""
    m = re.match(r"경기도\s*(\S+?)[시군]$", admin_name or "")
    return m.group(1) if m else "경기"


def seoul_records(day: str):
    """서울 API 노선 파일 → (노선 정보, 정류장 목록, 경로 좌표, 정류장 순서 좌표)"""
    for f in sorted((ROOT / "data" / "raw" / "seoul_api" / day / "routes").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        r = rec["route"]
        if r["routeType"] in EXCLUDE_TYPES:
            continue
        route = {
            "rid": r["busRouteId"], "name": r["busRouteNm"], "type": r["routeType"],
            "typeName": ROUTE_TYPES.get(r["routeType"], r["routeType"]), "city": "인천" if r["routeType"] == "7" else "서울",
            "start": r["stStationNm"], "end": r["edStationNm"], "corp": r.get("corpNm", ""),
        }
        stations = [{"sid": s["station"], "ars": s["arsId"], "name": s["stationNm"], "seq": int(s["seq"]),
                     "x": float(s["gpsX"] or 0), "y": float(s["gpsY"] or 0)} for s in rec["stations"]]
        pts = [(float(p["gpsX"]), float(p["gpsY"])) for p in sorted(rec["path"], key=lambda p: int(p["no"]))]
        if pts and all(x > 1000 for x, _ in pts):
            # 일부 노선은 gpsX/gpsY에 경위도 대신 중부원점 TM(EPSG:5181) 좌표가 들어 있다
            pts = list(zip(*TM_TO_WGS.transform(*zip(*pts))))
        yield route, stations, pts


def gyeonggi_records(day: str):
    """경기 API 노선 파일 → seoul_records와 같은 형태"""
    for f in sorted((ROOT / "data" / "raw" / "gyeonggi_api" / day / "routes").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        r = rec["route"]
        route = gg_route_info(r)
        stations = [{"sid": str(s["stationId"]), "ars": str(s.get("mobileNo", "")).strip(), "name": s["stationName"],
                     "seq": int(s["stationSeq"]), "x": float(s["x"]), "y": float(s["y"])} for s in rec["stations"]]
        pts = [(float(p["x"]), float(p["y"])) for p in sorted(rec["path"], key=lambda p: int(p["lineSeq"]))]
        yield route, stations, pts


def incheon_records(tago_day: str):
    """인천 노선 → seoul_records와 같은 형태.
    노선번호·유형·현재 정류장은 TAGO(collect_tago.py), 경로 모양은 인천시 공개 파일의 노선별 경유노드(교차로 포함)를 쓴다.
    경유노드 파일은 2024-08 자료라 그 뒤 바뀐 노선은 정류장과 맞지 않을 수 있다 → load_routes의 품질 검사·이어 붙이기가 처리한다."""
    tago = ROOT / "data" / "raw" / "tago" / tago_day
    node_file = sorted(INCHEON_FILE.glob("nodes_*.csv"))[-1]
    route_file = sorted(INCHEON_FILE.glob("route_nodes_*.csv"))[-1]
    nodes = pd.read_csv(node_file, encoding="cp949", dtype=str).set_index("노드아이디")
    rn = pd.read_csv(route_file, encoding="cp949", dtype=str)
    rn["seq"] = rn["경로순번"].astype(int)
    xy = dict(zip(nodes.index, zip(nodes["X좌표"].astype(float), nodes["Y좌표"].astype(float))))
    node_paths = {}
    for rid, g in rn.sort_values("seq").groupby("버스노선(아이디)"):
        tm = [xy[n] for n in g["노드(아이디)"] if n in xy]
        if len(tm) >= 2:
            lon, lat = TM_MID_TO_WGS.transform(*zip(*tm))
            node_paths[rid] = list(zip(lon, lat))

    for r in json.loads((tago / "incheon_routes.json").read_text(encoding="utf-8")):
        rid = r["localId"]
        route = {
            "rid": rid, "name": r["routeno"], "type": f"ic:{r['routetp']}",
            "typeName": INCHEON_ROUTE_TYPES.get(r["routetp"], "인천"), "city": "인천",
            "start": r.get("startnodenm", ""), "end": r.get("endnodenm", ""), "corp": "",
        }
        sf = tago / "stations" / f"{rid}.json"
        raw = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else []
        stations = [{"sid": "1" + s["nodeid"][-8:], "ars": str(s.get("nodeno", "")), "name": s["nodenm"],
                     "seq": int(s["nodeord"]), "x": float(s["gpslong"]), "y": float(s["gpslati"])} for s in raw]
        yield route, stations, node_paths.get(rid, [])


def gg_route_info(r: dict) -> dict:
    return {
        "rid": str(r["routeId"]), "name": str(r["routeName"]), "type": f"gg{r['routeTypeCd']}",
        "typeName": GG_ROUTE_TYPES.get(r["routeTypeCd"], "경기"), "city": gg_city(r.get("adminName", "")),
        "start": r.get("startStationName", ""), "end": r.get("endStationName", ""), "corp": "",
    }


def load_routes(day: str, gg_day: str | None, ic_day: str | None = None):
    records = {route["rid"]: (route, st, pts) for route, st, pts in seoul_records(day)}
    if ic_day:
        # 서울 API에도 있는 인천 노선(38개)은 인천 데이터로 바꾼다. 서울 API의 인천 경로 좌표는 뒤섞였거나 일부만 있다
        n = len(records)
        for route, st, pts in incheon_records(ic_day):
            if not pts and route["rid"] in records:
                # 인천 경유노드 파일(2024)에 없는 신설 노선은 서울 API 경로라도 쓴다 (품질 검사·이어 붙이기가 걸러냄)
                pts = records[route["rid"]][2]
            records[route["rid"]] = (route, st, pts)
        print(f"인천 노선(TAGO + 인천시 경유노드 파일) 반영 → {len(records) - n}개 추가, 합계 {len(records)}개")
    if gg_day:
        # 서울 API에도 있는 경기 노선은 이름·유형·면허 시군을 경기 목록 기준으로 바꾼다(운수사 이름은 서울 쪽만 있음)
        gg_list = {str(r["routeId"]): r for r in json.loads((ROOT / "data" / "raw" / "gyeonggi_api" / gg_day / "route_list.json").read_text(encoding="utf-8"))}
        # 경기 목록에 없는 서울 API의 경기 노선은 폐선 후 서울 쪽에만 남은 것으로 보고 뺀다 (예: 106-1의정부, 2026-10-01 폐선)
        gone = [route["name"] for route, _, _ in records.values() if route["type"] == "8" and route["rid"] not in gg_list]
        records = {rid: rec for rid, rec in records.items() if not (rec[0]["type"] == "8" and rid not in gg_list)}
        if gone:
            print(f"경기 목록에 없어 제외한 경기 노선 {len(gone)}개: {', '.join(gone)}")
        for rid, (route, st, pts) in records.items():
            if rid in gg_list:
                route.update({k: v for k, v in gg_route_info(gg_list[rid]).items() if k != "corp"})
        n_seoul = len(records)
        for route, st, pts in gyeonggi_records(gg_day):
            if route["rid"] in records:
                route["corp"] = records[route["rid"]][0]["corp"]
            records[route["rid"]] = (route, st, pts)
        print(f"서울 API 노선 {n_seoul}개 + 경기 API 노선 파일 반영 → 합계 {len(records)}개")

    # lines: 통과 판정용(실제 경로만), draw: 지도 표시용(다시 이은 경로 포함)
    routes, stops, lines, draw = [], [], [], []
    stitched = []
    for rid, (route, stations, pts) in records.items():
        pts = [p for p in pts if in_korea(*p)]
        parts = split_at_jumps(pts) if len(pts) >= 2 else []
        for s in stations:
            if VIRTUAL_STOP.search(s["name"]) or not in_korea(s["x"], s["y"]):
                continue
            stops.append({"rid": rid, **s})

        if len(parts) > 1:
            # 뒤섞여 끊긴 조각은 끝점끼리 다시 이어 원래 순서로 되돌린다
            parts = [list(to_wgs(LineString(p)).coords) for p in reassemble([list(to_metric(LineString(p)).coords) for p in parts])]
        if parts and far_stop_ratio(parts, stations) < MAX_FAR_RATIO:
            # 실제 정류장과 잘 맞는 경로는 그대로 쓴다 (다 이어지지 못한 조각은 여러 조각인 채로)
            real = LineString(parts[0]) if len(parts) == 1 else MultiLineString(parts)
            draw.append({"rid": rid, "geometry": real, "estParts": [], "realPct": 100})
        else:
            # 경로가 없거나, 정류장과 맞지 않는(구간이 빠진) 노선:
            # 정류장 순서를 뼈대로 실제 경로 구간을 이어 붙이고 빈 곳만 직선(추정 구간)으로 채운다
            segs, real, ratio = stitch(parts, stations)
            if segs:
                draw.append({"rid": rid, "geometry": MultiLineString([g for g, _ in segs]),
                             "estParts": [i for i, (_, is_real) in enumerate(segs) if not is_real], "realPct": round(ratio * 100)})
            if parts:
                stitched.append((route["name"], round(ratio * 100)))
        if real is not None:
            lines.append({"rid": rid, "geometry": real})
        routes.append({**route, "typeName": refine_type(route), "hasPath": real is not None})

    print(f"정류장 순서로 경로를 다시 이은 노선 {len(stitched)}개 (실제 경로 비율): "
          f"{', '.join(f'{n} {p}%' for n, p in stitched[:15])}{' …' if len(stitched) > 15 else ''}")
    routes = pd.DataFrame(routes).set_index("rid")
    stops = gpd.GeoDataFrame(stops, geometry=gpd.points_from_xy([s["x"] for s in stops], [s["y"] for s in stops]), crs="EPSG:4326")
    lines = gpd.GeoDataFrame(lines, crs="EPSG:4326")
    draw = gpd.GeoDataFrame(draw, geometry="geometry", crs="EPSG:4326")
    return routes, stops, lines, draw


def classify(regions: gpd.GeoDataFrame, key: str, stops: gpd.GeoDataFrame, lines: gpd.GeoDataFrame) -> dict:
    """지역 코드 → {"stop": {rid: [정류장 id...]}, "pass": {rid: 지역 내 길이(m)}}"""
    result = {code: {"stop": {}, "pass": {}} for code in regions[key]}

    hit = gpd.sjoin(stops, regions[[key, "geometry"]], predicate="within")
    for (code, rid), grp in hit.groupby([key, "rid"]):
        result[code]["stop"][rid] = sorted(set(grp["sid"]), key=list(grp.sort_values("seq")["sid"]).index)

    reg_m = regions[[key, "geometry"]].to_crs(METRIC)
    lines_m = lines.to_crs(METRIC)
    pairs = gpd.sjoin(lines_m, reg_m, predicate="intersects")
    reg_geom = reg_m.set_index(key).geometry
    for idx, row in pairs.iterrows():
        code, rid = row[key], row["rid"]
        if rid in result[code]["stop"]:
            continue
        length = lines_m.geometry[idx].intersection(reg_geom[code]).length
        if length >= MIN_PASS_M:
            result[code]["pass"][rid] = round(length)
    return result


def build_si(gu: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """시 단위 경계: 서울은 서울특별시 하나, 경기는 시·군(수원시처럼 구가 있는 시는 구를 합친다).
    코드는 구가 하나면 그 시군구 코드, 여럿이면 앞 네 자리+0(수원시 41111·41113… → 41110), 서울은 11000.
    sggs: 속한 시군구 코드 목록 — 웹이 시를 고르면 이 구들의 묶음 파일을 읽는다."""
    gu = gu.assign(city=np.where(gu["sido"] == "서울", "서울특별시", gu["sggnm"].str.split().str[0]))
    groups = gu.groupby("city")["sgg"].apply(sorted)
    si = gu.dissolve(by="city", as_index=False)[["city", "sido", "geometry"]]
    si["geometry"] = si.geometry.make_valid().buffer(0)
    si["code"] = si["city"].map(lambda c: "11000" if c == "서울특별시" else groups[c][0] if len(groups[c]) == 1 else groups[c][0][:4] + "0")
    si["sggs"] = si["city"].map(lambda c: ",".join(groups[c]))
    multi = si[si["sggs"].str.contains(",")]
    assert si["code"].is_unique and not set(multi["code"]) & set(gu["sgg"]), "시 코드가 겹칩니다"
    return si.rename(columns={"city": "name"})


def main(day: str, gg_day: str | None, ic_day: str | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 분석 대상 지역 경계 (scripts/extract_regions.py → build_gu.py 결과)
    bver = sorted((ROOT / "data" / "boundary").glob("dong_*.geojson"))[-1].stem.split("_")[-1]
    dong = gpd.read_file(ROOT / "data" / "boundary" / f"dong_{bver}.geojson")[["adm_cd2", "adm_nm", "sgg", "sggnm", "geometry"]]
    dong["name"] = dong["adm_nm"].str.split().str[-1]
    gu = gpd.read_file(ROOT / "data" / "boundary" / f"gu_{bver}.geojson")[["sgg", "sggnm", "geometry"]]
    for gdf in (dong, gu):
        gdf["sido"] = gdf["sgg"].str[:2].map({"11": "서울", "41": "경기"})
    si = build_si(gu)

    routes, stops, lines, draw = load_routes(day, gg_day, ic_day)
    print(f"노선 {len(routes)}개(판정용 실제 경로 {len(lines)}, 실제 경로 없이 추정만 {int((draw['realPct'] == 0).sum())}), 실제 정차 정류장 기록 {len(stops)}건")

    by_si = classify(si, "code", stops, lines)
    by_gu = classify(gu, "sgg", stops, lines)
    by_dong = classify(dong, "adm_cd2", stops, lines)

    # 지도용 노선 경로: 단순화하고 좌표를 소수 5자리(약 1m)로 줄인다
    simp = draw.to_crs(METRIC).simplify(SIMPLIFY_M).to_crs("EPSG:4326")
    geoms, features = {}, []
    for rid, geom, est_parts, pct in zip(draw["rid"], simp, draw["estParts"], draw["realPct"]):
        # 웹에는 조각 목록([[x,y]...] 의 목록)으로 넘긴다. est_parts는 그중 정류장을 직선으로 이은 추정 조각 번호
        parts = [[[round(x, 5), round(y, 5)] for x, y in g.coords] for g in getattr(geom, "geoms", [geom])]
        geoms[rid] = (parts, list(est_parts))
        props = {"rid": rid, **{k: v for k, v in routes.loc[rid].items() if k != "hasPath"}, "realPct": int(pct), "estParts": list(est_parts)}
        gj = {"type": "LineString", "coordinates": parts[0]} if len(parts) == 1 else {"type": "MultiLineString", "coordinates": parts}
        features.append({"type": "Feature", "properties": props, "geometry": gj})
    st = stops.drop_duplicates("sid").set_index("sid")
    stations = {sid: {"name": r["name"], "ars": r["ars"], "x": round(r["x"], 6), "y": round(r["y"], 6)} for sid, r in st.iterrows()}

    # 분석용 전체 파일은 웹 폴더 밖에 둔다 (웹에서 한 번에 읽기엔 크다)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    (PROCESSED / "routes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (PROCESSED / "stations.json").write_text(json.dumps(stations, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 웹용: 시군구마다 그 지역을 지나는 노선의 전체 경로와 지역 안 정류장을 묶는다.
    # 행정동을 고르면 그 동이 속한 시군구 묶음을 읽는다 (동의 노선·정류장은 시군구의 부분집합).
    for old in [OUT / "routes.geojson", OUT / "stations.json", *BUNDLES.glob("*.json")]:
        old.unlink(missing_ok=True)
    BUNDLES.mkdir(parents=True, exist_ok=True)
    for code, entry in by_gu.items():
        rids = [rid for rid in [*entry["stop"], *entry["pass"]] if rid in geoms]
        sids = {sid for sids in entry["stop"].values() for sid in sids}
        bundle = {
            "routes": {rid: geoms[rid][0] for rid in rids},
            "estParts": {rid: geoms[rid][1] for rid in rids if geoms[rid][1]},
            "stations": {sid: stations[sid] for sid in sids if sid in stations},
        }
        (BUNDLES / f"{code}.json").write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 노선 상세용 전체 정류장(가상 지점 제외): {노선 ID: [[정류장 id, 이름, 번호, x, y], ...]} — 운행 순서
    ROUTE_STOPS.mkdir(parents=True, exist_ok=True)
    for old in ROUTE_STOPS.glob("*.json"):
        old.unlink()
    shards = {}
    for rid, g in stops.sort_values("seq").groupby("rid"):
        shards.setdefault(rid[-2:], {})[rid] = [[r.sid, r.name, str(r.ars).strip(), round(r.x, 5), round(r.y, 5)] for r in g.itertuples()]
    for key, shard in shards.items():
        (ROUTE_STOPS / f"{key}.json").write_text(json.dumps(shard, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for name, gdf, tol in [("regions_si", si[["code", "name", "sido", "sggs", "geometry"]], 30),
                           ("regions_gu", gu.rename(columns={"sgg": "code", "sggnm": "name"}), 20),
                           ("regions_dong", dong.rename(columns={"adm_cd2": "code"})[["code", "name", "sgg", "sggnm", "sido", "geometry"]], 10)]:
        # 화면 표시용 경계는 단순화해서 파일 크기를 줄인다 (판정에는 원본 경계를 이미 사용)
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.to_crs(METRIC).simplify(tol).to_crs("EPSG:4326")
        gdf.to_file(OUT / f"{name}.geojson", driver="GeoJSON", COORDINATE_PRECISION=5)

    meta = {
        "collectedOn": day, "gyeonggiCollectedOn": gg_day, "incheonCollectedOn": ic_day, "boundaryVersion": bver, "builtAt": datetime.now().isoformat(timespec="seconds"),
        "minPassMeters": MIN_PASS_M, "routeCount": len(routes), "routesWithoutPath": routes.index[~routes["hasPath"]].tolist(),
        # 실제 경로 구간 비율(%)이 100 미만인 노선 — 0이면 경로 전체가 정류장을 이은 추정, 그 사이면 일부 추정
        "realPct": {r["rid"]: int(r["realPct"]) for _, r in draw.iterrows() if r["realPct"] < 100},
        "routes": routes.drop(columns="hasPath").to_dict(orient="index"),
    }
    (OUT / "region_routes.json").write_text(json.dumps({"meta": meta, "si": by_si, "gu": by_gu, "dong": by_dong}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for label, res in [("시", by_si), ("구", by_gu), ("행정동", by_dong)]:
        s = pd.Series({k: len(v["stop"]) for k, v in res.items()})
        p = pd.Series({k: len(v["pass"]) for k, v in res.items()})
        print(f"{label}: 정차 노선 중앙값 {s.median():.0f} (최소 {s.min()}, 최대 {s.max()}), 통과 노선 중앙값 {p.median():.0f} (최대 {p.max()})")
    for f in sorted(OUT.glob("*.json*")):
        print(f"  {f.name}  {f.stat().st_size / 1e6:.1f}MB")
    sizes = sorted(f.stat().st_size / 1e6 for f in BUNDLES.glob("*.json"))
    print(f"  bundles/ {len(sizes)}개, 합계 {sum(sizes):.1f}MB, 가장 큰 파일 {sizes[-1]:.1f}MB, 중앙값 {sizes[len(sizes) // 2]:.1f}MB")
    sizes = sorted(f.stat().st_size / 1e6 for f in ROUTE_STOPS.glob("*.json"))
    print(f"  route_stops/ {len(sizes)}개, 합계 {sum(sizes):.1f}MB, 가장 큰 파일 {sizes[-1]:.2f}MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("day", help="서울 API 수집일 YYYYMMDD")
    ap.add_argument("--gyeonggi", help="경기 API 수집일 YYYYMMDD")
    ap.add_argument("--incheon", help="TAGO 인천 수집일 YYYYMMDD (scripts/collect_tago.py)")
    args = ap.parse_args()
    main(args.day, args.gyeonggi, args.incheon)
