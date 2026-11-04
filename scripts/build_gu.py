"""서울 행정동 경계를 구 단위로 합쳐 구 경계 GeoJSON을 만든다.

사용법: uv run python scripts/build_gu.py <행정동 geojson> <출력 geojson>
"""
import sys

import geopandas as gpd


def main(src: str, dst: str) -> None:
    dong = gpd.read_file(src)
    gu = (
        dong.dissolve(by=["sgg", "sggnm"], as_index=False)[["sgg", "sggnm", "geometry"]]
        .assign(dong_count=lambda d: d["sgg"].map(dong["sgg"].value_counts()))
    )
    # 인접 행정동 경계가 완전히 맞물리지 않아 생기는 미세한 틈을 메운다
    gu["geometry"] = gu.geometry.make_valid().buffer(0)
    gu.to_file(dst, driver="GeoJSON")
    print(f"구 {len(gu)}개 → {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
