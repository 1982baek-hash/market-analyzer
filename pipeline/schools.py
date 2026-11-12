"""전국초중등학교위치표준데이터 → data/mart/schools/{지역}.parquet

- 전국 자료(약 1만 2천 곳)를 1,000건씩 받아 주소로 세 지역만 남깁니다.
- 매월 초에 갱신되는 자료라 한 달에 한 번만 새로 받습니다.
- 행정동은 가장 가까운 상가정보 점포(1km 이내)의 행정동을 붙입니다.
"""

import time

import pandas as pd

from pipeline.common import MART_DIR, SourceError, check_result, extract_items, find_key, get_json
from pipeline.licenses import attach_dong

API_URL = "https://api.data.go.kr/openapi/tn_pubr_public_elesch_mskul_lc_api"
OUT_DIR = MART_DIR / "schools"
PAGE = 1000

# 표준데이터 API의 영문 항목명 (혹시 한글 항목명으로 오면 그것도 받음)
FIELDS = {
    "school_id": ["schoolId", "학교ID"],
    "name": ["schoolNm", "학교명"],
    "level": ["schoolSe", "학교급구분"],
    "found_type": ["fondType", "설립형태"],
    "branch": ["bnhhYn", "본교분교구분"],
    "status": ["operSttus", "운영상태"],
    "addr_lot": ["lnmadr", "소재지지번주소"],
    "addr_road": ["rdnmadr", "소재지도로명주소"],
    "lat": ["latitude", "위도"],
    "lon": ["longitude", "경도"],
    "base_date": ["referenceDate", "데이터기준일자"],
}

_cache = {}


def fetch_all(api_key: str) -> pd.DataFrame:
    """전국 자료를 한 번만 받아 세 지역이 함께 씁니다."""
    if "all" in _cache:
        return _cache["all"]
    rows, page = [], 1
    while True:
        data = get_json(API_URL, {"serviceKey": api_key, "pageNo": str(page), "numOfRows": str(PAGE), "type": "json"},
                        tries=4, timeout=90)
        if not check_result(data, "학교 위치"):
            break
        items = extract_items(data)
        rows.extend(items)
        total = int(str(find_key(data, "totalCount") or 0) or 0)
        if not items or page * PAGE >= total:
            break
        page += 1
        time.sleep(0.2)
    if not rows:
        raise SourceError("학교 위치 API가 한 건도 돌려주지 않았습니다.")
    raw = pd.DataFrame(rows)
    out = pd.DataFrame(index=raw.index)
    for col, cands in FIELDS.items():
        hit = next((c for c in cands if c in raw.columns), None)
        out[col] = raw[hit].astype(str).str.strip() if hit else ""
    if (out["lat"] == "").all():
        raise SourceError(f"학교 위치 응답에 위도·경도가 없습니다 (항목: {list(raw.columns)[:20]})")
    out["lat"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon"] = pd.to_numeric(out["lon"], errors="coerce")
    _cache["all"] = out
    return out


def collect_region(region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    month = now.strftime("%Y%m")
    out_path = OUT_DIR / f"{region_key}.parquet"
    base = {"name": f"학교 위치 ({region['name']})", "region": region_key, "method": "API 자동 (GitHub Actions)",
            "cycle": "월 1회", "last_checked": now.isoformat()}
    if prev.get("collected_month") == month and out_path.exists():
        return {**prev, **base, "status": "SUCCESS", "message": f"이번 달 이미 받음 · {prev.get('rows', 0)}곳"}

    allsch = fetch_all(api_key)
    sido, city = region["sido_name"], region["name"].split()[-1]
    addr = allsch["addr_road"].where(allsch["addr_road"] != "", allsch["addr_lot"])
    df = allsch[addr.str.contains(f"{sido} {city}", regex=False) & allsch["lat"].notna()].copy()
    df = df[~df["status"].isin(["폐교", "휴교"])]
    if df.empty:
        raise SourceError(f"{region['name']} 학교가 0곳입니다.")
    df["addr"] = addr.loc[df.index]
    df = attach_dong(df.reset_index(drop=True), region_key)
    cols = ["school_id", "name", "level", "found_type", "branch", "status", "addr", "lat", "lon",
            "sigungu_cd", "adong_cd", "adong_nm", "base_date"]
    df = df[cols].sort_values(["level", "name"]).reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out_path)
    counts = df["level"].value_counts()
    detail = ", ".join(f"{k} {v}" for k, v in counts.items())
    return {**base, "status": "SUCCESS", "rows": len(df), "collected_month": month,
            "base_period": (df["base_date"].max() or month).replace("-", "")[:8],
            "file": str(out_path.relative_to(MART_DIR)), "last_updated": now.isoformat(),
            "message": f"새 자료 저장 ({detail})"}
