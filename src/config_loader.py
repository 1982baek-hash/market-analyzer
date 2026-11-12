import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
REGIONS_YAML_PATH = BASE_DIR / "config" / "regions.yaml"
CODE_MAPPING_PATH = BASE_DIR / "config" / "code_mapping.csv"
DB_PATH = BASE_DIR / "data" / "database" / "market.duckdb"
LOG_PATH = BASE_DIR / "data" / "logs" / "update_history.log"

load_dotenv(dotenv_path=ENV_PATH)


def get_api_key(key_name: str = "DATA_GO_KR_API_KEY") -> str:
    """로컬 .env 또는 Streamlit Cloud Secrets에서 설정값을 읽어 반환합니다."""
    val = os.getenv(key_name, "").strip()
    if not val:
        try:
            import streamlit as st
            if key_name in st.secrets:
                val = str(st.secrets[key_name]).strip()
        except Exception:
            pass
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1].strip()
    return val


def load_regions_config() -> dict:
    if not REGIONS_YAML_PATH.exists():
        raise FileNotFoundError(f"지역 설정 파일을 찾을 수 없습니다: {REGIONS_YAML_PATH}")
    with open(REGIONS_YAML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("regions", {})
