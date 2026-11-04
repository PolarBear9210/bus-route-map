# 지역별 버스 노선 지도

서울·경기의 시, 구, 행정동을 고르면 그 지역에 **정차하는 버스 노선**과 **정차 없이 통과하는 노선**을 구분해 지도에 보여주는 웹페이지입니다.

https://polarbear9210.github.io/bus-route-map/

## 데이터 출처

- 노선·정류장: 공공데이터포털 서울특별시 노선정보조회, 경기도 버스노선 조회, 국토교통부 TAGO 버스노선정보, 인천광역시 버스정보관리 파일데이터
- 행정구역 경계: [vuski/admdongkor](https://github.com/vuski/admdongkor)
- 배경 지도: © OpenStreetMap contributors

수집 시점은 페이지 왼쪽 위 카드 아래쪽에 표시됩니다. 실제 운행 정보와 다를 수 있습니다.

## 구조

- `scripts/`: 수집·가공 스크립트 (Python, uv)
- `web/`: 정적 웹페이지. `web/data/`의 가공 결과만 읽으며 서버나 API 키가 필요 없습니다
- `기획서.md`: 기획, 판정 기준, 알려진 한계, 데이터 갱신 절차

## 로컬 실행

```bash
python scripts/serve.py
```

## 데이터 갱신

`.env.example`을 `.env`로 복사해 공공데이터포털 인증키를 넣고, `기획서.md` 9절의 절차를 따릅니다. 원본 데이터(`data/raw/`)와 `.env`는 저장소에 올리지 않습니다.
