# ARGUS — DART 공시 QA Agent

**제10회 2026 미래에셋증권 AI Festival · 「공시 Agent」 주제 제출물** (마감 2026-09-06 23:59 KST).

거래소공시·정기공시를 **오프라인에서 정규화·색인**해 두고, 평가 질의가 들어오면
라우팅 → 검색·도구 호출(정정체인 추적·거래상대방 그래프·수치 계산) → 근거 기반 생성 →
검증(critic·judge)을 거쳐 고정 스키마 `GET /answer` 로 **답변·검색근거·사고과정**을 돌려준다.
HyperCLOVA X 는 생성·검증 단계에서만 부르고, 색인·검색·정규화·계산은 **LLM 호출 0회**로 돈다.
답할 수 없는 질의는 지어내지 않고 **한계를 고지**한다.

> 🔴 **성적·지출 수치는 이 문서에 쓰지 않는다.** 정본은
> [`docs/total_plan.md`](docs/total_plan.md) 한 곳이며, 이 README 는 그것을 복제하지 않고 가리킨다.

---

## 1. 🔴 평가용 API End-point — 제출물 3

주최측 2차 공지: *"End-point 주소는 `README.md` 파일에 명시합니다(필수)"*
(전문 [`docs/organizer-qa.md`](docs/organizer-qa.md) · 규격 정본 [`docs/CONVENTION.md`](docs/CONVENTION.md) §4).

| 항목 | 값 |
|---|---|
| **End-point** | 🔴 **`TBD-1` — 미정 (서버 미배포).** `http://<공인 IP>` 형태로 **08-25 코드 동결 이후 배포 리허설에서 확정해 이 칸에 적는다.** 임시 주소·예시 IP 를 대신 채우지 않는다 |
| **호출 URL 형태** | `http://<공인 IP>/answer?question_id={ID}&question={질의}` |
| 프로토콜 · 포트 | **HTTP · 80** — `deploy/argus.service` 의 `--port 80`. HTTPS 는 선택이고 self-signed 무방하므로 쓰지 않는다. 도메인 불필요(공인 IP 제출 허용) |
| 접근 제어 | 인증 헤더 없음. 주최측 발신 IP 허용 방식 → **`TBD-4`: 발신 IP 대역 미공지.** 공지되면 `deploy/README.md` 의 ACG 인바운드를 그 대역으로 좁힌다 |
| 운영 창 | 09-07 ~ 09-20 중 **주제별 최대 1주** → **`TBD-5`: 정확한 일자·시간대 미공지** |
| 헬스체크 | `GET /health` → `{"status": "ok"}` — **우리 운영용**이다. 평가가 쓰는 경로는 `/answer` 하나뿐이다 |

배포 절차(NCP `c2-g3a` · Ubuntu 24.04 · ACG · swap · systemd)는
[`deploy/README.md`](deploy/README.md) 에 있다.

---

## 2. API 명세

**정본은 코드다** — [`src/argus/api/server.py`](src/argus/api/server.py) 와
[`docs/CONVENTION.md`](docs/CONVENTION.md) §4. 아래는 그 두 곳과 일치시킨 서술이다.

### 요청

```
GET {end-point}/answer?question_id={ID}&question={평가 질의}
```

| | |
|---|---|
| 메서드 · 경로 | `GET` · **`/answer` 고정** |
| 쿼리 파라미터 | `question_id` (string, 필수) · `question` (string, 필수, 한글) |
| 헤더 | **없음.** 인증 헤더도 요구하지 않는다 |
| 본문 | 없음 |

```bash
curl -sG "http://<공인 IP>/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 최근 공급계약 규모는?"
```

### 응답

`application/json` · **5키 고정** · **전 필드 string** · 길이 제한 없음.

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 근거",
  "think_trace": "라우팅 · 검색 · 도구 호출 · 예산 사용량이 ' | ' 로 이어진 한 줄",
  "answer": "최종 생성 답변"
}
```

| 필드 | 타입 | 채우는 값 |
|---|---|---|
| `question_id` | string | 요청값 그대로 반향 |
| `question` | string | 요청값 그대로 반향 |
| `retrieved_context` | string | 검색·도구가 모은 근거 텍스트. **근거가 없어 한계를 고지한 경우 빈 문자열**(주최측 답변 #15 로 허용 확인) |
| `think_trace` | string | 사고·도구 사용 과정. 형식 규정이 없어 한 줄 로그로 낸다 |
| `answer` | string | 최종 답변 또는 한계 고지문 |

### 상태 코드 — 🔴 5xx 를 내지 않는다

| 상황 | 응답 |
|---|---|
| 정상 | `200` + 5키 |
| 답할 수 없는 질의 · 근거 없음 · 상류(HCX) 장애 · **내부 예외 일체** | 🔴 `200` + 5키 (`answer` 는 한계 고지). `server.py` 의 `except Exception` → `pipeline.fault_response` 가 마지막 방어선이다 |
| `question_id` 또는 `question` 누락 | `422` (FastAPI 파라미터 검증) |

**왜 5xx 를 안 내는가**: 주최측은 타임아웃·5xx 에 **최대 2회 재시도**한다. 5xx 를 흘리면
같은 질의가 최대 3회 들어와 세 번 다 실패하면 그 문항이 0점이고, 비결정적 실패면 캐시를
못 타 크레딧이 3배로 나간다. **200 + 한계 고지**는 채점 항목인 "정보한계 대응"에 걸린다.
근거는 `pipeline.fault_response` 독스트링과 `tests/test_server.py` 의 마지막 방어선 테스트다.

문항당 타임아웃은 **300초**이고 호출은 **순차 1건씩**(동시 요청 없음)이다.

---

## 3. 환경 구성 · 실행

### 🔴 `requirements.txt` 는 아직 없다 — `TBD-2`

의존성 정의는 현재 [`pyproject.toml`](pyproject.toml) 하나다(`fastapi` · `uvicorn` ·
`rank-bm25`, dev 로 `pytest` · `httpx`). 주최측은 `Dockerfile` 을 필수로 요구하지 않고
**재현 가능한 환경 구성 정보**(`requirements.txt` 등)를 요구한다.

**지금 만들지 않는다.** 개발 환경이 Windows/conda 라 여기서 `pip freeze` 를 뜨면
Windows 전용 휠(`pywin32` 등)과 conda 채널 빌드 문자열이 섞여 **Ubuntu 배포 서버에서
설치되지 않는다.** → **08-25 배포 리허설 때 서버(Ubuntu 24.04 · venv)에서 `pip freeze` 로
뽑아 커밋한다.** 그때가 코드 동결 시점이라 목록도 그 뒤로 움직이지 않는다.

### 개발 환경

```bash
pip install -e ".[dev]"
```

코퍼스 원본과 `.env` 는 저장소에 없다(둘 다 `.gitignore` 대상). 코퍼스는
`data/dart-disclosures/` 에 배치하고, CLOVA 키는 `.env` 의 `CLOVA_API_KEY` 로 넣는다.
코퍼스 규모·구성은 [`docs/pipeline/01-eda.md`](docs/pipeline/01-eda.md) 를 본다.

### 인덱스 빌드 (LLM 호출 0회)

```bash
python scripts/build_index.py --group exchange     # 거래소공시 BM25 (major · holding 도 같은 방식)
python scripts/build_sections.py                   # 정기공시 섹션 분할
python scripts/build_section_index.py              # 섹션 인덱스
python scripts/build_references.py                 # 정정·참조 체인
python scripts/build_counterparty_graph.py         # 거래상대방 그래프
python scripts/build_embeddings.py                 # (선택) dense 임베딩
```

산출물은 `artifacts/` 로 나간다. 문서군은 `exchange` · `major` · `holding` · `periodic` 넷이다
(`src/argus/agent/tools.py::RECORD_GROUPS`). `--limit N` 부분 빌드, `--out-dir` 출력 경로 변경.

### 서버 실행

```bash
# 로컬
uvicorn argus.api.server:app --host 0.0.0.0 --port 8000
curl -s "http://127.0.0.1:8000/answer?question_id=Q-001&question=테스트"

# 평가 서버 (systemd · 포트 80) — deploy/README.md
systemctl enable --now argus
```

### 테스트 · 평가

```bash
python -m pytest -q            # 전 테스트 (통과 수는 docs/total_plan.md §1)
python scripts/eval_e2e.py     # E2E 평가 하네스 → artifacts/eval_report.json
python scripts/ablation.py --arm A8 --dry   # arm 별 기여도 측정. --dry 는 LLM 0회
                                           # (--dry 없이 돌리면 유료다. 러너에 정지선이 있다)
```

---

## 4. 저장소 구조

| 경로 | 책임 |
|---|---|
| `src/argus/config.py` | `.env` 로딩, 코퍼스·산출물 경로 해석, 상수 |
| `src/argus/clova.py` | HyperCLOVA X 호출 단일 창구. 토큰·지연·캐시 기록 |
| `src/argus/corpus/` | manifest 프리필터 · HTML 파싱 · 정규화 · 마크다운 · 섹션 · 정정체인 · 거래상대방 · 날짜 연산 |
| `src/argus/retrieve/bm25.py` · `embed.py` | BM25 색인·검색 · 임베딩 (LLM 생성 호출 없음) |
| `src/argus/agent/` | 라우터 · 질의 확장 · 검색·계산 도구 · 파이프라인 · critic · 예산 · 안전 필터 |
| `src/argus/api/server.py` | FastAPI `GET /answer` · `GET /health` |
| `src/argus/ablation.py` | arm 플래그 — 단일 변수 조작을 코드로 강제 |
| `scripts/` | 인덱스 빌드 · EDA · 평가 하네스 · ablation · evalset 검증 CLI |
| `docs/` | 규약 · 총괄 계획 · Phase A~I 문서 · 기술제안서 |
| `evalset/` | 골든셋 (`questions.jsonl` · `heldout.jsonl` · `regression.jsonl` · `valuechain.jsonl`) |
| `artifacts/` | 전처리·색인·측정 산출물 (제출물 4) |
| `deploy/` | `setup.sh` · `argus.service` · `Dockerfile`(선택 — 주최측 필수 아님) |
| `tests/` | 계약·회귀 테스트 |

---

## 5. 문서 진입점

| 문서 | 무엇의 정본인가 |
|---|---|
| [`docs/total_plan.md`](docs/total_plan.md) | 🔴 **단일 진실 공급원 — 성적 · 지출 · 게이트 · 일정** |
| [`docs/CONVENTION.md`](docs/CONVENTION.md) | 과제 규정 · API 계약(§4) · 제출물(§9) |
| [`docs/organizer-qa.md`](docs/organizer-qa.md) | 주최측 공지 2건의 전사와 우리 영향 |
| [`docs/pipeline/`](docs/pipeline/) | Phase A~I — EDA · 골든셋 · 그래프 · 검색 · 리랭커 · 에이전트 · 검증 · 오류분석 · Ablation |
| [`docs/proposal/`](docs/proposal/) | 제출물 2 — 기술제안서 |
| [`docs/clova_payment.md`](docs/clova_payment.md) | 크레딧·단가·예산 |
| [`deploy/README.md`](deploy/README.md) | 배포 절차 |

---

## 6. 제출물 4종 대응 (CONVENTION §9)

| # | 제출물 | 소재 | 상태 |
|---|---|---|---|
| 1 | 소스코드 + 환경 구성 정보 + `README.md` | 이 저장소 · 이 파일 | 코드·README 있음 / **환경 정의는 `TBD-2`** |
| 2 | 기술 제안서 (자유 양식 · 분량 제한 없음) | [`docs/proposal/technical-proposal.md`](docs/proposal/technical-proposal.md) | 초안 |
| 3 | End-point URL + API 명세서 | **이 파일 §1 · §2** | 명세 있음 / **URL 은 `TBD-1`** |
| 4 | 전처리 산출물 (대용량은 스토리지 링크) | `artifacts/` · `evalset/` | 저장소에 있음 / **대용량 링크는 `TBD-3`** |

---

## 7. 🔴 자리표시자 대장 — 누가 언제 채우는가

| # | 미정 항목 | 채우는 주체 | 시점 |
|---|---|---|---|
| `TBD-1` | 평가용 End-point 공인 IP (§1) | 배포 담당(사용자) | **08-25 코드 동결 이후 배포 리허설.** 일정 정본은 [`docs/total_plan.md`](docs/total_plan.md) 일정표 — 날짜를 여기 박아 두지 않는다 |
| `TBD-2` | `requirements.txt` (§3) | 배포 담당 | 같은 칸 — 서버 venv 에서 `pip freeze` |
| `TBD-3` | 전처리 산출물 클라우드 스토리지 링크 (§6) | 배포 담당 | 같은 칸 — 동결 시 스냅샷 업로드 |
| `TBD-4` | 주최측 발신 IP 대역 (§1) | **주최측 공지 대기** | 미정 |
| `TBD-5` | 평가 서버 운영 일자·시간대 (§1) | **주최측 공지 대기** | 09-07 ~ 09-20 중 |

**공인 IP 는 한 번 받으면 09-20 까지 반납하지 않는다** — 반납 후 재획득하면 제출한 URL 이 죽는다.
