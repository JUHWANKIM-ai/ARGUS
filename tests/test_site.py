"""`scripts/build_site.py` — 정본 미러와 보안 게이트를 고정한다 (LLM 0회 · 크레딧 0원).

이 파일이 지키는 것은 둘이다.

  ① **사본을 만들지 않는다** — 소스에 금액·성적 리터럴이 없고, 정본 파서가 문서의
     검산과 대조하며(어긋나면 `SsotMismatch`), 세대는 산출물에서 해석된다.
  ② **보안 다섯이 실제로 발화한다** — 게이트 프로브를 통과시켜 `SiteLeak` 을 확인한다.
     🔴 발화를 확인하지 않은 게이트는 게이트가 아니다.

🔴 공인 IP·API 키·평가셋 문항을 **이 파일에도 적지 않는다.** 정본에서 뽑아 대조한다.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from scripts import build_site as b

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "build_site.py").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처 — 산출물이 없는 체크아웃에서는 렌더 검사를 건너뛴다 (`artifacts/` 는 비추적이다)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ssot() -> dict:
    return b.parse_ssot()


@pytest.fixture(scope="module")
def built() -> dict[str, str]:
    if not b.resolve_generation():
        pytest.skip("artifacts/ablation/*/summary.json 이 없다 — 렌더 검사를 건너뛴다")
    return b.build_site(write=False)


@pytest.fixture(scope="module")
def payloads() -> dict:
    if not b.resolve_generation():
        pytest.skip("artifacts/ablation/*/summary.json 이 없다")
    return b.build_payloads()


def _rows_of_every_report() -> list[dict]:
    """모든 세대의 `report.json` 행. 🔴 읽기 게이트를 그대로 쓴다 — **테스트도** 금지 경로를
    읽지 않는다(은닉 트랙 폴더가 있으면 `ForbiddenRead` 로 걸러진다)."""
    rows = []
    for path in sorted((ROOT / "artifacts" / "ablation").glob("v*/**/report.json")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            payload = b.read_json(rel)
        except b.ForbiddenRead:
            continue
        rows += payload.get("rows", [])
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# ① 정본 파서 — 미러이고, 조용히 틀리지 않는다
# ─────────────────────────────────────────────────────────────────────────────


def test_ledger_row_sum_mirrors_the_ssot_total(ssot):
    """🔴 사본 검사가 아니라 **동일성** 검사다. 자기합만 보면 사본이 낡아도 통과한다."""
    assert ssot["ledgerRowSum"] == pytest.approx(ssot["spentWon"], abs=0.05)
    assert len(ssot["ledger"]) >= 5
    assert 0 < ssot["spentWon"] < ssot["budgetWon"]


def test_phase_gate_numbers_sum_to_the_stated_total(ssot):
    assert sum(p["gatePassed"] or 0 for p in ssot["phases"]) == ssot["gates"]["passed"]
    assert sum(p["gateTotal"] or 0 for p in ssot["phases"]) == ssot["gates"]["total"]


def test_test_count_history_tail_matches_its_headline(ssot):
    """🔴 이 저장소가 열 번째로 겪은 결함이 *한 문장 안*에서 났다 — 꼬리만 안 고쳐졌다."""
    assert ssot["tests"]["historyTail"] == ssot["tests"]["passed"]


def test_phase_credit_column_plus_unallocated_equals_the_total(ssot):
    if ssot["unallocatedWon"] is None:
        pytest.skip("정본이 배분 밖 차액 문장을 들고 있지 않다")
    assert ssot["phaseCreditWon"] + ssot["unallocatedWon"] == pytest.approx(
        ssot["spentWon"], abs=0.05)


def test_parser_raises_when_a_ledger_row_is_added_without_updating_the_total(monkeypatch):
    """🔴 파서는 **재발 감지기**를 겸한다. 행만 넣고 `누적` 을 안 고치면 빌드가 죽는다."""
    original = b.read_text(b.SSOT)
    marker = "| | **누적**"
    assert marker in original
    doctored = original.replace(
        marker, "| 99 | **테스트용 새 행** | — | **1,111.1원** | 없음 |\n" + marker, 1)
    real_read = b.read_text
    monkeypatch.setattr(b, "read_text",
                        lambda rel: doctored if rel == b.SSOT else real_read(rel))
    with pytest.raises(b.SsotMismatch) as exc:
        b.parse_ssot()
    assert "지출 원장 행 합" in str(exc.value)


def test_parser_raises_when_the_gate_total_drifts(monkeypatch):
    original = b.read_text(b.SSOT)
    m = re.search(r"게이트\s*총계\s*\*{0,2}(\d+)\s*/\s*(\d+)", original)
    assert m
    doctored = original.replace(m.group(0), m.group(0).replace(m.group(1),
                                                               str(int(m.group(1)) + 3)), 1)
    real_read = b.read_text
    monkeypatch.setattr(b, "read_text",
                        lambda rel: doctored if rel == b.SSOT else real_read(rel))
    with pytest.raises(b.SsotMismatch):
        b.parse_ssot()


def test_welcome_credit_is_checked_against_the_convention():
    assert b.welcome_credit_won() > 0
    assert b._WELCOME_CREDIT_MARK in b.read_text("docs/CONVENTION.md")


def test_data_limits_keep_the_unverified_marks_and_drop_struck_values():
    limits = b.parse_data_limits()
    assert limits["rows"]
    scales = " ".join(r["scale"] for r in limits["rows"])
    assert "⚠️" in scales, "출처 미확인 표시를 지우면 규모를 잘못 인용하게 된다"
    assert "~~" not in scales, "취소선 값은 버려야 한다"


def test_arm_verdicts_are_classified_by_symbol_not_by_keyword():
    """*"⏸ 보류 (세우지 않는다)"* 를 키워드로 읽으면 **기각으로 잘못 분류**된다."""
    verdicts = b.parse_arm_verdicts()
    assert len(verdicts["rows"]) >= 5
    for row in verdicts["rows"]:
        if row["symbol"] == "⏸":
            assert row["kind"] == "pending", row
        if row["symbol"] == "⚠️":
            assert row["kind"] == "diagnostic", row
        if row["symbol"]:
            assert row["kind"] != "unknown", row


def test_inflated_ids_come_from_the_code_constant_without_importing_it():
    ids = b.inflated_question_ids()
    assert ids and all(re.fullmatch(r"Q-\d+", q) for q in ids)
    assert "import argus" not in SOURCE


# ─────────────────────────────────────────────────────────────────────────────
# ② 하드코딩 0 — 소스에 금액·성적 리터럴이 없다
# ─────────────────────────────────────────────────────────────────────────────

#: 예외 셋뿐이다 — 규정값(그것도 `welcome_credit_won()` 이 정본 문장과 대조한다) ·
#: 연도 경계(마스킹이 연도를 살려 두는 범위) · KB 환산. **성적도 금액도 여기 없다.**
ALLOWED_BIG_NUMBERS = {b._WELCOME_CREDIT_WON, 1900, 2100, 1024}


def _code_constants() -> tuple[list[float], list[str]]:
    """독스트링을 뺀 나머지 리터럴. 🔴 주석은 AST 에 없으므로 자동으로 빠진다."""
    tree = ast.parse(SOURCE)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    numbers, strings = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or id(node) in docstrings:
            continue
        if isinstance(node.value, bool):
            continue
        if isinstance(node.value, (int, float)):
            numbers.append(float(node.value))
        elif isinstance(node.value, str):
            strings.append(node.value)
    return numbers, strings


def test_source_holds_no_currency_or_score_literals():
    """🔴 이 라운드의 실제 산출물은 *"사본 없는 구조"* 다. 리터럴이 하나라도 있으면 낡는다."""
    numbers, strings = _code_constants()
    big = [n for n in numbers if abs(n) >= 1000 and n not in ALLOWED_BIG_NUMBERS]
    assert not big, f"큰 수 리터럴은 정본에서 파싱하거나 산출물에서 세야 한다: {big}"
    for text in strings:
        for m in re.finditer(r"(\d[\d,]*\.?\d*)\s*원", text):
            # 0원(크레딧 0)은 낡을 수 없는 값이다. 그 밖의 금액은 전부 금지다.
            assert float(m.group(1).replace(",", "")) == 0, f"금액 리터럴: {text[:60]}"
        assert not re.search(r"(?<!\d)\d{1,3}\s*/\s*\d{2,3}(?!\d)", text), \
            f"성적 리터럴(분자/분모): {text[:60]}"
        assert not re.search(r"[+-]\d+\.\d+\s*%p", text), f"Δ 리터럴: {text[:60]}"


def test_source_never_hardcodes_a_generation_path():
    """세대는 `resolve_generation()` 이 해석한다. `v2/`·`v3/` 를 박으면 다음 세대에 낡는다."""
    _, strings = _code_constants()
    for text in strings:
        assert not re.search(r"\bv[1-9]\d*/", text), f"세대 경로 리터럴: {text[:60]}"


def test_source_never_imports_argus_or_reads_source_paths():
    """🔴 보안 ①. `argus.config.settings()` 가 `.env` 를 읽는다 — 통로 자체를 만들지 않는다."""
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "argus" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "argus"
    assert "src/" in b.FORBIDDEN_PATHS


# ─────────────────────────────────────────────────────────────────────────────
# ③ 읽기 게이트 — 경로를 막는다 (보안 ①②③)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [
    "evalset/heldout.jsonl",
    "evalset/heldout_v2.jsonl",
    "artifacts/ablation/v2/heldout/heldout_run.json",
    "artifacts/ablation/v4/heldout/report.json",
    "data/dart-disclosures/manifest.jsonl",
    ".env",
    "src/argus/config.py",
    "artifacts/attic/anything.json",
])
def test_forbidden_paths_are_refused(rel):
    with pytest.raises(b.ForbiddenRead):
        b.read_text(rel)


@pytest.mark.parametrize("rel", ["docs/total_plan.md", "evalset/questions.jsonl",
                                 "deploy/README.md"])
def test_allowed_paths_are_readable(rel):
    assert b.read_text(rel)


def test_evalset_probe_sources_never_include_the_reserved_track():
    assert b.EVALSET_FILES
    for rel in b.EVALSET_FILES:
        assert "heldout" not in rel
    assert len(b._evalset_probes()) >= 4, "프로브가 비면 보안 ④ 는 빈 검사다"


# ─────────────────────────────────────────────────────────────────────────────
# ④ 행 화이트리스트 (보안 ⑤)
# ─────────────────────────────────────────────────────────────────────────────


def test_whitelist_and_blocklist_are_disjoint_and_cover_the_artifacts():
    """🔴 다음 세대가 필드를 늘리면 **이 테스트가 먼저 깨진다.** 조용히 새지 않는다."""
    assert not (b.ROW_FIELDS_ALLOWED & b.ROW_FIELDS_BLOCKED)
    rows = _rows_of_every_report()
    if not rows:
        pytest.skip("report.json 이 없다")
    seen = {k for r in rows for k in r}
    unknown = sorted(seen - b.ROW_FIELDS_KNOWN)
    assert not unknown, f"산출물에 새 필드가 붙었다 — 실을지 판단해야 한다: {unknown}"


def test_blocked_fields_are_the_ones_the_round_required():
    for field in ("golden", "golden_rank", "answer", "think_trace", "corp_name", "sector"):
        assert field in b.ROW_FIELDS_BLOCKED
        assert field not in b.ROW_FIELDS_ALLOWED


def test_project_row_publishes_only_whitelisted_fields():
    row = {k: "x" for k in b.ROW_FIELDS_KNOWN}
    row["question_id"] = "Q-001"
    row["failure_why"] = "재계산 1,842,200,000,000 vs 답변 97000000000 (2026년 반기)"
    out = b.project_row(row, generation="vX", arm="A0", cohort="full")
    assert not set(out) & b.ROW_FIELDS_BLOCKED
    assert set(out) <= b.ROW_FIELDS_ALLOWED | {"generation", "arm", "cohort"}
    assert "1,842,200,000,000" not in out["failure_why"]
    assert "97000000000" not in out["failure_why"]
    assert "2026년" in out["failure_why"], "연도는 남긴다 — 읽을 수 없게 만들지 않는다"


def test_project_row_dies_on_an_unknown_field():
    with pytest.raises(b.SiteLeak) as exc:
        b.project_row({"question_id": "Q-001", "brand_new_column": "…"},
                      generation="vX", arm="A0", cohort="full")
    assert "모르는 필드" in str(exc.value)


def test_sanitize_aggregate_passes_counts_but_not_content():
    assert b.sanitize_aggregate({"a": {"data_limit": 0}})["a"]["dataLimitCount"] == 0
    assert b.sanitize_aggregate({"golden": 60})["goldenDenominator"] == 60
    with pytest.raises(b.SiteLeak):
        b.sanitize_aggregate({"deep": {"golden": ["a document id"]}})


def test_filter_columns_are_a_subset_of_the_whitelist():
    assert set(b.FILTER_ROW_FIELDS) <= b.ROW_FIELDS_ALLOWED | {"generation", "arm", "cohort"}


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 게이트 발화 — 🔴 발화를 확인하지 않은 게이트는 게이트가 아니다
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label,payload", b.gate_probes(), ids=lambda v: str(v)[:24])
def test_every_gate_fires_on_a_deliberate_violation(label, payload):
    with pytest.raises(b.SiteLeak):
        b.gate_all(payload)


def test_gate_scans_html_too_not_only_json():
    with pytest.raises(b.SiteLeak):
        b.gate_all({"page.html": "<p>키는 nv-abcdefghij0123456789 이다</p>"})


def test_public_ip_gate_reads_the_ip_from_the_canonical_file():
    """🔴 IP 를 이 파일에 적지 않는다. 정본에서 뽑아 대조한다."""
    ips = b.public_ips()
    assert ips, "deploy/README.md 에서 IP 를 못 뽑으면 이 게이트는 빈 검사다"
    with pytest.raises(b.SiteLeak):
        b.gate_all({"x.html": f"<p>{ips[0]}</p>"})


def test_gate_passes_on_the_real_build(built):
    assert b.gate_all(built)


def test_no_output_carries_a_document_identifier(built):
    for name, text in built.items():
        m = b.DOC_ID.search(text)
        assert not m, f"{name}: 문서 식별자가 실렸다 — {m.group(0) if m else ''}"


def test_masking_is_what_keeps_failure_why_publishable(payloads, monkeypatch):
    """🔴 마스킹을 끄면 게이트가 발화한다 — 즉 마스킹은 장식이 아니다."""
    rows = [r for r in payloads["errors"]["rows"] if "[금액]" in (r.get("failure_why") or "")]
    if not rows:
        pytest.skip("현 세대 산출물의 자유 서술에 큰 수가 없다")
    monkeypatch.setattr(b, "_mask_amounts", lambda text: text or "")
    with pytest.raises(b.SiteLeak):
        b.gate_all({"errors.json": json.dumps(
            [b.project_row(r, generation="vX", arm="A0", cohort="full")
             for r in _rows_of_every_report()], ensure_ascii=False)})


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ 세대 라벨 — 🔴 라벨 없는 두 표는 그 자체가 세대 혼합 뺄셈이다
# ─────────────────────────────────────────────────────────────────────────────


def test_generation_resolution_is_graceful_when_a_newer_folder_has_no_summary(monkeypatch):
    """`artifacts/ablation/v4/` 가 생겨도 `summary.json` 이 없으면 조용히 빠진다."""
    real = b.generations()
    if not real:
        pytest.skip("세대 폴더가 없다")
    monkeypatch.setattr(b, "generations", lambda: ["v99"] + real)
    assert b.resolve_generation() == real[0]


def test_ladder_generation_actually_holds_the_ladder():
    gen = b.resolve_ladder_generation()
    if not gen:
        pytest.skip("사다리 세대가 없다")
    arms = b.load_summary(gen, "full")["summary"]["arms"]
    assert b.LADDER_ORDER[0] in arms and b.BASELINE_ARM in arms


def test_loo_baseline_is_the_same_generation(payloads):
    loo = payloads["ablation"]["loo"]
    if not loo["rows"]:
        pytest.skip("LOO arm 이 없다")
    assert loo["baseline"]["generation"] == loo["generation"]
    for row in loo["rows"]:
        assert row["generation"] == loo["generation"]


def test_every_page_footer_carries_the_generation_and_the_commit(built):
    pages = [name for name in built if name.endswith(".html")]
    assert len(pages) == len(b.PAGES)
    for name in pages:
        assert "성적 세대" in built[name], name
        assert "커밋" in built[name], name


def test_ablation_page_labels_both_generations(built, payloads):
    page = built["ablation.html"]
    ablation = payloads["ablation"]
    for key in ("ladder", "loo", "valuechain"):
        gen = ablation[key]["generation"]
        if gen:
            assert f"세대 {gen}" in page
    if ablation["ladder"]["generation"] != ablation["loo"]["generation"]:
        assert payloads["meta"]["generation"]["mixedGenerationWarning"]
        assert "세대 혼합 뺄셈" in built["index.html"]


def test_diagnostic_arms_are_labelled_as_not_a_score(payloads, built):
    rows = payloads["ablation"]["diagnostic"]["rows"]
    if not rows:
        pytest.skip("진단·기각 arm 이 없다")
    assert all("성적이 아니다" in r["label"] for r in rows)
    assert "성적이 아니다" in built["ablation.html"]


# ─────────────────────────────────────────────────────────────────────────────
# ⑦ 산출물 — 5쪽 · 분모 라벨 · 부풀림 병기 · 재현성
# ─────────────────────────────────────────────────────────────────────────────


def test_build_writes_five_pages_and_five_payloads(built):
    for name, _ in b.PAGES:
        assert name in built and built[name].startswith("<!DOCTYPE html>")
    for key in b.BUILDERS:
        assert f"data/site_{key}.json" in built
    assert "style.css" in built and "filter.js" in built and "README.md" in built


def test_no_payload_carries_a_reserved_track_key(payloads):
    text = json.dumps(payloads, ensure_ascii=False).lower()
    for token in b.HELDOUT_TOKENS:
        assert token not in text


def test_denominators_are_self_consistent(payloads):
    """🔴 *"단계 합 == 분모 − 정답"*. 어느 세대에서도 성립해야 한다."""
    dens = payloads["errors"]["denominators"]
    assert {d["key"] for d in dens} >= {"grid", "overall"}
    for d in dens:
        assert d["label"], d
        assert d["generation"] and d["arm"]
        assert d["stageSum"] == d["wrong"], d


def test_every_denominator_carries_its_label(payloads):
    for key, label in payloads["errors"]["denominatorDefs"].items():
        assert label and key in b.DENOMINATOR_DEFS


def test_valuechain_publishes_the_effective_score_next_to_the_judged_one(payloads, built):
    vc = payloads["meta"]["headline"].get("valuechain")
    if not vc:
        pytest.skip("밸류체인 산출물이 없다")
    assert vc["effective"]["correct"] == vc["scored"]["correct"] - vc["inflated"]["count"]
    assert vc["warning"] and vc["inflated"]["source"].startswith("scripts/error_analysis.py")
    assert "실질" in built["index.html"] and "실질" in built["ablation.html"]


def test_remaining_wrong_ids_are_counted_not_copied(payloads):
    remaining = payloads["errors"]["remainingWrong"]
    rows = [r for r in payloads["errors"]["rows"]
            if r["generation"] == remaining["generation"] and r["arm"] == remaining["arm"]
            and r["cohort"] == "full"]
    if not rows:
        pytest.skip("현 세대 기준선 행이 없다")
    assert remaining["ids"] == sorted({r["question_id"] for r in rows if not r["correct"]})
    assert remaining["count"] == len(remaining["ids"])


def test_payloads_are_reproducible_apart_from_the_timestamp(payloads):
    again = b.build_payloads()
    for key in payloads:
        first, second = dict(payloads[key]), dict(again[key])
        first["metadata"] = {k: v for k, v in first["metadata"].items() if k != "generated"}
        second["metadata"] = {k: v for k, v in second["metadata"].items() if k != "generated"}
        assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
            json.dumps(second, ensure_ascii=False, sort_keys=True), key


def test_pages_have_no_dead_local_links(built):
    for name, text in built.items():
        if not name.endswith(".html"):
            continue
        for href in re.findall(r'href="([^"]+)"', text):
            if href.startswith(("http", "#", "mailto:")):
                continue
            target = (b.SITE / href).resolve()
            if href.startswith("../artifacts"):
                continue  # 산출물은 비추적이라 체크아웃에 없을 수 있다
            assert target.exists() or (ROOT / href.lstrip("./")).exists(), f"{name} → {href}"


def test_metadata_declares_zero_llm_calls_and_zero_credit(payloads):
    for key, payload in payloads.items():
        assert payload["metadata"]["llmCalls"] == 0, key
        assert payload["metadata"]["creditWon"] == 0, key
        assert payload["metadata"]["sourceFiles"], key


def test_the_live_demo_slot_is_reserved_and_empty(built):
    """🔴 라이브 질의응답은 09-21 이후다. 자리만 남긴다."""
    page = built["index.html"]
    assert 'id="live-demo-slot"' in page and 'data-status="reserved"' in page
    assert "소비 상한" in page and "API 키" in page


def test_static_filter_ships_data_but_no_network_call(built):
    page, js = built["errors.html"], built["filter.js"]
    assert 'id="rows-data"' in page and "filter.js" in page
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "http://", "https://"):
        assert forbidden not in js, forbidden
    embedded = page.split('id="rows-data">', 1)[1].split("</script>", 1)[0]
    assert embedded.startswith("[")
    assert "</" not in embedded, "데이터가 <script> 를 깨고 나올 수 있다"
