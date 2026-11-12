import re
from datetime import datetime
from pathlib import Path
import pandas as pd

from src.config_loader import load_regions_config
from src.code_normalizer import AdminCodeNormalizer
from src.db_manager import DatabaseManager


class FloatingPopulationCollector:
    """
    경기데이터드림(성남시 행정동 유동인구·유출입),
    통계청 KOBIGS(안동시 생활인구),
    한국관광 데이터랩(안동시·예천군 방문자 통계) 파일을 읽어
    실제 컬럼명을 검증한 뒤 DuckDB에 적재합니다.
    (컬럼명이 일치하지 않으면 조용히 넘어가지 않고 실제 파일의 컬럼 목록을 오류로 알립니다.)
    """

    def __init__(self):
        self.db = DatabaseManager()
        self.normalizer = AdminCodeNormalizer()
        self.regions = load_regions_config()

    def _read_table(self, file_path: Path) -> pd.DataFrame:
        suffix = file_path.suffix.lower()
        if suffix in [".xlsx", ".xls"]:
            return pd.read_excel(file_path, dtype=str)
        try:
            return pd.read_csv(file_path, encoding="utf-8", low_memory=False, dtype=str)
        except UnicodeDecodeError:
            return pd.read_csv(file_path, encoding="cp949", low_memory=False, dtype=str)

    def _find_col(self, cols: list, candidates: list):
        cols_lower = {c.lower().strip(): c for c in cols}
        for cand in candidates:
            if cand.lower() in cols_lower:
                return cols_lower[cand.lower()]
        for c in cols:
            for cand in candidates:
                if cand.lower() in c.lower():
                    return c
        return None

    def load_seongnam_hourly_csv(self, file_path: Path, base_ym: str = None) -> int:
        """경기데이터드림 '유동인구_행정동 단위 집계' CSV를 읽어 성남시 데이터를 적재합니다."""
        raw_df = self._read_table(file_path)
        cols = list(raw_df.columns)

        adong_col = self._find_col(cols, ["행정동코드", "ADONG_CD", "ADMI_CD", "EMD_CD", "읍면동코드"])
        hour_col = self._find_col(cols, ["시간대", "시간코드", "TMZON", "TIME_CD", "HOUR"])
        pop_col = self._find_col(cols, ["유동인구수", "유동인구", "POP_CNT", "FLOW_POP", "인구수"])

        if not adong_col or not pop_col:
            raise ValueError(
                f"성남시 행정동 유동인구 파일의 필수 컬럼(행정동코드, 유동인구수)을 찾지 못했습니다.\n"
                f"파일 안의 실제 컬럼 목록: {cols}"
            )

        ym_col = self._find_col(cols, ["기준년월", "기준연월", "STD_YM", "BASE_YM", "연도_월_일", "기준일자"])
        day_col = self._find_col(cols, ["주중주말", "요일구분", "평일주말", "DAY_TYPE"])
        gender_col = self._find_col(cols, ["성별", "성연령코드", "GENDER", "SEX"])
        age_col = self._find_col(cols, ["연령대", "연령", "AGE_GRP", "AGE"])

        if not base_ym:
            m = re.search(r"(20\d{4})", file_path.name)
            base_ym = m.group(1) if m else datetime.now().strftime("%Y%m")

        df = raw_df.copy()
        df["adong_cd"] = self.normalizer.normalize_adong_code(df[adong_col])
        df["sigungu_cd"] = df["adong_cd"].str[:5]
        sgg_seongnam = self.regions["seongnam"]["sigungu_codes"]
        df = df[df["sigungu_cd"].isin(sgg_seongnam)].copy()

        if df.empty:
            raise ValueError("해당 파일에서 성남시(41131, 41133, 41135) 행정동 코드를 찾지 못했습니다.")

        df["base_ym"] = df[ym_col].astype(str).str[:6] if ym_col else base_ym
        df["hour"] = pd.to_numeric(df[hour_col], errors="coerce").fillna(-1).astype(int) if hour_col else -1
        df["day_type"] = df[day_col].fillna("전체") if day_col else "전체"
        df["gender"] = df[gender_col].fillna("전체") if gender_col else "전체"
        df["age_group"] = df[age_col].fillna("전체") if age_col else "전체"
        df["pop_count"] = pd.to_numeric(df[pop_col], errors="coerce").fillna(0.0)
        df["updated_at"] = datetime.now()

        save_df = df[["base_ym", "sigungu_cd", "adong_cd", "day_type", "hour", "gender", "age_group", "pop_count", "updated_at"]]

        with self.db.get_connection(read_only=False) as con:
            con.execute("DELETE FROM floating_hourly WHERE base_ym = ?", [base_ym])
            con.register("fh_view", save_df)
            con.execute("INSERT INTO floating_hourly SELECT * FROM fh_view")
            con.unregister("fh_view")

            now = datetime.now()
            con.execute("""
                INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                "floating_seongnam", "경기데이터드림 행정동 유동인구·유출입", "성남시",
                f"수동 파일 ({file_path.name})", "월 1회", base_ym, len(save_df),
                now, now, "SUCCESS", ""
            ])
        return len(save_df)

    def load_seongnam_od_csv(self, file_path: Path, base_ym: str = None) -> int:
        """경기데이터드림 '행정동간 유출인구' O-D 데이터를 읽어 성남시 도착 유입지 데이터를 적재합니다."""
        raw_df = self._read_table(file_path)
        cols = list(raw_df.columns)

        orig_col = self._find_col(cols, ["출발행정동코드", "출발지행정동코드", "O_ADONG_CD", "ORIG_CD"])
        dest_col = self._find_col(cols, ["도착행정동코드", "도착지행정동코드", "D_ADONG_CD", "DEST_CD"])
        pop_col = self._find_col(cols, ["유출인구수", "이동인구수", "인구수", "POP_CNT"])
        orig_nm_col = self._find_col(cols, ["출발행정동명", "출발지명", "O_ADONG_NM"])
        mode_col = self._find_col(cols, ["이동목적", "이동수단", "목적", "수단"])

        if not orig_col or not dest_col or not pop_col:
            raise ValueError(
                f"행정동간 유출입(O-D) 파일의 필수 컬럼(출발행정동코드, 도착행정동코드, 인구수)을 찾지 못했습니다.\n"
                f"파일 안의 실제 컬럼 목록: {cols}"
            )

        if not base_ym:
            m = re.search(r"(20\d{4})", file_path.name)
            base_ym = m.group(1) if m else datetime.now().strftime("%Y%m")

        df = raw_df.copy()
        df["origin_adong_cd"] = self.normalizer.normalize_adong_code(df[orig_col])
        df["dest_adong_cd"] = self.normalizer.normalize_adong_code(df[dest_col])
        df["dest_sigungu_cd"] = df["dest_adong_cd"].str[:5]

        sgg_seongnam = self.regions["seongnam"]["sigungu_codes"]
        df = df[df["dest_sigungu_cd"].isin(sgg_seongnam)].copy()
        if df.empty:
            raise ValueError("도착지 기준 성남시(41131, 41133, 41135) 행정동 데이터가 없습니다.")

        df["base_ym"] = base_ym
        df["origin_name"] = df[orig_nm_col].fillna(df["origin_adong_cd"]) if orig_nm_col else df["origin_adong_cd"]
        df["purpose_or_mode"] = df[mode_col].fillna("전체") if mode_col else "전체"
        df["pop_count"] = pd.to_numeric(df[pop_col], errors="coerce").fillna(0.0)
        df["updated_at"] = datetime.now()

        save_df = df[["base_ym", "origin_adong_cd", "origin_name", "dest_adong_cd", "dest_sigungu_cd", "purpose_or_mode", "pop_count", "updated_at"]]
        with self.db.get_connection(read_only=False) as con:
            con.execute("DELETE FROM floating_od WHERE base_ym = ?", [base_ym])
            con.register("od_view", save_df)
            con.execute("INSERT INTO floating_od SELECT * FROM od_view")
            con.unregister("od_view")
        return len(save_df)

    def load_sigungu_stats_file(self, file_path: Path, region_key: str, data_source: str) -> int:
        """안동시 생활인구 또는 안동·예천 한국관광 데이터랩 시군 단위 통계 파일을 적재합니다."""
        raw_df = self._read_table(file_path)
        cols = list(raw_df.columns)

        val_col = self._find_col(cols, ["방문자수", "방문자비율", "생활인구", "체류인구", "인구수", "비율", "수치", "값"])
        cat1_col = self._find_col(cols, ["구분", "방문자구분", "연령대", "시간대", "유입지", "시군구", "지표"])
        cat2_col = self._find_col(cols, ["성별", "세부구분", "유형", "요일"])
        period_col = self._find_col(cols, ["기준연월", "기준년월", "기준분기", "연월", "년월", "기간"])

        if not val_col or not cat1_col:
            raise ValueError(
                f"시군구 단위 통계 파일의 필수 컬럼(구분 항목, 수치/비율)을 찾지 못했습니다.\n"
                f"파일 안의 실제 컬럼 목록: {cols}"
            )

        df = raw_df.copy()
        df["base_period"] = df[period_col].astype(str) if period_col else datetime.now().strftime("%Y%m")
        df["region_key"] = region_key
        df["data_source"] = data_source
        df["metric_type"] = val_col
        df["category_1"] = df[cat1_col].astype(str)
        df["category_2"] = df[cat2_col].astype(str) if cat2_col else "전체"
        df["value"] = pd.to_numeric(df[val_col].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce").fillna(0.0)
        df["updated_at"] = datetime.now()

        save_df = df[["base_period", "region_key", "data_source", "metric_type", "category_1", "category_2", "value", "updated_at"]]
        with self.db.get_connection(read_only=False) as con:
            con.execute("DELETE FROM floating_sigungu WHERE region_key = ? AND data_source = ?", [region_key, data_source])
            con.register("sg_view", save_df)
            con.execute("INSERT INTO floating_sigungu SELECT * FROM sg_view")
            con.unregister("sg_view")
        return len(save_df)
