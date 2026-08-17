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
| **End-point** | ✅ **`http://223.130.129.131/answer`** (2026-08-17 확정) — 현재 코드(`A42`)가 이 주소에서 가동 중이고 규약 §4 계약을 실측으로 만족한다(200 · `application/json` · 5키 전부 string · 인증 헤더 없음 · 최대 10.0초). 🔴 **공인 IP `223.130.129.131` 은 2026-09-20 까지 유지해야 한다** — 반납 후 재획득하면 새 IP 가 나오고 제출 URL 이 죽어 전 문항 0점이다. 🔴 **2026-09-07 이전에 반드시 서버를 시작할 것** |
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
| `question_id` 또는 `question` 누락 | 🔴 `200` + 5키. 없는 값은 빈 문자열로 채우고 **있는 것으로 답한다** — `question` 만 온 요청은 답할 수 있는데 `422` 로 버리면 스스로 0점을 만든다. `server.py` 의 `RequestValidationError` 핸들러 (규약 감사 `F-4`) |

**왜 5xx 를 안 내는가**: 주최측은 타임아웃·5xx 에 **최대 2회 재시도**한다. 5xx 를 흘리면
같은 질의가 최대 3회 들어와 세 번 다 실패하면 그 문항이 0점이고, 비결정적 실패면 캐시를
못 타 크레딧이 3배로 나간다. **200 + 한계 고지**는 채점 항목인 "정보한계 대응"에 걸린다.
근거는 `pipeline.fault_response` 독스트링과 `tests/test_server.py` 의 마지막 방어선 테스트다.

문항당 타임아웃은 **300초**이고 호출은 **순차 1건씩**(동시 요청 없음)이다.

---

## 3. 환경 구성 · 실행

### ✅ `requirements.txt` — 작성 완료 (2026-08-15)

[`requirements.txt`](requirements.txt) 가 런타임 의존성을 **고정 버전으로** 담는다.
의존성 선언의 정본은 [`pyproject.toml`](pyproject.toml) 이고, `requirements.txt` 는
**실제로 검증된 버전을 고정한 것**이다(검증 환경 Python 3.13.13 · 전량 테스트 통과).

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .                    # argus 패키지 자체 (src/ 레이아웃)
```

🔴 **`pip freeze` 로 뜨지 않았다.** Windows/conda 에서 뜨면 Windows 전용 휠(`pywin32` 등)과
conda 채널 빌드 문자열이 섞여 **Ubuntu 배포 서버에서 설치되지 않는다.** 그래서
`pyproject.toml` 선언에 검증 버전을 고정해 **손으로 작성**했다.

🔴 **작성 과정에서 결함 하나를 찾았다.** 전 소스의 `import` 를 AST 로 전수 스캔한 결과
**`pydantic` 이 [`src/argus/api/server.py`](src/argus/api/server.py) 에서 직접 쓰이는데
`pyproject.toml` 에 선언돼 있지 않았다.** `fastapi` 가 끌어오므로 지금까지 우연히
동작했지만 `fastapi` 가 의존을 바꾸면 조용히 깨진다. 선언에 추가했다.

`numpy` 는 넣지 않았다 — `scripts/build_embeddings.py` 만 쓰고 그 arm(`A2` 임베딩)은
측정 후 기각됐다. **서버 런타임 경로에 없다.**

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
| 1 | 소스코드 + 환경 구성 정보 + `README.md` | 이 저장소 · 이 파일 · `requirements.txt` | ✅ **완비** (2026-08-15) |
| 2 | 기술 제안서 (자유 양식 · 분량 제한 없음) | [`docs/proposal/technical-proposal.md`](docs/proposal/technical-proposal.md) | 🟡 **초안 — 본문 완결 (2026-08-15 · v4 성적·판정 전량 반영).** 🔴 **남은 빈칸 둘은 08-25 이후에만 채울 수 있다**: 새 heldout 18 성적(`R4`) · 배포 완주 기록(`R4-2` = `TBD-1`·`TBD-3`) |
| 3 | End-point URL + API 명세서 | **이 파일 §1 · §2** | ✅ **닫힘 (2026-08-17)** — `http://223.130.129.131/answer` · 명세 §2 |
| 4 | 전처리 산출물 (대용량은 스토리지 링크) | `artifacts/` · `evalset/` + 아래 스토리지 | ✅ **닫힘 (2026-08-17)** — [artifacts](https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link) · [data](https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing). 🔴 `artifacts/`·`data/` 는 gitignore 라 **저장소만으로는 시스템이 안 돈다** — 이 두 링크가 그 구멍을 메운다 |

---

## 7. 🔴 자리표시자 대장 — 누가 언제 채우는가

| # | 미정 항목 | 채우는 주체 | 시점 |
|---|---|---|---|
| ~~`TBD-1`~~ | ✅ **닫힘 (2026-08-17)** — `http://223.130.129.131/answer`. IP 는 09-20 까지 유지 · 09-07 이전 기동 필수 | — | — |
> ### 🔴 새로 클론한 사람이 시스템을 세우는 법
>
> 저장소에는 코드만 있다. `artifacts/`(1.3GB)·`data/`(5.2GB)는 gitignore 라 위 두 링크에서 받아야 한다.
>
> ```
> git clone … && cd ARGUS && pip install -r requirements.txt
> # artifacts 를 받아 저장소 루트에 artifacts/ 로 푼다
> # data 를 받아 저장소 루트에 data/ 로 푼다
> #   🔴 data/dart-disclosures/manifest.jsonl · universe.csv 가 없으면
> #      라우터가 70개사를 못 읽어 전 질의가 "범위 밖" 으로 떨어진다 (실측)
> PYTHONIOENCODING=utf-8 python -m pytest -q      # 1,409 통과
> ```

| ~~`TBD-2`~~ | ✅ **닫힘 (2026-08-15)** — `requirements.txt` 작성 완료. 🔴 `pip freeze` 가 아니라 `pyproject` 선언에 검증 버전을 고정해 손으로 썼다 | — | — |
| ~~`TBD-3`~~ | ✅ **닫힘 (2026-08-17)** — 구글 드라이브 업로드 완료. [artifacts](https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link) (1.3GB · 색인·레코드·계약상대 그래프) · [data](https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing) (5.2GB · 원본 공시 + `manifest.jsonl`·`universe.csv`). 무결성 대조표는 `artifacts/audit/preprocessing_manifest.json` (20개 SHA256) | — | — |
| `TBD-4` | 주최측 발신 IP 대역 (§1) | **주최측 공지 대기** | 미정 |
| `TBD-5` | 평가 서버 운영 일자·시간대 (§1) | **주최측 공지 대기** | 09-07 ~ 09-20 중 |

**공인 IP 는 한 번 받으면 09-20 까지 반납하지 않는다** — 반납 후 재획득하면 제출한 URL 이 죽는다.

> 🔴 **자리표시자가 아닌 "빈칸" 둘이 제안서 쪽에 더 있다** (2026-08-15 · v4 `R5` 확인).
> 이 대장의 `TBD-*` 는 **값을 적을 칸**이고, 아래 둘은 **아직 측정·실행되지 않은 것**이라
> 성격이 다르다. 섞어 세지 않는다.
>
> | 빈칸 | 무엇 | 채우는 주체 | 시점 |
> |---|---|---|---|
> | 새 heldout 18 성적 | `evalset/heldout.jsonl` 은 **2회 소진**이라 새로 18문항을 만들어야 한다 (`evalset/heldout_v2.jsonl`) | 개발 담당 | **08-25 코드 동결 직후** — 동결 전에 만들면 지금 heldout 을 태운 것과 같아진다 |
> | 배포 완주 기록 | NCP 콘솔 로그인 · SSH 키 · `.env` 전송이 필요해 자동화 구간 밖이다. 절차서는 [`deploy/RUNBOOK.md`](deploy/RUNBOOK.md) 에 완비 | **배포 담당(사용자)** | 08-25 ~ 08-31 · 완주하면 `TBD-1`·`TBD-3` 이 함께 닫힌다 |
>
> ~~그리고 🔴 **`A18`·`A10`·`A22` 세 arm 은 계획에 있었으나 재지 않았다**~~
> ✅ **닫힘 (2026-08-15)** — **셋 다 측정했다.** `A18`(n-ary)은 Δ 0 으로 기각,
> `A10`(SC k=3)·`A22`(다중 허브)도 측정 후 미채택. 정본은
> [`docs/pipeline/09-ablation.md`](docs/pipeline/09-ablation.md) §I-5g.
