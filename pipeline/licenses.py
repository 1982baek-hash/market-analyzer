"""행정안전부 식품 인허가 (일반음식점·휴게음식점) → data/mart/licenses/{업종}_{지역}.parquet

- 첫 실행: 지역 전체 이력(영업·폐업 모두)을 받습니다.
- 이후: 지난 실행 이후 바뀐 건(데이터갱신시점)만 받아 관리번호 기준으로 합칩니다.
- 좌표는 EPSG:5174(중부원점TM)로 오므로 위경도로 바꾸고,
  가장 가까운 상가정보 점포의 행정동을 붙입니다 (1km 이내일 때만).
- 전화번호는 분석에 쓰지 않으므로 저장하지 않습니다.
"""

import os
import time
from collections import Counter
from datetime import timedelta

import numpy as np
import pandas as pd

from pipeline.common import MART_DIR, SourceError, check_result, extract_items, find_key, get_json

# 휴게음식점(카페·간식)이 커피+간식 매장 분석의 핵심이라 먼저 받는다
SERVICES = {"rest_cafes": "휴게음식점", "general_restaurants": "일반음식점"}
OUT_DIR = MART_DIR / "licenses"
PARTIAL_DIR = MART_DIR / "_partial"  # 첫 전체 수집이 한 번에 안 끝났을 때 이어 받을 자료
# Actions 작업 제한(120분) 안에 결과를 커밋할 수 있도록, 수집 시작 후 이 시간이 지나면 멈추고 다음에 이어 받는다
_DEADLINE = time.monotonic() + float(os.environ.get("LICENSE_DEADLINE_MIN", "95")) * 60
STORES_DIR = MART_DIR / "stores"
PAGE = 100

COLS = [
    "mgmt_no", "service_nm", "biz_name", "bzstat_nm", "open_date", "close_date", "status_cd", "status_nm",
    "detail_status_nm", "sigungu_cd", "adong_cd", "adong_nm", "addr", "lon", "lat",
    "area_m2", "opn_atmy_grp_cd", "data_updated", "updated_at",
]


def _time_left() -> float:
    return _DEADLINE - time.monotonic()


def _fetch(service: str, api_key: str, conds: dict, max_pages: int = 10_000,
           start_page: int = 1, resumable: bool = False):
    """페이지를 차례로 받습니다. (받은 행, 다음에 받을 페이지 또는 None, 전체 건수, 오류)를 돌려줍니다.

    resumable=True(첫 전체 수집)이면 마감 시간이 되거나 한 페이지가 끝내 실패할 때
    거기까지 받은 것만 돌려주고, 다음 실행이 그 페이지부터 이어 받습니다.
    """
    url = f"https://apis.data.go.kr/1741000/{service}/info"
    rows, page, total, t0 = [], start_page, 0, time.monotonic()
    while page < start_page + max_pages:
        if resumable and _time_left() <= 0:
            return rows, page, total, "이번 실행의 수집 시간이 다 되어 멈춤"
        params = {"serviceKey": api_key, "pageNo": str(page), "numOfRows": str(PAGE), "returnType": "json"}
        params.update({f"cond[{k}]": v for k, v in conds.items()})
        try:
            # 전국 데이터라 서버가 느릴 때가 있어 기다리는 시간과 재시도를 넉넉히 둔다
            data = get_json(url, params, tries=5, timeout=120)
        except SourceError as e:
            if resumable and rows:
                return rows, page, total, str(e)
            raise
        if not check_result(data, f"{SERVICES[service]} 인허가"):
            break
        items = extract_items(data)
        rows.extend(items)
        total = int(str(find_key(data, "totalCount") or 0) or 0)
        if not items or page * PAGE >= total:
            break
        if (page - start_page + 1) % 25 == 0:
            done = (page - start_page + 1)
            print(f"    {page * PAGE:,}/{total:,}건 · 페이지당 {(time.monotonic() - t0) / done:.1f}초", flush=True)
        page += 1
        time.sleep(0.1)
    return rows, None, total, None


def find_group_codes(service: str, api_key: str, sido: str, city: str) -> list:
    """주소로 몇 페이지 조회해서 그 지역을 담당하는 개방자치단체코드를 알아냅니다.

    주소 부분일치(LIKE) 검색은 전국 데이터를 훑어서 시간 초과가 잘 나므로,
    regions.yaml에 license_org_codes가 없을 때만 쓰는 예비 방법입니다.
    """
    sample = _fetch(service, api_key, {"ROAD_NM_ADDR::LIKE": f"{sido} {city}"}, max_pages=3)[0]
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


def _save(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def collect(service: str, region_key: str, region: dict, api_key: str, prev: dict, now) -> dict:
    sido, city = region["sido_name"], region["name"].split()[-1]
    codes = ([str(c) for c in region.get("license_org_codes") or []]
             or prev.get("group_codes")
             or find_group_codes(service, api_key, sido, city))
    out = OUT_DIR / f"{service}_{region_key}.parquet"
    label = f"인허가 {SERVICES[service]} ({region['name']})"
    base = {"name": label, "region": region_key, "method": "API 자동 (GitHub Actions)",
            "cycle": "매주 변경분 반영", "group_codes": codes, "last_checked": now.isoformat()}

    if out.exists() and prev.get("synced_through"):
        return _collect_changes(service, region_key, city, codes, api_key, prev, now, out, base)
    return _collect_full(service, region_key, city, codes, api_key, prev, now, out, base)


def _collect_changes(service, region_key, city, codes, api_key, prev, now, out, base) -> dict:
    """전체 수집이 끝난 뒤: 지난 실행 이후 바뀐 건만 받아 합칩니다."""
    since = (pd.Timestamp(prev["synced_through"]).tz_localize(None) - timedelta(days=3)).strftime("%Y%m%d000000")
    rows = []
    for code in codes:
        rows.extend(_fetch(service, api_key, {"DAT_UPDT_PNT::GTE": since, "OPN_ATMY_GRP_CD::EQ": code})[0])
    new = normalize(rows, service, city, now) if rows else pd.DataFrame(columns=COLS)
    if new.empty:
        return {**prev, **base, "synced_through": now.isoformat(), "status": "SUCCESS",
                "message": f"변경 없음 · 누적 {int(prev.get('rows') or 0):,}건"}
    new = attach_dong(new, region_key)
    merged = pd.concat([pd.read_parquet(out), new[COLS]], ignore_index=True)
    merged = merged.sort_values("data_updated").drop_duplicates("mgmt_no", keep="last")
    merged = merged.sort_values("mgmt_no").reset_index(drop=True)
    _save(merged, out)
    return _done(merged, out, base, now, now.isoformat(), f"변경분 {len(new):,}건 반영")


def _collect_full(service, region_key, city, codes, api_key, prev, now, out, base) -> dict:
    """첫 전체 수집: 시간이 모자라면 받은 데까지 저장하고 다음 실행에서 이어 받습니다."""
    partial = PARTIAL_DIR / f"licenses_{service}_{region_key}.parquet"
    resume = prev.get("resume") if partial.exists() else None
    if resume:
        acc = pd.read_parquet(partial)
        code_idx, page, started = int(resume["code_idx"]), int(resume["page"]), resume["started"]
    else:
        acc, code_idx, page, started = pd.DataFrame(), 0, 1, now.isoformat()

    while code_idx < len(codes):
        rows, next_page, total, err = _fetch(service, api_key, {"OPN_ATMY_GRP_CD::EQ": codes[code_idx]},
                                             start_page=page, resumable=True)
        if rows:
            chunk = normalize(rows, service, city, now)
            acc = pd.concat([acc, chunk], ignore_index=True) if not acc.empty else chunk
        if next_page is not None:
            acc = acc.drop_duplicates("mgmt_no", keep="last")
            _save(acc, partial)
            got = min((next_page - 1) * PAGE, total) if total else len(acc)
            return {**base, "status": "PARTIAL", "rows": len(acc),
                    "resume": {"code_idx": code_idx, "page": next_page, "started": started},
                    "message": (f"첫 전체 수집 진행 중 ({got:,}/{total:,}건). 다음 실행 때 이어 받습니다. "
                                f"멈춘 이유: {err}")[:500]}
        code_idx, page = code_idx + 1, 1

    if acc.empty:
        raise SourceError(f"{city} {SERVICES[service]} 인허가가 0건입니다.")
    acc = attach_dong(acc.drop_duplicates("mgmt_no", keep="last"), region_key)
    merged = acc[COLS].sort_values("mgmt_no").reset_index(drop=True)
    _save(merged, out)
    partial.unlink(missing_ok=True)
    # 여러 번에 걸쳐 받았다면 그 사이 바뀐 건을 다음 변경분 수집이 챙기도록 시작 시점을 기준으로 둔다
    return _done(merged, out, base, now, started, f"전체 {len(merged):,}건 받음")


def _done(merged, out, base, now, synced_through, what) -> dict:
    open_cnt = int((merged["status_cd"] == "01").sum()) if "status_cd" in merged else 0
    return {**base, "base_period": now.strftime("%Y%m%d"), "rows": len(merged),
            "file": str(out.relative_to(MART_DIR)), "last_updated": now.isoformat(),
            "synced_through": synced_through, "status": "SUCCESS",
            "message": f"{what} · 누적 {len(merged):,}건 (영업 중 {open_cnt:,}건)"}
