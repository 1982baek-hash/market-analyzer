"""data/mart/ (GitHub Actions가 커밋한 수집 결과) → 앱의 DuckDB로 옮기는 모듈.

Streamlit Cloud는 재시작할 때마다 저장소에 있는 파일로 돌아가므로,
앱이 시작될 때 data/mart/ 를 읽어 DuckDB를 채웁니다. 앱은 데이터를 직접 수집하지 않습니다.
"""

import hashlib
import json
import threading
from datetime import datetime

import pandas as pd

from src.config_loader import BASE_DIR, load_regions_config

MART_DIR = BASE_DIR / "data" / "mart"
STATUS_PATH = MART_DIR / "_status.json"
STORES_DIR = MART_DIR / "stores"

STORE_COLS = [
    "base_ym", "store_id", "store_name", "branch_name",
    "inds_lcls_cd", "inds_lcls_nm", "inds_mcls_cd", "inds_mcls_nm",
    "inds_scls_cd", "inds_scls_nm", "ksic_cd", "ksic_nm",
    "sido_cd", "sido_nm", "sigungu_cd", "sigungu_nm",
    "adong_cd", "adong_nm", "ldong_cd", "ldong_nm",
    "lno_adr", "rdnm_adr", "lon", "lat", "is_franchise", "updated_at",
]

_lock = threading.Lock()
_synced_signature = None


def read_status() -> dict:
    if not STATUS_PATH.exists():
        return {}
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def mart_signature() -> str:
    """수집 결과 파일이 바뀌었는지 판단하는 값 (파일 이름·크기·수정시각)."""
    parts = []
    for p in sorted(MART_DIR.rglob("*")) if MART_DIR.exists() else []:
        if p.is_file():
            st_ = p.stat()
            parts.append(f"{p.relative_to(MART_DIR)}:{st_.st_size}:{int(st_.st_mtime)}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _sql_path(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _to_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def sync_mart_to_duckdb() -> dict:
    """스냅샷 Parquet와 수집 상태를 DuckDB에 반영합니다. 이미 같은 건수가 있으면 건너뜁니다."""
    from src.db_manager import DatabaseManager  # 순환 import 방지

    db = DatabaseManager()
    regions = load_regions_config()
    status = read_status()
    loaded, skipped = [], []
    cols = ", ".join(STORE_COLS)

    with db.get_connection(read_only=False) as con:
        for p in sorted(STORES_DIR.glob("*.parquet")) if STORES_DIR.exists() else []:
            region_key, _, base_ym = p.stem.rpartition("_")
            if region_key not in regions:
                continue
            codes = [str(c) for c in regions[region_key]["sigungu_codes"]]
            ph = ", ".join(["?"] * len(codes))
            file_rows = con.execute(f"SELECT COUNT(*) FROM read_parquet({_sql_path(p)})").fetchone()[0]
            db_rows = con.execute(
                f"SELECT COUNT(*) FROM stores_history WHERE base_ym = ? AND sigungu_cd IN ({ph})",
                [base_ym, *codes],
            ).fetchone()[0]
            if db_rows == file_rows:
                skipped.append(p.name)
                continue
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(
                    f"DELETE FROM stores_history WHERE base_ym = ? AND sigungu_cd IN ({ph})",
                    [base_ym, *codes],
                )
                con.execute(
                    f"INSERT INTO stores_history ({cols}) SELECT {cols} FROM read_parquet({_sql_path(p)})"
                )
                con.execute("COMMIT")
                loaded.append(f"{p.name} ({file_rows:,}건)")
            except Exception:
                con.execute("ROLLBACK")
                raise

        # 보조 데이터 반영이 실패해도 점포 화면은 계속 쓸 수 있게 한다 (오류는 errors에 남김)
        errors = []
        for label, fn in (("인구", lambda: _sync_population(con, regions)), ("인허가", lambda: _sync_licenses(con))):
            try:
                loaded += fn()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{label}: {e}")
                print(f"[mart_sync] {label} 반영 실패: {e}", flush=True)

        for source_id, s in status.items():
            if source_id.startswith("_") or not isinstance(s, dict):
                continue
            con.execute(
                "INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    source_id, s.get("name", source_id), s.get("region", "-"),
                    s.get("method", "API 자동 (GitHub Actions)"), s.get("cycle", "-"),
                    str(s.get("base_period") or "-"), int(s.get("rows") or 0),
                    _to_ts(s.get("last_checked")), _to_ts(s.get("last_updated")),
                    s.get("status", "PENDING"), s.get("message", ""),
                ],
            )

    return {"loaded": loaded, "skipped": skipped, "errors": errors}


def _latest_per_region(folder, regions) -> dict:
    """{지역키: 가장 최근 파일} — 파일 이름이 {지역}_{기간}.parquet 인 폴더용"""
    latest = {}
    for p in sorted(folder.glob("*.parquet")) if folder.exists() else []:
        region_key, _, period = p.stem.rpartition("_")
        if region_key in regions and period >= latest.get(region_key, ("", None))[0]:
            latest[region_key] = (period, p)
    return {k: v[1] for k, v in latest.items()}


def _sync_population(con, regions) -> list:
    """주민등록 인구 → region_demographics. 가구 수는 SGIS 읍면동 이름으로 맞춰 붙인다."""
    pops = _latest_per_region(MART_DIR / "population", regions)
    sgis_files = _latest_per_region(MART_DIR / "sgis", regions)
    done = []
    for region_key, p in pops.items():
        base_ym = p.stem.rpartition("_")[2]
        hh_join, hh_col = "", "NULL"
        if region_key in sgis_files:
            hh_join = ("LEFT JOIN (SELECT sigungu_cd, adm_nm, MAX(household_cnt) AS household_cnt "
                       f"FROM read_parquet({_sql_path(sgis_files[region_key])}) GROUP BY 1, 2) h "
                       "ON h.sigungu_cd = p.sigungu_cd AND replace(h.adm_nm, ' ', '') = replace(p.adong_nm, ' ', '')")
            hh_col = "CAST(h.household_cnt AS BIGINT)"
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                f"""INSERT OR REPLACE INTO region_demographics
                    (base_ym, sigungu_cd, adong_cd, adong_nm, population, households, area_km2, updated_at)
                    SELECT p.base_ym, p.sigungu_cd, p.adong_cd, p.adong_nm, CAST(p.population AS BIGINT),
                           {hh_col}, NULL, now()
                    FROM read_parquet({_sql_path(p)}) p {hh_join}"""
            )
            con.execute("COMMIT")
            done.append(f"{p.name}")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return done


def _sync_licenses(con) -> list:
    """인허가 → licenses_history (관리번호 기준으로 덮어쓰기)"""
    folder = MART_DIR / "licenses"
    done = []
    for p in sorted(folder.glob("*.parquet")) if folder.exists() else []:
        con.execute(
            f"""INSERT OR REPLACE INTO licenses_history
                (mgmt_no, service_nm, biz_name, open_date, close_date, status_nm,
                 sigungu_cd, adong_cd, adong_nm, addr, lon, lat, updated_at)
                SELECT mgmt_no, service_nm, biz_name, open_date, close_date, status_nm,
                       sigungu_cd, adong_cd, adong_nm, addr, lon, lat, updated_at
                FROM read_parquet({_sql_path(p)})"""
        )
        done.append(p.name)
    return done


def ensure_synced() -> None:
    """프로세스당 한 번, 그리고 data/mart/ 가 바뀌었을 때만 동기화합니다."""
    global _synced_signature
    sig = mart_signature()
    if sig == _synced_signature:
        return
    with _lock:
        if sig == _synced_signature:
            return
        sync_mart_to_duckdb()
        _synced_signature = sig


def status_summary() -> dict:
    """홈 화면 안내용 요약: 마지막 실행 시각과 실패한 소스 목록."""
    status = read_status()
    run = status.get("_run", {})
    failures = [
        f"{s.get('name', sid)}: {s.get('message', '')}"
        for sid, s in status.items()
        if not sid.startswith("_") and isinstance(s, dict) and s.get("status") == "FAIL"
    ]
    return {"finished_at": run.get("finished_at"), "failures": failures, "has_run": bool(run)}
