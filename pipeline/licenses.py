"""행정안전부 식품 인허가 (일반음식점·휴게음식점) → data/mart/licenses/{업종}_{지역}.parquet

- 첫 실행: 지역 전체 이력(영업·폐업 모두)을 받습니다.
- 이후: 지난 실행 이후 바뀐 건(데이터갱신시점)만 받아 관리번호 기준으로 합칩니다.
- 좌표는 EPSG:5174(중부원점TM)로 오므로 위경도로 바꾸고,
  가장 가까운 상가정보 점포의 행정동을 붙입니다 (1km 이내일 때만).
- 전화번호는 분석에 쓰지 않으므로 저장하지 않습니다.
"""

import time
from collections import Counter
from datetime import timedelta

import numpy as np
import pandas as pd

from pipeline.common import MART_DIR, SourceError, check_result, extract_items, find_key, get_json

SERVICES = {"general_restaurants": "일반음식점", "rest_cafes": "휴게음식점"}
OUT_DIR = MART_DIR / "licenses"
STORES_DIR = MART_DIR / "stores"
PAGE = 100

COLS = [
    "mgmt_no", "service_nm", "biz_name", "bzstat_nm", "open_date", "close_date", "status_cd", "status_nm",
    "detail_status_nm", "sigungu_cd", "adong_cd", "adong_nm", "addr", "lon", "lat",
    "area_m2", "opn_atmy_grp_cd", "data_updated", "updated_at",
]


def _fetch(service: str, api_key: str, conds: dict, max_pages: int = 10_000) -> list:
    url = f"https://apis.data.go.kr/1741000/{service}/info"
    rows, page = [], 1
    while page <= max_pages:
        params = {"serviceKey": api_key, "pageNo": str(page), "numOfRows": str(PAGE), "returnType": "json"}
        params.update({f"cond[{k}]": v for k, v in conds.items()})
        data = get_json(url, params, timeout=60)
        if not check_result(data, f"{SERVICES[service]} 인허가"):
            break
        items = extract_items(data)
        rows.extend(items)
        total = int(str(find_key(data, "totalCount") or 0) or 0)
        if not items or page * PAGE >= total:
            break
        page += 1
        time.sleep(0.1)
    return rows


def find_group_codes(service: str, api_key: str, sido: str, city: str) -> list:
    """주소로 몇 페이지 조회해서 그 지역을 담당하는 개방자치단체코드를 알아냅니다."""
    sample = _fetch(service, api_key, {"ROAD_NM_ADDR::LIKE": f"{sido} {city}"}, max_pages=3)
    counts = Counter(str(r.get("OPN_ATMY_GRP_CD", "")).strip() for r in sample)
    counts.pop("", None)
    if not counts:
        raise SourceError(f"'{sido} {city}' 주소의 {SERVICES[service]}를 찾지 못했습니다.")
    top = counts.most_common(1)[0][1]
    return sorted(c for c, n in counts.items() if n >= max(3, top * 0.05))


def _to_wgs84(x: pd.Series, y: pd.Series):
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:5174", "EPSG:4326", always_xy=True)
    xs, ys = pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce")
    ok = xs.notna() & ys.notna() & (xs > 0) & (ys > 0)
    lon, lat = pd.Series(np.nan, index=x.index), pd.Series(np.nan, index=x.index)
    if ok.any():
        lo, la = tf.transform(xs[ok].to_numpy(), ys[ok].to_numpy())
        lon[ok], lat[ok] = lo, la
    return lon, lat


def attach_dong(df: pd.DataFrame, region_key: str) -> pd.DataFrame:
    """가장 가까운 상가정보 점포(1km 이내)의 시군구·행정동을 붙입니다."""
    snaps = sorted(STORES_DIR.glob(f"{region_key}_*.parquet"))
    df["sigungu_cd"], df["adong_cd"], df["adong_nm"] = "", "", ""
    if not snaps:
        return df
    stores = pd.read_parquet(snaps[-1], columns=["sigungu_cd", "adong_cd", "adong_nm", "lon", "lat"]).dropna()
    s_lon, s_lat = stores["lon"].to_numpy(), stores["lat"].to_numpy()
    has = df["lon"].notna().to_numpy()
    idx_all = np.flatnonzero(has)
    lon, lat = df["lon"].to_numpy(dtype=float), df["lat"].to_numpy(dtype=float)
    best = np.full(len(df), -1)
    for start in range(0, len(idx_all), 400):
        idx = idx_all[start:start + 400]
        # 짧은 거리라 평면 근사(km): 경도 1도 = 111km × cos(위도)
        dx = (lon[idx, None] - s_lon[None, :]) * 111.0 * np.cos(np.radians(lat[idx, None]))
        dy = (lat[idx, None] - s_lat[None, :]) * 111.0
        d2 = dx * dx + dy * dy
        nearest = d2.argmin(axis=1)
        within = d2[np.arange(len(idx)), nearest] <= 1.0  # 1km
        best[idx[within]] = nearest[within]
    hit = best >= 0
    for col in ("sigungu_cd", "adong_cd", "adong_nm"):
        vals = df[col].to_numpy(dtype=object)
        vals[hit] = stores[col].to_numpy()[best[hit]]
        df[col] = vals
    return df


def normalize(rows: list, service: str, city: str, now) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if "MNG_NO" not in df.columns:
        raise SourceError(f"{SERVICES[service]} 응답 형식이 바뀌었습니다 (관리번호 없음).")
    g = lambda c: df[c].fillna("").astype(str).str.strip() if c in df.columns else pd.Series("", index=df.index)  # noqa: E731
    road, lot = g("ROAD_NM_ADDR"), g("LOTNO_ADDR")
    out = pd.DataFrame({
        "mgmt_no": g("MNG_NO"),
        "service_nm": SERVICES[service],
        "biz_name": g("BPLC_NM"),
        "bzstat_nm": g("BZSTAT_SE_NM").where(g("BZSTAT_SE_NM") != "", g("SNTTN_BZSTAT_NM")),
        "open_date": g("LCPMT_YMD").str.replace("-", "").str[:8],
        "close_date": g("CLSBIZ_YMD").str.replace("-", "").str[:8],
        "status_cd": g("SALS_STTS_CD"),
        "status_nm": g("SALS_STTS_NM"),
        "detail_status_nm": g("DTL_SALS_STTS_NM"),
        "addr": road.where(road != "", lot),
        "area_m2": pd.to_numeric(g("FCLT_TOTAL_SCL").where(g("FCLT_TOTAL_SCL") != "", g("LCTN_AREA")), errors="coerce"),
        "opn_atmy_grp_cd": g("OPN_ATMY_GRP_CD"),
        "data_updated": g("DAT_UPDT_PNT"),
        "updated_at": pd.Timestamp(now.replace(tzinfo=None)),
    })
    out["lon"], out["lat"] = _to_wgs84(g("CRD_INFO_X"), g("CRD_INFO_Y"))
    # 같은 자치단체코드라도 다른 시 주소가 섞일 수 있어 주소로 한 번 더 거른다
    in_city = road.str.contains(city, regex=False) | lot.str.contains(city, regex=False)
    out = out[in_city & (out["mgmt_no"] != "")]
    return out


def collect(service: str, region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    sido, city = region["sido_name"], region["name"].split()[-1]
    codes = prev.get("group_codes") or find_group_codes(service, api_key, sido, city)
    out = OUT_DIR / f"{service}_{region_key}.parquet"
    incremental = out.exists() and prev.get("synced_through")

    conds_base = {}
    if incremental:
        since = (pd.Timestamp(prev["synced_through"]).tz_localize(None) - timedelta(days=3)).strftime("%Y%m%d000000")
        conds_base["DAT_UPDT_PNT::GTE"] = since

    rows = []
    for code in codes:
        rows.extend(_fetch(service, api_key, {**conds_base, "OPN_ATMY_GRP_CD::EQ": code}))

    new = normalize(rows, service, city, now) if rows else pd.DataFrame(columns=COLS)
    if not incremental and new.empty:
        raise SourceError(f"{city} {SERVICES[service]} 인허가가 0건입니다.")
    if not new.empty:
        new = attach_dong(new, region_key)

    if incremental and new.empty:
        return {
            **prev, "last_checked": now.isoformat(), "synced_through": now.isoformat(), "status": "SUCCESS",
            "message": f"변경 없음 · 누적 {int(prev.get('rows') or 0):,}건",
        }
    if incremental:
        old = pd.read_parquet(out)
        merged = pd.concat([old, new[COLS]], ignore_index=True)
        merged = merged.sort_values("data_updated").drop_duplicates("mgmt_no", keep="last")
    else:
        merged = new[COLS].drop_duplicates("mgmt_no", keep="last")
    merged = merged.sort_values("mgmt_no").reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(out)

    open_cnt = int((merged["status_cd"] == "01").sum()) if "status_cd" in merged else 0
    return {
        "name": f"인허가 {SERVICES[service]} ({region['name']})",
        "region": region_key, "method": "API 자동 (GitHub Actions)", "cycle": "매주 변경분 반영",
        "base_period": now.strftime("%Y%m%d"), "rows": len(merged), "file": str(out.relative_to(MART_DIR)),
        "group_codes": codes, "last_checked": now.isoformat(), "last_updated": now.isoformat(),
        "synced_through": now.isoformat(),
        "status": "SUCCESS",
        "message": (f"{'변경분' if incremental else '전체'} {len(new):,}건 반영 · 누적 {len(merged):,}건"
                    f" (영업 중 {open_cnt:,}건)"),
    }
