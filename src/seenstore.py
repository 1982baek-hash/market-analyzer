"""이 기기(브라우저)에서 이미 확인한 알림을 기억하는 작은 저장소 (Streamlit 양방향 컴포넌트, 화면에는 안 보임).

- 브라우저의 localStorage에 '확인한 가게 관리번호'를 적어 둡니다. 서버에는 아무것도 저장하지 않습니다.
- 그래서 휴대폰·PC마다 따로 기억하고, 같은 기기에서는 앱을 껐다 켜도 한 번 확인한 알림이 다시 뜨지 않습니다.
- sync()는 브라우저가 대답하기 전(첫 실행)에는 None을 돌려줍니다.
"""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_comp = components.declare_component("seenstore", path=str(Path(__file__).parent / "seenstore_frontend"))

STORAGE_KEY = "market_analyzer_seen_alerts_v1"


def sync(cmd: dict | None = None, key: str = "seenstore") -> dict | None:
    """확인한 알림 목록을 읽습니다. cmd가 있으면 먼저 반영합니다.

    cmd: {"nonce": 고유 숫자, "ack": [확인 처리할 관리번호…]} 또는 {"nonce": …, "reset": True}
    돌려주는 값: {"seen": [관리번호…], "applied": 마지막으로 반영한 nonce, "storage": 저장 가능 여부}
    """
    cmd = cmd or {}
    return _comp(storage_key=STORAGE_KEY, nonce=cmd.get("nonce"), ack=list(cmd.get("ack") or []),
                 reset=bool(cmd.get("reset")), key=key, default=None)
