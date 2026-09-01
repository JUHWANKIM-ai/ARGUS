"""Correction chains for periodic filings. No LLM call, and no source
document is read -- the manifest alone yields them.

    python scripts/build_periodic_chains.py
    python scripts/build_periodic_chains.py --check     # compare without rebuilding

For exchange, major and holding, `corpus/corrections.build_chains` links filings
by reading `정정관련 제출일` and `※관련공시` from the source. Periodic never takes
that path: `scripts/build_index.py` covers only those three groups, and
`periodic_sections.jsonl` carries nothing but an `is_correction` boolean. So the
runtime had no way to know what a correction corrected.

Periodic needs no source document. `report_nm` carries the form and the period as
text, which makes (company, form, period) the key. Measured 2026-08-21: 157 of 159
corrections resolve to a **unique** original filing, none are ambiguous, and the
two that fail are cases where the original is absent from the corpus.

What cannot be linked is recorded in `reason` rather than dropped, the same
discipline as `corpus/references.py`: a parser failure has to stay
distinguishable from a genuine absence.
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

#: Reads the form and period past the correction tag. Tags other than
#: `[기재정정]` occur (`[첨부추가]`), so the bracket content is left unspecified.
REPORT_KEY = re.compile(
    r"^(?:\[[^\]]*\])?\s*(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")


def _key(row: dict) -> tuple | None:
    match = REPORT_KEY.match(row.get("report_nm") or "")
    return (row["corp_name"], *match.groups()) if match else None


def build_chains(manifest: list[dict]) -> list[dict]:
    """One row per correction. Non-periodic filings and non-corrections are
    not emitted."""
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
            # Measured at zero. Should it happen, nothing is linked: picking
            # one of several would mark an unrelated report as superseded. The
            # same judgement as `corpus/corrections`.
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
        print("일치" if same else "불일치 — 재생성이 필요하다")
        raise SystemExit(0 if same else 1)
    out.write_text(body, encoding="utf-8")
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
