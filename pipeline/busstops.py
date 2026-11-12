"""국토교통부(TAGO) 버스정류소 → data/mart/busstops/{지역}.parquet

- 도시코드 목록에서 시·군 이름으로 코드를 찾고, 그 도시의 정류소를 모두 받습니다.
- TAGO에 연계되지 않은 시·군은 '제공되지 않음'으로 기록합니다 (실패로 치지 않음).
- 정류소 위치는 자주 바뀌지 않아 한 달에 한 번만 새로 받습니다.
"""

import time

import pandas as pd

from pipeline.common import MART_DIR, SourceError, check_result, extract_items, find_key, get_json
from pipeline.licenses import attach_dong

BASE = "https://apis.data.go.kr/1613000/BusSttnInfoInqireService"
OUT_DIR = MART_DIR / "busstops"
PAGE = 1000


def city_code(api_key: str, city: str):
    data = get_json(f"{BASE}/getCtyCodeList", {"serviceKey": api_key, "_type": "json"}, tries=4, timeout=60)
    check_result(data, "버스 도시코드")
    for it in extract_items(data):
        if str(it.get("cityname", "")).strip() in (city, city[:-1]):
            return str(it.get("citycode"))
    return None


def collect_region(region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    month = now.strftime("%Y%m")
    city = region["name"].split()[-1]
    out_path = OUT_DIR / f"{region_key}.parquet"
    base = {"name": f"버스정류장 위치 ({region['name']})", "region": region_key, "method": "API 자동 (GitHub Actions)",
            "cycle": "월 1회", "last_checked": now.isoformat()}
    if prev.get("collected_month") == month and (out_path.exists() or prev.get("status") == "NOT_PROVIDED"):
        return {**prev, **base}

    code = city_code(api_key, city)
    if not code:
        return {**base, "status": "NOT_PROVIDED", "collected_month": month, "rows": 0,
                "message": f"{city}은(는) 국가대중교통정보(TAGO)에 연계되지 않아 제공되지 않음"}

    rows, page = [], 1
    while True:
        data = get_json(f"{BASE}/getSttnNoList", {"serviceKey": api_key, "cityCode": code, "pageNo": str(page),
                                                  "numOfRows": str(PAGE), "_type": "json"}, tries=4, timeout=90)
        if not check_result(data, f"버스정류소 {city}"):
            break
        items = extract_items(data)
        rows.extend(items)
        total = int(str(find_key(data, "totalCount") or 0) or 0)
        if not items or page * PAGE >= total:
            break
        page += 1
        time.sleep(0.2)
    if not rows:
        raise SourceError(f"{city} 버스정류소가 0건입니다 (도시코드 {code}).")

    raw = pd.DataFrame(rows)
    df = pd.DataFrame({
        "node_id": raw.get("nodeid", pd.Series("", index=raw.index)).astype(str),
        "node_no": raw.get("nodeno", pd.Series("", index=raw.index)).astype(str),
        "name": raw.get("nodenm", pd.Series("", index=raw.index)).astype(str).str.strip(),
        "lat": pd.to_numeric(raw.get("gpslati"), errors="coerce"),
        "lon": pd.to_numeric(raw.get("gpslong"), errors="coerce"),
    }).dropna(subset=["lat", "lon"]).drop_duplicates("node_id")
    df = attach_dong(df.reset_index(drop=True), region_key)
    df = df[df["adong_cd"] != ""]  # 시 경계 밖(1km 넘게 떨어진) 정류소는 뺌

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out_path)
    return {**base, "status": "SUCCESS", "rows": len(df), "collected_month": month, "base_period": month,
            "city_code": code, "file": str(out_path.relative_to(MART_DIR)), "last_updated": now.isoformat(),
            "message": f"새 자료 저장 (정류장 {len(df):,}곳)"}
