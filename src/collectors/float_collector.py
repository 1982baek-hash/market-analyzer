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
    통계청 KOBIGS(안동시·예천군 생활인구/방문인구),
    한국관광 데이터랩(안동시·예천군 방문자 통계) 파일을 읽어
    실제 컬럼명을 검증한 뒤 DuckDB에 적재합니다.
    """

    def __init__(self):
        self.db = DatabaseManager()
        self.normalizer = AdminCodeNormalizer()
        self.regions = load_regions_config()

    def _read_excel_smart(self, file_path: Path) -> pd.DataFrame:
        """정부 기관 엑셀 상단의 제목 행·빈 행을 건너뛰고 실제 컬럼 헤더 행을 자동으로 찾습니다."""
        raw = pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
        raw = raw.dropna(how="all").reset_index(drop=True)
        if raw.empty:
            raise ValueError("엑셀 첫 번째 시트가 비어 있습니다.")

        header_keywords = [
            "시도", "시군구", "읍면동", "행정동", "구분",
            "방문", "체류", "생활", "인구", "기준", "연월", "성별", "연령"
        ]
        header_idx = None
        for idx in range(min(15, len(raw))):
            row_vals = [str(v).strip() for v in raw.iloc[idx].tolist() if pd.notna(v) and str(v).strip()]
            if len(row_vals) >= 2 and any(any(kw in cell for kw in header_keywords) for cell in row_vals):
                header_idx = idx
                break

        if header_idx is None:
            for idx in range(min(15, len(raw))):
                row_vals = [str(v).strip() for v in raw.iloc[idx].tolist() if pd.notna(v) and str(v).strip()]
                if len(row_vals) >= 3:
                    header_idx = idx
                    break

        if header_idx is None:
            header_idx = 0

        new_cols = []
        for i, v in enumerate(raw.iloc[header_idx].tolist()):
            col_str = re.sub(r"\s+", " ", str(v)).strip() if pd.notna(v) else f"Unnamed_{i}"
            new_cols.append(col_str if col_str else f"Unnamed_{i}")

        df = raw.iloc[header_idx + 1 :].copy()
        df.columns = new_cols
        df = df.dropna(how="all").reset_index(drop=True)
        return df

    def _read_table(self, file_path: Path) -> pd.DataFrame:
        suffix = file_path.suffix.lower()
        if suffix in [".xlsx", ".xls"]:
            return self._read_excel_smart(file_path)
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
        raw_df = self._read_table(file_path)
        cols = list(raw_df.columns)

        # 시군구 필터링 (전국 파일인 경우 안동시 / 예천군 행만 추출)
        sgg_col = self._find_col(cols, ["시군구명", "시군구", "지역명", "지자체"])
        target_kw = "안동" if region_key == "andong" else ("예천" if region_key == "yecheon" else "")
        df = raw_df.copy()
        if sgg_col and target_kw:
            filtered = df[df[sgg_col].astype(str).str.contains(target_kw, na=False)].copy()
            if not filtered.empty:
                df = filtered

        val_col = self._find_col(cols, ["방문자수", "방문인구", "방문자비율", "생활인구", "체류인구", "인구수", "합계", "계", "비율", "수치", "값"])
        cat1_col = self._find_col(cols, ["읍면동명", "읍면동", "행정동명", "행정동", "구분", "방문자구분", "연령대", "시간대", "유입지", "시군구", "지표"])
        cat2_col = self._find_col(cols, ["성별", "세부구분", "유형", "요일"])
        period_col = self._find_col(cols, ["기준연월", "기준년월", "기준분기", "연월", "년월", "기간"])

        # 가로형 월별 컬럼(예: 2026.04, 2026년 4월 등) 자동 감지
        month_cols = [c for c in cols if re.search(r"20\d{2}", str(c)) and c not in [period_col, cat1_col, cat2_col]]

        if not val_col and month_cols and cat1_col:
            id_vars = [c for c in [cat1_col, cat2_col] if c]
            melted = df.melt(id_vars=id_vars, value_vars=month_cols, var_name="_period", value_name="_val")
            melted["base_period"] = melted["_period"].astype(str)
            melted["region_key"] = region_key
            melted["data_source"] = data_source
            melted["metric_type"] = "방문/생활인구"
            melted["category_1"] = melted[cat1_col].astype(str)
            melted["category_2"] = melted[cat2_col].astype(str) if cat2_col else "전체"
            melted["value"] = pd.to_numeric(melted["_val"].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce").fillna(0.0)
            melted["updated_at"] = datetime.now()
            save_df = melted[["base_period", "region_key", "data_source", "metric_type", "category_1", "category_2", "value", "updated_at"]]
        elif val_col and cat1_col:
            df["base_period"] = df[period_col].astype(str) if period_col else datetime.now().strftime("%Y%m")
            df["region_key"] = region_key
            df["data_source"] = data_source
            df["metric_type"] = val_col
            df["category_1"] = df[cat1_col].astype(str)
            df["category_2"] = df[cat2_col].astype(str) if cat2_col else "전체"
            df["value"] = pd.to_numeric(df[val_col].astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce").fillna(0.0)
            df["updated_at"] = datetime.now()
            save_df = df[["base_period", "region_key", "data_source", "metric_type", "category_1", "category_2", "value", "updated_at"]]
        else:
            raise ValueError(
                f"시군구 단위 통계 파일의 필수 컬럼(구분 항목, 수치/비율)을 찾지 못했습니다.\n"
                f"감지된 헤더 컬럼 목록: {cols}\n"
                f"상위 2행 샘플: {df.head(2).to_dict(orient='records')}"
            )

        with self.db.get_connection(read_only=False) as con:
            con.execute("DELETE FROM floating_sigungu WHERE region_key = ? AND data_source = ?", [region_key, data_source])
            con.register("sg_view", save_df)
            con.execute("INSERT INTO floating_sigungu SELECT * FROM sg_view")
            con.unregister("sg_view")

            now = datetime.now()
            source_id = "floating_andong" if region_key == "andong" else "tourism_andong_yecheon"
            source_nm = "통계청 인구감소지역 생활인구" if region_key == "andong" else "한국관광 데이터랩 방문자 통계"
            con.execute("""
                INSERT OR REPLACE INTO data_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                source_id, source_nm, region_key, f"수동 파일 ({file_path.name})",
                "분기/월 1회", str(save_df["base_period"].iloc[0]) if not save_df.empty else "-",
                len(save_df), now, now, "SUCCESS", ""
            ])
        return len(save_df)
