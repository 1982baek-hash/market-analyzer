"""자동 수집 진입점: python -m pipeline.run

소스마다 따로 시도하고, 실패한 소스는 이전 데이터를 그대로 둡니다.
하나라도 실패하면 종료 코드 1로 끝나 GitHub가 실패 알림 메일을 보냅니다.
"""

import os
import sys

from pipeline import stores
from pipeline.common import get_secret, load_regions, load_status, now_kst, save_status


def main() -> int:
    now = now_kst()
    status = load_status()
    failures = []

    api_key = get_secret("DATA_GO_KR_API_KEY")
    for region_key, region in load_regions().items():
        source_id = f"stores_{region_key}"
        prev = status.get(source_id, {})
        print(f"[수집] {region['name']} 상가정보 ...", flush=True)
        try:
            if not api_key:
                raise stores.SourceError("GitHub Secrets에 DATA_GO_KR_API_KEY가 없습니다.")
            status[source_id] = stores.collect_region(region_key, region, api_key, prev, now)
            print(f"  → {status[source_id]['message']}", flush=True)
        except Exception as e:  # 한 지역이 실패해도 나머지는 계속
            msg = str(e) or e.__class__.__name__
            status[source_id] = {
                **prev,
                "name": prev.get("name", f"소상공인 상가(상권)정보 ({region['name']})"),
                "region": region_key,
                "last_checked": now.isoformat(),
                "status": "FAIL",
                "message": msg,
            }
            failures.append(f"{source_id}: {msg}")
            print(f"  → 실패: {msg}", flush=True)

    status["_run"] = {"finished_at": now.isoformat(), "failures": len(failures)}
    save_status(status)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 상권 데이터 자동 수집 ({now:%Y-%m-%d %H:%M} KST)\n\n| 소스 | 결과 | 내용 |\n|---|---|---|\n")
            for sid, s in sorted(status.items()):
                if not sid.startswith("_"):
                    f.write(f"| {s.get('name', sid)} | {s.get('status')} | {s.get('message', '')} |\n")

    if failures:
        print("\n실패한 소스:\n- " + "\n- ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
