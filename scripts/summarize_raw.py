"""수집한 노선 원본 데이터의 상태를 점검한다: 유형별 개수, 경로·정류장이 빈 노선, 서울 밖 정류장 비율.

사용법: uv run python scripts/summarize_raw.py <수집일 YYYYMMDD>
"""
import json
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent

ROUTE_TYPES = {
    "1": "공항", "2": "마을", "3": "간선", "4": "지선", "5": "순환", "6": "광역",
    "7": "인천", "8": "경기", "10": "투어", "14": "한강버스", "15": "심야",
}


def main(day: str) -> None:
    files = sorted((ROOT / "data" / "raw" / "seoul_api" / day / "routes").glob("*.json"))
    seoul = gpd.read_file(next((ROOT / "data" / "boundary").glob("seoul_gu_*.geojson"))).union_all()

    by_type = Counter()
    no_path, no_stations = [], []
    stations_in, stations_total = 0, 0
    unique_stations = set()
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        r = rec["route"]
        by_type[ROUTE_TYPES.get(r["routeType"], r["routeType"])] += 1
        if not rec["path"]:
            no_path.append(r["busRouteNm"])
        if not rec["stations"]:
            no_stations.append(r["busRouteNm"])
        for s in rec["stations"]:
            unique_stations.add(s["station"])
            stations_total += 1
            stations_in += seoul.contains(Point(float(s["gpsX"]), float(s["gpsY"])))

    print(f"노선 파일 {len(files)}개")
    print("유형별:", ", ".join(f"{k} {v}" for k, v in by_type.most_common()))
    print(f"경로 없음 {len(no_path)}개: {no_path[:20]}")
    print(f"정류장 없음 {len(no_stations)}개: {no_stations[:20]}")
    print(f"고유 정류장 {len(unique_stations)}개, 노선-정류장 {stations_total}건 중 서울 안 {stations_in / max(stations_total, 1):.1%}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
