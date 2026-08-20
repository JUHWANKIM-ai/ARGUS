"""A2, dense 인덱스 빌더. **크레딧을 쓴다. 2단계로 나뉘어 있다.**

    python scripts/build_embeddings.py --estimate        # 0단계: API 0회, 문자수만
    python scripts/build_embeddings.py --sample 100      # 1단계: 실측 usage 로 단가 역산
    python scripts/build_embeddings.py --records         # 2단계: 정형 3종 전량
    python scripts/build_embeddings.py --warm            # 2단계: 정기공시 후보 풀 지연 채움
    python scripts/build_embeddings.py --report          # 산출물·상주량 (API 0회)

## 왜 `--sample` 이 먼저인가

임베딩은 배치 입력이 없어서 (실측: 리스트를 주면 40001) **문서 하나에 호출 하나**다.
전량을 그냥 돌리면 멈출 자리가 없다. 그래서 표본으로 `chars/token` 을 실측해 전체를
역산하고, 추정이 상한을 넘으면 **범위를 줄이거나 중단**한다. 표본으로 만든 벡터도
저장소에 그대로 남으므로 버려지는 호출은 0이다.

## 정기공시를 전량 임베딩하지 않는 이유, 게이트 두 개가 동시에 막는다

    상주   157,397 조각 × 1024차원 × float32 = 615MB.  게이트 500MB (현재 427MB 사용).
           float16 으로 떨궈도 308MB 라 여전히 못 들어간다.
    비용   색인 문자 150.7M 자 ≈ 7천만 토큰대. 1단계 상한 10,000원에 근접·초과한다.

그래서 정기공시 dense 는 **메타 프리필터를 통과한 후보만** 담는다(`--warm`).
융합이 어차피 프리필터 뒤에서 일어나므로 **측정에는 손실이 없다**. 프리필터가 거른
조각은 전량 인덱스가 있어도 후보가 아니다. 잃는 것은 배포 일반성이다:
새 기업·새 연도 질의는 최초 1회 지연 임베딩(호출·지연)이 붙는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from argus import ablation, embed                                  # noqa: E402
from argus.agent import pipeline, tools                            # noqa: E402
from argus.agent.budget import Budget                              # noqa: E402
from argus.agent.router import route                               # noqa: E402
from argus.retrieve.bm25 import SECTION_INDEX_CHARS                # noqa: E402

OUT_DIR = ROOT / "scratch" / "ablation" / "A2"
SNAPSHOT = OUT_DIR / "evalset_snapshot.json"

#: 1단계 통과선. 전체 추정이 이 값을 넘으면 임베딩하지 않고 보고만 한다.
STAGE1_LIMIT_WON = 10_000.0
#: arm 전체 상한의 80%. 여기 닿으면 멈추고 부분 결과를 남긴다.
SOFT_STOP_WON = 12_000.0

RECORD_GROUPS = ("exchange", "major", "holding")


def rss_mb() -> float:
    """상주 메모리(Working Set). 게이트 500MB 를 재는 값이다."""
    import ctypes
    import ctypes.wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = PMC()
    counters.cb = ctypes.sizeof(PMC)
    psapi = ctypes.WinDLL("psapi.dll")
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
    handle = ctypes.WinDLL("kernel32.dll").GetCurrentProcess()
    psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 1024 / 1024


def load_snapshot() -> list[str]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["question_ids"]


def dev_cases() -> list[dict]:
    """**얼려 둔 스냅샷만** 읽는다. 다른 트랙의 확장분이 섞이면 Δ 가 오염된다."""
    wanted = set(load_snapshot())
    path = ROOT / "evalset" / "questions.jsonl"
    rows = [json.loads(x) for x in path.open(encoding="utf-8") if x.strip()]
    picked = [r for r in rows if r["question_id"] in wanted]
    missing = wanted - {r["question_id"] for r in picked}
    if missing:
        raise SystemExit(f"스냅샷 문항이 사라졌습니다: {sorted(missing)}")
    return picked


# --- 0단계 · 문자수 (API 0회) ---------------------------------------------------

def corpus_chars() -> dict:
    """색인 대상 문자수. **BM25 가 색인한 것과 같은 문자열** 기준이다."""
    out: dict[str, dict] = {}
    for group in RECORD_GROUPS:
        index = tools._group_index(group)
        texts = index.texts if index else []
        out[group] = {"n": len(texts), "chars": sum(len(t) for t in texts)}

    section = tools._section_index()
    if section is None:
        out["periodic"] = {"n": 0, "chars": 0}
        return out
    capped = np.minimum(section.cols["n_chars"], SECTION_INDEX_CHARS).astype(np.int64)
    path_len = np.array([len(v) for v in section.tables["path"]], dtype=np.int64)
    corp_len = np.array([len(v) for v in section.tables["corp_name"]], dtype=np.int64)
    meta = path_len[section.codes["path"]] + corp_len[section.codes["corp_name"]] + 2
    out["periodic"] = {"n": len(section), "chars": int((capped + meta).sum())}
    return out


def _won(chars: float, chars_per_token: float) -> float:
    return chars / chars_per_token / 1000.0 * embed.PRICE_WON_PER_1K


#: 실측 처리율(건/분). **분당 60호출 고정창**이 상한이라 57 로 잡는다 (창의 95%).
#: 근거와 실측 로그는 `embed.py` 의 처리율 주석에 있다.
THROUGHPUT_PER_MIN = 57.0
#: 상주 게이트. 04-retriever 게이트 D 기준, BM25 인덱스만으로 이미 427MB 를 쓴다.
RSS_GATE_MB = 500.0
RSS_USED_MB = 427.0


def _mb_fp16(n: int) -> float:
    return n * embed.EMBED_DIM * 2 / 1024 / 1024


def render_estimate(chars: dict, chars_per_token: float, measured: bool) -> str:
    """비용만 보면 안 된다. **상주량과 소요시간이 먼저 막는다**. 세 게이트를 한 표에 둔다."""
    tag = "실측" if measured else "가정"
    lines = [f"\n=== 임베딩 비용 산정 ({tag} {chars_per_token:.2f} 자/토큰 · "
             f"{embed.PRICE_WON_PER_1K}원/1K · {embed.EMBED_MODEL}) ===",
             f"{'범위':<22}{'건수':>10}{'색인문자':>14}{'추정토큰':>13}"
             f"{'추정원':>8}{'상주MB':>9}{'소요':>9}"]

    def row(name: str, n: int, ch: int) -> str:
        mb = _mb_fp16(n)
        return (f"{name:<22}{n:>10,}{ch:>14,}{ch / chars_per_token:>13,.0f}"
                f"{_won(ch, chars_per_token):>8,.0f}{mb:>9,.1f}"
                f"{n / THROUGHPUT_PER_MIN / 60:>8,.1f}h")

    total_chars = total_n = 0
    for name, cell in chars.items():
        lines.append(row(name, cell["n"], cell["chars"]))
        total_chars += cell["chars"]
        total_n += cell["n"]
    lines.append(row("합계 (전량)", total_n, total_chars))
    lines.append(row("축소안: 정형 3종만",
                     sum(chars[g]["n"] for g in RECORD_GROUPS),
                     sum(chars[g]["chars"] for g in RECORD_GROUPS)))
    vectors = int((RSS_GATE_MB - RSS_USED_MB) * 1024 * 1024 / (embed.EMBED_DIM * 2))
    lines.append(f"\n상주 게이트 {RSS_GATE_MB:.0f}MB · BM25 인덱스가 이미 "
                 f"{RSS_USED_MB:.0f}MB 사용 -> dense 여유 "
                 f"**{RSS_GATE_MB - RSS_USED_MB:.0f}MB** "
                 f"= float16 {vectors:,}벡터")
    return "\n".join(lines)


# --- 1단계 · 표본으로 chars/token 실측 -------------------------------------------

def sample_texts(n: int) -> list[tuple[str, str, str]]:
    """(문서군, id, 텍스트). **문서군마다 고르게** 뽑는다. 정형만 재면 과소추정된다."""
    out: list[tuple[str, str, str]] = []
    per = max(1, n // (len(RECORD_GROUPS) + 1))
    for group in RECORD_GROUPS:
        index = tools._group_index(group)
        if index is None:
            continue
        step = max(1, len(index.doc_ids) // per)
        for i in range(0, len(index.doc_ids), step):
            out.append((group, index.doc_ids[i], index.texts[i]))
            if sum(1 for g, _, _ in out if g == group) >= per:
                break
    section = tools._section_index()
    if section is not None:
        step = max(1, len(section) // per)
        for i in range(0, len(section), step):
            chunk_id = section.chunk_ids[i]
            out.append(("periodic", chunk_id, section.index_text(chunk_id)))
            if sum(1 for g, _, _ in out if g == "periodic") >= per:
                break
    return out[:n]


def stage1(n: int, budget_won: float) -> dict:
    """표본 임베딩 -> 실측 `inputTokens` 로 `chars/token` 역산 -> 전량 비용 추정."""
    samples = sample_texts(n)
    embed.USAGE.clear()
    embed.USAGE.limit_won = budget_won
    print(f"1단계 표본 {len(samples)}건 임베딩 (상한 {budget_won:,.0f}원) …")
    started = time.time()
    per_group: dict[str, dict] = {}
    for group in dict.fromkeys(g for g, _, _ in samples):
        rows = [(d, t) for g, d, t in samples if g == group]
        text_of = dict(rows)
        store = embed.VectorStore.open(_store_path(group))
        store.ensure([d for d, _ in rows], lambda d: text_of[d], workers=embed.WORKERS,
                     on_batch=lambda *_: store.save(_store_path(group)))
        store.save(_store_path(group))
        per_group[group] = {"n": len(rows), "chars": sum(len(t) for _, t in rows)}
        print(f'  {group:<9} {len(rows):>4}건 · 누적 {embed.USAGE.tokens:,}토큰 '
              f'{embed.USAGE.won:,.2f}원')
    elapsed = time.time() - started

    total_chars = sum(r["chars"] for r in per_group.values())
    chars_per_token = total_chars / max(embed.USAGE.tokens, 1)
    out = {"n": len(samples), "chars": total_chars,
           "tokens": embed.USAGE.tokens, "calls": embed.USAGE.calls,
           "won": round(embed.USAGE.won, 2), "elapsed_s": round(elapsed, 1),
           "chars_per_token": round(chars_per_token, 3),
           "per_group": {g: {**r, "chars_per_doc": round(r["chars"] / r["n"], 1)}
                         for g, r in per_group.items()}}
    print(f'  실측 {out["calls"]}호출 · {out["tokens"]:,}토큰 · {out["won"]:,.2f}원 '
          f'· {out["elapsed_s"]}s · {chars_per_token:.2f}자/토큰')
    return out


def _store_path(group: str):
    from argus.retrieve.bm25 import dense_path
    return dense_path("section" if group == "periodic" else group)


# --- 2단계 · 실제 빌드 -----------------------------------------------------------

def build_records(budget_won: float) -> dict:
    """정형 3종 전량. 3,150건이라 상주 6.5MB(float16) 로 게이트에 영향이 없다."""
    embed.USAGE.limit_won = budget_won
    out = {}
    for group in RECORD_GROUPS:
        index = tools._group_index(group)
        if index is None:
            continue
        dense = index.attach_dense(group)
        before = len(dense.store)
        started = time.time()
        text_of = {d: t for d, t in zip(index.doc_ids, index.texts)}

        def progress(done, total, _g=group):
            dense.save()
            print(f'    {_g} {done}/{total} · {embed.USAGE.tokens:,}토큰 '
                  f'{embed.USAGE.won:,.1f}원 {time.time() - started:,.0f}s', flush=True)

        added = dense.store.ensure(index.doc_ids, lambda d: text_of[d],
                                   workers=embed.WORKERS, on_batch=progress)
        dense.save()
        out[group] = {"n": len(dense.store), "added": added, "was": before,
                      "elapsed_s": round(time.time() - started, 1),
                      "mb": round(dense.store.nbytes / 1024 / 1024, 2)}
        print(f'  {group:<9} {out[group]["n"]:>6}벡터 (신규 {added}) '
              f'{out[group]["mb"]}MB {out[group]["elapsed_s"]}s '
              f'· 누적 {embed.USAGE.won:,.1f}원')
    return out


def warm_pools(budget_won: float) -> dict:
    """정기공시 후보 풀 지연 채움. dev 스냅샷을 A2F 로 관통시키면 필요한 것만 채워진다.

    LLM 은 0회다. `_gather_evidence` 만 부른다. 임베딩 호출만 나간다.
    """
    embed.USAGE.limit_won = budget_won
    cases = dev_cases()
    section = tools._section_index()
    section.attach_dense("section")
    started = time.time()
    for arm in ("A2F", "A2"):
        with ablation.use(arm):
            for case in cases:
                pipeline._gather_evidence(route(case["question"]),
                                          case["question"], Budget())
        print(f'  {arm} 관통 완료 · 섹션 벡터 {len(section.dense.store):,} '
              f'· 누적 {embed.USAGE.won:,.1f}원')
    section.dense.save()
    return {"n": len(section.dense.store),
            "mb": round(section.dense.store.nbytes / 1024 / 1024, 2),
            "elapsed_s": round(time.time() - started, 1)}


def pool_stats() -> dict:
    """dense 가 실제로 다루는 후보 풀의 크기 분포. **API 0회 · LLM 0회.**

    A2 의 Δ 를 해석하는 데 이 표가 결정적이다. 메타 프리필터가 후보를 1~5건까지
    좁혀 놓으면 **재순위가 바꿀 것이 없다**. Δ 0 의 기전이 성능 문제가 아니라
    후보 집합의 크기라는 뜻이고, 그건 기법이 아니라 코퍼스의 성질이다.
    """
    from argus.corpus.manifest import resolve_company

    calls: list[dict] = []
    original = tools.execute
    tools.execute = lambda n, a: (calls.append(dict(a))
                                  if n == "search_chunks" else None) or original(n, a)
    try:
        for case in dev_cases():
            with ablation.use("A6"):            # dense off, 순수 BM25 경로의 후보다
                pipeline._gather_evidence(route(case["question"]),
                                          case["question"], Budget())
    finally:
        tools.execute = original

    section = tools._section_index()
    sizes: list[int] = []
    unique = {g: set() for g in (*RECORD_GROUPS, "periodic")}
    for args in calls:
        if args.get("group") == "periodic" or args.get("base_year") or args.get("path"):
            filters, prefix = {}, {}
            if args.get("corp_name"):
                row = resolve_company(args["corp_name"])
                filters["corp_name"] = row["corp_name"] if row else args["corp_name"]
            for key in ("base_year", "doc_subtype"):
                if args.get(key):
                    filters[key] = str(args[key])
            if args.get("path"):
                prefix["path"] = args["path"]
            if args.get("rcept_dt") or args.get("ym"):
                prefix["rcept_dt"] = str(args.get("rcept_dt")
                                         or args["ym"]).replace("-", "")
            mask = section._mask(prefix, filters)
            idx = (np.flatnonzero(mask) if mask is not None
                   else np.arange(len(section)))
            sizes.append(int(idx.size))
            unique["periodic"].update(int(i) for i in idx)
        else:
            group = args.get("group")
            target = group if group in RECORD_GROUPS else "exchange"
            index = tools._group_index(target)
            filters, prefix = {}, {}
            if args.get("corp_name"):
                row = resolve_company(args["corp_name"])
                filters["corp_name"] = row["corp_name"] if row else args["corp_name"]
            if args.get("ym"):
                prefix["rcept_dt"] = str(args["ym"]).replace("-", "")[:6]
            elif args.get("year"):
                prefix["rcept_dt"] = str(args["year"])[:4]
            keep = index.candidates(prefix, **filters)
            sizes.append(len(keep))
            unique[target].update(keep)

    arr = np.asarray(sizes or [0])
    return {
        "n_search_calls": len(sizes),
        "pool_p50": int(np.percentile(arr, 50)),
        "pool_p75": int(np.percentile(arr, 75)),
        "pool_p90": int(np.percentile(arr, 90)),
        "pool_max": int(arr.max()),
        "pool_mean": round(float(arr.mean()), 1),
        "calls_with_one_candidate": int((arr <= 1).sum()),
        "share_one_candidate": round(float((arr <= 1).mean()), 3),
        "share_five_or_fewer": round(float((arr <= 5).mean()), 3),
        "unique_candidates": {g: len(s) for g, s in unique.items()},
    }


def report() -> dict:
    """산출물 상태 + **상주량 실측**. API 0회.

    게이트 500MB 를 재려면 `dense 없이` 와 `dense 포함` 을 같은 프로세스에서 순서대로
    재야 한다. 인덱스 로드가 dense 사이드카를 자동으로 붙이므로, 1단계에서는
    `dense_path` 를 없는 경로로 돌려 **BM25 만의 상주량**을 먼저 확정한다.
    """
    from argus.retrieve import bm25

    base = rss_mb()
    real_path = bm25.dense_path
    bm25.dense_path = lambda name: ROOT / "artifacts" / "_no_dense" / name
    try:
        for group in RECORD_GROUPS:
            tools._group_index(group)
            tools._group_records(group)
        tools._section_index()
        bm25_only = rss_mb()
    finally:
        bm25.dense_path = real_path

    stores, total = {}, 0
    for name in (*RECORD_GROUPS, "section"):
        index = (tools._group_index(name) if name in RECORD_GROUPS
                 else tools._section_index())
        if index is None:
            continue
        dense = index.attach_dense(name)
        stores[name] = {"n": len(dense.store),
                        "mb": round(dense.store.nbytes / 1024 / 1024, 2)}
        total += dense.store.nbytes
    with_dense = rss_mb()
    return {"rss_base_mb": round(base, 1),
            "rss_bm25_mb": round(bm25_only, 1),
            "rss_with_dense_mb": round(with_dense, 1),
            "rss_delta_mb": round(with_dense - bm25_only, 1),
            "gate_mb": RSS_GATE_MB,
            "within_gate": with_dense <= RSS_GATE_MB,
            "dense_mb": round(total / 1024 / 1024, 2),
            "stores": stores}


# --- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="A2 dense 인덱스 빌더")
    parser.add_argument("--estimate", action="store_true", help="0단계 — API 0회")
    parser.add_argument("--sample", type=int, default=0, help="1단계 표본 건수")
    parser.add_argument("--records", action="store_true", help="정형 3종 전량 빌드")
    parser.add_argument("--warm", action="store_true", help="정기공시 후보 풀 채움")
    parser.add_argument("--report", action="store_true", help="산출물·상주량")
    parser.add_argument("--pools", action="store_true",
                        help="후보 풀 크기 분포 (API 0회) — Δ 해석의 근거")
    parser.add_argument("--chars-per-token", type=float, default=0.0,
                        help="추정에 쓸 자/토큰. 비우면 저장된 1단계 실측값")
    parser.add_argument("--budget-won", type=float, default=SOFT_STOP_WON)
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = OUT_DIR / "embedding_cost.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) \
        if ledger_path.exists() else {}

    if args.sample:
        ledger["stage1"] = stage1(args.sample, args.budget_won)
        ledger["spent_won"] = round(ledger.get("spent_won", 0.0)
                                    + ledger["stage1"]["won"], 2)

    ratio = (args.chars_per_token
             or ledger.get("stage1", {}).get("chars_per_token") or 2.0)
    measured = bool(ledger.get("stage1")) and not args.chars_per_token

    if args.estimate or args.sample:
        chars = corpus_chars()
        ledger["corpus_chars"] = chars
        ledger["chars_per_token"] = ratio
        text = render_estimate(chars, ratio, measured)
        print(text)
        full_chars = sum(r["chars"] for r in chars.values())
        full_n = sum(r["n"] for r in chars.values())
        full = _won(full_chars, ratio)
        blockers = []
        if full > STAGE1_LIMIT_WON:
            blockers.append(f"비용 {full:,.0f}원 > 상한 {STAGE1_LIMIT_WON:,.0f}원")
        if _mb_fp16(full_n) > RSS_GATE_MB - RSS_USED_MB:
            blockers.append(f"상주 {_mb_fp16(full_n):,.0f}MB > 여유 "
                            f"{RSS_GATE_MB - RSS_USED_MB:,.0f}MB")
        hours = full_n / THROUGHPUT_PER_MIN / 60
        if hours > 3:
            blockers.append(f"소요 {hours:,.1f}시간 (배치 입력 미지원 · "
                            f"실측 {THROUGHPUT_PER_MIN:.0f}건/분)")
        verdict = ("STOP(전량) — " + " · ".join(blockers) + " -> 범위를 줄인다"
                   if blockers else "GO — 전량도 상한 안이다")
        ledger["stage1_verdict"] = {
            "full_won": round(full, 1), "limit_won": STAGE1_LIMIT_WON,
            "full_rss_mb_fp16": round(_mb_fp16(full_n), 1),
            "rss_headroom_mb": RSS_GATE_MB - RSS_USED_MB,
            "full_hours": round(hours, 1), "blockers": blockers, "verdict": verdict}
        print(f"\n판정: {verdict}")

    if args.records:
        print("\n=== 2단계 · 정형 3종 전량 ===")
        # **누적이어야 할 것을 대입으로 덮지 않는다.** `embed.USAGE.won` 은 이
        #    프로세스 전체의 누적값이라(`stage1()` 만 `.clear()` 한다), `--sample` 뒤
        #    `--records` 를 같은 프로세스에서 돌리면 1단계 지출까지 섞여 있다. 그래서
        #    "이 호출이 새로 쓴 만큼"(before/after 델타)만 더한다. 425행의 stage1
        #    누적 패턴과 같다. 대입(`=`)이면 이전 단계·이전 프로세스의 기록이 지워진다
        #    (실측 사고: `--sample` 의 2.64원이 `--records` 의 110.82원으로 덮였다).
        before = embed.USAGE.won
        ledger["records"] = build_records(args.budget_won)
        ledger["spent_won"] = round(ledger.get("spent_won", 0.0)
                                    + (embed.USAGE.won - before), 2)

    if args.warm:
        print("\n=== 2단계 · 정기공시 후보 풀 ===")
        before = embed.USAGE.won
        ledger["periodic_pools"] = warm_pools(args.budget_won)
        ledger["spent_won"] = round(ledger.get("spent_won", 0.0)
                                    + (embed.USAGE.won - before), 2)

    if args.pools:
        ledger["pool_stats"] = pool_stats()
        print("\n=== 후보 풀 (메타 프리필터 통과) ===")
        print(json.dumps(ledger["pool_stats"], ensure_ascii=False, indent=1))

    if args.report or args.records or args.warm:
        ledger["report"] = report()
        print("\n=== 산출물 · 상주량 ===")
        print(json.dumps(ledger["report"], ensure_ascii=False, indent=1))

    # 누적 지출은 **덮어쓰면 안 된다.** `--report` 처럼 호출이 0인 실행이 원장을
    # 0 으로 밀면 "얼마 썼는지" 가 사라진다. 실제로 그 사고를 냈다 (113.5원 -> 2.6원).
    ledger["last_run_usage"] = embed.USAGE.totals()
    ledger["embedding_spend_won"] = round(
        float(ledger.get("embedding_spend_won") or 0.0) + embed.USAGE.won, 2)
    ledger["embedding_spend_tokens"] = int(
        ledger.get("embedding_spend_tokens") or 0) + embed.USAGE.tokens
    ledger["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(f"\n-> {ledger_path}")


if __name__ == "__main__":
    main()
