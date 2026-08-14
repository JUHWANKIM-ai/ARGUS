"""Phase I — Ablation 러너. arm 을 하나씩 돌리고 Δ 를 축별로 분해한다.

    python scripts/ablation.py --dry                 # LLM 0회 리허설 (검색 차이만 본다)
    python scripts/ablation.py --arm A8              # arm 하나 실행 (크레딧 지출)
    python scripts/ablation.py --run                 # RUN_ORDER 전부 (A8 -> A0)
    python scripts/ablation.py --heldout --arm A8 \
        --out artifacts/ablation/v2/heldout --budget 1000    # 🔴 최종 구성 1회만
    python scripts/ablation.py --offline --out artifacts/ablation/v2 --cohort full
                                                     # 캐시만으로 재집계 (LLM 0회)
    python scripts/ablation.py --arm A8 --cohort valuechain \
        --out artifacts/ablation/v3/vc --judge-repeat 3 --budget-won 600
                                                     # judge 재현 편차 σ (생성은 재사용)
    python scripts/ablation.py --arm A8 --cohort valuechain \
        --out artifacts/ablation/v3/vc --judge-sigma  # σ 재집계만 (LLM 0회)

🔴 **재집계에 `--out` 을 빼지 마라.** `--out` 없이 돌리면 `OUT_DIR` 이 v1 자리
(`artifacts/ablation`)가 되고, 그 폴더는 **v2 를 자기 하위에 품고 있다** — v1 `summary.json`
의 지출이 v2 몫까지 흡수하고(3,144.7 → 8,211.3) v1 의 42문항 `report.json` 이 현재
78문항 기준으로 재작성돼 **기준선의 출처 추적이 끊긴다.** 그래서 v2 가 존재하는 동안
v1 자리 쓰기는 `assert_not_v1_root` 가 **거부**한다 (경고가 아니라 거부다 — v1 산출물은
§12·§12b 의 비교 기준선이고 gitignore 라 복구 수단이 없다).

v2 (88문항 · A8 추가):

    python scripts/ablation.py --cohort full \\
        --arms A8,L1,L2,L3,A7,A6,A5,A4,A1b,A1,A0 --out artifacts/ablation/v2 --budget 20000
    python scripts/ablation.py --cohort valuechain --arms A8,L3 --out artifacts/ablation/v2/vc
    python scripts/ablation.py --report --out artifacts/ablation/v2   # 게이트 I, LLM 0회

## 코호트와 산출 폴더를 인자로 뺀 이유

v1 은 dev 42문항을 `artifacts/ablation/<arm>/` 에 남겼고 그것이 비교 기준선이다.
v2 는 88문항을 재면서 **같은 이름을 덮지 않아야** 한다 — 덮으면 v1 과 대조할 축이 사라진다.
그래서 `--out` 이 arm 폴더의 부모를 갈고, `--cohort` 가 무엇을 재는지 고른다.
**요청 캐시(`CACHE_DIR`)는 갈지 않는다** — 프롬프트가 같은 요청을 두 번 사지 않는 것이
`--out` 을 가르는 것보다 중요하다.

🔴 크레딧 규칙을 코드로 강제한다.

1. **heldout 은 `--heldout` 없이는 열리지 않는다.** arm 루프는 dev 42문항만 본다.
   그리고 `--heldout` 은 **arm 을 하나만** 받고, **산출물이 이미 있으면 거부**한다
   (`assert_heldout_unused` — `artifacts/` 전체를 훑으므로 **`--out` 으로 우회되지 않는다**).
   문서에 "1회만" 을 적어 두는 것으로는 두 번째 실행을 못 막는다.
2. **두 겹으로 캐시한다.**
   ① 응답 파일 — arm 별 `artifacts/ablation/<arm>/responses/Q-xxx.json`.
   ② **요청 캐시** — 요청 본문 해시로 CLOVA 응답 원문을 저장한다. 온도 0.0 이라
      같은 프롬프트는 같은 답이 나오는데 크레딧은 두 번 나간다. arm 8개 중 다수가
      **정기공시 18문항에서 프롬프트가 글자 하나 안 바뀌므로** 이 캐시가 없으면
      같은 값을 여덟 번 산다.
3. **실측 usage 만 센다.** `--budget-won` 을 넘으면 그 자리에서 멈추고 부분 결과를 남긴다.
4. 채점기는 `scripts/error_analysis.py` 를 **그대로 재사용**한다. Phase H 가 버그 3건을
   잡아 회귀 테스트로 고정해 둔 채점기라 새로 만들면 그 수정이 통째로 날아간다.

## 비용을 두 가지로 보고하는 이유

요청 캐시 덕분에 **실지출**과 **arm 의 고유 비용**이 갈린다.

    spent_won   — 이번 실행에서 실제로 나간 돈 (캐시 적중 제외)
    arm_cost_won — 그 arm 을 혼자 돌렸을 때의 비용 (캐시 적중 포함, 실측 usage 합)

성능/비용 곡선의 x축은 뒤엣것이다. 앞엣것은 상한 12,000원을 지키기 위한 회계다.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


from argus import ablation                                        # noqa: E402
from scripts import error_analysis as ea                          # noqa: E402

OUT_DIR = ROOT / "artifacts" / "ablation"
#: 🔴 **`--out` 으로 갈리지 않는다.** 요청 캐시는 프롬프트 해시 -> 응답이라 실행 사이에
#: 공유돼야 값을 한다. v2 폴더에 새 캐시를 파면 v1·T7 이 이미 산 응답을 다시 산다.
CACHE_DIR = ROOT / "artifacts" / "ablation" / "cache"

#: 인덱스 스냅샷. §I-3 — "중간에 재빌드하면 비교가 깨진다." 해시를 arm 시작 전에 찍는다.
INDEX_FILES = (
    "artifacts/exchange_bm25.json", "artifacts/major_bm25.json",
    "artifacts/holding_bm25.json", "artifacts/section_index.json",
    "artifacts/section_index.npz", "artifacts/exchange_records.jsonl",
    "artifacts/major_records.jsonl", "artifacts/holding_records.jsonl",
    "artifacts/periodic_sections.jsonl", "artifacts/periodic_sections.txt",
    "artifacts/references.jsonl",
    "data/dart-disclosures/manifest.jsonl", "data/dart-disclosures/universe.csv",
)

BUDGET_WON = 12_000.0
#: 상한의 80%. 여기 닿으면 멈추고 부분 결과를 보고한다.
SOFT_STOP_WON = 9_600.0


# --- 요청 캐시 (크레딧 이중 지출 방지) -----------------------------------------

class RequestCache:
    """CLOVA 요청 본문 해시 -> 응답 원문. **API 키는 저장하지 않는다.**

    `clova._post(url, headers, body)` 를 감싼다. 키에는 url 과 body 만 넣는다 —
    headers 에는 Authorization 이 들어 있고, 그걸 디스크에 남기면 아티팩트가 비밀정보가 된다.
    """

    def __init__(self, root: Path, offline: bool = False) -> None:
        self.root = root
        self.offline = offline
        self.hits = self.misses = 0
        #: 🔴 우회 호출 수. 미적중(`misses`)과 **따로 센다** — 우회는 캐시에 없어서 산
        #: 것이 아니라 **있는데도 일부러 다시 산 것**이고, 그 둘을 합치면 캐시 적중률이
        #: 실제보다 나쁘게 보인다.
        self.bypassed = 0
        #: 🔴 `no_cache()` 만 켠다. 일반 경로는 이 값을 절대 건드리지 않는다.
        self.bypass = False
        self.fresh_prompt = self.fresh_completion = 0

    @staticmethod
    def _key(url: str, body: dict) -> str:
        blob = json.dumps({"url": url, "body": body}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @contextlib.contextmanager
    def no_cache(self):
        """🔴 **judge 재현 편차 측정 전용.** 이 블록 안의 호출은 캐시를 읽지도 쓰지도 않는다.

        온도 0.0 이라 같은 프롬프트는 캐시가 **반드시** 적중한다. 그 적중을 그대로 두면
        judge 를 3회 돌려도 디스크에서 같은 파일을 세 번 읽을 뿐이고, "편차 0" 이라는
        결론이 **측정이 아니라 캐시의 성질**에서 나온다 — 측정이 무의미해진다.

        🔴 **쓰지도 않는다.** 우회 호출의 응답을 캐시에 얹으면 다음 회차가 그 값을
        적중시키고(1회차 응답이 2·3회차를 오염시킨다), 나아가 일반 경로가 읽는 정본
        응답까지 이번 회차 값으로 갈린다. 우회는 읽기·쓰기 **양쪽 다** 꺼야 한다.

        토큰(비용)은 우회 여부와 무관하게 센다 — 안 세면 예산 제동이 안 걸린다.
        """
        before = self.bypass
        self.bypass = True
        try:
            yield self
        finally:
            self.bypass = before

    def install(self):
        from argus import clova

        original = clova._post

        def cached(url, headers, body, timeout=60):
            path = self.root / f"{self._key(url, body)}.json"
            if path.exists() and not self.bypass:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.hits += 1
                return saved["status"], saved["payload"], saved["latency_s"]
            if self.offline:
                raise RuntimeError("요청 캐시 미적중인데 --offline 입니다 (호출 금지)")
            status, payload, latency = original(url, headers, body, timeout)
            if status == 200:
                if self.bypass:
                    self.bypassed += 1
                else:
                    self.root.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(
                        {"status": status, "payload": payload, "latency_s": latency},
                        ensure_ascii=False), encoding="utf-8")
                    self.misses += 1
                usage = (payload.get("result") or {}).get("usage") or {}
                # 🔴 임베딩 응답은 `promptTokens` 가 아니라 `inputTokens` 를 쓴다
                #    (`src/argus/embed.py`). `promptTokens` 만 세면 dense arm(A2·A2F)의
                #    임베딩 비용이 **예산 제동과 `spent_won` 에서 통째로 빠진다** —
                #    상한 판정이 실제보다 낙관적으로 나온다. v2 는 dense arm 을 안 돌려
                #    영향 0 이지만, A2 를 다시 재는 순간 발화한다.
                self.fresh_prompt += (usage.get("promptTokens", 0)
                                      or usage.get("inputTokens", 0))
                self.fresh_completion += usage.get("completionTokens", 0)
            return status, payload, latency

        clova._post = cached
        return original

    @property
    def fresh_won(self) -> float:
        return ea.cost_won(self.fresh_prompt, self.fresh_completion)


def install_dry_stub() -> None:
    """LLM 0회 리허설. 검색·성형·검증의 arm 차이만 본다 — 크레딧 0원."""
    from argus import clova

    def stub(url, headers, body, timeout=60):
        text = ("리허설 응답입니다. (근거: 사업보고서 2025.12, 접수번호 20250101000001, 본문)"
                if "critic" not in json.dumps(body)[:200] else
                '{"supported":true,"has_citation":true,"hallucination":false,"reason":"dry"}')
        if "심사자" in json.dumps(body, ensure_ascii=False)[:2000]:
            text = ('{"covers_key_points":1,"grounded":1,"no_fabrication":1,"reason":"dry"}')
        return 200, {"result": {"message": {"content": text},
                                "usage": {"promptTokens": 0, "completionTokens": 0},
                                "finishReason": "stop"}}, 0.0

    clova._post = stub


# --- 입력 ---------------------------------------------------------------------

#: 🔴 A2 측정 스냅샷. 다른 트랙이 dev 를 42 -> 78 로 늘리는 중이라, v1 A6 과 비교하려면
#: **같은 42문항**으로 재야 한다. 안 그러면 재는 것이 arm 이 아니라 문항 구성의 변화다.
SNAPSHOT_PATH = OUT_DIR / "A2" / "evalset_snapshot.json"


def load_dev(snapshot: str | Path | None = None) -> list[dict]:
    cases = ea.load_dev_cases()
    if not snapshot:
        return cases
    meta = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    wanted = set(meta["question_ids"])
    picked = [c for c in cases if c["question_id"] in wanted]
    missing = wanted - {c["question_id"] for c in picked}
    if missing:
        raise SystemExit(f"스냅샷 문항이 questions.jsonl 에서 사라졌습니다: {sorted(missing)}")
    print(f'스냅샷 {meta["n"]}문항 고정 (sha256 {meta["sha256_of_joined_ids"][:16]}…)')
    return picked


def load_cases(cohort: str = "", snapshot: str | Path | None = None) -> list[dict]:
    """이번 실행의 문항 집합. `--cohort` 가 있으면 채점기와 **같은 출처**를 쓴다.

    코호트를 손으로 적지 않는 것이 요점이다 — `ea.load_cohort` 가 `measured` 를 v1
    산출물에서 읽으므로, 여기서 별도 목록을 두면 게이트 D 의 분모와 조용히 갈라진다.
    """
    if cohort:
        return ea.load_cohort(cohort)
    return load_dev(snapshot)


def install_oracle_golden(targets: list[str], cases: list[dict]) -> dict | None:
    """🔴 `ORACLE` arm 을 돌리기 전에 코호트의 `{question_id: evidence}` 를 주입한다.

    파이프라인은 `question_id` 만 알고 evalset 을 모른다 (`src/argus/ablation.py` 머리말).
    주입 없이 arm 만 켜면 `OracleGoldenMissing` 이 나서 **거기서 멈춘다** — 조용히 빈
    근거로 진행해 "오라클을 줘도 생성이 다 틀렸다" 가 되는 것보다 낫다.

    🔴 **heldout 은 절대 주입 대상이 아니다.** 구조적으로도 여기 못 온다 (`main` 의
    heldout 경로는 이 함수 앞에서 반환한다). 그래도 split 을 한 번 더 본다 — 되돌릴 수
    없는 조작의 제동은 겹으로 거는 것이 이 저장소의 규칙이다.

    🔴 **진단 전용이다.** 평가 시점에는 골든이 없으므로 이 arm 의 정답률은 제출 성적이
    아니다 (`ablation.DIAGNOSTIC_ARMS`).
    """
    arms = [a for a in targets if a in ablation.ARMS and ablation.ARMS[a].golden_oracle]
    if not arms:
        return None
    held = sorted(c["question_id"] for c in cases if c.get("split") == "heldout")
    if held:
        raise SystemExit(
            f"🔴 heldout 문항에 골든을 주입하려 했습니다: {held[:5]} — 거부합니다.")
    mapping = {c["question_id"]: (c.get("evidence") or []) for c in cases}
    ablation.set_oracle_golden(mapping)
    n_ev = sum(len(v) for v in mapping.values())
    n_sec = sum(1 for v in mapping.values() for e in v if e.get("section"))
    empty = sorted(q for q, v in mapping.items() if not v)
    print(f'\n🔴 진단 arm {arms} — 골든 근거 주입 {len(mapping)}문항 · evidence {n_ev}개 '
          f'(section 지정 {n_sec}) · 골든 없는 문항 {len(empty)}건 {empty}')
    print("   ORACLE 은 평가 시점에 재현할 수 없다 — **제출 성적으로 인용하지 않는다.**")
    return mapping


def load_heldout() -> list[dict]:
    """🔴 heldout 18문항. **최종 구성 1회 전용.** arm 루프는 이 함수를 부르지 않는다."""
    path = ROOT / "evalset" / "heldout.jsonl"
    with path.open(encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    bad = sorted({c.get("split") for c in cases} - {"heldout"})
    if bad:
        raise SystemExit(f"heldout 아닌 split 이 섞였습니다: {bad}")
    return cases


#: heldout 사용 원장 파일명. **이것이 있으면 유료 실행을 거부한다** (아래 함수).
HELDOUT_LEDGER = "heldout_run.json"

#: 🔴 **이미 쓴 이력.** heldout 의 값은 "몇 번 열었는가" 에 달려 있으므로 횟수를 코드가
#: 들고 있어야 한다 — 문서에만 적으면 다음 사람이 산출물만 보고 '아직 안 썼다' 로 읽는다.
HELDOUT_HISTORY = (
    {"use": 1, "date": "2026-08-09", "arm": "A6", "track": "Ablation v1",
     "correct": 15, "n_scored": 18, "rate": 0.833,
     "dev_grid": "31/36 (86.1%)", "gap_pp": -2.8,
     "artifact": "artifacts/ablation/A6_heldout_run"},
    #: 🔴 2회째. 이 항목이 없던 동안 **코드 쪽 원장이 사실과 어긋나 있었다** — 소스만 읽는
    #: 사람은 "heldout 은 1회 썼다" 로 읽고, 디스크 원장이 사라진 상태에서
    #: `heldout_next_use()` 가 3회째 실행을 "2회째" 로 기록했다.
    #: `gap_pp` 는 dev 72 축, `gap_pp_measured36` 는 v1(36) 과 나란히 읽는 축이다.
    {"use": 2, "date": "2026-08-10", "arm": "A8", "track": "Ablation v2",
     "correct": 15, "n_scored": 18, "rate": 0.833,
     "dev_grid": "61/72 (84.7%)", "gap_pp": -1.4,
     "dev_grid_measured36": "33/36 (91.7%)", "gap_pp_measured36": -8.3,
     "artifact": "artifacts/ablation/v2/heldout"},
)


def heldout_ledgers() -> list[Path]:
    """🔴 저장소 안의 **모든** heldout 원장. `--out` 과 무관하게 찾는다.

    처음엔 `root / HELDOUT_LEDGER` 하나만 봤다. 그것은 **`--out` 으로 우회된다** —
    `--out artifacts/ablation/v3/heldout` 으로 돌리면 그 디렉터리에 원장이 없어 제동이
    무방비로 통과하고, `v2` 전례가 있으니 `v3` 경로는 충분히 있을 수 있다. 되돌릴 수 없는
    조작에서 "인자를 바꾸면 풀리는 제동" 은 제동이 아니다. 그래서 `artifacts/` 전체를 훑는다.
    """
    base = ROOT / "artifacts"
    return sorted(base.rglob(HELDOUT_LEDGER)) if base.exists() else []


def heldout_reports() -> list[Path]:
    """🔴 **제동의 세 번째 겹.** 원장(`heldout_run.json`)이 지워져도 `report.json` 이 남는다.

    `run_heldout` 은 `report["split"] = "heldout"` 을 박으므로, `*_heldout_run/report.json`
    은 그 자체로 "heldout 을 열었다" 는 증거다. 원장 파일과 달리 이 파일들은 **커밋되어
    있다**(`artifacts/` 가 gitignore 인데도 강제 추적된 33개 중 하나). 원장 하나를 지우면
    풀리는 제동은 제동이 아니다.
    """
    base = ROOT / "artifacts"
    if not base.exists():
        return []
    found = []
    for path in sorted(base.rglob("*_heldout_run/report.json")):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("split") == "heldout":
                found.append(path)
        except (OSError, ValueError):
            found.append(path)          # 못 읽으면 '있다' 쪽으로 본다
    return found


def heldout_next_use() -> int:
    """다음 실행이 **몇 회째**인가. 코드의 이력과 디스크의 원장 중 **큰 쪽** + 1.

    🔴 `len(HELDOUT_HISTORY) + 1` 로만 세면 `--out` 을 바꾼 3번째 실행도 "2회째" 로
    기록된다 — 횟수가 틀리면 낙폭의 의미가 사라진다. 원장을 못 읽으면 **'이미 썼다' 쪽으로
    본다** (읽기 실패를 '안 썼다' 로 해석하면 파일 하나 깨진 것이 재측정 허가가 된다).
    """
    used = [len(HELDOUT_HISTORY)]
    for path in heldout_ledgers():
        try:
            used.append(int(json.loads(path.read_text(encoding="utf-8")).get("use") or 0))
        except (OSError, ValueError, TypeError):
            used.append(len(HELDOUT_HISTORY) + 1)
    return max(used) + 1


def assert_heldout_unused(offline: bool) -> None:
    """🔴 "최종 1회" 를 **코드가** 강제한다. 원장이 하나라도 있으면 유료 실행을 거부한다.

    문서에 "1회만" 을 적어 두는 것으로는 두 번째 실행을 막지 못한다 — 다음 사람이(또는
    다음 세션의 내가) 같은 명령을 다시 치면 heldout 이 조용히 개발셋이 되고, 그것은
    **되돌릴 수 없다**. 되돌릴 수 없는 조작은 프로세스가 아니라 코드로 막아야 한다.

    🔴 **`root` 를 받지 않는다.** 받으면 `--out` 이 제동을 푼다 (`heldout_ledgers` 참조).
    인자로 위치를 바꿀 수 있는 검사는 이 용도에 쓰면 안 된다.

    `--offline` 은 예외다. 요청 캐시 미적중이 `RuntimeError` 라 **LLM 0회가 증명되므로**,
    그 경로는 heldout 을 새로 '여는' 것이 아니라 이미 산 응답을 재집계할 뿐이다.
    """
    if offline:
        return
    found = heldout_ledgers()
    lines = []
    for path in found:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        lines.append(f"   · {path.relative_to(ROOT)} — {saved.get('use', '?')}회째 · "
                     f"arm {saved.get('arm', '?')} · "
                     f"{saved.get('correct', '?')}/{saved.get('n_scored', '?')} · "
                     f"{saved.get('generated', '?')}")
    # 🔴 원장이 지워졌어도 채점 산출물이 증거다 (`heldout_reports` — 커밋되어 있다).
    for path in heldout_reports():
        lines.append(f'   · {path.relative_to(ROOT)} — `"split": "heldout"` '
                     "(원장 없이도 이 파일이 증거다)")
    if not lines:
        return
    raise SystemExit(
        "🔴 heldout 은 최종 구성 1회용입니다 — 이미 결과가 있습니다 "
        f"(다음 실행은 {heldout_next_use()}회째가 됩니다):\n"
        + "\n".join(lines) + "\n"
        "   🔴 `--out` 을 바꿔도 이 검사는 풀리지 않습니다 — `artifacts/` 전체를 훑습니다.\n"
        "   재집계만 하려면 `--offline` 을 붙이세요 (요청 캐시 적중만 허용 = LLM 0회).\n"
        "   그래도 새로 측정해야 한다면 그것은 arm 을 하나 더 재는 일이 아니라 "
        "**평가셋을 하나 더 만드는 일**입니다.")


def index_snapshot() -> dict:
    """인덱스 파일 해시. arm 간 비교의 전제라 실행마다 다시 찍고 대조한다."""
    out = {}
    for name in INDEX_FILES:
        path = ROOT / name
        if not path.exists():
            out[name] = None
            continue
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        out[name] = f"{digest.hexdigest()[:16]}:{path.stat().st_size}"
    return out


# --- arm 실행 -----------------------------------------------------------------

def arm_dir(arm: str) -> Path:
    return OUT_DIR / arm


def _use_dirs(arm: str) -> None:
    """채점기의 캐시 경로를 arm 폴더로 돌린다. 채점 로직은 그대로 재사용한다."""
    base = arm_dir(arm)
    ea.RESPONSE_DIR = base / "responses"
    ea.JUDGE_DIR = base / "judge"
    ea.OUT_DIR = base


#: 🔴 회차별 judge 산출물의 자리. **`judge/` 를 덮지 않는다** — 그 폴더는 `ea.analyse`
#: 가 읽는 정본이고 형식을 깨면 다른 도구가 통째로 못 읽는다. 회차는 자기 폴더에 쌓는다.
JUDGE_REPEAT_DIRNAME = "judge_repeat"


def judge_round_dirs(arm: str) -> list[Path]:
    """`judge_repeat/round_1`, `round_2`, … 를 회차 순으로. 없으면 빈 목록."""
    base = arm_dir(arm) / JUDGE_REPEAT_DIRNAME
    if not base.exists():
        return []
    rounds = [p for p in base.glob("round_*") if p.is_dir()]
    return sorted(rounds, key=lambda p: int(p.name.split("_")[1]))


def run_judge_rounds(arm: str, case: dict, response: dict, cache: RequestCache,
                     repeat: int, force: bool = False) -> list[dict]:
    """🔴 **같은 응답에 judge 만 N 회 다시 돌린다.** 생성은 다시 하지 않는다.

    응답(`response`)은 호출부가 이미 디스크에서 읽어 넘겨 주므로 이 함수는 judge 만
    산다. 회차마다 **요청 캐시를 우회**한다(`cache.no_cache()`) — 우회하지 않으면
    온도 0.0 의 같은 프롬프트가 반드시 적중해서 3회가 전부 같은 값이 되고, 그러면
    "편차 0" 이 캐시의 성질이지 judge 의 성질인지 가를 수 없다.

    회차 파일이 이미 있으면 다시 사지 않는다. 예산 제동이나 429 로 중간에 끊긴
    실행을 이어 돌릴 때 앞 회차를 두 번 결제하지 않기 위한 것이고, 회차마다 **파일이
    따로**라 이 재사용이 회차 간 독립성을 깨지 않는다.
    """
    base = arm_dir(arm) / JUDGE_REPEAT_DIRNAME
    out: list[dict] = []
    for k in range(1, repeat + 1):
        path = base / f"round_{k}" / f'{case["question_id"]}.json'
        if path.exists() and not force:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["replayed"] = True
            out.append(record)
            continue
        with cache.no_cache():
            record, _ = ea.with_retry(ea.run_judge, case, response,
                                      force=True, path=path)
        record["replayed"] = False
        out.append(record)
        time.sleep(ea.PACE_S)
    return out


def run_arm(arm: str, cases: list[dict], cache: RequestCache,
            budget_won: float, force: bool = False, config: str | None = None,
            judge_repeat: int = 1) -> dict:
    """한 arm 을 전수로 돌린다. **문항마다 즉시 디스크에 남긴다** — 끊겨도 잃지 않는다.

    `config` 는 폴더 이름과 arm 구성을 가르기 위한 것이다. heldout 실행은 결과를
    `A6_heldout_run/` 에 남기지만 구성은 **A6 그대로**여야 한다.

    🔴 `judge_repeat` 는 **기본값 1 에서 종전 동작과 한 글자도 다르지 않다** (아래
    `if judge_repeat > 1` 안으로만 들어간다). 2 이상이면 open 문항마다 judge 를 그
    횟수만큼 **캐시를 우회해** 더 돌려 회차별 폴더에 남긴다 — judge 재현 편차(σ) 측정용.
    반복은 **문항 단위로 묶여** 돌므로, 예산 제동에 걸려 중간에 끊겨도 끝난 문항은
    회차가 전부 있고 안 끝난 문항은 회차가 전부 없다 (회차 간 비교가 깨지지 않는다).
    """
    _use_dirs(arm)
    base = arm_dir(arm)
    base.mkdir(parents=True, exist_ok=True)
    started_won = cache.fresh_won
    started_bypass = getattr(cache, "bypassed", 0)
    rows, stopped = [], None

    with ablation.use(config or arm):
        for case in cases:
            # 🔴 **재생인가 측정인가를 행 단위로 남긴다.** 이 세 값이 없으면 산출물만
            #    보고 어느 문항이 이번에 결제됐는지 알 수 없고, 그러면 `spent_won 0.0` 인
            #    arm(전량 재생)이 §2 누적 곡선에서 신규 측정과 같은 격으로 선다.
            #    `latency_s` 의 의미도 여기서만 갈린다 — 요청 캐시가 적중하면 파이프라인이
            #    ~1ms 에 끝나므로 그 문항의 지연은 **측정값이 아니라 재생 시간**이다.
            hits_before, won_before = cache.hits, cache.fresh_won
            run, cached = ea.with_retry(ea.run_case, case, force=force)
            judged, judge_cached = None, None
            repeats: list[dict] = []
            if case.get("answer_mode") == "open":
                judged, judge_cached = ea.with_retry(ea.run_judge, case,
                                                     run["response"], force=force)
                if not judge_cached:
                    time.sleep(ea.PACE_S)
                if judge_repeat > 1:
                    repeats = run_judge_rounds(arm, case, run["response"], cache,
                                               judge_repeat, force)
            if not cached:
                time.sleep(ea.PACE_S)
            row = {
                "question_id": case["question_id"],
                "cached": cached,                    # 응답 파일 캐시 적중 (= 재생)
                "judge_cached": judge_cached,        # open 문항만. closed 는 None
                "request_cache_hits": cache.hits - hits_before,
                "spent_won": round(cache.fresh_won - won_before, 2),
                "llm_calls": run["llm_calls"],
                "prompt_tokens": run["prompt_tokens"],
                "completion_tokens": run["completion_tokens"],
                "judge_tokens": (judged or {}).get("prompt_tokens", 0)
                                + (judged or {}).get("completion_tokens", 0),
                "latency_s": run["latency_s"],
            }
            if repeats:
                # 🔴 회차별 판정을 **행에도** 남긴다. 집계는 회차 폴더를 다시 읽지만,
                #    `run.json` 만 보는 쪽에도 "이 문항이 갈렸다" 가 보여야 한다.
                row["judge_repeat_verdicts"] = [bool(r["correct"]) for r in repeats]
                row["judge_repeat_agreed"] = len(
                    {bool(r["correct"]) for r in repeats}) == 1
                row["judge_repeat_tokens"] = sum(
                    r.get("prompt_tokens", 0) + r.get("completion_tokens", 0)
                    for r in repeats if not r.get("replayed"))
            rows.append(row)
            spent = cache.fresh_won - started_won
            print(f'  [{arm}] {case["question_id"]} calls={run["llm_calls"]} '
                  f'tok={run["prompt_tokens"]}+{run["completion_tokens"]} '
                  f'· 이번 arm 실지출 {spent:,.0f}원 · 누적 {cache.fresh_won:,.0f}원')
            if cache.fresh_won > budget_won:
                stopped = case["question_id"]
                print(f"\n🔴 예산 상한 {budget_won:,.0f}원 도달 — {arm} 을 "
                      f"{stopped} 에서 중단합니다 (누적 {cache.fresh_won:,.0f}원)")
                break

    live = {"arm": arm, "n_run": len(rows), "stopped_at": stopped,
            "spent_won": round(cache.fresh_won - started_won, 1), "runs": rows}
    if judge_repeat > 1:
        # 🔴 무엇을 몇 회 쟀는지가 산출물에 남아야 한다. 회차 폴더만 보고는 "3회로
        #    돌렸는데 1회에서 끊긴 것" 과 "1회로 돌린 것" 이 구별되지 않는다.
        live["judge_repeat"] = judge_repeat
        # 🔴 이 지출이 **캐시 미적중이 아니라 의도적 우회**에서 나왔다는 증거.
        live["judge_repeat_bypassed_calls"] = (getattr(cache, "bypassed", 0)
                                               - started_bypass)
    return live


def _write_run(folder: str, live: dict) -> dict:
    """arm 의 `run.json` 을 쓴다. 🔴 **여기가 지출 기록의 유일한 서식지다.**

    재집계(`--offline`, 또는 이미 다 캐시된 arm 을 `--arms` 로 다시 부르는 것)는 `spent_won`
    이 0 이다. 그 0 을 그대로 덮으면 **최초 유료 실행의 지출 기록이 사라진다** — `_spend_meta`
    가 총지출을 **항상 `run.json` 합**으로 계산하므로 권위값 자체가 지워지고, `artifacts/`
    는 gitignore 라 복구 수단이 없다.

    🔴 이 사고는 **이 저장소에서 세 번 서식지가 나왔다.**
      ① `_spend_meta` — v1 재집계가 `summary.json` 의 `spent_won` 을 0.0 으로 덮었다
         (`artifacts/ablation/summary.json` 에 그 흔적이 남아 있다). `live` 가 비면
         `run.json` 합을 쓰는 것으로 고쳤다.
      ② `run_heldout` — T9 가 673.8원을 지키는 가드를 넣었다.
      ③ **사다리 루프** — 가드가 없었다. `--arms A0` 재집계 한 번으로 1,901.0 → 0.0,
         총지출 5,066.6 → 3,165.6원이 됐다.
    그래서 **가드를 함수 하나로 합쳤다.** 서식지가 둘이면 세 번째가 생긴다.

    두 번째 유료 실행도 앞의 값을 지우지 않는다 — 새 값을 권위값으로 쓰되 앞의 값을
    `spent_won_previous` 로 남긴다. 지운 돈은 복구되지 않지만 지워진 사실은 남는다.
    """
    run_path = arm_dir(folder) / "run.json"
    previous = (json.loads(run_path.read_text(encoding="utf-8"))
                if run_path.exists() else {})
    before = previous.get("spent_won")
    if before:
        if not live.get("spent_won"):
            live["spent_won_rescore"] = live.get("spent_won", 0.0)
            live["spent_won"] = before
            live["spent_won_source"] = "최초 유료 실행 기록 (이번은 재집계 — LLM 0회)"
            # 🔴 `spent_won` 은 최초 유료 실행의 값인데 `runs` 행은 **이번 재집계**의
            #    기록이다 (`cached: true` 가 그 증거다). 표시가 없으면 두 값을 한 실행의
            #    것으로 읽어 "유료 실행이 전량 캐시 적중이었다" 는 모순이 생긴다.
            live["runs_describe"] = (
                "이번 재집계 — 행의 `cached`/`spent_won` 은 재생 기록이고, 위 "
                "`spent_won` 은 최초 유료 실행의 값이다. 토큰·지연은 응답 파일에 "
                "저장된 최초 측정값이라 두 실행에서 같다.")
        elif live["spent_won"] != before:
            live["spent_won_previous"] = before
            live["spent_won_source"] = (
                f"이번 실행 기록. 🔴 앞선 실행이 {before:,.1f}원을 결제했고 그것은 "
                "이 값에 안 들어 있다 — 누계는 두 값을 더해야 한다.")
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(live, ensure_ascii=False, indent=1), encoding="utf-8")
    return live


def run_heldout(final_arm: str, cache: RequestCache, records: dict,
                stop_won: float, force: bool = False) -> dict:
    """🔴 heldout 18문항을 **최종 구성 하나로** 돌린다. arm 을 여러 개 받지 않는다.

    폴더는 `<arm>_heldout_run/` 이고 **구성은 `<arm>` 그대로**다 (v1 이 `A6_heldout_run/`
    에 남기면서 `report["arm"] == "A6"` 으로 적어 둔 것과 같은 규약). 폴더 이름을 arm 으로
    적어 버리면 `ablation.ARMS` 에 없는 이름이 되어 `arm_config` 가 빈 사전이 되고,
    그러면 **어떤 구성으로 쟀는지가 산출물에서 사라진다.**
    """
    cases = load_heldout()
    folder = f"{final_arm}_heldout_run"
    # 🔴 지출 보존 가드는 `_write_run` 안에 있다 — 사다리 루프와 **같은 함수**를 쓴다.
    live = _write_run(folder, run_arm(folder, cases, cache, stop_won, force,
                                      config=final_arm))
    report = score_arm(folder, cases, records, cohort="heldout")
    report["arm"] = final_arm
    report["arm_config"] = vars(ablation.ARMS[final_arm])
    report["split"] = "heldout"
    report["this_run"] = live
    (arm_dir(folder) / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def _grid_overall(report: dict | None) -> dict | None:
    return ((report or {}).get("crosstab") or {}).get("grid_overall")


def write_heldout_ledger(root: Path, final_arm: str, report: dict) -> dict:
    """일반화 낙폭과 **몇 회째 사용인지**를 한 파일에 박는다.

    🔴 비교 축을 산출물이 직접 들고 있게 한다. v1 heldout 은 **A6 시점**이고 v2 는 A8 이며,
    dev 쪽 분모도 36 vs 72 로 다르다 — 그 세 가지를 문서에만 적어 두면 다음 사람이 낙폭
    두 개를 같은 축으로 읽는다. heldout 18문항은 **v1·v2 가 같은 18문항**이라, 낙폭의
    분모만은 유일하게 안 바뀐 값이다.
    """
    held = _grid_overall(report) or {}
    v1_root = ROOT / "artifacts" / "ablation"
    v1_held = _grid_overall(_load_report(v1_root, "A6_heldout_run")) or {}
    v1_dev = _grid_overall(_load_report(v1_root, "A6")) or {}
    # dev 쪽 기준선은 **부모 폴더**의 같은 arm 이다 (`v2/heldout` -> `v2/A8`).
    v2_dev = _grid_overall(_load_report(root.parent, final_arm)) or {}

    def gap(dev: dict | None, hold: dict | None) -> float | None:
        """낙폭(%p). 🔴 **원 분자/분모에서 계산한다** (`_exact_rate` — 정밀도 규칙 한 곳).

        예전에는 `hold["rate"] - dev["rate"]` 였고 그 `rate` 는 `ea._rate()` 가 이미
        소수 셋째 자리로 반올림한 값이라 **이중 반올림**이 생겼다. 실측 피해:
        `measured 36` 낙폭이 15/18 − 33/36 = **−8.333** 인데 0.833 − 0.917 로 계산해
        **−8.4%p** 가 나왔고, 게이트 §14 가 그 수를 "판정은 이 수로 해야 한다" 며 찍었다.
        여유가 1.7%p 뿐인 게이트에서 0.1%p 를 **무작위 방향으로** 흘리는 계산이었다
        (기우는 방향은 두 rate 의 반올림 방향에 달려 있어 보수적이지도 않다).

        🔴 `None` 을 받는다. `measured 36` 축은 못 구할 수 있고, 그때 여기서 죽으면
        heldout 실행이 **채점 뒤 원장 쓰기에서** 터진다 — 다시 못 돌리는 실행에서
        최악의 자리다 (테스트가 실제로 이 경로에서 AttributeError 를 잡았다).
        """
        exact = gap_exact(dev, hold)
        return None if exact is None else round(exact, 1)

    def gap_exact(dev: dict | None, hold: dict | None) -> float | None:
        """반올림 **전** 낙폭(%p). 🔴 파생값은 이쪽에서 계산한다.

        여유(`overfit_slack_*`)를 반올림된 낙폭에서 계산하면 그것도 이중 반올림이다 —
        −8.3 에서 세면 0.61문항이고 −8.3333 에서 세면 **0.60문항**이다. 기록·표시는
        반올림값(`gap`), 계산은 이 값이다.
        """
        if not dev or not hold:
            return None
        dev_rate, hold_rate = _exact_rate(dev), _exact_rate(hold)
        if dev_rate is None or hold_rate is None:
            return None
        return (hold_rate - dev_rate) * 100

    gap_v2, gap_v1 = gap(v2_dev, held), gap(v1_dev, v1_held)

    # 🔴 **세 번째 축.** v2 dev 는 분모가 72 로 늘었으므로, v1(36) 과 나란히 읽을 수 있는
    #    dev 값은 `measured 36` 이다. 낙폭이 축마다 다르게 나오므로 셋을 다 남긴다 —
    #    하나만 적으면 그것이 "그 낙폭" 으로 굳고, 가장 유리한 축이 뽑히기 쉽다.
    #
    #    🔴 **실패를 삼키지 않는다.** `measured_dev_cohort()` 는 gitignore 대상인
    #    `artifacts/eval/error_analysis.json` 을 읽으므로, 그 파일이 없는 환경(신규 클론 ·
    #    격리 워크트리 · CI)에서는 이 축이 안 나온다. 예전 코드는 `except: dev_m36 = None`
    #    으로 조용히 넘겼는데, 그러면 `gap_pp_worst` 가 **남은 유리한 축(−1.4%p)으로 조용히
    #    떨어지고** "과적합 아님" 이 찍힌다. 그것이 과적합 검사를 검사이기를 그만두게 하는
    #    바로 그 경로다. 이유를 남기고 **판정 불가로 표시**한다.
    dev_m36, m36_error = None, None
    try:
        from scripts.validate_evalset import measured_dev_cohort
        v2_dev_report = _load_report(root.parent, final_arm)
        if not v2_dev_report:
            raise FileNotFoundError(f"{root.parent}/{final_arm}/report.json 이 없다")
        dev_m36 = _grid_rate(v2_dev_report["rows"], set(measured_dev_cohort()))
    except Exception as exc:                                   # noqa: BLE001
        dev_m36, m36_error = None, f"{type(exc).__name__}: {exc}"

    # 🔴 heldout 쪽 수가 v1 과 같은지 본다. 같으면 낙폭이 좁아진 몫은 **전부 dev 쪽 변화**다.
    def wrong_ids(report: dict | None) -> list[str]:
        return sorted(r["question_id"] for r in (report or {}).get("rows", [])
                      if not r.get("correct"))

    # 🔴 판정과 여유는 **반올림 전** 낙폭에서 계산한다 (`gap_exact`). 기록은 반올림값이다.
    worst_candidates = [g for g in (gap_exact(v2_dev, held), gap_exact(dev_m36, held))
                        if g is not None]
    worst_exact = min(worst_candidates) if worst_candidates else None

    v1_full = _load_report(v1_root, "A6_heldout_run")
    # 🔴 재집계(`--offline`)는 **횟수를 올리지 않는다.** 이미 있는 원장의 `use` 를 이어받는다.
    existing = root / HELDOUT_LEDGER
    if existing.exists():
        use = json.loads(existing.read_text(encoding="utf-8")).get("use")
    else:
        use = heldout_next_use()
    ledger = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "use": use,
        "note": ("🔴 heldout 은 최종 구성 1회용이다. 이 원장이 `artifacts/` 아래 "
                 "**어디에라도** 있으면 러너가 유료 실행을 거부한다 — `--out` 으로 "
                 "우회되지 않는다 (`scripts/ablation.py::assert_heldout_unused`)."),
        "arm": final_arm,
        "arm_config": vars(ablation.ARMS[final_arm]),
        "n_cases": report.get("n_cases"),
        "correct": held.get("correct"),
        "n_scored": held.get("n_scored"),
        "rate": held.get("rate"),
        "dev_grid": {"correct": v2_dev.get("correct"), "n_scored": v2_dev.get("n_scored"),
                     "rate": v2_dev.get("rate"), "source": f"{root.parent.name}/{final_arm}"},
        "gap_pp": gap_v2,
        #: v1(36) 과 나란히 읽는 축. 🔴 **dev 쪽이 재생(replay)이라 독립 측정이 아니다** (§12).
        "dev_grid_measured36": (
            {"correct": dev_m36.get("correct"), "n_scored": dev_m36.get("n_scored"),
             "rate": dev_m36.get("rate"), "gap_pp": gap(dev_m36, held),
             "caveat": "재생(replay)이다 — 답변이 A7 과 바이트 동일이라 독립 측정이 아니다"}
            if dev_m36 else {"unavailable": m36_error}),
        "wrong_ids": wrong_ids(report),
        "wrong_ids_v1": wrong_ids(v1_full),
        "same_failures_as_v1": wrong_ids(report) == wrong_ids(v1_full),
        "v1": {"arm": "A6",
               "heldout": {"correct": v1_held.get("correct"),
                           "n_scored": v1_held.get("n_scored"),
                           "rate": v1_held.get("rate")},
               "dev_grid": {"correct": v1_dev.get("correct"),
                            "n_scored": v1_dev.get("n_scored"),
                            "rate": v1_dev.get("rate")},
               "gap_pp": gap_v1},
        # 🔴 축이 다르다는 사실을 값 옆에 붙여 둔다 — 표만 옮겨 적히면 이 문장이 사라진다.
        "axis_caveat": ("v1 낙폭은 **A6 시점 · dev 분모 36**, v2 낙폭은 **A8 · dev 분모 72** 다. "
                        "heldout 18문항만 두 판에서 동일하다. 두 낙폭의 차를 Δ 로 읽지 않는다."),
        "overfit_threshold_pp": -10.0,
        # 🔴 판정은 **가장 나쁜 축**으로 한다. 유리한 축으로 판정하면 과적합 검사가
        #    검사이기를 그만둔다. 축이 하나라도 빠지면 `worst_axis_available=False` 이고
        #    그때는 '신호 없음' 이 아니라 **판정 불가**다.
        "gap_pp_worst": None if worst_exact is None else round(worst_exact, 1),
        "worst_axis_available": dev_m36 is not None,
        "measured36_error": m36_error,
        #: 🔴 **이 게이트는 이번이 마지막 사용이다.** heldout 을 다시 못 재므로 다음 판에는
        #: 새 heldout 값이 없고, 낙폭의 분자가 83.3% 에 **영구 고정**된다. `measured 36` 은
        #: n=36 이라 **1문항 = 2.8%p** — dev 가 33 → 34 로 1문항만 올라도 낙폭이 −11.1%p 로
        #: 임계를 넘는다. 즉 다음 판에서는 "성능이 올랐다" 와 "과적합이다" 가 같은 수에서
        #: 나온다. 그래서 이 게이트는 **은퇴**시키고 회귀 10 · 밸류체인 12 로 대체한다.
        "gate_retired_after_this_run": True,
        "gate_retirement_reason": (
            "heldout 을 다시 못 재므로 낙폭의 분자가 83.3% 에 영구 고정된다. "
            "measured 36 은 n=36 이라 1문항 = 2.8%p — dev 33→34 만으로 낙폭이 "
            "-11.1%p 가 되어 임계(-10%p)를 넘는다. 성능 향상과 과적합이 같은 수에서 "
            "나오는 지표는 지표가 아니다. 이후 회귀 10 · 밸류체인 12 로 감시한다."),
        "history": list(HELDOUT_HISTORY),
        "overfit_signal": None,          # 아래에서 채운다 (worst 축 기준)
        "verdict": None,
        "spent_won": (report.get("this_run") or {}).get("spent_won"),
        "arm_cost_won": (report.get("totals") or {}).get("cost_won"),
        "failure_histogram": report.get("failure_histogram"),
        "root_cause_histogram": report.get("root_cause_histogram_grid")
                                or report.get("root_cause_histogram"),
    }
    # 🔴 판정·여유는 `worst_exact`(반올림 전)로 계산하고, 표시만 반올림한다.
    #    반올림된 −8.3 에서 여유를 세면 0.61문항이 되고, 옳은 값은 −8.3333 에서 나온
    #    **0.60문항**이다. 파생값의 이중 반올림도 같은 사고다.
    worst, threshold = worst_exact, ledger["overfit_threshold_pp"]
    if not ledger["worst_axis_available"] or worst is None:
        # 🔴 유리한 축만 남았을 때 '신호 없음' 을 찍지 않는다 — 그것이 조용히 통과하는 길이다.
        ledger["overfit_signal"] = None
        ledger["verdict"] = (
            "🔴 판정 불가 — 가장 나쁜 축(`measured 36`)을 못 구했다: "
            f"{m36_error or '값 없음'}. 남은 축(dev 72)만으로 '과적합 아님' 이라고 "
            "말하지 않는다.")
    else:
        ledger["overfit_signal"] = bool(worst <= threshold)
        # 🔴 여유를 %p 로만 적으면 작아 보이지 않는다. 최악축(`measured 36`)은 n=36 이라
        #    **1문항 = 2.8%p** 이므로, 여유를 **문항 수**로 환산해 함께 적는다.
        n_worst = (dev_m36 or {}).get("n_scored") or 36
        per_q = 100.0 / n_worst
        slack_q = abs(worst - threshold) / per_q
        ledger["overfit_slack_pp"] = round(abs(worst - threshold), 1)
        ledger["overfit_slack_questions"] = round(slack_q, 2)
        ledger["verdict"] = (
            f"🔴 dev 과적합 신호 — 가장 나쁜 축 {worst:+.1f}%p ≤ 임계 {threshold:+.1f}%p"
            if worst <= threshold else
            f"과적합 신호 아님 — 가장 나쁜 축 {worst:+.1f}%p > 임계 {threshold:+.1f}%p. "
            f"🔴 여유는 {abs(worst - threshold):.1f}%p 뿐이고 최악축은 n={n_worst} 이라 "
            f"1문항 = {per_q:.1f}%p — **dev 가 1문항만 더 맞아도 임계를 넘는다**")
    root.mkdir(parents=True, exist_ok=True)
    (root / HELDOUT_LEDGER).write_text(
        json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return ledger


def _spend_meta(root: Path, cache: "RequestCache", live: dict) -> dict:
    """지출 필드. 🔴 **재집계 실행이 실행 기록을 덮지 않게 한다.**

    `--arms` 없이 돌리는 재집계는 `live` 가 비고 `cache.fresh_won` 이 0 이다. 그 값을 그냥
    쓰면 `summary.json`·`summary.md` 가 **"실지출 0원"** 으로 갈리고, 진짜 기록은 arm 별
    `run.json` 에만 남는다 — 실측하고도 0원으로 보고하는 최악의 실패다 (실제로 한 번
    그렇게 덮였다). 그래서 `live` 가 비면 **arm 별 `run.json` 합**을 권위값으로 쓴다.

    `run.json` 은 arm 이 끝날 때마다 쓰이므로, 중간에 끊긴 실행도 거기까지는 남아 있다.
    """
    # 🔴 총지출은 **하위 코호트 폴더까지** 센다 (`v2/vc/`). 얕게 세면 밸류체인 실행분이
    #    조용히 빠지고, 그러면 보고한 총액이 실제보다 작아진다.
    from_runs = spend_from_runs(root, recursive=True)
    previous = {}
    path = root / "summary.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
    out = {
        # 🔴 총지출은 **언제나 run.json 합**이다 — 프로세스를 몇 번 나눠 돌려도 단조롭다.
        #    `cache.fresh_won` 은 이번 프로세스 몫뿐이라, arm 을 여러 번에 걸쳐 돌리면
        #    마지막 프로세스의 값이 총액으로 굳어 실제보다 작게 보고된다.
        "spent_won": from_runs["won"],
        "spent_won_source": "arm·코호트별 run.json 합 (하위 폴더 포함)",
        "spent_won_by_arm": from_runs["per_arm"],
        "this_process_spent_won": round(cache.fresh_won, 1),
    }
    if live:
        out.update({"cache_hits": cache.hits, "cache_misses": cache.misses,
                    # 🔴 **우회 호출 수.** 이 값이 없으면 `--judge-repeat` 실행의
                    #    산출물이 자기모순으로 읽힌다 — 실측 v3 full 은
                    #    `cache_misses 0` 인데 `this_process_spent_won 1,266.0` 이다.
                    #    미적중 0 에 지출이 있는 이유는 회차 호출이 **캐시에 있는데도
                    #    일부러 다시 산 것**(`RequestCache.no_cache`)이기 때문이고,
                    #    그 사실이 필드로 안 남으면 다음 사람이 지출 기록을 의심한다.
                    "cache_bypassed": cache.bypassed,
                    "fresh_prompt_tokens": cache.fresh_prompt,
                    "fresh_completion_tokens": cache.fresh_completion,
                    "cache_stats_are_this_process": True,
                    "this_run": live})
    else:
        # 재집계 실행이 실행 기록을 덮지 않게 한다 — 예전 값을 그대로 이어받는다.
        # 🔴 다만 **이어받았다는 사실을 필드 수준에 박는다.** 예전에는 아무 표시 없이
        #    남아서 다음 집계가 그 수를 이번 프로세스의 값으로 읽었다. 한계를 게이트 본문
        #    한 줄에만 적어 두면 `summary.json` 을 직접 읽는 쪽에는 그 한계가 안 간다.
        out.update({
            "cache_hits": previous.get("cache_hits", 0),
            "cache_misses": previous.get("cache_misses", 0),
            "cache_bypassed": previous.get("cache_bypassed", 0),
            "fresh_prompt_tokens": previous.get("fresh_prompt_tokens", 0),
            "fresh_completion_tokens": previous.get("fresh_completion_tokens", 0),
            "cache_stats_are_this_process": False,
            # 🔴 **한 번 정해지면 안 움직인다.** 매번 앞 파일의 `generated` 를 읽으면 그것이
            #    직전 *재집계* 시각이라 재집계마다 값이 바뀌고, 그러면 산출물이 두 번
            #    돌려도 바이트가 달라진다(재현성이 깨진다). 게다가 뜻도 틀린다 — 이 통계가
            #    나온 곳은 재집계가 아니라 **최초 실행**이다. 그래서 앞의 값을 먼저 이어받고,
            #    없을 때만 시각으로 대체한다. 진짜 출처는 `cache_stats_note` 가 적는다.
            "cache_stats_carried_from": (previous.get("cache_stats_carried_from")
                                         or previous.get("rescored_at")
                                         or previous.get("generated")
                                         or "이전 summary.json"),
            "this_run": previous.get("this_run", {}),
            "rescored_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        if previous.get("cache_stats_note"):
            out["cache_stats_note"] = previous["cache_stats_note"]
    # 🔴 **자기모순을 조용히 넘기지 않는다.** 미적중이 있었다면 신규 토큰이 0 일 수 없다.
    #    v2 `summary.json` 이 실제로 `cache_misses 435` + `fresh_*_tokens 0` 이었고,
    #    그 수는 프로세스 로그에서 수동 복구된 값이라 자기검증이 불가능하다.
    fresh_tokens = out["fresh_prompt_tokens"] + out["fresh_completion_tokens"]
    if out["cache_misses"] and not fresh_tokens:
        out["cache_stats_inconsistent"] = (
            f'🔴 `cache_misses {out["cache_misses"]}` 인데 `fresh_*_tokens` 합이 0 이다 — '
            "두 수는 같은 프로세스에서 나올 수 없다. 미적중 수가 다른 실행에서 이어받은 "
            "값이거나 수동 복구값이다. **이 필드로 지출·절감을 계산하지 않는다** "
            "(지출의 권위값은 `spent_won` = arm 별 `run.json` 합이다).")
        print(f'\n{out["cache_stats_inconsistent"]}')
    return out


def score_arm(arm: str, cases: list[dict], records: dict, cohort: str = "") -> dict:
    """캐시된 응답만으로 집계한다. LLM 0회 — 재집계는 공짜여야 한다.

    🔴 `cohort` 를 넘긴다. 안 넘기면 `report.json` 의 `cohort` 가 빈 문자열로 남아
    **어떤 코호트로 채점된 파일인지 산출물만 보고 알 수 없다** (`summary.json` 에만 있고,
    arm 폴더만 복사되면 그 연결이 끊긴다).
    """
    _use_dirs(arm)
    rows = ea.analyse(cases, records)
    report = ea.build_report(cases, rows, cohort=cohort)
    report["arm"] = arm
    report["arm_config"] = vars(ablation.ARMS[arm]) if arm in ablation.ARMS else {}
    base = arm_dir(arm)
    base.mkdir(parents=True, exist_ok=True)
    (base / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "error_matrix.md").write_text(
        ea.render_matrix(rows, report), encoding="utf-8")
    return report


# --- judge 재현 편차 (σ) — LLM 0회 집계 -----------------------------------------
# 🔴 **왜 재는가.** 개방형 문항은 LLM judge 가 채점한다. 그런데 judge 의 재현 편차를
# 한 번도 안 쟀다. 실제로 밸류체인 12문항에서 A8 의 정답이 재측정마다 6 → 5 → 6 으로
# 움직였고, 문서들은 그것을 "재측정마다 ±1문항" 이라고 **추정으로** 적고 있다.
# 다음 라운드 arm 들의 Δ 가 1~2문항 규모로 예상되므로, σ 를 모르면 Δ 가 채택 신호인지
# 잡음인지 **판정할 수 없다**. KDD '26(paper/idea/QA.pdf)이 GPT-4o judge 를 3회 돌려
# 표준편차를 표(Table 6·7·8)로 내는 것과 같은 이유이고 같은 방법이다.

#: 판정을 이루는 세 축. `correct` 는 셋의 AND 다 (`critic.OpenVerdict.correct`).
JUDGE_FIELDS = ("covers_key_points", "grounded", "no_fabrication")


def _short(path: Path) -> str:
    """산출물에 적는 경로. 저장소 밖(테스트 tmp)이면 그대로 둔다."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _cohort_rate(rows: list[dict], cohort: str) -> dict:
    """이 코호트의 정답 수. **분모를 코호트에 맞춘다.**

    밸류체인은 그리드 밖 트랙이라 `grid_rows` 로 세면 **0문항**이 된다 (6→5→6 이
    관측된 축이 통째로 사라진다). 그리드 코호트는 반대로 회귀·밸류체인이 섞이면 안 된다.
    """
    if cohort in ea.OFF_GRID_TRACKS:
        sub = [r for r in rows if r.get("track") == cohort]
    else:
        sub = ea.grid_rows(rows)
    return ea._rate(sub)


def judge_sigma(arm: str, cases: list[dict], records: dict,
                cohort: str = "") -> dict | None:
    """회차별 judge 산출물을 다시 채점해 **문항별 일치와 arm 별 σ** 를 낸다. LLM 0회.

    🔴 회차 점수는 **`ea.analyse` 로 다시 낸다** — 회차 폴더를 `JUDGE_DIR` 로 갈아
    끼우고 정본과 같은 채점기를 태운다. judge 파일에서 `correct` 만 세지 않는 이유는
    그것이 코호트 정답 수가 아니기 때문이다. closed 문항의 EM·DATA_LIMIT 분모 제외 ·
    트랙 분리가 전부 채점기 안에 있고, 그 규칙을 여기 다시 적으면 두 곳이 갈린다.

    회차가 하나도 없으면 `None` — `--judge-repeat` 없이 돈 arm 에는 잴 것이 없다.
    """
    rounds = judge_round_dirs(arm)
    if not rounds:
        return None

    open_ids = [c["question_id"] for c in cases if c.get("answer_mode") == "open"]
    per_round, verdicts = [], {}
    # 🔴 **먼저 정본 경로로 맞춘다.** `ea.RESPONSE_DIR` 은 모듈 전역이라 앞선 호출의
    #    값이 남아 있다 — 응답을 엉뚱한 arm 폴더에서 읽으면 회차 점수가 통째로 틀린다.
    _use_dirs(arm)
    try:
        for path in rounds:
            ea.JUDGE_DIR = path
            rows = ea.analyse(cases, records)
            rate = _cohort_rate(rows, cohort)
            judged = {}
            for qid in open_ids:
                file = path / f"{qid}.json"
                judged[qid] = (json.loads(file.read_text(encoding="utf-8"))
                               if file.exists() else None)
            verdicts[path.name] = judged
            per_round.append({
                "round": int(path.name.split("_")[1]),
                "dir": _short(path),
                "n_judged": sum(1 for v in judged.values() if v),
                "n_open": len(open_ids),
                "correct": rate["correct"], "n_scored": rate["n_scored"],
                "rate": rate["rate"],
            })
    finally:
        _use_dirs(arm)                     # 🔴 정본 경로를 반드시 되돌린다

    # 🔴 정본(`judge/`)의 판정도 나란히 둔다. 이것은 **다른 날 산 표본**이라 회차와
    #    같은 격으로 σ 에 넣지 않지만, "3회가 서로 같은데 정본과는 다르다" 를 볼 수 있다.
    baseline = {}
    for qid in open_ids:
        file = arm_dir(arm) / "judge" / f"{qid}.json"
        baseline[qid] = (json.loads(file.read_text(encoding="utf-8"))
                         if file.exists() else None)

    names = [p.name for p in rounds]
    questions, split, split_fields = {}, [], []
    for qid in open_ids:
        got = [verdicts[n][qid] for n in names]
        if not any(got):
            continue                       # 회차가 하나도 없는 문항 (예산 제동 뒤)
        marks = [None if v is None else bool(v["correct"]) for v in got]
        fields = {f: [None if v is None else bool(v[f]) for v in got]
                  for f in JUDGE_FIELDS}
        base = baseline.get(qid)
        agreed = len(set(marks)) == 1
        agreed_fields = all(len(set(vals)) == 1 for vals in fields.values())
        questions[qid] = {
            "rounds": marks,
            "agreed": agreed,
            "fields": fields,
            "fields_agreed": agreed_fields,
            "baseline": None if base is None else bool(base["correct"]),
            "agrees_with_baseline": (base is not None and len(set(marks)) == 1
                                     and marks[0] == bool(base["correct"])),
            "reasons": [None if v is None else (v.get("reason") or "")[:400]
                        for v in got],
        }
        if not agreed:
            split.append(qid)
        elif not agreed_fields:
            split_fields.append(qid)

    counts = [r["correct"] for r in per_round]
    out = {
        "arm": arm,
        "cohort": cohort,
        "n_cases": len(cases),
        "n_open": len(open_ids),
        "n_rounds": len(rounds),
        "rounds": per_round,
        "correct_counts": counts,
        "mean": round(statistics.fmean(counts), 3) if counts else None,
        # 🔴 두 σ 를 **둘 다** 낸다. 표본 3개에서 모집단 σ(ddof=0)와 표본 σ(ddof=1)는
        #    1.22 배 차이가 나고, 어느 쪽인지 안 밝히면 Δ 판정의 문턱이 그만큼 흔들린다.
        #    본문 인용은 표본 σ(ddof=1) 쪽이다 — 논문 표와 같은 관행.
        "sigma_sample": (round(statistics.stdev(counts), 3)
                         if len(counts) > 1 else None),
        "sigma_population": (round(statistics.pstdev(counts), 3)
                             if counts else None),
        "min": min(counts) if counts else None,
        "max": max(counts) if counts else None,
        "range": (max(counts) - min(counts)) if counts else None,
        "baseline_correct": _baseline_correct(arm, cases, records, cohort),
        "questions": questions,
        "split_questions": split,
        "split_fields_only": split_fields,
        "n_compared": len(questions),
        "agreement_rate": (round(1 - len(split) / len(questions), 4)
                           if questions else None),
        "note": ("생성은 다시 하지 않았다 — 같은 응답 파일에 judge 만 다시 돌렸다. "
                 "회차 호출은 요청 캐시를 우회한다 (`RequestCache.no_cache`). "
                 "judge 온도는 0.0 그대로다 (`critic.judge_open`)."),
    }
    (arm_dir(arm) / "judge_sigma.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _baseline_correct(arm: str, cases: list[dict], records: dict,
                      cohort: str) -> dict | None:
    """정본 `judge/` 로 채점한 정답 수. 회차와 나란히 읽는 기준선이다."""
    if not (arm_dir(arm) / "judge").exists():
        return None
    _use_dirs(arm)
    return _cohort_rate(ea.analyse(cases, records), cohort)


def print_judge_sigma(result: dict) -> None:
    """표를 콘솔에 찍는다. 🔴 **갈린 문항이 핵심 산출**이라 그것을 마지막에 둔다."""
    print(f'\n=== judge 재현 편차 · {result["arm"]} · 코호트 {result["cohort"]} '
          f'· {result["n_rounds"]}회 ===')
    for row in result["rounds"]:
        print(f'  회차 {row["round"]}: {row["correct"]}/{row["n_scored"]} '
              f'(rate={row["rate"]}) · judge 산출 {row["n_judged"]}/{row["n_open"]}')
    base = result.get("baseline_correct") or {}
    if base:
        print(f'  정본(judge/): {base["correct"]}/{base["n_scored"]} '
              f'(rate={base["rate"]}) — 다른 날 산 표본, σ 에는 안 넣는다')
    print(f'  정답 수 {result["correct_counts"]} · 평균 {result["mean"]} · '
          f'σ(표본) {result["sigma_sample"]} · σ(모집단) {result["sigma_population"]} · '
          f'최소 {result["min"]} · 최대 {result["max"]}')
    print(f'  일치 {result["n_compared"] - len(result["split_questions"])}'
          f'/{result["n_compared"]} 문항 (일치율 {result["agreement_rate"]})')
    if result["split_questions"]:
        print(f'  🔴 갈린 문항 {result["split_questions"]}')
        for qid in result["split_questions"]:
            q = result["questions"][qid]
            print(f'     {qid}: 회차 {q["rounds"]} · 정본 {q["baseline"]} · '
                  f'축 {[(f, v) for f, v in q["fields"].items() if len(set(v)) > 1]}')
    else:
        print("  갈린 문항 **0건** — 이 코호트에서 judge 는 결정적이다")
    if result["split_fields_only"]:
        print(f'  판정은 같은데 축이 갈린 문항 {result["split_fields_only"]}')


# --- 검색 단계만 재기 (LLM 0회) --------------------------------------------------
# 🔴 **정답률보다 검색 지표를 먼저 잰다.** 생성은 검색 결과의 함수이므로, 골든 근거 적중과
# Recall@5 가 하나도 안 움직이면 정답률도 안 움직인다 — 그 경우 생성 호출은 크레딧을
# 태워 잡음을 사는 것이다. A2 는 정확히 그 위험이 큰 arm 이라(Phase H 겨냥 실패 0건)
# 이 모드가 먼저 돈다.
#
# 채점기는 `scripts/error_analysis.py` 를 그대로 재사용한다 —
# `golden_positions`(후보 안 순위) 와 `evidence_hit`(프롬프트에 실렸는가) 둘 다.

RECALL_AT = 5


def retrieval_metrics(arm: str, cases: list[dict]) -> dict:
    """한 arm 의 검색 지표. **LLM 0회 · 생성 프롬프트를 만들지 않는다.**"""
    from argus.agent import pipeline
    from argus.agent.budget import Budget
    from argus.agent.router import route
    from argus.embed import USAGE

    rows = []
    started_tokens = USAGE.tokens
    with ablation.use(arm):
        for case in cases:
            budget = Budget()
            evidence, trace_lines = pipeline._gather_evidence(
                route(case["question"]), case["question"], budget)
            evidence = ablation.shape_evidence(evidence)
            trace = " | ".join(trace_lines)
            hops = pipeline.parse_hops(trace)
            positions = ea.golden_positions(case, hops)
            hit = ea.evidence_hit(case, evidence)
            golden = positions["golden"]
            ranked = positions["rank"]
            rows.append({
                "question_id": case["question_id"],
                "difficulty": case.get("difficulty"),
                "doc_group": case.get("doc_group"),
                "n_golden": len(golden),
                "n_recall1": sum(1 for g in golden if ranked.get(g) == 1),
                "n_recall5": sum(1 for g in golden
                                 if ranked.get(g) and ranked[g] <= RECALL_AT),
                "n_evidence_hit": hit["n_present"],
                "evidence_all": hit["all"],
                "golden_rank": ranked,
                "n_blocks": evidence.count("## "),
                "n_chars": len(evidence),
                "n_hops": budget.hops, "n_steps": budget.steps,
                "dense_calls": trace.count("fusion=rrf"),
                "dense_skipped": trace.count("fusion=skip_pool"),
                "dense_gain": sum(int(m) for m in
                                  re.findall(r"dense_gain=(\d+)", trace)),
                "dense_new": sum(int(m) for m in
                                 re.findall(r"dense_new=(\d+)", trace)),
                "think_trace": trace,
            })
    return {"arm": arm, "n_cases": len(cases), "rows": rows,
            "embed_tokens": USAGE.tokens - started_tokens,
            "totals": _retrieval_totals(rows)}


def _retrieval_totals(rows: list[dict]) -> dict:
    def by(field, values):
        out = {}
        for value in values:
            sub = [r for r in rows if r["difficulty"] == value] if field == "difficulty" \
                else [r for r in rows if r["doc_group"] == value]
            golden = sum(r["n_golden"] for r in sub)
            out[value] = {"golden": golden,
                          "recall5": sum(r["n_recall5"] for r in sub),
                          "evidence_hit": sum(r["n_evidence_hit"] for r in sub),
                          "rate": round(sum(r["n_recall5"] for r in sub) / golden, 3)
                          if golden else None}
        return out

    golden = sum(r["n_golden"] for r in rows)
    return {
        "golden": golden,
        "evidence_hit": sum(r["n_evidence_hit"] for r in rows),
        "evidence_all_questions": sum(1 for r in rows if r["evidence_all"]),
        "recall1": sum(r["n_recall1"] for r in rows),
        "recall5": sum(r["n_recall5"] for r in rows),
        "recall5_rate": round(sum(r["n_recall5"] for r in rows) / golden, 3)
        if golden else None,
        "blocks": sum(r["n_blocks"] for r in rows),
        "chars": sum(r["n_chars"] for r in rows),
        "hops": sum(r["n_hops"] for r in rows),
        "steps_max": max((r["n_steps"] for r in rows), default=0),
        "dense_calls": sum(r["dense_calls"] for r in rows),
        "dense_skipped": sum(r["dense_skipped"] for r in rows),
        "dense_gain": sum(r["dense_gain"] for r in rows),
        "dense_new": sum(r["dense_new"] for r in rows),
        "by_difficulty": by("difficulty", ("하", "중", "상")),
        "by_group": by("doc_group", ("exchange", "major", "holding", "periodic")),
    }


def diff_retrieval(base: dict, other: dict) -> dict:
    """두 arm 의 문항별 차이. **어느 문항이 어떻게 바뀌었는지**가 결론의 근거다."""
    left = {r["question_id"]: r for r in base["rows"]}
    changed = []
    for row in other["rows"]:
        old = left.get(row["question_id"])
        if not old:
            continue
        keys = ("n_recall5", "n_recall1", "n_evidence_hit", "n_blocks", "n_chars")
        delta = {k: row[k] - old[k] for k in keys if row[k] != old[k]}
        if delta or old["golden_rank"] != row["golden_rank"]:
            changed.append({"question_id": row["question_id"],
                            "difficulty": row["difficulty"],
                            "doc_group": row["doc_group"], "delta": delta,
                            "rank_before": old["golden_rank"],
                            "rank_after": row["golden_rank"]})
    a, b = base["totals"], other["totals"]
    return {"base": base["arm"], "arm": other["arm"],
            "delta": {k: b[k] - a[k] for k in
                      ("evidence_hit", "evidence_all_questions", "recall1",
                       "recall5", "blocks", "chars", "hops")},
            "changed_questions": changed}


def render_retrieval(reports: dict[str, dict], diffs: list[dict], meta: dict) -> str:
    out = [f'# A2 — 검색 단계 Δ (LLM 0회)\n',
           f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 '
           f'(`scripts/ablation.py --retrieval`)\n',
           f'평가셋 스냅샷 **{meta["n_cases"]}문항** · sha256 `{meta["snapshot_sha"][:16]}…`\n',
           "\n## 1. arm 별 검색 지표\n",
           "| arm | 골든 근거 적중 | 전건 적중 문항 | Recall@1 | Recall@5 | 근거 블록 | 근거 글자 | hop | 스텝 최대 | dense 호출 | dense 신규 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, report in reports.items():
        t = report["totals"]
        out.append(f'| **{arm}** | {t["evidence_hit"]}/{t["golden"]} '
                   f'| {t["evidence_all_questions"]}/{report["n_cases"]} '
                   f'| {t["recall1"]}/{t["golden"]} '
                   f'| {t["recall5"]}/{t["golden"]} ({t["recall5_rate"]}) '
                   f'| {t["blocks"]} | {t["chars"]:,} | {t["hops"]} | {t["steps_max"]} '
                   f'| {t["dense_calls"]} | {t["dense_new"]} |')

    out.append("\n## 2. 난이도별 Recall@5\n")
    out.append("| arm | 하 | 중 | 상 |")
    out.append("|---|---|---|---|")
    for arm, report in reports.items():
        cells = []
        for level in ("하", "중", "상"):
            cell = report["totals"]["by_difficulty"][level]
            cells.append(f'{cell["recall5"]}/{cell["golden"]} ({cell["rate"]})')
        out.append(f'| **{arm}** | ' + " | ".join(cells) + " |")

    out.append("\n## 3. 문서군별 골든 근거 적중\n")
    out.append("| arm | exchange | major | holding | periodic |")
    out.append("|---|---|---|---|---|")
    for arm, report in reports.items():
        cells = [f'{report["totals"]["by_group"][g]["evidence_hit"]}'
                 f'/{report["totals"]["by_group"][g]["golden"]}'
                 for g in ("exchange", "major", "holding", "periodic")]
        out.append(f'| **{arm}** | ' + " | ".join(cells) + " |")

    out.append("\n## 4. Δ — 기준선 대비\n")
    for diff in diffs:
        out.append(f'\n### {diff["arm"]} vs {diff["base"]}\n')
        deltas = ", ".join(f"{k} {v:+d}" for k, v in diff["delta"].items() if v) or "**전부 0**"
        out.append(f"총계 Δ: {deltas}\n")
        if not diff["changed_questions"]:
            out.append("문항 단위 변화 **0건**. 후보 순위·근거 구성이 글자 하나 안 바뀌었다.\n")
            continue
        out.append("| 문항 | 난이도 | 문서군 | 변화 | 골든 순위 (전 -> 후) |")
        out.append("|---|---|---|---|---|")
        for row in diff["changed_questions"]:
            delta = ", ".join(f"{k} {v:+d}" for k, v in row["delta"].items()) or "순위만"
            out.append(f'| {row["question_id"]} | {row["difficulty"]} | {row["doc_group"]} '
                       f'| {delta} | `{row["rank_before"]}` -> `{row["rank_after"]}` |')
    return "\n".join(out) + "\n"


# --- 집계 ---------------------------------------------------------------------

AXES = {"difficulty": ("하", "중", "상"),
        "answer_mode": ("closed", "open"),
        "task_type": ("search_extract", "multi_compare", "complex_reason")}
AXIS_LABELS = {"difficulty": "난이도", "answer_mode": "답변형식", "task_type": "과제유형"}

#: 🔴 v1 에서 세 난이도가 전부 1~2/2 로 진동했다. 셀이 커진 v2 에서 **다시 확인**하고,
#: 여전하면 이 행의 Δ 는 읽지 않는다.
BLIND_CELL = "multi_compare|closed"


def _exact_rate(cell: dict) -> float | None:
    """🔴 **정밀도 규칙은 이 함수 하나다.** `correct / n_scored` 를 반올림 없이 돌려준다.

    `report.json` 의 `rate` 는 `ea._rate()` 가 소수 셋째 자리로 반올림한 값이다. 그 값을
    서로 빼면 **이중 반올림**이 생긴다. 실측 피해 셋:

      · A1b→A4  46/72 → 59/72 = **+18.06%p** 인데 0.819 − 0.639 = **+18.0%p**
      · measured 36 낙폭  15/18 − 33/36 = **−8.333** 인데 0.833 − 0.917 = **−8.4**
        (파생: 여유 1.667 → 1.6%p · 0.60 → 0.58문항)
      · `ablation_report.py` 의 v1 표 전량 (같은 기전, 세 번째 서식지)

    **비율의 차·비율의 비를 계산하는 모든 지점은 이 함수를 통과한다.** 예외는 값을
    그대로 찍는 표시(`_frac`)뿐이고, 그마저도 `_pct(_exact_rate(...))` 를 쓰는 편이 낫다.
    `scripts/ablation_report.py` 는 이 함수를 import 해서 쓴다 — 규칙을 두 번 적지 않는다.
    """
    n = cell.get("n_scored") or 0
    correct = cell.get("correct")
    return correct / n if n and correct is not None else None


def summarise(reports: dict[str, dict]) -> dict:
    """arm 별 정답률·비용과 축별 분해를 한 표로 모은다.

    🔴 정답률·히스토그램은 **그리드 기준**을 쓴다. 회귀 10·밸류체인 12는
    `answerable=true` 이고 3축 라벨도 채워져 있어 전체 기준으로 세면 분모에 섞여 들고,
    그러면 v1 42문항과 나란히 읽을 축이 하나도 안 남는다. 트랙별 수치는 `tracks` 에
    **따로** 담는다 — 합치지 않는다.
    """
    out: dict = {"arms": {}, "axes": {a: {} for a in AXES}, "cells": {}}
    for arm, report in reports.items():
        cross = report["crosstab"]
        grid = cross["grid_overall"]
        out["arms"][arm] = {
            # 🔴 `rate` 는 반올림 없는 값이다 (`_exact_rate`). Δ 를 여기서 뺀다.
            "correct": grid["correct"], "n_scored": grid["n_scored"],
            "rate": _exact_rate(grid), "rate_reported": grid["rate"],
            "n_grid": grid["n"], "data_limit": grid["data_limit"],
            "overall": cross["overall"]["rate"],
            "overall_correct": cross["overall"]["correct"],
            "overall_n_scored": cross["overall"]["n_scored"],
            "info_limit_correct": cross["info_limit"]["correct"],
            "info_limit_n": cross["info_limit"]["n"],
            "hallucination": cross["info_limit"]["stages"].get("HALLUCINATION", 0),
            "tracks": cross.get("by_track", {}),
            "tokens": report["totals"]["total_tokens"],
            "arm_cost_won": report["totals"]["cost_won"],
            "llm_calls": report["totals"]["llm_calls"],
            # 🔴 **이름을 갈았다.** `latency_s_mean` 은 측정값이 아니라 **재생 시간이 섞인
            #    값**이다 — 응답 파일에 저장된 벽시계 시간의 평균이고, 그 문항을 만들 때
            #    요청 캐시가 적중했다면 파이프라인이 ~1ms 에 끝나 그 수가 저장된다.
            #    실측: A0 3.73초(88 중 28건만 <0.5s) vs A5·A8 **0.02~0.03초**(88/88 적중).
            #    기울기가 **언제나 빠르게 보이는 한 방향**이라 기술제안서가 "평균 응답
            #    시간" 으로 뽑으면 두 자릿수 배로 틀린다. 옛 이름을 지운 것은 조용히
            #    잘못 읽히는 것보다 **KeyError 로 시끄럽게 죽는 편이 낫기** 때문이다.
            #    행 단위 구분은 `run.json` 의 `cached`/`request_cache_hits` 에 있다
            #    (2026-08-11 이후 실행분). 진짜 지연을 재려면 캐시 없이 다시 재야 한다.
            "latency_s_mean_recorded": report["totals"]["latency_s_mean"],
            "latency_s_note": ("응답 파일에 저장된 벽시계 시간의 평균 — 요청 캐시 적중 "
                               "문항은 ~0초로 기록되므로 **재생 시간이 섞인다.** "
                               "'평균 응답 시간' 으로 인용하지 않는다."),
            # 🔴 `_grid` 변종을 쓴다 (Task 7 이 만들어 둔 것). 전체 기준을 쓰면
            #    같은 표 안에서 분모가 둘이 된다.
            "failures": report.get("failure_histogram_grid") or report["failure_histogram"],
            "failures_all": report["failure_histogram"],
            "root_causes": (report.get("root_cause_histogram_grid")
                            or report["root_cause_histogram"]),
            "citation_rate": report.get("citation_rate_grid") or report["citation_rate"],
            "flags": report.get("arm_config") or {},
            "golden_evidence_all": sum(1 for r in ea.grid_rows(report["rows"])
                                       if r["golden_evidence_all"]),
            "golden_evidence_hit": sum(len(r["golden_in_evidence"])
                                       for r in ea.grid_rows(report["rows"])),
            "golden_total": sum(len(r["golden"]) for r in ea.grid_rows(report["rows"])),
        }
        for axis, values in AXES.items():
            for value in values:
                cell = cross["marginals"][axis][value]
                out["axes"][axis].setdefault(value, {})[arm] = {
                    "correct": cell["correct"], "n": cell["n_scored"],
                    "rate": _exact_rate(cell)}
        for level in ("하", "중", "상"):
            cell = cross["cells"][f"{BLIND_CELL}|{level}"]
            out["cells"].setdefault(level, {})[arm] = {
                "correct": cell["correct"], "n": cell["n_scored"]}
    return out


def _delta(a: dict | None, b: dict | None) -> float | None:
    """두 셀의 Δ(%p). `summarise` 가 넣어 준 **반올림 없는** rate 를 쓴다 (`_exact_rate`)."""
    if not a or not b or a["rate"] is None or b["rate"] is None:
        return None
    return round((b["rate"] - a["rate"]) * 100, 1)


def render_summary(summary: dict, meta: dict) -> str:
    ladder = [a for a in ablation.LADDER if a in summary["arms"]]
    loo = [a for a in ablation.LOO_REMOVED if a in summary["arms"]]
    base_name = ladder[-1] if ladder else ""
    out = [f'# Phase I — Ablation ({meta.get("cohort") or "dev"} '
           f'{meta.get("n_cases", 0)}문항)\n',
           f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 (`scripts/ablation.py`)\n',
           f'단가 {ea.PRICE_WON_PER_1K}원/1K · 실지출 합계 **{meta["spent_won"]:,.1f}원** '
           f'/ 상한 {meta["budget_won"]:,.0f}원 · 요청 캐시 적중 {meta["cache_hits"]}회\n',
           f'지출 출처: {meta.get("spent_won_source", "—")}\n']

    # 🔴 진단 arm 은 **성적이 아니다.** 산출물만 보는 사람이 숫자를 제출 성적으로 읽는 것을
    #    막는 유일한 수단이 이 줄이다 — 표 안에서는 다른 arm 과 똑같이 생겼다.
    diagnostic = [a for a in ablation.DIAGNOSTIC_ARMS if a in summary["arms"]]
    if diagnostic:
        out.append(f'> 🔴 **{", ".join(diagnostic)} 는 진단 전용이다.** 평가 시점에 없는 '
                   "정보(골든 근거)를 쓰므로 제출 구성이 될 수 없고, 그 정답률을 "
                   "성적으로 인용하면 자기 성적표를 부풀리는 것이 된다. 읽는 법은 "
                   "§2b 의 Δ 하나뿐이다.\n")

    out.append("\n## 1. 누적 곡선 — 정답률과 Δ (**그리드 기준**)\n")
    out.append("| arm | 정답 / 채점 | 정답률 | Δ(직전) | 골든 전건적중 | LLM 호출 | 토큰 | arm 비용(원) |")
    out.append("|---|---|---|---|---|---|---|---|")
    previous = None
    for arm in ladder:
        row = summary["arms"][arm]
        # 🔴 밸류체인 코호트는 그리드 문항이 0건이라 `rate` 가 None 이다. 여기서 죽으면
        #    A8 의 진짜 겨냥점을 담은 실행이 **집계 단계에서 통째로 날아간다** —
        #    응답은 디스크에 남았는데 표가 안 나오는 것이 가장 헷갈리는 실패다.
        out.append(f'| **{arm}** | {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                   f'| {_pp(previous, row["rate"])} '
                   f'| {row["golden_evidence_all"]}/{row["n_grid"]} '
                   f'| {row["llm_calls"]} | {row["tokens"]:,} | {row["arm_cost_won"]:,.1f} |')
        previous = row["rate"]

    # 🔴 사다리 arm 없이 LOO 만 돌린 실행 (`--arm L4` 등). 계획서가 "arm 하나씩 돌려라" 라고
    #    지시하므로 **정상 경로**다. 위 표가 비면 실행이 통째로 안 보이므로 무엇이 돌았는지
    #    적는다 — 1057행 주석과 같은 이유다.
    if not ladder:
        present = sorted(summary["arms"])
        out.append(f'| — | — | — | — | — | — | — | — |')
        out.append(f'\n> 🔴 **이 실행에는 사다리 arm 이 없다** (`{", ".join(present)}`). '
                   "누적 곡선은 사다리 arm 을 함께 돌려야 나온다 — "
                   "`--report` 로 세대 전체를 다시 렌더하면 채워진다.\n")
        for arm in present:
            row = summary["arms"][arm]
            out.append(f'| **{arm}** | {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                       f'| — | {row["golden_evidence_all"]}/{row["n_grid"]} '
                       f'| {row["llm_calls"]} | {row["tokens"]:,} | {row["arm_cost_won"]:,.1f} |')

    if loo and base_name:
        base = summary["arms"][base_name]
        out.append(f"\n## 2. Leave-one-out — 완성 구성({base_name})에서의 필수성\n")
        out.append(f'| 구성 | 뺀 것 | 정답 / 채점 | 정답률 | Δ({base_name} 대비) |')
        out.append("|---|---|---|---|---|")
        out.append(f'| {base_name} | — | {base["correct"]}/{base["n_scored"]} '
                   f'| {_pct(base["rate"])} | — |')
        for arm in loo:
            row = summary["arms"][arm]
            out.append(f'| {arm} | {ablation.LOO_REMOVED[arm]} '
                       f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                       f'| {_pp(base["rate"], row["rate"])} |')

    # 🔴 진단 arm 은 사다리에도 LOO 에도 없으므로 위 두 표에 **안 찍힌다.** 자리를 안 주면
    #    경고만 뜨고 잰 값은 산출물 어디에도 안 남아, 다음 사람이 report.json 을 직접
    #    열어야 한다. 여기 한 칸을 주되 **경고를 표 안에 같이 적는다.**
    if diagnostic:
        out.append("\n## 2b. 진단 arm — **성적이 아니다**\n")
        out.append("| arm | 재는 것 | 정답 / 채점 | 정답률 | Δ(A8 대비) | 골든 전건적중 |")
        out.append("|---|---|---|---|---|---|")
        base = summary["arms"].get("A8")
        for arm in diagnostic:
            row = summary["arms"][arm]
            out.append(f'| {arm} | 검색을 골든으로 고정한 **생성 능력** '
                       f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                       f'| {_pp(base["rate"] if base else None, row["rate"])} '
                       f'| {row["golden_evidence_all"]}/{row["n_grid"]} |')
        out.append("\n> 🔴 평가 시점에 골든이 없다. 이 줄의 정답률은 **제출 성적이 아니라 "
                   "진단값**이다. `Δ = 검색이 아직 못 가져온 몫` 이고, 잔여 오답은 "
                   "`순수 생성 실패` 로 읽는다.\n")

    out.append("\n## 3. 축별 Δ 분해\n")
    # 🔴 `ladder[0]`·`base_name` 을 무방비로 쓰면 LOO 단독 실행에서 IndexError 로 죽는다 —
    #    응답과 judge 는 디스크에 다 남았는데 요약만 안 나오는, 가장 헷갈리는 실패다.
    #    실측으로 겪었다 (2026-08-12 · `--arm L4` 단독 실행).
    if not ladder:
        out.append("사다리 arm 이 없어 축별 Δ 를 낼 수 없다. "
                   "`--report` 로 세대 전체를 렌더하면 나온다.\n")
        return "\n".join(out) + "\n"
    for axis, table in summary["axes"].items():
        out.append(f'\n### {AXIS_LABELS[axis]}\n')
        out.append("| 값 | " + " | ".join(ladder) + f' | Δ {ladder[0]}→{base_name} |')
        out.append("|---|" + "---|" * (len(ladder) + 1))
        for value, per_arm in table.items():
            cells = []
            for arm in ladder:
                cell = per_arm.get(arm)
                cells.append(f'{cell["correct"]}/{cell["n"]}' if cell else "—")
            span = _delta(per_arm.get(ladder[0]), per_arm.get(base_name))
            out.append(f'| {value} | ' + " | ".join(cells)
                       + f' | {"—" if span is None else f"{span:+.1f}%p"} |')

    out.append("\n## 4. 트랙별 (**합치지 않는다**)\n")
    tracks = [t for t in (ea.GRID_TRACK,) + ea.OFF_GRID_TRACKS
              if any(t in summary["arms"][a]["tracks"] for a in summary["arms"])]
    if tracks:
        out.append("| arm | " + " | ".join(tracks) + " |")
        out.append("|---|" + "---|" * len(tracks))
        for arm in ladder + loo:
            cells = []
            for track in tracks:
                cell = summary["arms"][arm]["tracks"].get(track)
                cells.append(f'{cell["correct"]}/{cell["n_scored"]}' if cell else "—")
            out.append(f"| {arm} | " + " | ".join(cells) + " |")

    out.append("\n## 5. 실패 단계 분포 (**그리드 기준**)\n")
    out.append("| arm | " + " | ".join(ea.ALL_LABELS) + " |")
    out.append("|---|" + "---|" * len(ea.ALL_LABELS))
    for arm in ladder + loo:
        hist = summary["arms"][arm]["failures"]
        out.append(f"| {arm} | " + " | ".join(str(hist.get(s, 0)) for s in ea.ALL_LABELS) + " |")
    return "\n".join(out) + "\n"


# --- 게이트 I 보고서 (LLM 0회) --------------------------------------------------
# 🔴 `scripts/ablation_report.py` 를 고치지 않는다 — 그 스크립트는 v1 `gate_I.md` 를
# **재생성**하는 유일한 수단이고, 출력 경로가 v1 자리에 박혀 있다. v2 는 여기 붙인다.

#: 부록 D 의 논문 보고값. 우리 Δ 가 타당한 크기인지 판정하는 기준이다 (D-1 · D-2).
PAPER_EFFECTS = (
    ("A0", "메타 프리필터", "우리 실측 Q-001", "14/1,332 → 1/1", "—"),
    ("A1", "BM25", "G-Retriever w/o retrieval", "-9.4%", "제거"),
    ("A1b", "인덱스 커버리지", "ToG-2 vs Vanilla RAG (ToG-FinQA)", "0% → 34.0%", "조건 비교"),
    ("A4", "Agent 루프", "ToG-2 → ToG-2 (AdvHotpotQA)", "+16.6%", "추가"),
    ("A4", "Agent 루프", "HippoRAG + IRCoT (2Wiki R@5)", "+18%", "추가"),
    ("A5", "계산 Tool", "— (해당 논문 없음, policy §4)", "—", "—"),
    ("A6", "Verifier", "Self-Consistency (GSM8K)", "+17.9%", "추가"),
    ("A7", "거절 강제 (플래그 없음)", "— (§I-5d 가 42문항으로 이미 쟀다)", "—", "—"),
    ("A8", "발주처 2-hop", "HippoRAG PPR 이웃까지 (2Wiki R@2) 59.2 → 70.7", "+11.5", "대체"),
    ("L1", "체인 hop 제거", "Relink w/o query-driven ranker", "-19.4% EM", "제거"),
    ("L2", "major·holding 필드 제거", "G-Retriever w/o textualized graph", "-19.2%", "제거"),
    ("L3", "발주처 hop 제거", "Relink w/o 명시 그래프 (Gb)", "-12.9% EM", "제거"),
)

#: 🔴 A8 의 성공 기준은 전체 정답률이 아니다. 밸류체인 12문항을 세 갈래로 가른다 —
#: ①에서 안 오르면 A8 은 실패이고, ③에서 떨어지면 Δ 가 양수여도 넣지 않는다.
VC_FAN_IN = ("Q-110", "Q-114", "Q-117")       # 완전 열거 — A8 의 진짜 겨냥점
VC_UNVERIFIABLE = ("Q-116",)                  # 완결성 확인 불가


def _load_report(root: Path, arm: str) -> dict | None:
    path = root / arm / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _pp(before, after) -> str:
    if before is None or after is None:
        return "—"
    return f"{(after - before) * 100:+.1f}%p"


def _pp_of(value) -> str:
    """이미 %p 단위인 값. `None` 을 `0.0` 으로 찍지 않는다 — 낙폭 0 과 미측정은 다르다."""
    return "—" if value is None else f"{value:+.1f}%p"


def _frac(cell: dict | None) -> str:
    cell = cell or {}
    if cell.get("n_scored") is None:
        return "—"
    return f'{cell.get("correct")}/{cell.get("n_scored")} ({_pct(cell.get("rate"))})'


def spend_from_runs(root: Path, recursive: bool = False) -> dict:
    """이 폴더의 arm 들이 실제로 결제한 몫. `run.json` 의 `spent_won` 합이다.

    요청 캐시 파일 전체 합(`ablation_report.spend_from_cache`)을 쓰면 v1·T7 이 산 것까지
    더해져 **v2 의 지출이 부풀어 오른다.** v2 는 자기 실행분만 센다.

    `recursive=True` 는 하위 코호트 폴더(`v2/vc/` 등)까지 센다 — **총지출을 말할 때는
    이쪽이다.** 기본값이 얕은 이유는 §1 의 표가 직속 arm 과 코호트 arm 을 따로 내기
    때문이고, 둘을 한 번에 세면 그 표가 이중계상된다.

    🔴 **결함은 쓰기가 아니라 읽기였다** (재검토 B I1). `_write_run` 이 이전 세대를
    `spent_won_previous` 로 보존하는데 여기서 그것을 안 읽었다. arm 을 두 번 돌리면
    첫 실행의 결제가 회계에서 사라졌다 — 실측 9파일 합 **4,627.7원**.

    그래서 두 축을 낸다. `won_current` 는 이번 세대만, `won_cumulative` 는 승계분까지다.
    **"실제 결제" 를 말할 때는 누계**이고, 세대별 비교를 할 때만 현세대를 쓴다.
    """
    total, per_arm = 0.0, {}
    carried, per_arm_cum = 0.0, {}
    paths = root.rglob("*/run.json") if recursive else root.glob("*/run.json")
    for path in sorted(paths):
        run = json.loads(path.read_text(encoding="utf-8"))
        key = str(path.parent.relative_to(root)).replace("\\", "/")
        now = run.get("spent_won", 0.0) or 0.0
        prev = run.get("spent_won_previous", 0.0) or 0.0
        per_arm[key] = now
        per_arm_cum[key] = now + prev
        total += now
        carried += prev
    return {"won": round(total, 1), "per_arm": per_arm,
            "won_current": round(total, 1),
            "won_carried": round(carried, 1),
            "won_cumulative": round(total + carried, 1),
            "per_arm_cumulative": per_arm_cum,
            "note": ("`won` 은 이번 세대만이다 — 실제 결제 총액은 `won_cumulative` 다. "
                     "arm 을 다시 돌리면 이전 결제가 `spent_won_previous` 로 밀려나고, "
                     "🔴 슬롯이 하나뿐이라 **3세대째에는 첫 세대가 흔적 없이 사라진다.** "
                     "그리고 `run.json` 이 없는 지출(A2 임베딩 113.4원 · A7 표적 80.3원)은 "
                     "어떤 규칙으로도 여기 안 잡힌다 — 이 수는 하한이다.")}


def version_dirs() -> list[Path]:
    """v1 폴더 **안에** 들어앉은 판 폴더들 (`artifacts/ablation/v2` 등)."""
    v1 = ROOT / "artifacts" / "ablation"
    return sorted(p.parent for p in v1.glob("*/summary.json")) if v1.exists() else []


def assert_not_v1_root(out_dir: Path, what: str) -> None:
    """🔴 v2 가 존재하는 동안 **v1 자리에 쓰지 않는다.** 경고가 아니라 거부다.

    `artifacts/ablation/v2/` 는 `artifacts/ablation/` **안**에 있다. 그래서 `--out` 없이
    돌리는 재집계·`--report` 는 세 가지를 조용히 망친다:

      ① `_spend_meta` 가 `spend_from_runs(recursive=True)` 로 세므로 v1 `summary.json` 의
         지출이 **3,144.7 → 8,211.3원**(= v1 + v2 5,066.6)이 된다.
      ② v1 arm 의 `report.json` 이 **현재 questions.jsonl(88문항) 기준으로 재작성**된다.
         v1 은 42문항 산출물이고 §12·§12b 의 비교 기준선이다 — `n_cases` 가 42 → 88 로
         바뀌고 `generated` 가 갱신돼 기준선의 출처 추적이 끊긴다.
      ③ `--report` 는 v2 렌더러(`render_gate`)로 **v1 `gate_I.md` 를 덮는다.**
         그 파일은 `scripts/ablation_report.py` 가 만드는 다른 문서다.

    거부하는 이유: `artifacts/` 는 gitignore 라 v1 산출물에 **백업이 없다.** 되돌릴 수
    없는 덮어쓰기는 프로세스가 아니라 코드로 막는다.
    """
    if out_dir.resolve() != (ROOT / "artifacts" / "ablation").resolve():
        return
    nested = version_dirs()
    if not nested:
        return                      # v2 가 없으면 여기가 그냥 그 판의 자리다
    names = ", ".join(p.name for p in nested)
    raise SystemExit(
        f"🔴 v1 자리({out_dir.relative_to(ROOT)})에 {what} 를 쓰려고 합니다 — "
        f"그 폴더는 판 폴더 [{names}] 를 하위에 품고 있습니다.\n"
        "   · 지출이 하위 판까지 합산됩니다 (v1 3,144.7 → 8,211.3원).\n"
        "   · v1 의 42문항 report.json 이 현재 88문항 기준으로 재작성돼 §12 기준선이 "
        "끊깁니다.\n"
        "   · `--report` 는 v2 렌더러로 v1 `gate_I.md`(ablation_report.py 산출물)를 "
        "덮습니다.\n"
        f"   👉 `--out artifacts/ablation/{nested[-1].name}` 을 붙이세요. "
        "v1 `gate_I.md` 를 다시 만들려면 `python scripts/ablation_report.py` 입니다.")


def _grid_rate(rows: list[dict], wanted: set[str] | None = None) -> dict:
    sub = [r for r in ea.grid_rows(rows) if wanted is None or r["question_id"] in wanted]
    return ea._rate(sub)


def resolve_budget_cap(meta: dict, cli_cap: float) -> tuple[float, str, str]:
    """🔴 상한은 **산출물에서 읽는다.** `(상한, 출처, 경고)` 를 돌려준다.

    `budget_cap` 이 CLI `--budget` 에서만 오면 `--report` 를 플래그 없이 돌렸을 때 **같은
    산출물에서 다른 문장**이 나온다: `상한 20,000원 대비 25.3% (정지선 16,000)` ↔
    `상한 12,000원 대비 42.2% (정지선 9,600)`. 게이트가 스스로 "LLM 0회 재생성 가능" 이라
    적어 두었으므로, 재생성에 인자를 기억해야 한다면 그 문장이 절반만 사실이 된다.
    `summary.json` 의 `budget_won` 이 그 실행이 실제로 쓴 상한이니 그쪽이 권위값이고,
    CLI 값은 산출물에 상한이 없을 때의 폴백이다. 둘이 다르면 **다르다고 적는다.**
    """
    from_meta = meta.get("budget_won")
    if not from_meta:
        return cli_cap, "CLI `--budget` (산출물에 `budget_won` 이 없다)", ""
    note = ""
    if abs(float(from_meta) - cli_cap) > 1e-9:
        note = (f'🔴 `--budget {cli_cap:,.0f}` 을 받았지만 산출물 `summary.json` 의 상한은 '
                f'{float(from_meta):,.0f}원이다 — **산출물 값을 쓴다** '
                "(이 문서는 산출물만으로 재현돼야 한다).")
    return float(from_meta), "summary.json `meta.budget_won`", note


def render_gate(root: Path, budget_cap: float) -> str:
    """게이트 I 5항목(heldout 제외)을 표로 낸다. **LLM 0회.**"""
    reports = {a: _load_report(root, a) for a in ablation.RUN_ORDER}
    reports = {k: v for k, v in reports.items() if v}
    ladder = [a for a in ablation.LADDER if a in reports]
    loo = [a for a in ablation.LOO_REMOVED if a in reports]
    # 🔴 진단 arm 은 사다리에도 LOO 에도 없다 (`ablation.DIAGNOSTIC_ARMS` 머리말). 그래서
    #    이 목록을 따로 들지 않으면 **게이트 문서에 한 글자도 안 나온다** — 실제로 `ORACLE`
    #    1,747원을 쓰고도 gate_I.md 어디에도 없었다. 더 나쁜 것은 §1 의 "캐시가 막은 금액"
    #    이다: 실지출(재귀 합)에는 그 돈이 들어가는데 arm 비용 합계에는 안 들어가서
    #    차액이 그만큼 작게 찍힌다. **비용 표에는 넣고, 성적 표(누적 곡선·LOO)에는 안 넣는다.**
    diag = [a for a in ablation.DIAGNOSTIC_ARMS if a in reports]
    full = ladder[-1] if ladder else ""
    summary = summarise(reports)
    arms = summary["arms"]
    spend = spend_from_runs(root, recursive=True)
    meta_path = root / "summary.json"
    meta = (json.loads(meta_path.read_text(encoding="utf-8"))["meta"]
            if meta_path.exists() else {})
    budget_cap, budget_source, budget_note = resolve_budget_cap(meta, budget_cap)
    vc = {a: _load_report(root / "vc", a)
          for a in ("A8", "L3", "A7") + ablation.DIAGNOSTIC_ARMS}
    vc = {k: v for k, v in vc.items() if v}
    vc_diag = [a for a in ablation.DIAGNOSTIC_ARMS if a in vc]

    o: list[str] = []
    o.append("# Phase I — Ablation v2 결과 (게이트 I)\n")
    o.append(f'생성 {time.strftime("%Y-%m-%d %H:%M")} · 자동 생성물 '
             "(`scripts/ablation.py --report`) · **LLM 0회 재생성 가능**\n")
    # 코호트 설명을 문자열로 박아 두면 `--cohort valuechain` 에 `--report` 를 돌렸을 때
    # 틀린 설명이 찍힌다. 코호트 이름에서 끌어낸다.
    cohort = meta.get("cohort", "full")
    cohort_note = {"full": "dev 78 + 회귀 10", "measured": "v1 이 실제로 잰 dev 42",
                   "valuechain": "밸류체인 트랙 (그리드 밖)"}.get(cohort, "")
    o.append(f'실측 단가 {ea.PRICE_WON_PER_1K}원/1K · 코호트 `{cohort}` '
             f'**{meta.get("n_cases", 0)}문항**'
             + (f' ({cohort_note})' if cohort_note else "")
             + ' · 모델 HCX-005 · 온도 0.0\n')
    # 🔴 heldout 은 **최종 1회**라 이 문서가 두 상태를 가진다. 문구를 하드코딩하면
    #    측정 후에도 "아직 안 쟀다" 가 찍히거나 그 반대가 된다 — 원장 파일로 가른다.
    heldout_ledger = root / "heldout" / HELDOUT_LEDGER
    held = (json.loads(heldout_ledger.read_text(encoding="utf-8"))
            if heldout_ledger.exists() else None)
    if held:
        o.append(f'🔴 **heldout 낙폭 {_pp_of(held["gap_pp"])}** — §14. '
                 f'heldout 18문항을 **{held["use"]}회째** 열었고 최종 구성 '
                 f'`{held["arm"]}` 1회다.\n')
    else:
        o.append("🔴 **heldout 낙폭은 이 문서에 없다 — Task 10 몫이다.** "
                 "여기서 `evalset/heldout.jsonl` 을 열지 않았다.\n")

    # --- 0. arm 구성 확인 — 무엇이 실제로 켜졌는가 ---
    o.append("\n## 0. arm 구성 — 켠 것이 실제로 켜졌는가\n")
    o.append("Δ 를 읽기 전에 스위치를 확인한다. **끈 줄 알았는데 안 꺼진 것**이 "
             "가장 비싼 실패다 (Δ 가 0 으로 나오고 우리는 그것을 '기여 없음' 으로 읽는다).\n")
    flags = [f for f in ablation.FLAG_NAMES if f != "dense"]
    o.append("| arm | " + " | ".join(f"`{f}`" for f in flags) + " |")
    o.append("|---|" + "---|" * len(flags))
    for arm in ladder + loo + diag:
        cells = []
        for f in flags:
            value = arms[arm]["flags"].get(f)
            cells.append(str(value) if isinstance(value, str)
                         else ("✓" if value else "✗"))
        o.append(f"| **{arm}**{' 🔬' if arm in diag else ''} | "
                 + " | ".join(cells) + " |")
    if diag:
        o.append(f'\n🔬 = 진단 arm (`{"`·`".join(diag)}`). **성적이 아니다** — 평가 시점에 '
                 "없는 정보(골든 근거)를 쓰므로 제출 구성이 될 수 없다. 스위치 표에만 "
                 "싣고 누적 곡선·LOO 표에는 넣지 않는다 (`ablation.DIAGNOSTIC_ARMS`).\n")
    o.append("\n🔴 **`A7` 은 플래그가 A6 과 같다** — 거절 강제·시점 지정은 무조건 실행되는 "
             "코드이고(`router.missing_named_report` · `pipeline._as_of_ym`) 플래그로 끌 수 "
             "없다. 그래서 사다리의 `Δ(A6→A7)` 은 **측정값이 아니라 정의상 0** 이다. "
             "A7 의 실효는 §I-5d 가 42문항 코호트로 이미 쟀다 (grid +2.8%p · 환각 1→0).\n")
    o.append("🔴 **`L3` 은 플래그가 A7 과 같다** (`A8 − counterparty_hop = A7`). "
             "두 수는 부호만 다른 같은 값이어야 하고, 아니면 어딘가 틀렸다.\n")

    # --- 1. 비용 ---
    o.append("\n## 1. arm 별 비용 실측\n")
    o.append("`arm 비용` = 그 arm 을 혼자 돌렸을 때의 실측 usage 합 (캐시 적중 포함). "
             "`신규 지출` = 요청 캐시가 못 막아 **이번에 결제된** 몫.\n")
    o.append("| arm | LLM 호출 | prompt | completion | judge | 총 토큰 | arm 비용(원) | 신규 지출(원) |")
    o.append("|---|---|---|---|---|---|---|---|")
    arm_sum = 0.0
    for arm in ladder + loo + diag:
        t = reports[arm]["totals"]
        arm_sum += t["cost_won"]
        fresh = spend["per_arm"].get(arm)
        o.append(f'| {arm}{" 🔬" if arm in diag else ""} | {t["llm_calls"]} '
                 f'| {t["prompt_tokens"]:,} '
                 f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                 f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} '
                 f'| {"—" if fresh is None else f"{fresh:,.1f}"} |')
    vc_runs = (spend_from_runs(root / "vc")["per_arm"]
               if (root / "vc").exists() else {})
    for arm, report in vc.items():
        t = report["totals"]
        fresh = vc_runs.get(arm)
        o.append(f'| {arm}{" 🔬" if arm in vc_diag else ""}/밸류체인 (12문항) '
                 f'| {t["llm_calls"]} | {t["prompt_tokens"]:,} '
                 f'| {t["completion_tokens"]:,} | {t["judge_tokens"]:,} '
                 f'| {t["total_tokens"]:,} | {t["cost_won"]:,.1f} '
                 f'| {"—" if fresh is None else f"{fresh:,.1f}"} |')
        arm_sum += t["cost_won"]
    # 🔴 heldout 도 이 폴더 안에서 결제됐으므로 `spend["won"]`(재귀 합)에 이미 들어 있다.
    #    표에 행을 안 내면 `arm_sum` 만 빠져서 "캐시가 막은 금액" 이 그만큼 작게 찍힌다
    #    (heldout 은 캐시 적중 0 이라 막은 금액에 기여가 없어야 한다).
    held_report = _load_report(root / "heldout", f'{held["arm"]}_heldout_run') if held else None
    if held_report:
        t = held_report["totals"]
        o.append(f'| {held["arm"]}/heldout (18문항 · §14) | {t["llm_calls"]} '
                 f'| {t["prompt_tokens"]:,} | {t["completion_tokens"]:,} '
                 f'| {t["judge_tokens"]:,} | {t["total_tokens"]:,} '
                 f'| {t["cost_won"]:,.1f} | {held["spent_won"] or 0:,.1f} |')
        arm_sum += t["cost_won"]
    # `spend` 는 재귀 합이므로 밸류체인·heldout 몫이 이미 들어 있다 — 다시 더하면 이중계상이다.
    total_spend = spend["won"]
    o.append(f'| **합계 (캐시 없었다면)** | | | | | | **{arm_sum:,.0f}** | |')
    o.append(f'| **실제 결제 (하위 코호트 포함)** | | | | | | | **{total_spend:,.1f}** |')
    if held_report:
        o.append(f'\n🔴 **사다리 실행분과 heldout 을 갈라 읽는다.** 위 총액 '
                 f'{total_spend:,.1f}원 = 사다리·LOO·밸류체인 '
                 f'{total_spend - (held["spent_won"] or 0):,.1f}원 + heldout '
                 f'{held["spent_won"] or 0:,.1f}원(§14). 앞엣것이 Ablation v2 측정 비용이고, '
                 "뒤엣것은 **최종 1회** 몫이라 회계가 다르다.\n")
        # 🔴 `summary.json` 과 이 표가 맞는지 **여기서 검산한다.** 예전에는 "그 파일은
        #    heldout 이전 값이다" 를 문장으로 박아 뒀는데, 재집계로 그 파일이 갱신되면
        #    그 문장이 조용히 거짓이 된다. 문장을 값에서 만든다.
        meta_spend = meta.get("spent_won")
        if meta_spend is None:
            o.append('🔴 `summary.json` 에 `spent_won` 이 없다 — **총지출은 이 표의 '
                     f'{total_spend:,.1f}원**(arm 별 `run.json` 합)이 권위값이다.\n')
        elif abs(float(meta_spend) - total_spend) < 0.05:
            o.append(f'✅ **검산 일치** — `summary.json` 의 `spent_won` '
                     f'{float(meta_spend):,.1f}원 = 이 표의 총액 {total_spend:,.1f}원 '
                     "(둘 다 arm·코호트별 `run.json` 합이고 heldout 을 포함한다).\n")
        else:
            o.append(f'🔴 **`summary.json` 의 `spent_won`({float(meta_spend):,.1f})과 이 표의 '
                     f'총액({total_spend:,.1f})이 다르다** — 차이 '
                     f'{total_spend - float(meta_spend):+,.1f}원. 그 파일이 heldout 이전 '
                     "시점의 산출물이면 그 차이가 heldout 몫"
                     f'({held["spent_won"] or 0:,.1f}원)과 같아야 한다. **총지출은 이 표의 '
                     f'{total_spend:,.1f}원**이 권위값이다 (`run.json` 합).\n')
    o.append(f'\n상한 {budget_cap:,.0f}원 대비 **{total_spend / budget_cap * 100:.1f}%** '
             f'사용 (80% 정지선 {budget_cap * 0.8:,.0f}원 · 상한 출처 {budget_source}). '
             f'**캐시 전체가 {arm_sum - total_spend:,.0f}원을 막았다** — 요청 캐시만이 '
             "아니라 **응답 파일 캐시와 프롬프트 동일성까지 합친** 절감액이다 "
             "(§13 이 A6·A7·A8 의 프롬프트 동일성을 밝힌다).\n")
    if budget_note:
        o.append(budget_note + "\n")
    o.append(f'지출 출처: {meta.get("spent_won_source", "—")}. '
             f'요청 캐시 적중 {meta.get("cache_hits", "?")} / 미적중 '
             f'{meta.get("cache_misses", "?")}'
             + ("" if meta.get("cache_stats_are_this_process", True)
                else f' (🔴 이번 프로세스 값이 아니다 — '
                     f'{meta.get("cache_stats_carried_from", "이전 실행")} 에서 이어받았다)')
             + (f' — {meta["cache_stats_note"]}' if meta.get("cache_stats_note") else "")
             + "\n")
    if meta.get("cache_stats_inconsistent"):
        o.append(meta["cache_stats_inconsistent"] + "\n")
    # 🔴 부분 실행을 표에 드러낸다. `analyse` 는 응답 없는 문항을 조용히 건너뛰므로
    #    예산 중단된 arm 은 **더 작은 분모로** 표에 실린다 — 그러면 그 arm 의 정답률이
    #    다른 arm 과 다른 잣대가 되고, 표만 보면 그 사실이 안 보인다.
    partial = []
    for arm in ladder + loo + diag:
        rep = reports[arm]
        run_path = root / arm / "run.json"
        run = (json.loads(run_path.read_text(encoding="utf-8"))
               if run_path.exists() else {})
        if rep.get("n_analysed") != rep.get("n_cases") or run.get("stopped_at"):
            partial.append(f'   · **{arm}** — 분석 {rep.get("n_analysed")}'
                           f'/{rep.get("n_cases")}문항'
                           + (f' · 예산 중단 `{run["stopped_at"]}`'
                              if run.get("stopped_at") else ""))
    o.append("\n🔴 **부분 실행 검사**: "
             + ("전 arm 이 전수 분석됐다 (`n_analysed == n_cases`, 예산 중단 0건).\n"
                if not partial else
                "아래 arm 은 **분모가 다르다** — Δ 를 다른 arm 과 나란히 읽지 않는다.\n"
                + "\n".join(partial) + "\n"))

    # --- 2. 누적 곡선 ---
    n_scored = arms[full]["n_scored"] if full else 0
    o.append(f"\n## 2. 누적 곡선 — A0 → {full} (**그리드 기준**)\n")
    o.append(f'정답률은 **채점 대상 {n_scored}문항** 기준이다. 그리드 밖 트랙'
             f'(회귀 10 · 정보한계)은 §4 에 따로 낸다 — 섞으면 v1 42문항과 나란히 '
             "읽을 축이 하나도 안 남는다.\n")
    o.append("| arm | 정답 | 정답률 | Δ(직전) | 골든 근거 적중 | 전건 적중 문항 "
             "| arm 비용(원) | Δ정답률/Δ비용 |")
    o.append("|---|---|---|---|---|---|---|---|")
    prev_rate = prev_cost = None
    for arm in ladder:
        row = arms[arm]
        cost = row["arm_cost_won"]
        eff = "—"
        if prev_rate is not None and prev_cost is not None and cost != prev_cost:
            eff = f'{(row["rate"] - prev_rate) * 100 / (cost - prev_cost):+.3f}%p/원'
        o.append(f'| **{arm}** | {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                 f'| {_pp(prev_rate, row["rate"])} '
                 f'| {row["golden_evidence_hit"]}/{row["golden_total"]} '
                 f'| {row["golden_evidence_all"]}/{row["n_grid"]} | {cost:,.0f} | {eff} |')
        prev_rate, prev_cost = row["rate"], cost

    # --- 3. 축별 분해 ---
    o.append("\n## 3. 축별 Δ 분해 — 전체 Δ 하나만 보면 '왜 올랐는지' 가 사라진다\n")
    for axis, values in AXES.items():
        o.append(f'\n### {AXIS_LABELS[axis]}\n')
        o.append("| 값 | " + " | ".join(ladder)
                 + f' | Δ {ladder[0]}→{full} | 최대 단일 Δ |')
        o.append("|---|" + "---|" * (len(ladder) + 2))
        for value in values:
            cells, rates = [], []
            for arm in ladder:
                cell = summary["axes"][axis][value][arm]
                cells.append(f'{cell["correct"]}/{cell["n"]}')
                rates.append(cell["rate"])
            steps = [(rates[i + 1] - rates[i], ladder[i + 1])
                     for i in range(len(rates) - 1)
                     if rates[i] is not None and rates[i + 1] is not None]
            best = max(steps, key=lambda s: s[0]) if steps else None
            o.append(f'| {value} | ' + " | ".join(cells)
                     + f' | {_pp(rates[0], rates[-1])} | '
                     + (f'{best[1]} {best[0] * 100:+.1f}%p |' if best else "— |"))

    # 🔴 문서군 행 — 이번 최대 발견(A0→A1 뒤집힘)의 근거가 여기 있다. 게이트만 읽고도
    #    결론이 서야 하므로 `report.json` 에만 두지 않는다.
    groups = ("exchange", "major", "holding", "periodic")
    o.append("\n### 문서군 — **`A0→A1` 뒤집힘의 근거는 이 행이다**\n")
    o.append("3축(난이도·답변형식·과제유형)은 문서군을 안 보여 준다. `A1` 의 정의가 "
             "**exchange 인덱스 1,469건만 본다**는 것이므로, 그 arm 의 손실은 문서군 축에서만 "
             "드러난다.\n")
    o.append("| arm | " + " | ".join(groups) + " | ROUTING 실패 | RETRIEVAL 실패 |")
    o.append("|---|" + "---|" * (len(groups) + 2))
    for arm in ladder + loo:
        by_group = reports[arm].get("by_doc_group_grid") or {}
        cells = []
        for g in groups:
            cell = by_group.get(g)
            cells.append(f'{cell["correct"]}/{cell["n_scored"]}' if cell else "—")
        hist = arms[arm]["failures"]
        o.append(f'| **{arm}** | ' + " | ".join(cells)
                 + f' | {hist.get("ROUTING", 0)} | {hist.get("RETRIEVAL", 0)} |')
    a0g = (reports.get("A0") or {}).get("by_doc_group_grid") or {}
    a1g = (reports.get("A1") or {}).get("by_doc_group_grid") or {}
    if a0g and a1g:
        gained = a1g["exchange"]["correct"] - a0g["exchange"]["correct"]
        lost = sum(a0g[g]["correct"] - a1g[g]["correct"]
                   for g in ("major", "holding", "periodic"))
        o.append(f'\n**읽는 법.** `A0 → A1` 에서 exchange 는 **{gained:+d}건**이다 — '
                 "BM25 순위가 인덱스가 닿는 곳에서는 제 일을 한다. 그런데 나머지 세 문서군에서 "
                 f'**−{lost}건**을 잃는다. 순 **{gained - lost:+d}건**이 −8.3%p 의 전부다.\n')
        o.append("기전은 문항 구성이 아니라 **인덱스 커버리지**다. `A1` 의 `ROUTING` 실패 "
                 f'**{arms["A1"]["failures"].get("ROUTING", 0)}건**(A0 은 '
                 f'{arms["A0"]["failures"].get("ROUTING", 0)}건)은 라우터 버그가 아니다 — '
                 "arm 정의상 검색이 exchange 로 강제되므로 `parse_trace` 가 문서군 불일치로 "
                 "읽는다. 실체는 **major·holding·periodic 질의가 검색 단계에서 통째로 "
                 "떨어지는 것**이고, 그것이 곧 `A1b` 의 Δ 다.\n")

    o.append(f'\n### ⚠ 변별력 재확인 — `{BLIND_CELL}`\n')
    o.append("v1 에서 이 셀은 세 난이도가 전부 1~2/2 로 진동했다. **셀 n 이 늘어난 "
             "v2 에서 다시 확인한다** — n 이 2 에 머물러 있으면 이 행의 Δ 는 읽지 않는다.\n")
    o.append("| arm | 하 | 중 | 상 |")
    o.append("|---|---|---|---|")
    for arm in ladder:
        cells = [summary["cells"][d][arm] for d in ("하", "중", "상")]
        o.append(f'| {arm} | ' + " | ".join(f'{c["correct"]}/{c["n"]}' for c in cells) + " |")
    cell_n = {summary["cells"][d][full]["n"] for d in ("하", "중", "상")} if full else set()
    biggest = max(cell_n or {0})
    if biggest <= 2:
        verdict = "**여전히 2 이하다 — 이 행의 Δ 는 읽지 않는다.**"
    else:
        verdict = (f"v1 의 2 에서 **{biggest} 로 늘었다.** 다만 한 문항이 뒤집히면 "
                   f"{100 / biggest:.0f}%p 가 움직인다 — **부호(방향)만 읽고 크기는 "
                   f"읽지 않는다.** 이 셀 하나로 결론을 내지 않는다 (02-golden-set B-7).")
    o.append(f'\n셀 n = {sorted(cell_n)}. {verdict}\n')

    # --- 4. 트랙별 ---
    o.append("\n## 4. 트랙별 — **합치지 않는다**\n")
    o.append("회귀는 고친 실패의 감시이고 정보한계는 거절이 정답이다. 같은 분모에 "
             "넣으면 둘 다 거짓말이 된다.\n")
    tracks = [t for t in (ea.GRID_TRACK,) + ea.OFF_GRID_TRACKS
              if any(t in arms[a]["tracks"] for a in arms)]
    o.append("| arm | " + " | ".join(tracks) + " | 정보한계 거절정답 | 환각 |")
    o.append("|---|" + "---|" * (len(tracks) + 2))
    for arm in ladder + loo:
        cells = []
        for track in tracks:
            cell = arms[arm]["tracks"].get(track)
            cells.append(f'{cell["correct"]}/{cell["n_scored"]}' if cell else "—")
        o.append(f'| {arm} | ' + " | ".join(cells)
                 + f' | {arms[arm]["info_limit_correct"]}/{arms[arm]["info_limit_n"]} '
                 f'| {arms[arm]["hallucination"]} |')

    # --- 5. LOO ---
    if loo and full:
        base = arms[full]
        o.append(f"\n## 5. Leave-one-out **3구성** — 완성 구성({full})에서의 필수성\n")
        o.append(f'v1 은 2개였다. v2 는 발주처 hop 을 더해 3개다.\n')
        o.append(f'| 구성 | 뺀 것 | 정답 | 정답률 | Δ({full}) | 골든 근거 적중 |')
        o.append("|---|---|---|---|---|---|")
        o.append(f'| **{full}** | — | {base["correct"]}/{base["n_scored"]} '
                 f'| {_pct(base["rate"])} | — '
                 f'| {base["golden_evidence_hit"]}/{base["golden_total"]} |')
        for arm in loo:
            row = arms[arm]
            o.append(f'| {arm} | {ablation.LOO_REMOVED[arm]} '
                     f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                     f'| {_pp(base["rate"], row["rate"])} '
                     f'| {row["golden_evidence_hit"]}/{row["golden_total"]} |')
        o.append("\n### LOO 축별 분해\n")
        for axis, values in AXES.items():
            o.append(f'\n**{AXIS_LABELS[axis]}**\n')
            o.append(f'| 값 | {full} | ' + " | ".join(f"{a} | Δ{a}" for a in loo) + " |")
            o.append("|---|---|" + "---|" * (2 * len(loo)))
            for value in values:
                b = summary["axes"][axis][value][full]
                cells = [f'{b["correct"]}/{b["n"]}']
                for arm in loo:
                    r = summary["axes"][axis][value][arm]
                    cells += [f'{r["correct"]}/{r["n"]}', _pp(b["rate"], r["rate"])]
                o.append(f'| {value} | ' + " | ".join(cells) + " |")

    # --- 5b. 진단 arm — 사다리에도 LOO 에도 자리가 없다 ---
    # 🔴 성적 표와 **같은 표에 넣지 않는다.** 같은 표에 있으면 다음 사람이 그 줄을 성적으로
    #    인용한다. 그렇다고 안 실으면 잰 값이 게이트 문서에서 사라진다 — 자리를 주되
    #    경고를 표 안에 같이 쓴다 (`render_summary` §2b 와 같은 규칙).
    if diag and full:
        base = arms[full]
        o.append("\n## 5b. 진단 arm — **성적이 아니다**\n")
        o.append("| arm | 재는 것 | 정답 | 정답률 | Δ(A8) | 골든 근거 적중 | 전건 적중 |")
        o.append("|---|---|---|---|---|---|---|")
        for arm in diag:
            row = arms[arm]
            o.append(f'| {arm} | 검색을 골든으로 고정한 **생성 능력** '
                     f'| {row["correct"]}/{row["n_scored"]} | {_pct(row["rate"])} '
                     f'| {_pp(base["rate"], row["rate"])} '
                     f'| {row["golden_evidence_hit"]}/{row["golden_total"]} '
                     f'| {row["golden_evidence_all"]}/{row["n_grid"]} |')
        o.append("\n> 🔴 평가 시점에 골든 근거가 없다. 이 줄의 정답률은 **제출 성적이 "
                 "아니라 진단값**이다. 읽는 법은 둘뿐이다 — `Δ` 는 **검색이 아직 못 "
                 "가져온 몫**이고, 잔여 오답은 **순수 생성 실패**다.\n")
        o.append("| arm | " + " | ".join(ea.ALL_LABELS) + " |")
        o.append("|---|" + "---|" * len(ea.ALL_LABELS))
        for arm in [full] + diag:
            hist = arms[arm]["failures"]
            o.append(f'| {arm}{" 🔬" if arm in diag else ""} | '
                     + " | ".join(str(hist.get(s, 0)) for s in ea.ALL_LABELS) + " |")
        o.append("\n🔴 **이 두 줄을 나란히 읽는 것이 이 절의 전부다.** 골든 근거 적중이 "
                 "최대치인데도 `GENERATION` 칸이 안 줄면, 남은 실패는 검색 몫이 아니다.\n")

    # --- 6. A8 — 밸류체인 세 갈래 ---
    o.append("\n## 6. 🔴 A8 의 성공 기준은 정답률이 아니라 완결성이다\n")
    o.append("밸류체인 12문항 중 8건은 A8 없이도 근거가 닿는다 (BM25 자유텍스트). "
             "전체 정답률로만 재면 Δ 가 작게 나오고 오판한다. 세 갈래로 가른다.\n")
    o.append(f'**① 발주처 hop 발화 문항 (LLM 0회 실측)**: 88문항 그리드에서 **0건**, '
             f'밸류체인 12문항에서 **8건** (Q-107 Q-109 Q-110 Q-113 Q-114 Q-115 '
             f'Q-116 Q-118).\n')
    o.append("→ 그래서 **88문항 그리드에서 `Δ(A7→A8)` 은 정의상 0 이다** — 근거가 "
             "글자 하나 안 바뀌므로 프롬프트가 같고 답이 같다. A8 의 Δ 는 밸류체인 "
             "트랙에서만 읽을 수 있다.\n")
    if vc.get("A8"):
        groups = (("① 완전 열거 (A8 의 진짜 겨냥점)", set(VC_FAN_IN)),
                  ("② 완결성 확인 불가", set(VC_UNVERIFIABLE)),
                  ("③ 이미 닿는 나머지 (깨지면 순손실)",
                   {r["question_id"] for r in vc["A8"]["rows"]}
                   - set(VC_FAN_IN) - set(VC_UNVERIFIABLE)))
        names = [a for a in ("A8", "L3", "A7") if a in vc]
        o.append("\n### 밸류체인 12문항 — 세 갈래\n")
        o.append("| 갈래 | 문항 | " + " | ".join(names) + " |")
        o.append("|---|---|" + "---|" * len(names))
        for label, ids in groups:
            cells = []
            for name in names:
                rate = ea._rate([r for r in vc[name]["rows"]
                                 if r["question_id"] in ids])
                cells.append(f'{rate["correct"]}/{rate["n_scored"]}')
            o.append(f'| {label} | {" ".join(sorted(ids))} | ' + " | ".join(cells) + " |")
        o.append("\n### 문항별 (A8 vs L3 = 발주처 hop 없음)\n")
        o.append("🔴 `hop 발화` 칸을 먼저 본다. **발화하지 않은 문항에서 A8 의 Δ 는 정의상 "
                 "0** 이고, 그것은 hop 이 약한 것이 아니라 **라우터가 발주처를 못 알아본 "
                 "것**이다 — 처방이 정반대다 (hop 개선이 아니라 노드 이름 인식).\n")
        o.append("| 문항 | 갈래 | hop 발화 | A8 | L3 | 판정 |")
        o.append("|---|---|---|---|---|---|")
        l3 = {r["question_id"]: r for r in (vc.get("L3") or {}).get("rows", [])}
        for row in sorted(vc["A8"]["rows"], key=lambda r: r["question_id"]):
            qid = row["question_id"]
            other = l3.get(qid)
            fired = "strat=counterparty" in (row.get("think_trace") or "")
            branch = ("①열거" if qid in VC_FAN_IN else
                      "②확인불가" if qid in VC_UNVERIFIABLE else "③이미닿음")
            mark = {(True, True): "둘 다 정답", (False, False): "둘 다 오답",
                    (True, False): "**A8 이 벌었다**", (False, True): "🔴 **A8 이 잃었다**"}
            verdict = ("—" if other is None
                       else mark[(bool(row["correct"]), bool(other["correct"]))])
            if not fired and other is not None:
                verdict += " (hop 미발화 — Δ 는 정의상 0)"
            o.append(f'| {qid} | {branch} | {"✓" if fired else "**✗**"} '
                     f'| {"✓" if row["correct"] else "✗"} '
                     f'| {"—" if other is None else ("✓" if other["correct"] else "✗")} '
                     f'| {verdict} |')
        n_fired = sum(1 for r in vc["A8"]["rows"]
                      if "strat=counterparty" in (r.get("think_trace") or ""))
        no_fire = sorted(r["question_id"] for r in vc["A8"]["rows"]
                         if "strat=counterparty" not in (r.get("think_trace") or ""))
        o.append(f'\n밸류체인 12문항 중 hop 발화 **{n_fired}건** · 미발화 '
                 f'{len(no_fire)}건 ({" ".join(no_fire)}).\n')

    # --- 7. 실패 단계 · 근본 원인 ---
    o.append("\n## 7. 실패 단계 · 근본 원인 분포 (**그리드 기준**)\n")
    o.append("| arm | " + " | ".join(ea.ALL_LABELS) + " |")
    o.append("|---|" + "---|" * len(ea.ALL_LABELS))
    for arm in ladder + loo:
        hist = arms[arm]["failures"]
        o.append(f"| {arm} | " + " | ".join(str(hist.get(s, 0))
                                           for s in ea.ALL_LABELS) + " |")
    if full:
        o.append(f'\n### {full} 잔여 실패 — 근본 원인 (그리드)\n')
        o.append("| 문항 | 난이도 | 단계 | 원인 | 내용 |")
        o.append("|---|---|---|---|---|")
        for row in ea.grid_rows(reports[full]["rows"]):
            if row["first_failure"]:
                o.append(f'| {row["question_id"]} | {row["difficulty"]} '
                         f'| {row["first_failure"]} | {row["root_cause"]} '
                         f'| {row["failure_why"][:64]} |')
        o.append(f'\n**근본 원인 히스토그램 (그리드)**: '
                 f'`{json.dumps(arms[full]["root_causes"], ensure_ascii=False)}`\n')

    # --- 8. 뒤집힌 문항 ---
    o.append("\n## 8. arm 사이에서 뒤집힌 문항 — Δ 가 어느 문항에서 왔는가\n")
    o.append("| 전이 | 새로 맞힌 문항 | 잃은 문항 |")
    o.append("|---|---|---|")
    pairs = list(zip(ladder, ladder[1:])) + [(full, a) for a in loo]
    for lower, upper in pairs:
        a = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[lower]["rows"])}
        b = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[upper]["rows"])}
        gained = sorted(q for q in a if b.get(q) and not a[q])
        lost = sorted(q for q in a if a[q] and not b.get(q))
        o.append(f'| {lower} → {upper} | {" ".join(gained) or "—"} '
                 f'| {" ".join(lost) or "—"} |')

    # --- 9. 겨냥과 Δ 대조 ---
    o.append("\n## 9. arm 겨냥 배분 vs 실제 Δ — **안 맞으면 그것이 발견이다**\n")
    o.append("Task 7 이 오답 13건을 근거 원문까지 대조해 배분한 표다. 🔴 이 표는 "
             "**앞을 보는 처방**이지 v2 의 Δ 예측이 아니다 — 겨냥 문항은 정의상 "
             f'`{full}` 에서 **아직 틀리고 있는** 문항이므로, 사다리의 어느 arm 도 그것을 '
             "건지지 못했다. 그래서 두 칸을 따로 낸다: `실제로 건진 문항`(그 arm 이 "
             "이번에 번 것)과 `겨냥 문항의 현재 상태`(그 arm 의 **다음 판**이 해야 할 것).\n")
    # 🔴 **배분표를 여기서 다시 적지 않는다.** 같은 13건 배분이 이 파일과 채점기에 각각
    #    있으면 둘이 갈리고, 어느 쪽이 참인지 산출물로 판정할 수 없다. 채점기가 단일
    #    원천이다 (`ea.TARGETED_BY_MANUAL_REVIEW` — 원문 대조 결과이고, 자동 분류기와의
    #    대조표를 `arm_allocation_reconciliation()` 이 낸다).
    targeted = ea.TARGETED_BY_MANUAL_REVIEW
    final_ok = {r["question_id"]: r["correct"]
                for r in reports[full]["rows"]} if full else {}
    o.append("| arm | 겨냥 | 겨냥 문항 | 사다리 Δ | 실제로 건진 문항 "
             f'| 겨냥 문항의 {full} 상태 |')
    o.append("|---|---|---|---|---|---|")
    idx = {a: i for i, a in enumerate(ladder)}
    for arm, ids, label in targeted:
        status = " ".join(f'{q}{"✓" if final_ok.get(q) else "✗"}' for q in ids)
        if arm not in idx or idx[arm] == 0:
            o.append(f'| {arm} ({label}) | {len(ids)} | {" ".join(ids)} | — | — '
                     f'| {status} |')
            continue
        lower = ladder[idx[arm] - 1]
        a = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[lower]["rows"])}
        b = {r["question_id"]: r["correct"] for r in ea.grid_rows(reports[arm]["rows"])}
        gained = sorted(q for q in a if b.get(q) and not a[q])
        hit = [q for q in ids if q in gained]
        o.append(f'| {arm} ({label}) | {len(ids)} | {" ".join(ids)} '
                 f'| {_pp(arms[lower]["rate"], arms[arm]["rate"])} '
                 f'| {len(gained)}건 (겨냥 적중 {len(hit)}) | {status} |')
    # 🔴 **자동 배분과 수동 배분을 코드가 대조한다.** 지금까지는 사람이 기억으로 이었고,
    #    그 기억이 두 번 틀렸다("A6·A1b 공동 1위"). 자동 히스토그램은 A5 를 구조적으로 못
    #    본다 — `verify_calculation` 이 답변 수치 중 최유리 값을 골라 비교하므로 10× 단위
    #    오류가 `mismatch=False` 로 통과하고 TOOL 버킷이 그리드에서 0건이 된다.
    rec = (reports[full] or {}).get("arm_allocation_reconciliation") if full else None
    if rec:
        o.append("\n### 9c. 🔴 자동 분류기 vs 원문 대조 — **어긋나는 것이 정상이다**\n")
        o.append(f'{rec["note"]}\n')
        o.append(f'분모가 다르다: {rec["denominator_note"]}. '
                 f'권위값은 **{rec["authoritative"]}**(원문 대조)이다.\n')
        o.append("| arm | 자동 (그리드) | 자동 (전 트랙) | 수동 (전 트랙) | 일치 |")
        o.append("|---|---|---|---|---|")
        for row in rec["by_arm"]:
            same = "✓" if row["auto_all_tracks"] == row["manual_all_tracks"] else "**✗**"
            o.append(f'| {row["arm"]} | {row["auto_grid"]} | {row["auto_all_tracks"]} '
                     f'| {row["manual_all_tracks"]} | {same} |')
        o.append(f'\n불일치 **{rec["n_disagreements"]}/{rec["n_compared"]}건**'
                 + (f' — {", ".join(p["question_id"] + "(" + p["auto_arm"] + "→" + p["manual_arm"] + ")" for p in rec["disagreements"])}'
                    if rec["disagreements"] else "")
                 + ".\n")
        if rec.get("critic_passed_wrong_answer"):
            o.append("🔴 **critic 이 통과시킨 오답**: "
                     f'{" ".join(rec["critic_passed_wrong_answer"])} — 이 문항들의 arm 은 '
                     "자동 분류기가 지목할 수 없다 (그 판정 자체가 입력이다).\n")

    o.append("\n**읽는 법**: 어느 arm 도 자기 겨냥 문항을 하나도 못 건졌다 — 그것이 "
             "겨냥 문항의 정의이므로 동어반복이다. 읽을 값은 `사다리 Δ`(그 arm 이 실제로 "
             "번 것)와, 겨냥 문항이 **여전히 틀리고 있다**는 사실이다. 후자는 "
             "'그 arm 을 더 밀어야 한다' 는 뜻이고, 우선순위는 겨냥 건수 순서 그대로다 "
             "(A4 5 → A5 4 → A1b 2 → A6 1).\n")

    # --- 9b. 겨냥 문항을 LOO 짝과 대조한다 — 여기서 처방이 뒤집힌다 ---
    o.append("\n### 9b. 🔴 겨냥 문항을 **그 arm 을 끈 구성**과 대조한다\n")
    o.append("겨냥표만 보면 '그 arm 을 더 밀어라' 로 읽힌다. 그런데 **그 arm 을 끄면 맞는** "
             "문항이 있으면 처방이 정반대다 — 그 arm 이 **원인**이라는 뜻이다. "
             "사다리에서 한 칸 아래 구성과 문항 단위로 대조한다.\n")
    o.append("| arm | 겨냥 문항 | 한 칸 아래 | 아래는 맞고 위는 틀린 문항 (= **그 arm 이 원인**) |")
    o.append("|---|---|---|---|")
    for arm, ids, label in targeted:
        if arm not in idx or idx[arm] == 0:
            continue
        lower = ladder[idx[arm] - 1]
        a = {r["question_id"]: r["correct"] for r in reports[lower]["rows"]}
        b = {r["question_id"]: r["correct"] for r in reports[arm]["rows"]}
        # 겨냥 문항만이 아니라 **전 트랙**에서 본다 — 회귀 트랙에 숨어 있을 수 있다.
        caused = sorted(q for q in a if a[q] and not b.get(q))
        o.append(f'| {arm} ({label}) | {" ".join(ids)} | {lower} '
                 f'| {" ".join(f"**{q}**" for q in caused) or "없다"} |')
    o.append("\n이 칸이 비지 않은 arm 은 **겨냥 배분을 다시 읽어야 한다** — 그 arm 을 "
             "고치는 것이 아니라, 그 arm 이 만든 부작용을 없애는 것이 처방이다.\n")

    # --- 10. 논문 대조 ---
    o.append("\n## 10. 부록 D 대조 — 우리 Δ 가 타당한 크기인가\n")
    ours: dict[str, float | None] = {}
    prev = None
    for arm in ladder:
        r = arms[arm]["rate"]
        ours[arm] = None if prev is None else (r - prev) * 100
        prev = r
    for arm in loo:
        if full:
            ours[arm] = (arms[arm]["rate"] - arms[full]["rate"]) * 100
    o.append("| 우리 arm | 우리 Δ | 논문 | 논문 보고값 | 방식 |")
    o.append("|---|---|---|---|---|")
    for arm, label, paper, value, method in PAPER_EFFECTS:
        mine = ours.get(arm)
        o.append(f'| {arm} ({label}) | {"—" if mine is None else f"{mine:+.1f}%p"} '
                 f'| {paper} | {value} | {method} |')

    # --- 11. 꺾이는 지점 ---
    o.append("\n## 11. 성능/비용 곡선의 꺾이는 지점 → 제출 구성\n")
    o.append("🔴 `Δ/원` 은 **Δ 가 양수일 때만 읽는다.** Δ 와 Δ비용이 **둘 다 음수**면 몫이 "
             "양수로 나와 '효율이 좋다' 로 보이는데, 실제로는 **싸지고 못 맞히게 된 것**이다. "
             "그 칸은 수를 쓰지 않고 이름을 붙였다.\n")
    o.append("| 전이 | Δ 정답률 | Δ 비용(원) | Δ/원 | 판정 |")
    o.append("|---|---|---|---|---|")
    for lower, upper in zip(ladder, ladder[1:]):
        ra, rb = arms[lower]["rate"], arms[upper]["rate"]
        ca, cb = arms[lower]["arm_cost_won"], arms[upper]["arm_cost_won"]
        d_rate, d_cost = (rb or 0) - (ra or 0), cb - ca
        # 1원 미만의 비용 차는 0 으로 본다 — 반올림 잡음이 '정상' 과 '꺾이는 지점' 을 가르면 안 된다.
        flat_cost = abs(d_cost) < 1.0
        if d_rate < 0:
            eff = "—"
            note = ("🔴 **싸지고 못 맞힌다** — 몫을 읽으면 안 된다" if d_cost < 0
                    else "🔴 **비싸지고 못 맞힌다**")
        elif d_rate == 0:
            eff = "—"
            note = ("**꺾이는 지점** — Δ 0 · 한계 비용 0" if flat_cost
                    else "Δ 0 인데 비용은 움직였다 — 뺄 후보")
        elif flat_cost:
            eff, note = "∞", "**공짜 이득** — 비용 0 에 정답률 상승"
        else:
            eff = f'{d_rate * 100 / d_cost:+.3f}'
            note = "정상" if d_cost > 0 else "**공짜 이득** — 싸지고 더 맞힌다"
        o.append(f'| {lower} → {upper} | {_pp(ra, rb)} | {d_cost:+,.0f} | {eff} | {note} |')
    # 밸류체인 Δ 를 문자열로 박지 않는다 — 2026-08-11 에 실제로 뒤집혔다(+16.7 → +8.3%p).
    # 하드코딩이던 동안 재생성한 게이트는 §6(자동 계산, 5/12 vs 4/12)과 여기가 서로를
    # 반박하는 상태로 나왔다. 옆 문장의 비용은 산출물에서 계산돼 자동 갱신됐는데
    # 정답률만 문자열이었던 것이 원인이다.
    if vc.get("A8") and vc.get("L3"):
        r8 = ea._rate(vc["A8"]["rows"])
        r3 = ea._rate(vc["L3"]["rows"])
        # 🔴 `_exact_rate` 를 통과시킨다 — `ea._rate()` 의 `rate` 는 셋째 자리 반올림이라
        # 그대로 빼면 이중 반올림이다 (`_exact_rate` 독스트링의 규칙).
        vc_delta = (f'**{_pp(_exact_rate(r3), _exact_rate(r8))}** 를 번다 '
                    f'(§6 — {r8["correct"]}/{r8["n_scored"]} vs L3 '
                    f'{r3["correct"]}/{r3["n_scored"]})')
    else:
        vc_delta = "이득을 본다 (§6 — 밸류체인 산출물이 없어 Δ 를 못 적는다)"
    # 꺾이는 지점도 문자열로 박지 않는다 — 2026-08-11 에 A5 에서 A4 로 옮겨갔다.
    # 🔴 첫 수정이 `flat[0]`(전이의 **위쪽** arm)을 써서 **같은 틀린 A5 를 계산으로
    # 재생산**했다(재검토 B I2). 꺾이는 지점은 **마지막으로 번 arm** — 즉 Δ=0 인 첫 전이의
    # 아래쪽이다. 그리고 "전부 한계 비용 0" 도 실측해서 쓴다. A5 는 +79원이라 거짓이었다.
    zeros = [(lo, up) for lo, up in zip(ladder, ladder[1:])
             if (arms[up]["rate"] or 0) - (arms[lo]["rate"] or 0) == 0]
    flat = [up for _, up in zeros]
    if zeros:
        knee = f'**꺾이는 지점은 `{zeros[0][0]}`**'
        paid = [up for lo, up in zeros
                if abs(arms[up]["arm_cost_won"] - arms[lo]["arm_cost_won"]) >= 1.0]
        cost_note = ("전부 **그리드에서** 한계 비용이 0 이다"
                     if not paid else
                     "대부분 **그리드에서** 한계 비용이 0 인데 "
                     + " · ".join(
                         f'`{up}` 는 {arms[up]["arm_cost_won"] - arms[lo]["arm_cost_won"]:+,.0f}원'
                         for lo, up in zeros if up in paid)
                     + " 이다 — 🔴 **그 arm 은 그리드 기준으로 뺄 후보다**")
    else:
        knee = "**정답률 Δ 가 0 이 되는 지점이 없다**"
        cost_note = "한계 비용은 위 표를 보라"
    o.append(f"\n{knee} — 그 뒤 arm 은 그리드에서 Δ 가 0 이다"
             + (f" ({' · '.join(flat)})" if len(flat) > 1 else "")
             + f". 그럼에도 **다 넣는다**: {cost_note}"
             f"(A6·A7 은 LLM 0회, A8 은 발화 0건 → 토큰 0 증가). A8 은 밸류체인에서 "
             f"{vc_delta}.\n")
    o.append("🔴 **A8 의 정당화를 정답률에 싣지 않는다** — n=12 에서 Δ 가 문항 하나(Q-110)에 "
             "매달리고, 실제로 두 라운드 연속 뒤집혔다(6→5→6/12, L3 도 4→5). 정본 축은 "
             "**근거 완결성**이다(§6): 밸류체인 골든 근거가 A8 **28/46** vs L3 23/46 으로 "
             "안정적이고, C1 수정에서 정답률이 내렸을 때도 Q-110 5/6 · Q-114 1/5 · Q-116 3/3 "
             "이 한 건도 안 움직였다. 검색은 닿고 생성이 흔들리는 것이라 hop 의 기전은 "
             "무손상이다.\n")
    if vc.get("A8") and vc.get("L3"):
        c8 = vc["A8"]["totals"]["cost_won"]
        c3 = vc["L3"]["totals"]["cost_won"]
        o.append(f'🔴 **"한계 비용 0" 은 그리드에 한정된 말이다.** 발주처 hop 이 발화하는 '
                 f'밸류체인 12문항에서는 A8 {c8:,.1f}원 vs L3 {c3:,.1f}원 = '
                 f'**{c8 - c3:+,.1f}원({(c8 / c3 - 1) * 100:+.0f}%)** 이다 — 발주처 블록이 '
                 f'근거에 실리므로 토큰이 는다. 정확한 진술은 "발주처를 지목하지 않은 질의에서 '
                 f'한계 비용 0, 지목한 질의에서 +{(c8 / c3 - 1) * 100:.0f}%" 다.\n')
    o.append("A2 를 뺀 기준(\"Δ 0 이면 뺀다\")이 A8 에 오지 않는 이유는 A2 가 **모든 질의에** "
             "임베딩 비용을 물리면서 Δ 0 이었던 것과 달리, A8 은 **발주처를 지목한 질의에서만** "
             "비용과 이득이 동시에 생기기 때문이다.\n")
    o.append("**제출 후보에서 `A0`·`A1` 을 배제한다.** 🔴 이것은 *제거 지시가 아니다* — "
             "`_gather_meta` 는 `uses_bm25=False` 에서만 도달하므로 **A8 안에 떼어낼 'A0' "
             "구성요소가 없다.** 뜻은 사다리의 그 두 칸을 제출 구성 후보에서 뺀다는 것이고, "
             f'근거는 `A1` 이 그리드 최저({arms.get("A1", {}).get("correct", "?")}/'
             f'{arms.get("A1", {}).get("n_scored", "?")} = '
             f'{_pct(arms.get("A1", {}).get("rate"))})이고 `A0` 이 가성비 최악이라는 것이다 '
             f'({arms.get("A0", {}).get("arm_cost_won", 0):,.0f}원에 '
             f'{_pct(arms.get("A0", {}).get("rate"))} vs A1b '
             f'{arms.get("A1b", {}).get("arm_cost_won", 0):,.0f}원에 '
             f'{_pct(arms.get("A1b", {}).get("rate"))}).\n')

    # --- 12. measured 42 — v1 과 비교 가능한 유일한 축 ---
    # 🔴 제목과 셀 라벨을 `measured 42 중 그리드 36` 으로 통일한다. `measured_dev_cohort()`
    #    는 42개 ID 를 주지만 `_grid_rate` 가 `grid_rows` 로 한 번 더 걸러 **36** 이 된다
    #    (정보한계 6문항 제외). 예전 표는 분모 36 을 "measured 42" 로 라벨링해서, 같은
    #    보고서 안에서 같은 코호트를 42 와 36 두 이름으로 불렀다 — 코호트/분모 혼용은 이
    #    프로젝트가 가장 조심하는 실패 유형이고, 표 라벨은 문서로 옮겨 적힐 때 값보다
    #    먼저 떨어진다. (값은 안 바뀐다.)
    o.append("\n## 12. `measured 42 중 그리드 36` 코호트 — v1 과 비교 가능한 유일한 축\n")
    o.append("🔴 **이것은 재생(replay)이다.** Task 7 의 measured 42 는 답변 42/42 가 A7 과 "
             "바이트 동일이고 토큰 167,914 가 전 항목 일치했다. 온도 0.0 하에 타당하나 "
             "**'현재 시스템의 독립 측정' 이 아니다** — 새로 얻은 정보는 '현재 코드가 그 "
             "42문항에 같은 요청을 만든다' 는 사실뿐이다.\n")
    try:
        from scripts.validate_evalset import measured_dev_cohort
        wanted = set(measured_dev_cohort())
    except Exception:                                     # noqa: BLE001
        wanted = set()
    o.append("🔴 **'v1 42문항' 이라는 이름의 기준선이 둘 있다 — 섞으면 Δ 가 2배로 부푼다.**\n")
    o.append("| 출처 | 무엇 | grid | 이 표에서 |")
    o.append("|---|---|---|---|")
    ph_path = ROOT / "artifacts" / "eval" / "error_analysis.json"
    if ph_path.exists():
        ph = json.loads(ph_path.read_text(encoding="utf-8"))
        g = ph["crosstab"]["grid_overall"]
        o.append(f'| `artifacts/eval/error_analysis.json` | **Phase H v1** — 체인 hop · '
                 f'스키마 필드가 붙기 **전** | {g["correct"]}/{g["n_scored"]} '
                 f'({_pct(g["rate"])}) | 쓰지 않는다 |')
    v1 = _load_report(ROOT / "artifacts" / "ablation", "A6")
    if v1:
        g = v1["crosstab"]["grid_overall"]
        o.append(f'| `artifacts/ablation/A6/report.json` | **Ablation v1 A6** — 사다리의 '
                 f'그때 완성 구성 | {g["correct"]}/{g["n_scored"]} ({_pct(g["rate"])}) '
                 f'| ✅ **이것과 비교한다** |')
    o.append("\nT7 이 보고한 `+38.9%p` 는 위쪽(Phase H v1, 19/36)과의 차이다 — 그것은 "
             "**시스템 전체가 좋아진 폭**이다. 아래 표가 재는 것은 **같은 사다리의 완성 "
             "구성끼리**(Ablation v1 A6 vs v2 A8) 이고, 두 수를 더하거나 섞으면 안 된다.\n")
    if wanted and full:
        o.append("| 구성 | 코호트 | 정답 / 채점 | 정답률 | Δ(v1 A6) |")
        o.append("|---|---|---|---|---|")
        base_rate = _exact_rate(v1["crosstab"]["grid_overall"]) if v1 else None
        if v1:
            g = v1["crosstab"]["grid_overall"]
            o.append(f'| **Ablation v1 A6** (2026-08-09) | measured 42 중 그리드 '
                     f'{g["n_scored"]} | {g["correct"]}/{g["n_scored"]} '
                     f'| {_pct(_exact_rate(g))} | — |')
        for arm in ladder:
            r = _grid_rate(reports[arm]["rows"], wanted)
            o.append(f'| v2 {arm} | measured 42 중 그리드 {r["n_scored"]} '
                     f'| {r["correct"]}/{r["n_scored"]} '
                     f'| {_pct(_exact_rate(r))} | {_pp(base_rate, _exact_rate(r))} |')
        o.append(f'\n🔴 **분모는 {len(wanted)} 가 아니라 그리드 36 이다.** '
                 f'`measured_dev_cohort()` 는 {len(wanted)}개 ID 를 주지만 정보한계 6문항이 '
                 "그리드 밖이라 채점 대상은 36 이다. 이 표의 코호트 이름과 §12b·원장의 "
                 "`dev_grid_measured36` 은 **같은 것**을 가리킨다.\n")

        # 🔴 12b. 뒤집힘을 두 성분으로 가른다 — 코호트 효과와 증설 효과는 다른 것이다.
        o.append("\n### 12b. 🔴 `A0→A1` 뒤집힘을 두 성분으로 가른다\n")
        o.append('"문항이 42→88 로 늘자 부호가 뒤집혔다" 는 **절반만 맞다.** 그리드 72문항을 '
                 "v1 이 실제로 잰 36문항(`measured`)과 증설된 36문항으로 갈라 보면 "
                 "**동일 코호트가 이미 뒤집혀 있다.**\n")
        o.append("| 구간 | A0 | A1 | Δ(A0→A1) |")
        o.append("|---|---|---|---|")
        v1_a0, v1_a1 = (_load_report(ROOT / "artifacts" / "ablation", a)
                        for a in ("A0", "A1"))
        if v1_a0 and v1_a1:
            g0 = v1_a0["crosstab"]["grid_overall"]
            g1 = v1_a1["crosstab"]["grid_overall"]
            o.append(f'| **v1** measured 36 | {g0["correct"]}/{g0["n_scored"]} '
                     f'| {g1["correct"]}/{g1["n_scored"]} '
                     f'| {_pp(_exact_rate(g0), _exact_rate(g1))} |')
        # 🔴 이 분해는 `A0`·`A1` 산출물이 **이 세대 폴더 안에** 있을 때만 성립한다.
        #    부분 세대(`--out artifacts/ablation/v3` 처럼 LOO·진단 arm 만 담긴 폴더)에서
        #    무방비로 색인하면 `KeyError: 'A0'` 으로 보고서 전체가 죽는다 — 실제로 죽었다.
        #    다른 세대의 A0/A1 을 끌어와 채우지 않는다. 그것이야말로 이 절이 경고하는
        #    **세대 혼합**이고, 그렇게 만든 Δ 는 어느 시스템의 것도 아니다.
        if all(a in reports for a in ("A0", "A1")):
            for label, keep in (("**v2** measured 36 (동일 코호트)", True),
                                ("**v2** 증설 36", False)):
                cells = []
                for arm in ("A0", "A1"):
                    rows_ = [r for r in ea.grid_rows(reports[arm]["rows"])
                             if (r["question_id"] in wanted) == keep]
                    cells.append(ea._rate(rows_))
                o.append(f'| {label} | {cells[0]["correct"]}/{cells[0]["n_scored"]} '
                         f'| {cells[1]["correct"]}/{cells[1]["n_scored"]} '
                         f'| {_pp(_exact_rate(cells[0]), _exact_rate(cells[1]))} |')
        else:
            missing = [a for a in ("A0", "A1") if a not in reports]
            o.append(f'| 🔴 이 세대에 없다 (`{"`·`".join(missing)}`) | — | — | — |')
            o.append("\n🔴 **이 세대 폴더에는 `A0`·`A1` 산출물이 없어 분해를 못 한다.** "
                     "다른 세대의 값을 끌어와 채우지 않는다 — 이 절의 요지가 **세대를 "
                     "섞지 말라**는 것이므로, 섞어서 채운 표는 이 절의 결론을 거짓으로 "
                     "만든다. 분해가 필요하면 그 세대 폴더에 `--report` 를 돌린다 "
                     "(`artifacts/ablation/v2`).\n")
        if all(a in reports for a in ("A0", "A1")):
            o.append("\n**두 성분이 같은 방향이고 크기가 다르다.** 동일 36문항에서 v1 은 "
                     "`+2.8%p` 였는데 v2 는 음수다 — 그 몫은 **문항 증설이 아니라 v1→v2 코드 "
                     "변화**다. 🔴 **어느 커밋이 원인인지는 v2 산출물로 특정할 수 없다** "
                     "(v1 이후 파이프라인이 여러 트랙에서 바뀌었고, 이 트랙은 arm 플래그만 "
                     "변수로 뒀다). 특정하려면 커밋별 재측정이 필요하고 그것은 별개 작업이다.\n")

    # --- 13. 읽을 때 주의 ---
    o.append("\n## 13. 이 표를 읽을 때 주의할 것\n")
    o.append("- **`Δ(A5→A6)` · `Δ(A6→A7)` · `Δ(A7→A8)` 은 88문항 그리드에서 정의상 0 이다.** "
             "A6(Verifier)은 경고만 붙이고 답을 갈아엎지 않으며(LLM 0회), A7 은 플래그가 "
             "없고, A8 의 발주처 hop 은 그리드 88문항에서 발화가 0건이다. 셋 다 "
             "**생성 프롬프트가 A5 와 바이트 동일**임을 LLM 0회로 확인했다.\n")
    o.append("- `DATA_LIMIT` 은 정답률 분모에서 뺀다. 정보한계 문항은 `answerable=false` 로 "
             "따로 세고, 지어내면 `HALLUCINATION` 으로 잡는다 (§4).\n")
    o.append("- 근본 원인 히스토그램은 **그리드 기준**이다 (`root_cause_histogram_grid`). "
             "전체 88 기준을 쓰면 회귀 트랙이 섞여 겨냥 건수가 부푼다.\n")
    # 🔴 이 문단의 실측 수치도 문자열이었다 — 재실행으로 방향까지 뒤집혔는데(옛 문자열은
    # "A0 3.73초 vs A5·A8 0.02~0.03초") 갱신되지 않았다. 하드코딩 사고의 네 번째 얼굴이다.
    # 이제 산출물에서 읽는다. 값이 없으면 수를 쓰지 않는다.
    lat = [(a, c.get("latency_s_mean_recorded", c.get("latency_s_mean")))
           for a, c in arms.items()]
    lat = sorted(((a, v) for a, v in lat if v is not None), key=lambda t: -t[1])
    o.append("- 🔴 **기록된 지연을 '평균 응답 시간' 으로 인용하지 않는다.** 그 값은 응답 "
             "파일에 저장된 벽시계 시간의 평균인데, 그 파일을 만들 때 요청 캐시가 적중했다면 "
             "파이프라인이 ~1ms 에 끝난 시간이 저장돼 있다. **캐시 적중률이 arm 마다 다르므로 "
             "arm 사이 비교가 성립하지 않고, 세대가 바뀌면 순서까지 뒤집힌다** — 실제로 이 "
             "문단의 수치가 한 번 뒤집혔다."
             + (" 현 세대 실측: "
                + " · ".join(f"`{a}` {v:.2f}초" for a, v in lat[:3])
                + (f" … `{lat[-1][0]}` {lat[-1][1]:.2f}초" if len(lat) > 3 else "")
                if lat else " 현 세대 산출물에 값이 없다.")
             + " 진짜 응답 시간은 캐시 없이 다시 재야 나온다 (제출 후, 배포 리허설에서).\n")
    # 🔴 A3 의 겨냥 건수를 **손으로 적지 않는다.** 종전에는 "겨냥점 1건 (Q-094, rank 8·9)"
    #    이 문자열로 박혀 있었고, Q-094 가 리랭커 없이 정답이 된 뒤에도 그대로 렌더됐다.
    #    상수가 단일 원천이므로 거기서 센다 (I-6 과 같은 뿌리 — 문자열이 상수를 복제한 경우).
    a3 = next((ids for arm, ids, _ in ea.TARGETED_BY_MANUAL_REVIEW if arm == "A3"), ())
    o.append("- **A2·A3 는 v1 에서 실측으로 제외했다** — A2 는 dense 가 top_k 에 31건을 "
             "새로 올렸는데 골든 0건, A3 는 겨냥점 1건. "
             + (f"**현 세대 A3 겨냥 {len(a3)}건**: {' '.join(a3)}.\n" if a3 else
                "🔴 **현 세대 A3 겨냥은 0건이다** — v1 의 `WRONG_SECTION` 도, 그 뒤 신규 "
                "섹터에서 섰던 `RANKING/LOW_RANK`(Q-094)도 리랭커 없이 해소됐다. Q-094 는 "
                "다문서 질의 분해만으로 맞았고 원 진단이 틀렸다 "
                "(`docs/pipeline/05-reranker.md §E-4`). **잴 대상이 사라진 것이지 "
                "못 한 것이 아니다.**\n"))

    # --- 14. heldout — 게이트 I 의 마지막 항목 ---
    o.append("\n## 14. heldout 최종 측정 — 일반화 낙폭\n")
    if not held:
        o.append("🔴 **아직 열지 않았다.** `evalset/heldout.jsonl` 18문항은 최종 구성 "
                 "1회용이고, 이 트랙은 그것을 열지 않았다. 게이트 I 의 6번째 항목은 "
                 "여기서 비어 있다.\n")
    else:
        dev = held["dev_grid"]
        # 🔴 "이번이 두 번째" 를 하드코딩하지 않는다 — 바로 앞이 `use` 를 동적으로 찍으므로
        #    3회째가 되면 한 문단 안에서 모순이 생긴다. 이력은 `HELDOUT_HISTORY` 가 든다.
        prior = [h for h in HELDOUT_HISTORY if h["use"] < (held.get("use") or 0)]
        prior_note = " · ".join(f'{h["use"]}회 {h["arm"]} {h["correct"]}/{h["n_scored"]}'
                                for h in prior) or "없음"
        ordinal = {1: "첫", 2: "두", 3: "세"}.get(held.get("use"), str(held.get("use")))
        o.append(f'🔴 **{held["use"]}회째 사용이다.** 앞선 사용: {prior_note}. '
                 f'이번이 {ordinal} 번째이자 최종 구성 `{held["arm"]}` 1회다. '
                 "arm 별로 돌리지 않았다 — 그러면 heldout 이 개발셋이 되고 되돌릴 수 없다. "
                 f'원장 `{HELDOUT_LEDGER}` 이 `artifacts/` 아래 **어디에라도** 있으면 러너가 '
                 "유료 실행을 거부한다 — `--out` 으로 우회되지 않는다 "
                 "(`scripts/ablation.py::assert_heldout_unused`).\n")
        o.append("🔴 **낙폭은 축마다 다르게 나온다.** dev 쪽 분모가 v1 36 → v2 72 로 "
                 "늘었으므로 세 축을 다 낸다 — 하나만 적으면 그것이 '그 낙폭' 으로 굳고, "
                 "가장 유리한 축이 뽑히기 쉽다. heldout 쪽 18문항만 두 판에서 동일하다.\n")
        # 🔴 이 절의 dev 쪽 수는 **원장이 얼어붙은 시점**의 것이다. heldout 은 다시 못 재므로
        # 원장을 갱신하지 않는 것이 옳지만, 그 결과 dev 수가 §2·§12 와 갈린다. 라벨이 없으면
        # 같은 문서 안에서 모순으로 읽힌다 (재검토 B C1 — 실제로 그 상태였다).
        live = arms.get(held.get("arm")) or {}
        if live.get("n_scored") and (
                live.get("correct") != dev.get("correct")
                or live.get("n_scored") != dev.get("n_scored")):
            o.append(
                f'🔴 **이 절의 dev 수는 얼어붙은 값이다.** heldout 을 측정한 시점의 dev 는 '
                f'**{dev["correct"]}/{dev["n_scored"]}** 이었고, 지금 dev 는 '
                f'**{live["correct"]}/{live["n_scored"]}** 이다(§2·§12). 원장을 갱신하지 '
                "않는 이유는 **heldout 을 다시 못 재기 때문**이다 — 새 dev 와 옛 heldout 을 "
                "짝지으면 그 낙폭은 어느 시스템의 것도 아니다. **이 절의 낙폭은 그 시점의 "
                "시스템에 대한 판정이고, 현재 시스템의 일반화 추정치가 아니다.** 현재 시스템의 "
                "값을 알려면 새 홀드아웃이 필요하다.\n")
        o.append("| 축 | 구성 | dev 그리드 | heldout | 낙폭 |")
        o.append("|---|---|---|---|---|")
        v1 = held["v1"]
        o.append(f'| v1 · dev 36 | {v1["arm"]} | {_frac(v1["dev_grid"])} '
                 f'| {_frac(v1["heldout"])} | {_pp_of(v1["gap_pp"])} |')
        o.append(f'| **v2 · dev 72 (증설 후 · 이번 판의 축)** | **{held["arm"]}** '
                 f'| {_frac(dev)} | **{_frac(held)}** | **{_pp_of(held["gap_pp"])}** |')
        m36 = held.get("dev_grid_measured36") or {}
        if m36.get("n_scored"):
            o.append(f'| v2 · measured 36 (v1 과 같은 문항 · dev 쪽이 **재생**) '
                     f'| {held["arm"]} | {_frac(m36)} | {_frac(held)} '
                     f'| **{_pp_of(m36["gap_pp"])}** |')
        else:
            o.append("| 🔴 v2 · measured 36 | — | **구할 수 없었다** "
                     f'({m36.get("unavailable")}) | — | **판정 불가** |')
        o.append(f'\n🔴 **비교 축.** {held["axis_caveat"]}\n')
        if m36.get("n_scored"):
            o.append(f'🔴 **가장 큰 낙폭은 `measured 36` 축의 {_pp_of(m36["gap_pp"])} 다** — '
                     "dev 쪽을 v1 과 나란히 읽는 유일한 축이므로 **판정은 이 수로 해야 한다.** "
                     f'다만 이 축의 dev 값은 {m36["caveat"]} (§12).\n')
        if held.get("same_failures_as_v1"):
            o.append("🔴 **정답 집합이 v1 과 문항 단위로 완전히 같다** — "
                     f'{_frac(held)} 이고 틀린 문항도 같은 '
                     f'{", ".join(held["wrong_ids"])} 다. 그러므로 '
                     f'`{_pp_of(v1["gap_pp"])} → {_pp_of(held["gap_pp"])}` 로 낙폭이 좁아진 '
                     "몫은 **일반화가 좋아진 것이 아니라 dev 쪽 정답률이 내려간 것**이다 "
                     "(문항 증설로 84.7%). 좁아진 낙폭을 개선으로 읽으면 안 된다.\n")
            o.append("🔴 **그러나 이것은 재생이 아니다 — 독립 유료 측정이다.** 답변 텍스트가 "
                     "**18/18 전부 다르고**(v1 A6 시점 코드와 프롬프트가 달라 요청 캐시 적중 0) "
                     "토큰도 79,326 → 95,984 로 늘었다. 정답 집합만 같다. 그래서 이 15/18 은 "
                     "**이 판에서 유일한 독립 측정치**이고, `measured 36`(dev 쪽이 재생)과 "
                     "성격이 다르다.\n")
        o.append(f'**판정** — {held.get("verdict") or "—"}\n')
        if held.get("gate_retired_after_this_run"):
            o.append(f'🔴 **이 게이트는 이번이 마지막 사용이다.** {held["gate_retirement_reason"]}\n')
        o.append(f'실지출 **{held["spent_won"] or 0:,.1f}원** (상한 1,000원) · '
                 f'arm 비용 {held["arm_cost_won"] or 0:,.1f}원 · '
                 f'실패 단계 `{json.dumps(held["failure_histogram"], ensure_ascii=False)}` · '
                 f'근본 원인 `{json.dumps(held["root_cause_histogram"], ensure_ascii=False)}`\n')
    return "\n".join(o) + "\n"


def write_gate_report(root: Path, budget_cap: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    text = render_gate(root, budget_cap)
    (root / "gate_I.md").write_text(text, encoding="utf-8")
    print(text)
    print(f'\n-> {root / "gate_I.md"}')


# --- CLI ----------------------------------------------------------------------

def run_retrieval(args) -> None:
    """🔴 크레딧 규칙: 생성 0회. 임베딩만 실측 usage 로 집계한다."""
    from argus.embed import USAGE

    snapshot = args.snapshot or (SNAPSHOT_PATH if SNAPSHOT_PATH.exists() else "")
    cases = load_dev(snapshot)
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}
        cases = [c for c in cases if c["question_id"] in wanted]
    arms = [a.strip() for a in args.retrieval.split(",") if a.strip()]
    USAGE.limit_won = args.budget_won

    base = OUT_DIR / "A2"
    base.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict] = {}
    for arm in arms:
        started = time.time()
        reports[arm] = retrieval_metrics(arm, cases)
        total = reports[arm]["totals"]
        print(f'  [{arm}] 골든 근거 {total["evidence_hit"]}/{total["golden"]} '
              f'· Recall@5 {total["recall5"]}/{total["golden"]} '
              f'· dense 호출 {total["dense_calls"]} 신규벡터 {total["dense_new"]} '
              f'· 임베딩 누적 {USAGE.won:,.1f}원 · {time.time() - started:.0f}s')

    diffs = []
    for pair in (("A6", "A2F"), ("A1b", "A2")):
        if pair[0] in reports and pair[1] in reports:
            diffs.append(diff_retrieval(reports[pair[0]], reports[pair[1]]))
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_cases": len(cases),
            "snapshot_sha": (json.loads(Path(snapshot).read_text(encoding="utf-8"))
                             ["sha256_of_joined_ids"] if snapshot else "-"),
            "index_snapshot": index_snapshot(),
            "embed_usage": USAGE.totals()}
    (base / "retrieval.json").write_text(
        json.dumps({"meta": meta, "reports": reports, "diffs": diffs},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (base / "retrieval.md").write_text(
        render_retrieval(reports, diffs, meta), encoding="utf-8")
    print(f'\n임베딩 실측 {USAGE.tokens:,}토큰 = {USAGE.won:,.1f}원 · 생성 0회')
    for diff in diffs:
        moved = {k: v for k, v in diff["delta"].items() if v}
        print(f'  {diff["arm"]} vs {diff["base"]}: '
              f'{moved or "Δ 전부 0"} · 변화 문항 {len(diff["changed_questions"])}건')
    print(f'-> {base / "retrieval.md"}')


def run_judge_sigma(args) -> None:
    """🔴 크레딧 규칙: LLM 0회. 회차 폴더가 있는 arm 만 집계한다."""
    records = ea._load_records()
    cases = load_cases(args.cohort, args.snapshot)
    arms = ([a.strip() for a in args.arm.split(",") if a.strip()]
            or list(ablation.RUN_ORDER))
    found = 0
    for arm in arms:
        result = judge_sigma(arm, cases, records, cohort=args.cohort or "dev")
        if not result:
            continue
        found += 1
        print_judge_sigma(result)
        print(f'-> {arm_dir(arm) / "judge_sigma.json"}')
    if not found:
        print(f'회차별 judge 산출물이 없습니다 ({OUT_DIR}/<arm>/'
              f'{JUDGE_REPEAT_DIRNAME}/round_*). `--judge-repeat N` 으로 먼저 재세요.')


def _utf8_console() -> None:
    """🔴 Windows 콘솔 기본 코드페이지(cp949)는 `—`·`✓` 를 못 찍어 **실행이 다 끝난 뒤
    마지막 print 에서** UnicodeEncodeError 로 죽는다. 산출물은 이미 디스크에 있으니 측정
    손실은 없지만 측정이 **실패한 것처럼 보이고**, heldout 처럼 다시 못 돌리는 실행에서는
    그 착각이 위험하다.

    🔴 **import 시점에 하지 않는다.** pytest 의 capture 는 sys.stdout 을 기본 인코딩으로
    쓴 임시파일로 갈아 두므로, 모듈 import 만으로 이걸 utf-8 로 바꾸면 pytest 가 자기
    캡처를 되읽을 때 `UnicodeDecodeError` 로 죽는다 (실제로 그렇게 1,163건이 깨졌다).
    그래서 CLI 진입점 안에서만 부른다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")        # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> None:
    """🔴 `argv` 를 받는다. 통합 테스트가 `main()` 을 실제로 태울 수 있어야 한다 —
    "거부된 heldout 실행이 빈 폴더를 흔적으로 남기지 않는다" 는 성질은 `mkdir` 과
    `assert_heldout_unused` 의 **호출 순서**로만 참이고, 그것을 지키는 회귀 테스트가
    없으면 다음 사람이 순서를 바꿔도 스위트가 잡지 못한다.
    """
    _utf8_console()
    parser = argparse.ArgumentParser(description="Phase I ablation (dev 전용)")
    parser.add_argument("--arm", "--arms", dest="arm", default="",
                        help="쉼표 구분 arm 이름. 비면 실행 안 함")
    parser.add_argument("--run", action="store_true", help="RUN_ORDER 전부 실행")
    parser.add_argument("--only", default="", help="쉼표 구분 question_id 만")
    parser.add_argument("--dry", action="store_true", help="LLM 0회 리허설")
    parser.add_argument("--offline", action="store_true", help="요청 캐시 미적중 시 중단")
    parser.add_argument("--force", action="store_true", help="응답 파일 캐시 무시")
    parser.add_argument("--heldout", action="store_true",
                        help="🔴 heldout 18문항을 최종 구성으로 1회 실행")
    parser.add_argument("--retrieval", default="",
                        help="검색 지표만 LLM 0회로 잰다. 쉼표 구분 arm (기준선을 앞에)")
    parser.add_argument("--snapshot", default="",
                        help="평가셋 스냅샷 json. 비면 questions.jsonl 전체")
    parser.add_argument("--cohort", default="", choices=("",) + ea.COHORTS,
                        help="measured=v1 이 잰 42문항 · full=dev 78+회귀 10 · valuechain=12")
    parser.add_argument("--out", default="",
                        help="arm 폴더의 부모. 비면 artifacts/ablation (v1 자리)")
    parser.add_argument("--report", action="store_true",
                        help="게이트 I 보고서만 생성한다 (LLM 0회 · 크레딧 0원)")
    parser.add_argument("--budget-won", "--budget", dest="budget_won", type=float,
                        default=None,
                        help="상한(원). 러너는 그 80%%(=정지선)에서 멈춘다")
    parser.add_argument("--judge-repeat", dest="judge_repeat", type=int, default=1,
                        help="🔴 open 문항의 judge 를 N 회 반복해 재현 편차(σ)를 잰다. "
                             "기본 1 = 종전 동작. 반복 회차는 요청 캐시를 우회한다")
    parser.add_argument("--judge-sigma", action="store_true",
                        help="이미 있는 회차별 judge 산출물만 다시 집계한다 (LLM 0회)")
    args = parser.parse_args(argv)

    if args.judge_repeat < 1:
        raise SystemExit(f"--judge-repeat 은 1 이상이어야 합니다: {args.judge_repeat}")
    # 🔴 `--offline` 은 "요청 캐시 적중만 허용" 이고 반복 회차는 **정의상 우회**다.
    #    두 인자를 같이 받으면 첫 우회 호출이 `RuntimeError` 로 죽는다 — 인자 단계에서 막는다.
    if args.offline and args.judge_repeat > 1:
        raise SystemExit(
            "🔴 `--offline` 과 `--judge-repeat > 1` 은 같이 못 씁니다. 반복 회차는 "
            "요청 캐시를 **우회**해야 측정이 성립하는데(온도 0.0 이라 안 우회하면 3회가 "
            "전부 같은 파일을 읽습니다), `--offline` 은 캐시 미적중 호출을 금지합니다.\n"
            "   이미 돌려 둔 회차를 다시 집계만 하려면 `--judge-sigma` 를 쓰세요 (LLM 0회).")
    # 🔴 heldout 은 반복 대상이 아니다. 회차마다 judge 를 새로 사는 것은 heldout 을
    #    한 번 더 '여는' 일이 아니지만, 되돌릴 수 없는 자산에 새 호출을 얹는 경로는
    #    아예 열지 않는다 — 이 측정은 dev 쪽에서 끝난다.
    if args.heldout and args.judge_repeat > 1:
        raise SystemExit("🔴 `--heldout` 과 `--judge-repeat > 1` 은 같이 못 씁니다.")

    global OUT_DIR
    if args.out:
        OUT_DIR = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out

    # 🔴 상한과 정지선을 가른다. 브리프의 규칙은 "상한 N, 80% 에서 중단" 이므로
    #    러너에 넘기는 값은 정지선이고, 보고에 쓰는 값은 상한이다.
    budget_cap = args.budget_won if args.budget_won else BUDGET_WON
    stop_won = round(budget_cap * 0.8, 1)

    if args.report:
        assert_not_v1_root(OUT_DIR, "게이트 보고서")
        write_gate_report(OUT_DIR, budget_cap)
        return

    if args.retrieval:
        run_retrieval(args)
        return

    if args.judge_sigma:
        # 🔴 LLM 0회. 디스크에 이미 있는 회차 폴더만 다시 읽는다.
        run_judge_sigma(args)
        return

    # 🔴 heldout 제동은 **디렉터리를 만들기 전에** 건다. 뒤에 걸면 거부된 실행이 빈 폴더와
    #    `index_snapshot.json` 을 남기고, 산출물만 보는 사람에게 그것이 "돌렸다" 는 흔적이 된다.
    heldout_final = ""
    if args.heldout:
        # 🔴 arm 을 **하나만** 받는다. 여러 개를 허용하면 heldout 이 개발셋이 되고
        #    그것은 되돌릴 수 없다 — 인자 형태에서부터 막는다.
        finals = [a.strip() for a in args.arm.split(",") if a.strip()]
        if len(finals) != 1:
            raise SystemExit(
                "🔴 `--heldout` 은 **최종 구성 하나**만 받습니다 (예: `--arm A8`). "
                f"받은 것: {finals or '없음'}\n"
                "   arm 별로 돌리면 heldout 이 개발셋이 되고 그것은 되돌릴 수 없습니다.")
        if finals[0] not in ablation.ARMS:
            raise SystemExit(f"그런 arm 이 없습니다: {finals[0]} (있는 것: "
                             f"{sorted(ablation.ARMS)})")
        assert_heldout_unused(args.offline)
        heldout_final = finals[0]

    if args.dry:
        # 리허설 응답을 본 실행 캐시에 섞으면 가짜 답이 영원히 굳는다. 폴더부터 가른다.
        OUT_DIR = ROOT / "artifacts" / "ablation_dry"
    # 🔴 v1 자리 쓰기 거부도 **디렉터리를 만들기 전에** 건다 (heldout 제동과 같은 이유).
    assert_not_v1_root(OUT_DIR, "arm 산출물·summary.json")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = RequestCache(CACHE_DIR, offline=args.offline)
    if args.dry:
        install_dry_stub()
    else:
        cache.install()

    records = ea._load_records()
    snapshot = index_snapshot()
    (OUT_DIR / "index_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    if heldout_final:
        print(f"\n=== heldout 18문항 · 최종 구성 {heldout_final} · "
              f"{heldout_next_use()}회째 사용 · "
              f"정지선 {stop_won:,.0f}원 (상한 {budget_cap:,.0f}) ===")
        report = run_heldout(heldout_final, cache, records, stop_won, args.force)
        ledger = write_heldout_ledger(OUT_DIR, heldout_final, report)
        print(f'\nheldout {_frac(ledger)} · dev 그리드 {_frac(ledger["dev_grid"])} '
              f'· 낙폭 {_pp_of(ledger["gap_pp"])}')
        m36 = ledger.get("dev_grid_measured36") or {}
        print(f'  v2 measured 36 축(=v1 과 같은 문항, dev 쪽 재생) {_frac(m36)} '
              f'· 낙폭 {_pp_of(m36.get("gap_pp"))}'
              + (f' 🔴 구할 수 없었다: {m36["unavailable"]}'
                 if m36.get("unavailable") else ""))
        print(f'  v1(A6) 낙폭 {_pp_of(ledger["v1"]["gap_pp"])} · '
              f'{ledger["axis_caveat"]}')
        print(f'  판정: {ledger["verdict"]}')
        if ledger.get("same_failures_as_v1"):
            print("  🔴 정답 집합이 v1 과 문항 단위로 동일하다 — 틀린 문항도 같은 "
                  f'{ledger["wrong_ids"]}. 낙폭이 좁아진 몫은 dev 쪽 변화다.')
        print(f'  실지출 {ledger["spent_won"]:,.1f}원 · 요청 캐시 적중 {cache.hits} / '
              f'미적중 {cache.misses}')
        print(f'-> {OUT_DIR / HELDOUT_LEDGER}')
        return

    cases = load_cases(args.cohort, args.snapshot)
    if args.only:
        wanted = {q.strip() for q in args.only.split(",") if q.strip()}
        cases = [c for c in cases if c["question_id"] in wanted]

    targets = []
    if args.run:
        targets = list(ablation.RUN_ORDER)
    elif args.arm:
        targets = [a.strip() for a in args.arm.split(",") if a.strip()]

    # 🔴 `ORACLE` 은 골든이 주입돼야 돈다. heldout 경로는 위에서 이미 반환했다.
    install_oracle_golden(targets, cases)

    live: dict[str, dict] = {}
    for arm in targets:
        if cache.fresh_won > stop_won:
            print(f"\n🔴 정지선 도달 — {arm} 이하를 실행하지 않습니다")
            break
        print(f"\n=== {arm} · {len(cases)}문항 · 누적 {cache.fresh_won:,.0f}원 "
              f"/ 정지선 {stop_won:,.0f}원 (상한 {budget_cap:,.0f})"
              + (f" · judge {args.judge_repeat}회 반복" if args.judge_repeat > 1 else "")
              + " ===")
        # 🔴 `_write_run` 이 지출 보존 가드를 들고 있다. 직접 `write_text` 하지 않는다 —
        #    그것이 1,901.0원을 0.0 으로 덮은 경로다 (`_write_run` docstring ③).
        live[arm] = _write_run(arm, run_arm(arm, cases, cache, stop_won, args.force,
                                            judge_repeat=args.judge_repeat))

    scored_cases = load_cases(args.cohort, args.snapshot)
    reports = {}
    for arm in ablation.RUN_ORDER:
        if (arm_dir(arm) / "responses").exists():
            reports[arm] = score_arm(arm, scored_cases, records,
                                     cohort=args.cohort or "dev")

    # 🔴 회차별 judge 산출물이 있으면 σ 를 낸다. LLM 0회 — `score_arm` 뒤에 두는 것은
    #    `_use_dirs` 를 갈아 끼우는 함수 둘이 서로의 경로를 물려받지 않게 하기 위함이다.
    for arm in (targets or list(reports)):
        sigma = judge_sigma(arm, scored_cases, records, cohort=args.cohort or "dev")
        if sigma:
            print_judge_sigma(sigma)
            print(f'-> {arm_dir(arm) / "judge_sigma.json"}')

    summary = summarise(reports)
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cohort": args.cohort or "dev",
            "n_cases": len(scored_cases),
            "question_ids": [c["question_id"] for c in scored_cases],
            "budget_won": budget_cap, "stop_won": stop_won,
            "price_won_per_1k": ea.PRICE_WON_PER_1K,
            "index_snapshot": snapshot}
    meta.update(_spend_meta(OUT_DIR, cache, live))
    (OUT_DIR / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT_DIR / "summary.md").write_text(render_summary(summary, meta), encoding="utf-8")

    print(f'\n실지출 {meta["spent_won"]:,.1f}원 ({meta["spent_won_source"]})')
    print(f'  이번 프로세스 {meta["this_process_spent_won"]:,.1f}원 · '
          f'캐시 적중 {cache.hits} / 미적중 {cache.misses}')
    for arm, row in summary["arms"].items():
        print(f'  {arm:<4} {row["correct"]:>2}/{row["n_scored"]:<2} '
              f'rate={row["rate_reported"]} tokens={row["tokens"]:,} '
              f'arm_cost={row["arm_cost_won"]:,.1f}원')
    print(f'-> {OUT_DIR / "summary.md"}')


if __name__ == "__main__":
    main()
