import pandas as pd
from src.config_loader import CODE_MAPPING_PATH


class AdminCodeNormalizer:
    """법정동/구 행정동/통계청 코드를 현행 행안부 10자리 행정동 코드로 통합하는 클래스"""

    def __init__(self):
        self.mapping_df = self._load_mapping()

    def _load_mapping(self) -> pd.DataFrame:
        if CODE_MAPPING_PATH.exists():
            return pd.read_csv(CODE_MAPPING_PATH, dtype=str)
        return pd.DataFrame(
            columns=["source_type", "source_code", "source_name", "std_adong_cd", "std_adong_nm", "sigungu_cd", "note"]
        )

    def normalize_adong_code(self, code_series: pd.Series, name_series: pd.Series = None) -> pd.Series:
        """
        입력된 행정동 코드 시리즈를 10자리 문자열로 맞추고,
        구 행정동 코드나 8자리 코드가 있으면 표준 10자리 코드로 치환합니다.
        """
        cleaned = (
            code_series.astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )

        # 8자리로 들어온 행안부 행정동 코드(뒤 '00' 생략형)는 뒤에 '00'을 붙여 10자리로 통일
        cleaned = cleaned.apply(
            lambda x: x + "00" if (len(x) == 8 and x[:2] in ["11", "26", "27", "28", "29", "30", "31", "36", "41", "42", "43", "44", "45", "46", "47", "48", "50", "51", "52"]) else x
        )

        # code_mapping.csv에 등록된 개편/승격 코드 치환
        if not self.mapping_df.empty:
            code_map = dict(zip(self.mapping_df["source_code"], self.mapping_df["std_adong_cd"]))
            cleaned = cleaned.replace(code_map)

        return cleaned
