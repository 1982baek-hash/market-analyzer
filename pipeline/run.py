"""자동 수집 진입점: python -m pipeline.run

소스마다 따로 시도하고, 실패한 소스는 이전 데이터를 그대로 둡니다.
하나라도 실패하면 종료 코드 1로 끝나 GitHub가 실패 알림 메일을 보냅니다.
"""

import os
import sys
import traceback

from pipeline import busstops, ggfloat, licenses, population, schools, sgis, stores
from pipeline.common import SourceError, get_secret, load_regions, load_status, now_kst, save_status


def main() -> int:
    now = now_kst()
    status = load_status()
    failures = []
    regions = load_regions()

    data_key = get_secret("DATA_GO_KR_API_KEY")
    sgis_key, sgis_secret = get_secret("SGIS_CONSUMER_KEY"), get_secret("SGIS_CONSUMER_SECRET")
    gg_key = get_secret("GG_API_KEY")

    def need_data_key():
        if not data_key:
            raise SourceError("GitHub Secrets에 DATA_GO_KR_API_KEY가 없습니다.")
        return data_key

    jobs = []
    # 순서가 중요: 인허가는 상가정보 스냅샷으로 행정동을 붙이므로 상가정보 다음에 돈다
    for rk, r in regions.items():
        jobs.append((f"stores_{rk}", f"상가정보 {r['name']}", r,
                     lambda rk=rk, r=r, p=None: stores.collect_region(rk, r, need_data_key(), p, now)))
    for rk, r in regions.items():
        jobs.append((f"population_{rk}", f"주민등록 인구 {r['name']}", r,
                     lambda rk=rk, r=r, p=None: population.collect_region(rk, r, need_data_key(), p, now)))
    for rk, r in regions.items():
        jobs.append((f"sgis_{rk}", f"SGIS 사업체·가구 {r['name']}", r,
                     lambda rk=rk, r=r, p=None: sgis.collect_region(rk, r, sgis_key, sgis_secret, p, now)))
    # 학교·버스정류장도 상가정보 스냅샷으로 행정동을 붙이므로 상가정보 다음에 돈다
    for rk, r in regions.items():
        jobs.append((f"schools_{rk}", f"학교 위치 {r['name']}", r,
                     lambda rk=rk, r=r, p=None: schools.collect_region(rk, r, need_data_key(), p, now)))
    for rk, r in regions.items():
        jobs.append((f"busstops_{rk}", f"버스정류장 {r['name']}", r,
                     lambda rk=rk, r=r, p=None: busstops.collect_region(rk, r, need_data_key(), p, now)))
    for rk, r in regions.items():
        if rk in ggfloat.SIGUNGU_PREFIX:
            jobs.append((f"floating_{rk}", f"경기도 유동인구 {r['name']}", r,
                         lambda rk=rk, r=r, p=None: ggfloat.collect_region(rk, r, gg_key, p, now)))
    for svc in licenses.SERVICES:
        for rk, r in regions.items():
            jobs.append((f"license_{svc}_{rk}", f"인허가 {licenses.SERVICES[svc]} {r['name']}", r,
                         lambda svc=svc, rk=rk, r=r, p=None: licenses.collect(svc, rk, r, need_data_key(), p, now)))

    for source_id, label, region, fn in jobs:
        prev = status.get(source_id, {})
        print(f"[수집] {label} ...", flush=True)
        try:
            status[source_id] = fn(p=prev)
            print(f"  → {status[source_id].get('message', '')}", flush=True)
        except Exception as e:  # 한 소스가 실패해도 나머지는 계속
            msg = str(e) or e.__class__.__name__
            if not isinstance(e, SourceError):
                traceback.print_exc()
                msg = f"{e.__class__.__name__}: {msg}"
            status[source_id] = {
                **prev,
                "name": prev.get("name", label),
                "region": prev.get("region", source_id.rsplit("_", 1)[-1]),
                "last_checked": now.isoformat(),
                "status": "FAIL",
                "message": msg[:500],
            }
            failures.append(f"{source_id}: {msg[:300]}")
            print(f"  → 실패: {msg[:500]}", flush=True)
        save_status(status)  # 중간에 작업이 끊겨도 그때까지의 결과는 남긴다

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
