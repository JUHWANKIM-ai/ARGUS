# ARGUS

**DART 전자공시 질의응답 에이전트**

제10회 미래에셋증권 AI Festival 출품작. 국내 상장사 70곳의 2023년 1월부터 2026년 3월까지
공시 문서를 대상으로, 자연어 질문에 **근거 공시를 명시한 답변**을 돌려준다.

```
질문   HD현대일렉트릭이 2023년 1월에 체결한 2500kVA 배전변압기 등 3,500대
       공급계약의 계약금액은 얼마인가?

답변   HD현대일렉트릭이 2023년 1월에 체결한 2500kVA 배전변압기 등 3,500대
       공급계약의 계약금액은 97,000,000,000원(970억 원)입니다.
       (근거: 단일판매ㆍ공급계약체결, 2023-01-31, 접수번호 20230131800162)
```

---

## 무엇을 푸는가

공시 질의응답은 검색과 생성을 잇는 것만으로 풀리지 않는다. 실무에서 부딪히는 벽이 셋이다.

**정정공시가 답을 바꾼다.** 한 계약이 최초 공시 이후 여러 차례 정정된다. 최신본만 봐도,
원본만 봐도 틀린다. 무엇이 언제 얼마로 바뀌었는지를 체인으로 따라가야 한다.

**답이 한 문서에 없다.** 계약상대를 축으로 여러 상장사의 공시를 모아야 비로소 답이 되는
질의가 있다. 합계와 차액은 문서 사이에서 계산된다.

**모르는 것을 모른다고 해야 한다.** 코퍼스 밖 기업, 아직 나오지 않은 보고서, 공시에
적히지 않는 미래 전망. 이때 그럴듯한 답을 지어내는 것이 가장 나쁜 실패다.

## 어떻게 푸는가

**계산과 검증을 모델에서 빼내 코드로 옮겼다.** 검색, 정정체인 추적, 집계, 수치 검증,
근거 표기, 안전 처리가 전부 결정적 규칙으로 돈다. 언어 모델은 답변 문장을 짓는 데만
쓴다. 파이프라인의 대부분이 LLM 호출 없이 끝나므로 응답이 빠르고 재현성이 높다.

**아는 값을 모델에게 맡기지 않는다.** 합계와 차액을 시스템이 먼저 계산해 근거 첫 줄에
싣고, 답변이 그 값을 쓰지 않으면 결정적으로 바로잡는다. 근거 표기의 접수번호 14자리도
모델이 옮겨 적게 하지 않고 시스템이 붙인다.

**답할 수 없는 질의를 앞에서 가른다.** 코퍼스 범위 밖, 지목한 보고서 부재, 지시대상 불명,
미래 추정 요구, 개인 식별번호 요구를 다섯 갈래로 나누어 언어 모델을 부르기 전에 한계를
고지하거나 되묻는다.

## 성능

| | 값 | 분모 |
|---|---|---|
| 평가셋 정확도 | 110/112 (98.2%) | 자체 골든셋. 응답 캐시 재생 기준 |
| **미공개 문항 정확도** | **49/54 (90.7%)** | **캐시 없이 새로 생성. 실전 기대값** |

두 수를 함께 싣는 이유는 앞의 수가 낙관적이기 때문이다. 골든셋 성적은 저장된 응답을
재생해 얻은 값이고, 실제 평가는 매 질의를 새로 생성한다. 온도를 0으로 고정해도 긴
답변의 문안은 회차마다 달라진다는 것을 실측으로 확인했다. 그래서 **한 번도 본 적 없는
문항 54개를 캐시 없이 돌린 90.7%** 를 기대값으로 인용한다.

질의 유형별로는 빈 칸이 없다.

| 유형 | Closed | Open |
|---|---|---|
| 검색 및 정보 추출 | 15/15 | 13/13 |
| 다중 조회 및 비교 연산 | 19/19 | 15/15 |
| 복합 문서 추론 | 15/16 | 14/15 |
| 안전성 및 정보 한계 | 19/19 | |

평가 지표 여덟 축은 다음과 같다. 지표마다 분모가 다르므로 함께 적는다.

| 지표 | 값 | 분모 |
|---|---|---|
| 정확성 | 97.3% | 112문항 전체 |
| 근거 완전성 | 97.0% | 답변 도출에 필수적인 근거 166건 |
| 요구사항 충족 | 99.1% | 112문항 전체 |
| 근거 기반 | 97.7% | 판정기 적용 대상 43문항 |
| 추론 논리성 | 97.3% | 112문항 전체. 실패 양식 일곱 중 다섯을 검사 |
| 안전성 및 신뢰성 | 100% | 안전성 트랙 12문항 |
| 정보 한계 대응 | 100% | 정보 한계 트랙 6문항 |
| 근거 표시 | 100% | 표기가 필요한 94문항 |

---

## 평가용 API

```
GET http://223.130.129.131/answer?question_id={id}&question={질의}
```

공인 IP 직접 접근. HTTP 표준 포트 80. 인증 헤더 없음.

### 응답

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고, 추론, 도구 사용 과정",
  "answer": "최종 생성 답변"
}
```

다섯 필드 모두 문자열이며 `Content-Type: application/json` 으로 내려간다.

### 운영 특성

| 항목 | 값 |
|---|---|
| 응답 시간 | 실측 최대 10.0초 (규약 상한 300초) |
| 오류 응답 | 없음. 내부 장애를 포함한 모든 경우에 200과 한계 고지를 돌려준다 |
| 동시성 | 순차 호출 1건 기준 |
| 가용 기간 | 2026-09-20까지 유지 |

5xx를 내지 않는 것은 설계 선택이다. 규약상 타임아웃과 5xx는 최대 2회 재시도되고 세 번
모두 실패하면 그 문항이 0점이 된다. 상류 장애가 나더라도 스키마를 지킨 한계 고지를
돌려주는 편이 안전하다.

### 호출 예시

```python
import requests

resp = requests.get(
    "http://223.130.129.131/answer",
    params={"question_id": "Q-001", "question": "평가 질의"},
    timeout=300,
)
result = resp.json()
```

---

## 환경 구성

파이썬 3.12 이상. 의존성은 `requirements.txt` 에 검증된 버전으로 고정되어 있다.

```bash
git clone <repository>
cd ARGUS
pip install -r requirements.txt
```

전처리 산출물과 코퍼스 대장은 용량 때문에 저장소에 포함하지 않았다. 아래에서 내려받아
저장소 루트에 푼다.

| 묶음 | 용량 | 내용 |
|---|---|---|
| [artifacts](https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link) | 1.3GB | 색인, 정형 레코드, 계약상대 그래프 |
| [data](https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing) | 5.2GB | 원본 공시, 문서 대장, 기업 목록 |

`data/dart-disclosures/manifest.jsonl` 과 `universe.csv` 가 없으면 라우터가 대상 기업
70곳을 읽지 못해 모든 질의가 범위 밖으로 처리된다. 반드시 함께 배치한다.

무결성 대조표는 `artifacts/audit/preprocessing_manifest.json` 에 있다. 파일 20개의
SHA256과 바이트 수를 담고 있다.

### 실행

```bash
# API 서버
python -m uvicorn argus.api.server:app --host 0.0.0.0 --port 8000
# 평가 서버는 systemd 로 포트 80에서 구동한다. deploy/README.md 참고

# 테스트
python -m pytest -q
```

### 색인 재생성

전처리 산출물을 직접 만들려면 아래를 순서대로 실행한다. 언어 모델을 호출하지 않는다.

```bash
python scripts/build_index.py --group exchange   # 거래소공시 레코드와 BM25
python scripts/build_index.py --group major      # 주요사항보고
python scripts/build_index.py --group holding    # 지분공시
python scripts/build_sections.py                 # 정기공시 섹션 색인
python scripts/build_counterparty_graph.py       # 계약상대 2-hop 그래프
python scripts/build_nary.py                     # 규칙이 놓친 관계 보강
```

---

## 저장소 구조

```
src/argus/
  api/          FastAPI 서버. 다섯 필드 스키마를 고정한다
  agent/        라우팅, 검색, 집계, 검증, 처분
  corpus/       공시 파싱, 섹션 분해, 정정 관계 추출
  retrieve/     BM25 색인
  ablation.py   구성 스위치 레지스트리

evalset/        평가 문항과 정답
scripts/        색인 빌드, 측정, 채점
tests/          1,409개
docs/           설계 문서와 측정 기록
deploy/         배포 절차와 systemd 유닛
```

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/baseline-final-2026-08-17.md`](docs/baseline-final-2026-08-17.md) | 제출 구성 정본. 성적, 구성, 검증 기록 |
| [`docs/compliance-audit-v5-A38-2026-08-17.md`](docs/compliance-audit-v5-A38-2026-08-17.md) | 규약 준수 감사 |
| [`docs/CONVENTION.md`](docs/CONVENTION.md) | 과제 규약 정리 |
| [`docs/pipeline/`](docs/pipeline/) | 단계별 설계와 측정 |
| [`deploy/README.md`](deploy/README.md) | 배포 절차 |
| [`docs/figures/argus-algorithm-v6.drawio`](docs/figures/argus-algorithm-v6.drawio) | 파이프라인 도면 |

---

## 제출물

| | 항목 | 위치 |
|---|---|---|
| 1 | 소스코드 및 환경 정의 | 본 저장소, `requirements.txt` |
| 2 | 기술 제안서 | `docs/proposal/` |
| 3 | End-point URL 및 API 명세 | 본 문서 |
| 4 | 전처리 산출물 | [artifacts](https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link) (1.3GB) · [data](https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing) (5.2GB) · `evalset/` |

주최 측 공지를 기다리는 항목이 둘 있다. 발신 IP 대역이 공지되면 방화벽 인바운드를 해당
대역으로 좁히고, 평가 운영 일자가 공지되면 그 기간에 맞추어 서버를 운영한다.
