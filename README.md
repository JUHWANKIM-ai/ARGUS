# ARGUS

DART 공시 질의응답 Agent. 거래소공시를 정규화·색인해 두고, 고정 스키마의
`GET /answer` 로 답변과 근거를 돌려준다.

## 구조

| 경로 | 책임 |
|---|---|
| `src/argus/config.py` | `.env` 로딩, 코퍼스·산출물 경로 해석, 상수 |
| `src/argus/clova.py` | HyperCLOVA X 호출 단일 창구. 토큰·지연 기록 |
| `src/argus/corpus/` | manifest 프리필터 · HTML 파싱 · 정규화 · 마크다운 · 정정체인 · 날짜 연산 |
| `src/argus/retrieve/bm25.py` | BM25 인덱스 생성·검색 (LLM 호출 없음) |
| `src/argus/api/server.py` | FastAPI `GET /answer` · `GET /health` |
| `scripts/build_index.py` | 코퍼스 → `artifacts/` 인덱스 빌드 CLI |
| `scripts/eval_e2e.py` | E2E 평가 하네스 → `artifacts/eval_report.json` |
| `evalset/questions.jsonl` | 평가 질의셋 |
| `deploy/` | `setup.sh` · `Dockerfile` · `argus.service` (제출 요건) |

## 준비

코퍼스 원본(5.2GB)은 저장소에 없다. `data/dart-disclosures/` 에 배치하고,
API 키는 `.env` 의 `CLOVA_API_KEY` 로 넣는다. 둘 다 `.gitignore` 대상이다.

```bash
pip install -e ".[dev]"
```

## 인덱스 빌드

```bash
python scripts/build_index.py --group exchange
```

`artifacts/exchange_records.jsonl` 과 `artifacts/exchange_bm25.json` 이 생긴다.
LLM 호출은 0회다. `--limit N` 으로 부분 빌드, `--out-dir` 로 출력 경로를 바꾼다.

## 서버 실행

```bash
uvicorn argus.api.server:app --host 0.0.0.0 --port 8000
curl -s "http://127.0.0.1:8000/answer?question_id=Q-001&question=테스트"
```

응답은 `question_id` · `question` · `retrieved_context` · `think_trace` · `answer`
5개 키 고정이다 (`docs/CONVENTION.md` §4).

## 테스트와 평가

```bash
python -m pytest tests/ -v
python scripts/eval_e2e.py
```

## 배포

`deploy/README.md` 참고.
