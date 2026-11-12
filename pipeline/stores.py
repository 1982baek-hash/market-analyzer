"""소상공인시장진흥공단 상가(상권)정보 API → data/mart/stores/{지역}_{기준연월}.parquet

매주 실행하지만, 점포 목록이 지난 스냅샷과 같으면 새 파일을 만들지 않습니다.
(상가정보는 분기마다 갱신되므로 대부분의 주에는 '변경 없음'이 정상입니다.)
"""

import hashlib
import sys
import time

import pandas as pd

from pipeline.common import MART_DIR, ROOT, SourceError, get_json

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.code_normalizer import AdminCodeNormalizer  # noqa: E402  (v1 코드 재사용)

API_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"
STORES_DIR = MART_DIR / "stores"
PAGE_SIZE = 1000
# 새 수집 건수가 직전 스냅샷의 70% 미만이면 API 이상으로 보고 저장하지 않는다
MIN_RATIO_VS_PREVIOUS = 0.7

API_TO_STD = {
    "bizesId": "store_id", "bizesNm": "store_name", "brchNm": "branch_name",
    "indsLclsCd": "inds_lcls_cd", "indsLclsNm": "inds_lcls_nm",
    "indsMclsCd": "inds_mcls_cd", "indsMclsNm": "inds_mcls_nm",
    "indsSclsCd": "inds_scls_cd", "indsSclsNm": "inds_scls_nm",
    "ksicCd": "ksic_cd", "ksicNm": "ksic_nm",
    "ctprvnCd": "sido_cd", "ctprvnNm": "sido_nm",
    "signguCd": "sigungu_cd", "signguNm": "sigungu_nm",
    "adongCd": "adong_cd", "adongNm": "adong_nm",
    "ldongCd": "ldong_cd", "ldongNm": "ldong_nm",
    "lnoAdr": "lno_adr", "rdnmAdr": "rdnm_adr",
    "lon": "lon", "lat": "lat",
}

# DuckDB stores_history 테이블과 같은 순서 (src/db_manager.py)
STORE_COLS = [
    "base_ym", "store_id", "store_name", "branch_name",
    "inds_lcls_cd", "inds_lcls_nm", "inds_mcls_cd", "inds_mcls_nm",
    "inds_scls_cd", "inds_scls_nm", "ksic_cd", "ksic_nm",
    "sido_cd", "sido_nm", "sigungu_cd", "sigungu_nm",
    "adong_cd", "adong_nm", "ldong_cd", "ldong_nm",
    "lno_adr", "rdnm_adr", "lon", "lat", "is_franchise", "updated_at",
]
TEXT_COLS = [c for c in STORE_COLS if c not in ("lon", "lat", "is_franchise", "updated_at")]


def fetch_sigungu(api_key: str, sigungu_cd: str) -> list:
    """시군구 하나의 점포 전체를 페이지 단위로 받아 옵니다."""
    items, page = [], 1
    while True:
        data = get_json(API_URL, {
            "serviceKey": api_key, "divId": "signguCd", "key": sigungu_cd,
            "numOfRows": str(PAGE_SIZE), "pageNo": str(page), "type": "json",
        })
        header = data.get("header", {})
        code = str(header.get("resultCode", ""))
        if code == "03":  # NODATA
            break
        if code != "00":
            raise SourceError(f"API 오류 {code}: {header.get('resultMsg')} (시군구 {sigungu_cd})")
        body = data.get("body", {})
        page_items = body.get("items") or []
        if isinstance(page_items, dict):  # 1건일 때 dict로 오는 경우
            page_items = [page_items]
        items.extend(page_items)
        total = int(body.get("totalCount", 0) or 0)
        if not page_items or page * PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(0.2)
    return items


def normalize(raw: pd.DataFrame, sigungu_codes: list, base_ym: str, now) -> pd.DataFrame:
    df = raw.rename(columns={k: v for k, v in API_TO_STD.items() if k in raw.columns}).copy()
    required = ["store_id", "store_name", "inds_scls_cd", "sigungu_cd", "adong_cd", "lon", "lat"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SourceError(f"API 응답 형식이 바뀌었습니다. 없는 항목: {missing}")

    for col in TEXT_COLS:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    df["adong_cd"] = AdminCodeNormalizer().normalize_adong_code(df["adong_cd"], df["adong_nm"])
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df = df[df["sigungu_cd"].isin([str(c) for c in sigungu_codes])]
    df = df[(df["store_id"] != "") & df["lon"].notna() & df["lat"].notna()]
    df = df.drop_duplicates(subset=["store_id"])

    df["base_ym"] = base_ym
    df["is_franchise"] = df["branch_name"].str.len() > 0
    df["updated_at"] = pd.Timestamp(now.replace(tzinfo=None))
    return df[STORE_COLS].sort_values("store_id").reset_index(drop=True)


def fingerprint(df: pd.DataFrame) -> str:
    """점포 번호·업종·상호가 모두 같으면 같은 스냅샷으로 봅니다."""
    keys = (df["store_id"] + "|" + df["inds_scls_cd"] + "|" + df["store_name"]).sort_values()
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()


def collect_region(region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    """한 지역을 수집해 상태 정보를 돌려줍니다. 실패하면 SourceError를 던지고 파일은 건드리지 않습니다."""
    base_ym = now.strftime("%Y%m")
    codes = [str(c) for c in region["sigungu_codes"]]

    raw_items = []
    for code in codes:
        raw_items.extend(fetch_sigungu(api_key, code))
    if not raw_items:
        raise SourceError("API가 점포를 한 건도 돌려주지 않았습니다.")

    df = normalize(pd.DataFrame(raw_items), codes, base_ym, now)
    if df.empty:
        raise SourceError("좌표·행정동 코드가 있는 점포가 0건입니다.")
    found = set(df["sigungu_cd"])
    if not found.issuperset(codes):
        raise SourceError(f"일부 시군구 데이터가 비어 있습니다: {sorted(set(codes) - found)}")

    prev_rows = int(prev.get("rows") or 0)
    if prev_rows and len(df) < prev_rows * MIN_RATIO_VS_PREVIOUS:
        raise SourceError(
            f"점포 수가 {prev_rows:,}건에서 {len(df):,}건으로 급감해 저장하지 않았습니다 (API 이상 의심)."
        )

    fp = fingerprint(df)
    result = {
        "name": f"소상공인 상가(상권)정보 ({region['name']})",
        "region": region_key,
        "method": "API 자동 (GitHub Actions)",
        "cycle": "분기 갱신 · 매주 확인",
        "last_checked": now.isoformat(),
        "status": "SUCCESS",
        "message": "",
    }
    if fp == prev.get("fingerprint"):
        result.update({k: prev.get(k) for k in ("base_period", "rows", "fingerprint", "file", "last_updated")})
        result["message"] = "변경 없음 (이전 스냅샷 유지)"
        return result

    STORES_DIR.mkdir(parents=True, exist_ok=True)
    out = STORES_DIR / f"{region_key}_{base_ym}.parquet"
    tmp = out.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)  # 다 쓴 뒤에만 바꿔 끼워서, 중간 실패 시 반쪽 파일이 남지 않게 한다

    result.update({
        "base_period": base_ym,
        "rows": len(df),
        "fingerprint": fp,
        "file": str(out.relative_to(MART_DIR)),
        "last_updated": now.isoformat(),
        "message": f"새 스냅샷 저장 ({len(df):,}건)",
    })
    return result
