from datetime import datetime, timedelta
import duckdb
import pandas as pd
from src.config_loader import DB_PATH, LOG_PATH

DEFAULT_SOURCES = [
    {
        "source_id": "stores_seongnam",
        "source_name": "소상공인 상가(상권)정보 (성남시)",
        "region_scope": "성남시",
        "collect_method": "API 자동 (OpenAPI)",
        "update_cycle": "분기 1회 (주 1회 확인)",
        "cycle_days": 90,
        "download_url": "https://www.data.go.kr/data/15012005/openapi.do",
        "target_folder": "자동 수집 (data/raw/stores/)",
    },
    {
        "source_id": "stores_andong",
        "source_name": "소상공인 상가(상권)정보 (안동시)",
        "region_scope": "안동시",
        "collect_method": "API 자동 (OpenAPI)",
        "update_cycle": "분기 1회 (주 1회 확인)",
        "cycle_days": 90,
        "download_url": "https://www.data.go.kr/data/15012005/openapi.do",
        "target_folder": "자동 수집 (data/raw/stores/)",
    },
    {
        "source_id": "stores_yecheon",
        "source_name": "소상공인 상가(상권)정보 (예천군)",
        "region_scope": "예천군",
        "collect_method": "API 자동 (OpenAPI)",
        "update_cycle": "분기 1회 (주 1회 확인)",
        "cycle_days": 90,
        "download_url": "https://www.data.go.kr/data/15012005/openapi.do",
        "target_folder": "자동 수집 (data/raw/stores/)",
    },
    {
        "source_id": "demographics_all",
        "source_name": "행정안전부 행정동별 주민등록 인구",
        "region_scope": "공통 (세 지역)",
        "collect_method": "수동 파일 (CSV)",
        "update_cycle": "월 1회",
        "cycle_days": 30,
        "download_url": "https://www.data.go.kr/data/15097972/fileData.do",
        "target_folder": "data/raw/population/",
    },
    {
        "source_id": "boundaries_all",
        "source_name": "통계청 SGIS 행정동 경계 (면적)",
        "region_scope": "공통 (세 지역)",
        "collect_method": "수동 파일 (SHP/GeoJSON)",
        "update_cycle": "연 1회",
        "cycle_days": 365,
        "download_url": "https://sgis.mods.go.kr/view/pss/openDataIntrcn",
        "target_folder": "data/raw/boundaries/",
    },
    {
        "source_id": "floating_seongnam",
        "source_name": "경기데이터드림 행정동 유동인구·유출입",
        "region_scope": "성남시",
        "collect_method": "수동 파일 (CSV)",
        "update_cycle": "월 1회",
        "cycle_days": 30,
        "download_url": "https://data.gg.go.kr",
        "target_folder": "data/raw/floating/",
    },
    {
        "source_id": "floating_andong",
        "source_name": "통계청 인구감소지역 생활인구",
        "region_scope": "안동시 (시 단위)",
        "collect_method": "수동 파일 (CSV)",
        "update_cycle": "분기 1회",
        "cycle_days": 90,
        "download_url": "https://data.kostat.go.kr/nowcast/newBigDataBrdMgr.do?boardId=1&menuId=5&subMenuId=1",
        "target_folder": "data/raw/floating/",
    },
    {
        "source_id": "tourism_andong_yecheon",
        "source_name": "한국관광 데이터랩 방문자 통계",
        "region_scope": "안동시·예천군 (시군 단위)",
        "collect_method": "수동 파일 (XLSX/CSV)",
        "update_cycle": "월 1회",
        "cycle_days": 30,
        "download_url": "https://datalab.visitkorea.or.kr",
        "target_folder": "data/raw/floating/",
    },
    {
        "source_id": "licenses_all",
        "source_name": "지방행정 인허가 데이터 (개·폐업)",
        "region_scope": "공통 (세 지역)",
        "collect_method": "수동 파일 (CSV)",
        "update_cycle": "월 1회",
        "cycle_days": 30,
        "download_url": "https://www.data.go.kr",
        "target_folder": "data/raw/licenses/",
    },
]


class DatabaseManager:
    """로컬 DuckDB 파일에 데이터를 검증 후 안전하게 저장·누적하고 상태를 기록하는 클래스"""

    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)
        self._init_tables()

    def get_connection(self, read_only: bool = False):
        try:
            return duckdb.connect(self.db_path, read_only=read_only)
        except Exception:
            return duckdb.connect(self.db_path)

    def _init_tables(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with self.get_connection(read_only=False) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS stores_history (
                    base_ym VARCHAR,
                    store_id VARCHAR,
                    store_name VARCHAR,
                    branch_name VARCHAR,
                    inds_lcls_cd VARCHAR,
                    inds_lcls_nm VARCHAR,
                    inds_mcls_cd VARCHAR,
                    inds_mcls_nm VARCHAR,
                    inds_scls_cd VARCHAR,
                    inds_scls_nm VARCHAR,
                    ksic_cd VARCHAR,
                    ksic_nm VARCHAR,
                    sido_cd VARCHAR,
                    sido_nm VARCHAR,
                    sigungu_cd VARCHAR,
                    sigungu_nm VARCHAR,
                    adong_cd VARCHAR,
                    adong_nm VARCHAR,
                    ldong_cd VARCHAR,
                    ldong_nm VARCHAR,
                    lno_adr VARCHAR,
                    rdnm_adr VARCHAR,
                    lon DOUBLE,
                    lat DOUBLE,
                    is_franchise BOOLEAN,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (base_ym, store_id)
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS region_demographics (
                    base_ym VARCHAR,
                    sigungu_cd VARCHAR,
                    adong_cd VARCHAR,
                    adong_nm VARCHAR,
                    population BIGINT,
                    households BIGINT,
                    area_km2 DOUBLE,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (base_ym, adong_cd)
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS region_boundaries (
                    adong_cd VARCHAR PRIMARY KEY,
                    sigungu_cd VARCHAR,
                    adong_nm VARCHAR,
                    area_km2 DOUBLE,
                    geojson_str VARCHAR,
                    source_type VARCHAR,
                    updated_at TIMESTAMP
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS data_status (
                    source_id VARCHAR PRIMARY KEY,
                    source_name VARCHAR,
                    region_scope VARCHAR,
                    collect_method VARCHAR,
                    update_cycle VARCHAR,
                    source_base_period VARCHAR,
                    row_count BIGINT,
                    last_checked_at TIMESTAMP,
                    last_updated_at TIMESTAMP,
                    status VARCHAR,
                    error_message VARCHAR
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS manual_floating_input (
                    adong_cd VARCHAR,
                    sigungu_cd VARCHAR,
                    adong_nm VARCHAR,
                    base_ym VARCHAR,
                    daily_avg_pop BIGINT,
                    peak_time_slot VARCHAR,
                    main_age_group VARCHAR,
                    male_ratio DOUBLE,
                    female_ratio DOUBLE,
                    weekday_ratio DOUBLE,
                    note VARCHAR,
                    updated_at TIMESTAMP,
                    PRIMARY KEY (adong_cd, base_ym)
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS floating_hourly (
                    base_ym VARCHAR,
                    sigungu_cd VARCHAR,
                    adong_cd VARCHAR,
                    day_type VARCHAR,
                    hour INTEGER,
                    gender VARCHAR,
                    age_group VARCHAR,
                    pop_count DOUBLE,
                    updated_at TIMESTAMP
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS floating_od (
                    base_ym VARCHAR,
                    origin_adong_cd VARCHAR,
                    origin_name VARCHAR,
                    dest_adong_cd VARCHAR,
                    dest_sigungu_cd VARCHAR,
                    purpose_or_mode VARCHAR,
                    pop_count DOUBLE,
                    updated_at TIMESTAMP
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS floating_sigungu (
                    base_period VARCHAR,
                    region_key VARCHAR,
                    data_source VARCHAR,
                    metric_type VARCHAR,
                    category_1 VARCHAR,
                    category_2 VARCHAR,
                    value DOUBLE,
                    updated_at TIMESTAMP
                )
            """)

            # 7단계 추가: 지방행정 인허가 데이터(개업·폐업 이력) 테이블
            con.execute("""
                CREATE TABLE IF NOT EXISTS licenses_history (
                    mgmt_no VARCHAR PRIMARY KEY,
                    service_nm VARCHAR,
                    biz_name VARCHAR,
                    open_date VARCHAR,
                    close_date VARCHAR,
                    status_nm VARCHAR,
                    sigungu_cd VARCHAR,
                    adong_cd VARCHAR,
                    adong_nm VARCHAR,
                    addr VARCHAR,
                    lon DOUBLE,
                    lat DOUBLE,
                    updated_at TIMESTAMP
                )
            """)

            for src in DEFAULT_SOURCES:
                con.execute("""
                    INSERT INTO data_status (
                        source_id, source_name, region_scope, collect_method,
                        update_cycle, source_base_period, row_count,
                        last_checked_at, last_updated_at, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, '-', 0, NULL, NULL, 'PENDING', '아직 수집되지 않음')
                    ON CONFLICT (source_id) DO NOTHING
                """, [
                    src["source_id"], src["source_name"], src["region_scope"],
                    src["collect_method"], src["update_cycle"]
                ])

    def get_data_status_df(self) -> pd.DataFrame:
        with self.get_connection(read_only=True) as con:
            df = con.execute("SELECT * FROM data_status ORDER BY source_id").df()

        meta_map = {s["source_id"]: s for s in DEFAULT_SOURCES}
        now = datetime.now()

        urls, folders, need_manual_flags = [], [], []
        for _, row in df.iterrows():
            sid = row["source_id"]
            meta = meta_map.get(sid, {})
            urls.append(meta.get("download_url", "-"))
            folders.append(meta.get("target_folder", "-"))

            is_manual = "수동" in str(row["collect_method"])
            cycle_days = meta.get("cycle_days", 30)
            last_upd = row["last_updated_at"]
            if is_manual and (pd.isna(last_upd) or (now - pd.to_datetime(last_upd)).days >= cycle_days):
                need_manual_flags.append(True)
            else:
                need_manual_flags.append(False)

        df["download_url"] = urls
        df["target_folder"] = folders
        df["needs_manual_action"] = need_manual_flags
        return df

    def needs_weekly_check(self) -> tuple[bool, str]:
        with self.get_connection(read_only=True) as con:
            res = con.execute("""
                SELECT MAX(last_checked_at) AS last_chk
                FROM data_status
                WHERE source_id LIKE 'stores_%'
            """).fetchone()

        if not res or res[0] is None:
            return True, "아직 자동 업데이트 확인 이력이 없습니다."

        last_chk = pd.to_datetime(res[0]).to_pydatetime()
        elapsed = datetime.now() - last_chk
        if elapsed >= timedelta(days=7):
            return True, f"마지막 업데이트 확인({last_chk.strftime('%Y-%m-%d')}) 후 {elapsed.days}일이 경과했습니다."
        return False, f"최근 확인일: {last_chk.strftime('%Y-%m-%d %H:%M')}"

    def mark_checked_only(self, source_id: str, message: str = ""):
        now = datetime.now()
        with self.get_connection(read_only=False) as con:
            con.execute("""
                UPDATE data_status
                SET last_checked_at = ?, status = 'SUCCESS', error_message = ?
                WHERE source_id = ?
            """, [now, message, source_id])

    def validate_and_upsert_stores(
        self,
        df: pd.DataFrame,
        base_ym: str,
        region_key: str,
        expected_sigungu_codes: list,
        collect_method: str,
    ) -> int:
        source_id = f"stores_{region_key}"
        source_name = f"소상공인 상가(상권)정보 ({region_key})"
        now = datetime.now()

        required_cols = [
            "store_id", "store_name", "branch_name",
            "inds_lcls_cd", "inds_lcls_nm", "inds_mcls_cd", "inds_mcls_nm",
            "inds_scls_cd", "inds_scls_nm", "sigungu_cd", "adong_cd", "adong_nm",
            "lon", "lat"
        ]

        try:
            missing_cols = [c for c in required_cols if c not in df.columns]
            if missing_cols:
                raise ValueError(f"원본 데이터 형식 변경 감지 - 필수 컬럼 누락: {missing_cols}")

            if len(df) == 0:
                raise ValueError("수집된 점포 데이터가 0건입니다.")

            actual_sgg = set(df["sigungu_cd"].astype(str).unique())
            valid_sgg = actual_sgg.intersection(set(expected_sigungu_codes))
            if not valid_sgg:
                raise ValueError(
                    f"대상 시군구 코드({expected_sigungu_codes})에 해당하는 데이터가 없습니다. (실제 코드: {list(actual_sgg)[:5]})"
                )

            df = df[df["sigungu_cd"].astype(str).isin(expected_sigungu_codes)].copy()
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df = df.dropna(subset=["store_id", "adong_cd", "lon", "lat"]).copy()

            if len(df) == 0:
                raise ValueError("유효한 좌표(경도·위도) 및 행정동 코드를 가진 점포가 0건입니다.")

            df["base_ym"] = str(base_ym)
            df["updated_at"] = now

            insert_cols = [
                "base_ym", "store_id", "store_name", "branch_name",
                "inds_lcls_cd", "inds_lcls_nm", "inds_mcls_cd", "inds_mcls_nm",
                "inds_scls_cd", "inds_scls_nm", "ksic_cd", "ksic_nm",
                "sido_cd", "sido_nm", "sigungu_cd", "sigungu_nm",
                "adong_cd", "adong_nm", "ldong_cd", "ldong_nm",
                "lno_adr", "rdnm_adr", "lon", "lat", "is_franchise", "updated_at"
            ]
            for col in insert_cols:
                if col not in df.columns:
                    df[col] = ""

            df_to_save = df[insert_cols].drop_duplicates(subset=["base_ym", "store_id"])

            with self.get_connection(read_only=False) as con:
                con.execute("BEGIN TRANSACTION")
                sgg_placeholders = ",".join([f"'{c}'" for c in expected_sigungu_codes])
                con.execute(
                    f"DELETE FROM stores_history WHERE base_ym = ? AND sigungu_cd IN ({sgg_placeholders})",
                    [str(base_ym)],
                )
                con.register("df_view", df_to_save)
                con.execute("INSERT INTO stores_history SELECT * FROM df_view")
                con.unregister("df_view")

                con.execute("""
                    INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    source_id, source_name, region_key, collect_method,
                    "분기 1회 (주 1회 확인)", str(base_ym), len(df_to_save),
                    now, now, "SUCCESS", ""
                ])
                con.execute("COMMIT")

            self._write_log("SUCCESS", source_id, f"기준시점={base_ym}, 적재건수={len(df_to_save):,}건")
            return len(df_to_save)

        except Exception as e:
            err_msg = str(e)
            with self.get_connection(read_only=False) as con:
                con.execute("""
                    INSERT INTO data_status (
                        source_id, source_name, region_scope, collect_method,
                        update_cycle, source_base_period, row_count,
                        last_checked_at, last_updated_at, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, 'FAIL', ?)
                    ON CONFLICT (source_id) DO UPDATE SET
                        last_checked_at = excluded.last_checked_at,
                        status = 'FAIL',
                        error_message = excluded.error_message
                """, [
                    source_id, source_name, region_key, collect_method,
                    "분기 1회 (주 1회 확인)", str(base_ym), now, err_msg
                ])
            self._write_log("FAIL", source_id, err_msg)
            raise

    def upsert_demographics(self, df: pd.DataFrame, base_ym: str, collect_method: str = "수동 파일 (CSV)") -> int:
        now = datetime.now()
        required = ["sigungu_cd", "adong_cd", "adong_nm", "population"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"인구 데이터 필수 컬럼 누락: {missing}")

        df = df.copy()
        df["base_ym"] = str(base_ym)
        if "households" not in df.columns:
            df["households"] = None
        if "area_km2" not in df.columns:
            df["area_km2"] = None
        df["updated_at"] = now

        cols = ["base_ym", "sigungu_cd", "adong_cd", "adong_nm", "population", "households", "area_km2", "updated_at"]
        df_to_save = df[cols].drop_duplicates(subset=["base_ym", "adong_cd"])

        with self.get_connection(read_only=False) as con:
            con.register("pop_view", df_to_save)
            con.execute("INSERT OR REPLACE INTO region_demographics SELECT * FROM pop_view")
            con.unregister("pop_view")

            con.execute("""
                INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                "demographics_all", "행정안전부 행정동별 주민등록 인구", "공통 (세 지역)", collect_method,
                "월 1회", str(base_ym), len(df_to_save), now, now, "SUCCESS", ""
            ])
        self._write_log("SUCCESS", "demographics_all", f"기준시점={base_ym}, 적재건수={len(df_to_save):,}건")
        return len(df_to_save)

    def upsert_manual_floating(self, record: dict):
        now = datetime.now()
        with self.get_connection(read_only=False) as con:
            con.execute("""
                INSERT OR REPLACE INTO manual_floating_input VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                record["adong_cd"], record["sigungu_cd"], record["adong_nm"],
                record["base_ym"], int(record["daily_avg_pop"]),
                record["peak_time_slot"], record["main_age_group"],
                float(record["male_ratio"]), float(record["female_ratio"]),
                float(record["weekday_ratio"]), record.get("note", ""), now
            ])
        self._write_log("SUCCESS", "manual_floating", f"{record['adong_nm']} ({record['base_ym']}) 수동 유동인구 저장")

    def _write_log(self, level: str, source_id: str, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{level}] [{source_id}] {message}\n"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
