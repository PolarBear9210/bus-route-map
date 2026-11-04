"""전국 행정동 경계 GeoJSON에서 분석 대상 지역만 걸러 저장한다.

대상은 시군구 코드 앞자리로 고른다. 예: 11 = 서울 전체, 4113 = 성남시(수정·중원·분당구)
"성남시분당구"처럼 붙어 있는 시+구 이름은 "성남시 분당구"로 띄운다.

사용법: uv run python scripts/extract_regions.py <입력 geojson> <출력 geojson> <시군구 코드 앞자리...>
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def main(src: str, dst: str, prefixes: list[str]) -> None:
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    features = [f for f in data["features"] if f["properties"]["sgg"].startswith(tuple(prefixes))]
    for f in features:
        p = f["properties"]
        p["sggnm"] = re.sub(r"^(.+시)(.+구)$", r"\1 \2", p["sggnm"])

    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps({"type": "FeatureCollection", "name": data["name"], "features": features}, ensure_ascii=False), encoding="utf-8")

    by_sgg = {}
    for f in features:
        by_sgg.setdefault(f["properties"]["sggnm"], 0)
        by_sgg[f["properties"]["sggnm"]] += 1
    print(f"행정동 {len(features)}개, 시군구 {len(by_sgg)}개 → {dst}")
    print(", ".join(f"{k} {v}" for k, v in by_sgg.items() if not k.endswith("구") or " " in k))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
