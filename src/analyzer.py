import pandas as pd
from src.config_loader import load_regions_config
from src.db_manager import DatabaseManager


class MarketAnalyzer:
    """DuckDB에 적재된 점포·인구·경계 데이터를 조회하여 상권 지표를 계산하는 분석 클래스"""

    def __init__(self):
        self.db = DatabaseManager()
        self.regions = load_regions_config()

    def get_available_regions(self) -> dict:
        return self.regions

    def get_latest_base_ym(self, sigungu_codes: list) -> str:
        sgg_sql = ",".join([f"'{c}'" for c in sigungu_codes])
        with self.db.get_connection(read_only=True) as con:
            res = con.execute(f"""
                SELECT MAX(base_ym) AS max_ym
                FROM stores_history
                WHERE sigungu_cd IN ({sgg_sql})
            """).fetchone()
        return res[0] if res and res[0] else ""

    def get_adong_list(self, sigungu_codes: list, base_ym: str) -> pd.DataFrame:
        sgg_sql = ",".join([f"'{c}'" for c in sigungu_codes])
        with self.db.get_connection(read_only=True) as con:
            df = con.execute(f"""
                SELECT DISTINCT sigungu_nm, adong_cd, adong_nm
                FROM stores_history
                WHERE sigungu_cd IN ({sgg_sql}) AND base_ym = ?
                ORDER BY sigungu_nm, adong_nm
            """, [base_ym]).df()
        return df

    def get_category_tree(self, sigungu_codes: list, base_ym: str) -> pd.DataFrame:
        """대분류 > 중분류 > 소분류 3단계 전체 업종 목록을 반환합니다."""
        sgg_sql = ",".join([f"'{c}'" for c in sigungu_codes])
        with self.db.get_connection(read_only=True) as con:
            df = con.execute(f"""
                SELECT inds_lcls_cd, inds_lcls_nm,
                       inds_mcls_cd, inds_mcls_nm,
                       inds_scls_cd, inds_scls_nm,
                       COUNT(*) AS store_cnt
                FROM stores_history
                WHERE sigungu_cd IN ({sgg_sql}) AND base_ym = ?
                GROUP BY 1, 2, 3, 4, 5, 6
                ORDER BY inds_lcls_nm, inds_mcls_nm, inds_scls_nm
            """, [base_ym]).df()
        return df

    def get_filtered_stores(
        self,
        sigungu_codes: list,
        base_ym: str,
        adong_cd: str = "ALL",
        lcls_cd: str = "ALL",
        mcls_cd: str = "ALL",
        scls_cd: str = "ALL",
    ) -> pd.DataFrame:
        """선택된 지역(전체 또는 특정 행정동)과 3단계 업종 조건에 맞는 개별 점포 목록을 반환합니다."""
        sgg_sql = ",".join([f"'{c}'" for c in sigungu_codes])
        conditions = [f"sigungu_cd IN ({sgg_sql})", "base_ym = ?"]
        params = [base_ym]

        if adong_cd and adong_cd != "ALL":
            conditions.append("adong_cd = ?")
            params.append(adong_cd)
        if lcls_cd and lcls_cd != "ALL":
            conditions.append("inds_lcls_cd = ?")
            params.append(lcls_cd)
        if mcls_cd and mcls_cd != "ALL":
            conditions.append("inds_mcls_cd = ?")
            params.append(mcls_cd)
        if scls_cd and scls_cd != "ALL":
            conditions.append("inds_scls_cd = ?")
            params.append(scls_cd)

        where_clause = " AND ".join(conditions)
        with self.db.get_connection(read_only=True) as con:
            df = con.execute(f"""
                SELECT store_id, store_name, branch_name,
                       inds_lcls_nm, inds_mcls_nm, inds_scls_nm,
                       sigungu_nm, adong_cd, adong_nm,
                       rdnm_adr, lno_adr, lon, lat, is_franchise
                FROM stores_history
                WHERE {where_clause}
            """, params).df()
        return df

    def get_adong_summary_and_density(
        self,
        sigungu_codes: list,
        base_ym: str,
        lcls_cd: str = "ALL",
        mcls_cd: str = "ALL",
        scls_cd: str = "ALL",
    ) -> pd.DataFrame:
        """시군구 내 모든 행정동별로 선택 업종 점포 수, 프랜차이즈 비율, 인구·면적당 밀집도를 계산해 반환합니다."""
        sgg_sql = ",".join([f"'{c}'" for c in sigungu_codes])
        cat_conditions = []
        params = [base_ym]

        if lcls_cd and lcls_cd != "ALL":
            cat_conditions.append("s.inds_lcls_cd = ?")
            params.append(lcls_cd)
        if mcls_cd and mcls_cd != "ALL":
            cat_conditions.append("s.inds_mcls_cd = ?")
            params.append(mcls_cd)
        if scls_cd and scls_cd != "ALL":
            cat_conditions.append("s.inds_scls_cd = ?")
            params.append(scls_cd)

        cat_filter_sql = (" AND " + " AND ".join(cat_conditions)) if cat_conditions else ""

        with self.db.get_connection(read_only=True) as con:
            query = f"""
                WITH all_dongs AS (
                    SELECT sigungu_cd, sigungu_nm, adong_cd, adong_nm,
                           AVG(lon) AS center_lon, AVG(lat) AS center_lat,
                           COUNT(*) AS total_all_stores
                    FROM stores_history
                    WHERE sigungu_cd IN ({sgg_sql}) AND base_ym = ?
                    GROUP BY 1, 2, 3, 4
                ),
                selected_stores AS (
                    SELECT adong_cd,
                           COUNT(*) AS store_count,
                           SUM(CASE WHEN is_franchise THEN 1 ELSE 0 END) AS franchise_count
                    FROM stores_history s
                    WHERE s.sigungu_cd IN ({sgg_sql}) AND s.base_ym = ? {cat_filter_sql}
                    GROUP BY adong_cd
                ),
                latest_pop AS (
                    SELECT adong_cd, population, area_km2
                    FROM region_demographics
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY adong_cd ORDER BY base_ym DESC) = 1
                ),
                bound_info AS (
                    SELECT adong_cd, area_km2 AS bound_area_km2, geojson_str, source_type
                    FROM region_boundaries
                )
                SELECT
                    d.sigungu_nm,
                    d.adong_cd,
                    d.adong_nm,
                    COALESCE(s.store_count, 0) AS store_count,
                    COALESCE(s.franchise_count, 0) AS franchise_count,
                    CASE WHEN COALESCE(s.store_count, 0) > 0
                         THEN ROUND(COALESCE(s.franchise_count, 0) * 100.0 / s.store_count, 1)
                         ELSE 0.0 END AS franchise_ratio,
                    d.total_all_stores,
                    d.center_lon,
                    d.center_lat,
                    p.population,
                    COALESCE(b.bound_area_km2, p.area_km2) AS area_km2,
                    CASE WHEN p.population IS NOT NULL AND p.population > 0
                         THEN ROUND(COALESCE(s.store_count, 0) * 1000.0 / p.population, 2)
                         ELSE NULL END AS stores_per_1k_pop,
                    CASE WHEN COALESCE(b.bound_area_km2, p.area_km2) IS NOT NULL AND COALESCE(b.bound_area_km2, p.area_km2) > 0
                         THEN ROUND(COALESCE(s.store_count, 0) / COALESCE(b.bound_area_km2, p.area_km2), 2)
                         ELSE NULL END AS stores_per_km2,
                    b.geojson_str,
                    COALESCE(b.source_type, 'NONE') AS boundary_source
                FROM all_dongs d
                LEFT JOIN selected_stores s ON d.adong_cd = s.adong_cd
                LEFT JOIN latest_pop p ON d.adong_cd = p.adong_cd
                LEFT JOIN bound_info b ON d.adong_cd = b.adong_cd
                ORDER BY store_count DESC, d.adong_nm ASC
            """
            full_params = [base_ym] + params
            df = con.execute(query, full_params).df()
        return df