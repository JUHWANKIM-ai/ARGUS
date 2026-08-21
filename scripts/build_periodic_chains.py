"""periodic 정정 체인. LLM 호출 없음. 원문도 안 읽는다 — 대장 하나로 나온다.

    python scripts/build_periodic_chains.py
    python scripts/build_periodic_chains.py --check     # 재생성 없이 대조만

exchange·major·holding 은 `corpus/corrections.build_chains` 가 원문의 `정정관련 제출일` 과
`※관련공시` 를 읽어 잇는다. periodic 은 그 경로를 안 탄다 — `scripts/build_index.py` 가
그 세 그룹만 돌고, `periodic_sections.jsonl` 은 `is_correction` 불리언만 갖는다. 그래서
정정본이 무엇을 정정한 것인지 런타임이 알 방법이 없었다.

periodic 은 원문을 열 필요가 없다. `report_nm` 이 서식과 기간을 글자로 갖고 있어
(기업 · 서식 · 기간) 이 키가 된다. 실측(2026-08-21): 정정 159건 중 157건이 **유일한**
원공시로 확정되고, 모호 0건, 못 이은 2건은 원공시가 코퍼스에 없는 경우다.

🔴 못 이은 것을 버리지 않고 `reason` 에 적는다. `corpus/references.py` 의 규율과 같다 —
파서 실패와 진짜 부재가 구별돼야 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from argus.config import settings                      # noqa: E402

OUT_NAME = "periodic_chains.jsonl"

#: 정정 태그를 지나 서식과 기간을 읽는다. 태그는 `[기재정정]` 외 형태도 있어(`[첨부추가]`)
#: 대괄호 내용을 특정하지 않는다.
REPORT_KEY = re.compile(
    r"^(?:\[[^\]]*\])?\s*(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")


def _key(row: dict) -> tuple | None:
    match = REPORT_KEY.match(row.get("report_nm") or "")
    return (row["corp_name"], *match.groups()) if match else None


def build_chains(manifest: list[dict]) -> list[dict]:
    """정정본마다 한 줄. periodic 이 아닌 것과 정정이 아닌 것은 안 낸다."""
    periodic = [r for r in manifest if r.get("doc_group") == "periodic"]
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in periodic:
        key = _key(row)
        if key:
            buckets[key].append(row)

    out: list[dict] = []
    for row in periodic:
        if not row.get("is_correction"):
            continue
        key = _key(row)
        if key is None:
            out.append({"rcept_no": row["rcept_no"], "root": None, "prev": None,
                        "corp_name": row["corp_name"], "report": "", "period": "",
                        "reason": "report_nm 에서 서식·기간을 못 읽었다"})
            continue
        bucket = sorted(buckets[key], key=lambda r: r["rcept_dt"])
        originals = [r for r in bucket if not r.get("is_correction")]
        earlier = [r for r in bucket
                   if r["rcept_no"] != row["rcept_no"] and r["rcept_dt"] <= row["rcept_dt"]]
        reason = ""
        if len(originals) > 1:
            # 실측 0건. 일어나면 잇지 않는다 — 여럿 중 하나를 고르면 무관한 보고서를
            # 대체된 것으로 표시하게 된다. `corpus/corrections` 의 같은 판단이다.
            reason = f"원공시 후보가 {len(originals)}건이라 확정 못 한다"
        elif not originals:
            reason = "원공시가 코퍼스에 없다"
        out.append({
            "rcept_no": row["rcept_no"],
            "root": originals[0]["rcept_no"] if len(originals) == 1 else None,
            "prev": earlier[-1]["rcept_no"] if earlier else None,
            "corp_name": row["corp_name"],
            "report": key[1],
            "period": f"{key[2]}.{key[3]}",
            "reason": reason})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="재생성 없이 기존 파일과 대조만 한다")
    args = parser.parse_args()

    path = settings().corpus_root / "manifest.jsonl"
    with path.open(encoding="utf-8") as fh:
        manifest = [json.loads(line) for line in fh if line.strip()]
    rows = build_chains(manifest)
    linked = sum(1 for r in rows if r["root"])
    print(f"periodic 정정 {len(rows)}건 · 원공시 확정 {linked}건 "
          f"({linked / max(1, len(rows)):.1%}) · 못 이음 {len(rows) - linked}건")
    for row in rows:
        if row["reason"]:
            print(f"  [못 이음] {row['rcept_no']} {row['corp_name']} · {row['reason']}")

    out = settings().artifacts_root / OUT_NAME
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if args.check:
        same = out.exists() and out.read_text(encoding="utf-8") == body
        print("일치" if same else "🔴 불일치 — 재생성이 필요하다")
        raise SystemExit(0 if same else 1)
    out.write_text(body, encoding="utf-8")
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
