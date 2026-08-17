# ARGUS

**DART 전자공시 질의응답 에이전트**

제10회 미래에셋증권 AI Festival 출품작

---

## 목차

1. [개요](#1-개요)
2. [문제 정의](#2-문제-정의)
3. [시스템 아키텍처](#3-시스템-아키텍처)
4. [핵심 기술](#4-핵심-기술)
5. [성능 평가](#5-성능-평가)
6. [리스크 관리](#6-리스크-관리)
7. [운영 및 배포](#7-운영-및-배포)
8. [제출물](#8-제출물)
9. [부록](#9-부록)
10. [참고 문헌](#10-참고-문헌)

---

## 1. 개요

ARGUS는 국내 상장사 70곳의 전자공시를 대상으로 자연어 질의에 답하는 에이전트다.
대상 기간은 2023년 1월부터 2026년 3월까지이며, 모든 답변에 근거 공시를 명시한다.

```
질의   HD현대일렉트릭이 2023년 1월에 체결한 2500kVA 배전변압기 등 3,500대
       공급계약의 계약금액은 얼마인가?

응답   HD현대일렉트릭이 2023년 1월에 체결한 2500kVA 배전변압기 등 3,500대
       공급계약의 계약금액은 97,000,000,000원(970억 원)입니다.
       (근거: 단일판매ㆍ공급계약체결, 2023-01-31, 접수번호 20230131800162)
```

설계 원칙은 하나다. 검증할 수 있는 것은 언어 모델에 맡기지 않는다. 검색, 정정 이력
추적, 수치 계산, 근거 표기, 안전 처리를 결정적 규칙으로 구현하고, 언어 모델은 답변
문장 생성에만 사용한다.

| 구분 | 값 |
|---|---|
| 생성 모델 | HyperCLOVA X (HCX-005) |
| 대상 코퍼스 | 상장사 70곳, 2023.01 ~ 2026.03, 공시 4,902건 |
| 평가셋 정확도 | 110/112 (98.2%) |
| 미공개 문항 정확도 | 49/54 (90.7%) |
| 응답 시간 | 실측 최대 10.0초 |
| 자동화 시험 | 1,420건 |

---

## 2. 문제 정의

공시 질의응답은 문서 검색과 문장 생성을 연결하는 것만으로 성립하지 않는다. 구조적
난점은 셋이다.

### 2.1 정정공시가 정답을 바꾼다
 한 계약이 최초 공시 이후 여러 차례 정정된다. 최신본만 봐도,
원본만 봐도 틀린다. 무엇이 언제 얼마로 바뀌었는지를 체인으로 따라가야 한다.

### 2.2 정답이 단일 문서에 존재하지 않는다
 계약상대를 축으로 여러 상장사의 공시를 모아야 비로소 답이 되는
질의가 있다. 합계와 차액은 문서 사이에서 계산된다.

### 2.3 답할 수 없는 질의를 식별해야 한다
 코퍼스 밖 기업, 아직 나오지 않은 보고서, 공시에
적히지 않는 미래 전망. 이때 그럴듯한 답을 지어내는 것이 가장 나쁜 실패다.

---

## 3. 시스템 아키텍처

### 3.1 계산과 검증을 코드로 옮긴다
 검색, 정정체인 추적, 집계, 수치 검증,
근거 표기, 안전 처리가 전부 결정적 규칙으로 돈다. 언어 모델은 답변 문장을 짓는 데만
쓴다. 파이프라인의 대부분이 LLM 호출 없이 끝나므로 응답이 빠르고 재현성이 높다.

### 3.2 아는 값은 모델에 맡기지 않는다
 합계와 차액을 시스템이 먼저 계산해 근거 첫 줄에
싣고, 답변이 그 값을 쓰지 않으면 결정적으로 바로잡는다. 근거 표기의 접수번호 14자리도
모델이 옮겨 적게 하지 않고 시스템이 붙인다.

### 3.3 답할 수 없는 질의를 앞에서 가른다
 코퍼스 범위 밖, 지목한 보고서 부재, 지시대상 불명,
미래 추정 요구, 개인 식별번호 요구를 다섯 갈래로 나누어 언어 모델을 부르기 전에 한계를
고지하거나 되묻는다.

---

## 4. 핵심 기술

대괄호 번호는 [10.4 문헌 목록](#104-문헌-목록)을 가리킨다. **채택한 것과 재고 후 기각한
것을 같은 자리에 적는다.** 무엇을 안 썼는지가 무엇을 썼는지만큼 설계를 설명하기 때문이다.
판정 근거는 [10.2](#102-재고-후-기각한-것)에 있다.

생성 자체는 두 문헌 위에 선다. 가중치를 고치지 않고 문맥만으로 과제를 수행한다는 전제가
GPT-3 [1]이고, 과제 규약이 베이스 모델 학습을 막으므로 이 전제가 접근의 출발점이다.
답보다 근거와 계산을 먼저 쓰게 하는 순서는 Chain-of-Thought [2]에서 왔고, 응답의
`think_trace` 필드가 그 산출물이다. 다만 그 논문의 few-shot exemplar 기법은 쓰지 않는다.
시스템 프롬프트가 attention의 상당 부분을 차지한다는 보고 [15]에 따라 규칙 수를 최소로
유지한다.

### 4.1 계층적 검색

단일 전략으로는 공시 질의의 다양성을 감당할 수 없어 네 경로를 병행한다.

| 경로 | 대상 | 용도 |
|---|---|---|
| 메타 필터 | 기업명, 접수일, 공시 종류 | 후보 축소 |
| BM25 색인 | 거래소공시, 주요사항보고, 지분공시 | 본문 검색 |
| 섹션 색인 | 정기공시 목차 경로 | 사업보고서 내부 탐색 |
| 계약상대 그래프 | 발주처 2-hop 관계 | 문서 간 열거 |

근거가 부족하면 질의를 재구성해 최대 3회 재검색한다.

검색과 추론을 교차하는 다단계 구조는 ReAct [4]에서 왔다. **행동 선택만 다르다** — 매 hop을
언어 모델이 고르지 않고 `plan_section_hops`가 만든 결정적 계획이 정한다. 정기공시를 목차
경로로 나눠 들어가는 방식은 GraphReader [10], 구조 검색과 본문 검색을 번갈아 도는 형태는
Think-on-Graph 2.0 [11]의 발상이며 두 논문의 원 기제는 구현하지 않았다.

**본문 검색은 BM25 하나다.** 임베딩 융합 [9][11]은 스위치를 세워 재고 후 기각했다
(밸류체인 7/12 대 최종 10/12). 운영 경로에서 임베딩 API를 호출하지 않는다. 계약상대
그래프도 이진 관계다. 공시 팩트를 n-ary 하이퍼엣지로 표현하자는 처방 [12]은 우리 코퍼스의
arity ≤ 2가 58.4%로 과반이라 **논문의 전제가 성립하지 않아** 기각했다. 서브그래프를 PCST로
최적화하는 처방 [7]은 근거가 이미 문맥에 들어오는 분기라 절감할 대상이 없다. 그 분기는
논문 자신이 적어 두었다.

### 4.2 정정 이력 추적

공시 간 참조 관계를 사전에 추출해 그래프로 보관한다. 정정본에서 원공시로 거슬러
올라가는 역방향 탐색과 원공시에서 후속 공시로 내려가는 순방향 탐색을 모두 지원하며,
정정 이력 질의는 체인 전체의 금액 변화를 접수일 순으로 근거에 싣는다.

**이 갈래에는 참조 문헌이 없다.** 정정공시 체인은 국내 공시 제도의 구조라 도메인에서 직접
설계했다. 사전 구축 그래프의 불완전성을 질의 시점에 메우는 처방 [13]과는 방향이 반대다.
그쪽은 끊긴 경로를 즉석에서 복구하고, 이쪽은 참조 관계가 원문에 명시되어 있어 미리
추출해 둔다.

### 4.3 결정적 계산

합계, 차액, 평균은 근거 레코드의 금액 필드를 직접 읽어 시스템이 계산하고 결과를 근거
첫 줄에 배치한다. 계산 조건이 유일하게 결정되지 않으면 아무것도 싣지 않는다. 잘못된
값을 확신 있게 제시하는 것은 제시하지 않는 것보다 나쁘기 때문이다.

계산을 언어 모델 밖으로 빼는 발상은 Chain-of-Thought 부록 B [2]의 외부 계산기다.
Toolformer [5]는 같은 문제를 **모델이 스스로 도구를 호출하게** 해서 푸는데, 그 처방은
재고 후 기각했다(74/88 대 81/88). 호출 시점과 인자를 모델이 정하는 순간 검증할 수 없는
자유도가 하나 늘고, 우리 실측에서는 그것이 성적을 깎았다. 그래서 호출을 모델에 맡기지
않고 코드가 레코드의 금액 필드에서 직접 계산한다. 여러 근거를 하나로 모으는 집계 단계를
따로 두는 것은 Graph of Thoughts [6]의 발상이다.

### 4.4 검증과 처분

생성된 답변에 결정적 검증 네 가지를 적용한다.

| 검증 | 확인 대상 |
|---|---|
| 재계산 | 답변의 계산 결과가 근거와 일치하는가 |
| 답변 내부 산술 | 답변에 제시된 수식이 성립하는가 |
| 수치 출처 | 답변의 수치가 근거에 존재하는가 |
| 완전성 | 필요한 근거가 누락되지 않았는가 |

검증 실패 시 열 가지 처분 중 해당하는 것을 적용한다. 각주 부착, 재계산값 치환, 1회
재생성, 근거 표기 부착, 단위 환산 교정, 반올림 복원, 결과값 보완 등이며 전부 결정적
규칙이다. 접수번호 14자리처럼 전사 오류가 발생하기 쉬운 값은 모델에 옮겨 적게 하지
않고 시스템이 부착한다.

「답변 내부 산술」과 재계산값 치환은 Chain-of-Thought 부록 B [2]가 명시한 절차의 재현이다.
그 논문은 생성된 등식을 외부 계산기로 다시 풀고 그 결과를 다음 등식으로 전파한다.
생성 뒤에 판정을 두는 배치 자체는 SWE-Lancer [18]의 결과를 근거로 한다. 그 벤치마크에서
모델은 구현보다 제안 판단에서 더 높은 점수를 냈다. 판정이 생성보다 쉽다면 생성한 답을
다시 판정하는 단계에 값이 있다.

**같은 이유로 자기일관성 [3]은 기각했다.** k개 경로를 표집해 다수결하는 처방인데, 밸류체인
5/12로 기준 6/12보다 낮았다. 표집 비용을 한 번의 결정적 검증에 쓰는 편이 나았다. 최종
구성은 `sc_k=1`·`temperature=0.0`이다. 검증의 실효를 재는 데는 골든 근거를 강제 주입하는
스위치를 쓰는데, 그 측정 설계가 Diagnosing Evidence Utilization [15]에서 왔다. 근거를
줘도 모델이 쓰지 않는다는 진단이라, 근거를 넣은 것과 답에 반영된 것을 갈라 세야 한다.

### 4.5 안전 처리

답할 수 없는 질의를 다섯 갈래로 분류해 생성 이전에 처리한다.

| 갈래 | 대응 |
|---|---|
| 코퍼스 범위 밖 | 대상 범위를 명시하고 한계 고지 |
| 지목한 보고서 부재 | 어느 보고서인지 되묻기 |
| 지시대상 불명 | 어느 기업인지 되묻기 |
| 미래 추정 요구 | 공시 기재사항이 아님을 고지 |
| 개인 식별번호 요구 | 제공 불가 고지 |

각 갈래는 두 신호의 곱집합으로만 발화한다. 단일 신호로 판정하면 정상 질의가 거절되기
때문이며, 평가셋 112문항 전수에서 오탐 0건을 확인했다.

개인정보 마스킹은 별도 계층으로 동작한다. 질의에 포함된 주민등록번호와 연락처는 응답에
노출되지 않으며, 법인등록번호처럼 공시에 정당하게 기재된 식별번호는 보존한다.

**이 갈래에도 참조 문헌이 없다.** 과제 규약의 「정보 한계 대응」 요구에서 갈래를 뽑고
실측한 오답에서 신호를 정했다. 다섯 갈래를 한 게이트로 합치지 않은 것도 측정 때문이다.
한계의 종류가 다르면 고지도 달라야 하고, 함께 켜면 어느 갈래가 무엇을 막았는지 귀속할 수
없다.

---

## 5. 성능 평가

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

---

## 6. 리스크 관리

| 리스크 | 대응 |
|---|---|
| 평가 중 서버 장애 | 내부 예외를 포함한 모든 경우에 200과 한계 고지 반환 |
| 응답 지연 | 실측 최대 10.0초로 규약 상한 300초 대비 30배 여유 |
| 환각 생성 | 수치 출처 검증으로 근거에 없는 수치를 탐지 |
| 개인정보 노출 | 라우팅 차단과 출력 마스킹 2중 계층 |
| 코퍼스 무결성 | 색인과 레코드를 SHA256으로 고정, 배포본과 개발본 일치 확인 |
| 구성 변경 사고 | 자동화 시험 1,420건이 제출 구성과 기능별 동작을 고정 |

5xx를 반환하지 않는 것은 설계 선택이다. 규약상 타임아웃과 5xx는 최대 2회 재시도되며
세 번 모두 실패하면 해당 문항이 0점 처리된다. 상류 장애가 발생하더라도 응답 스키마를
지킨 한계 고지를 반환하는 편이 안전하다.

---

## 7. 운영 및 배포

### 7.1 평가용 API

```
GET http://223.130.129.131/answer?question_id={id}&question={질의}
```

공인 IP 직접 접근. HTTP 표준 포트 80. 인증 헤더 없음.

**응답**

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

**운영 특성**

| 항목 | 값 |
|---|---|
| 응답 시간 | 실측 최대 10.0초 (규약 상한 300초) |
| 오류 응답 | 없음. 내부 장애를 포함한 모든 경우에 200과 한계 고지를 돌려준다 |
| 동시성 | 순차 호출 1건 기준 |
| 운영 기간 | 2026-09-07 ~ 2026-09-20 전 기간 가동 |

5xx를 내지 않는 것은 설계 선택이다. 규약상 타임아웃과 5xx는 최대 2회 재시도되고 세 번
모두 실패하면 그 문항이 0점이 된다. 상류 장애가 나더라도 스키마를 지킨 한계 고지를
돌려주는 편이 안전하다.

**호출 예시**

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

### 7.2 환경 구성

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
| artifacts | 1.3GB | 색인, 정형 레코드, 계약상대 그래프 |
| data | 5.2GB | 원본 공시, 문서 대장, 기업 목록 |

```
artifacts   https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link
data        https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing
```

`data/dart-disclosures/manifest.jsonl` 과 `universe.csv` 가 없으면 라우터가 대상 기업
70곳을 읽지 못해 모든 질의가 범위 밖으로 처리된다. 반드시 함께 배치한다.

무결성 대조표는 `artifacts/audit/preprocessing_manifest.json` 에 있다. 파일 20개의
SHA256과 바이트 수를 담고 있다.

### 7.3 실행

```bash
# API 서버
python -m uvicorn argus.api.server:app --host 0.0.0.0 --port 8000
# 평가 서버는 systemd 로 포트 80에서 구동한다. deploy/README.md 참고

# 테스트
python -m pytest -q
```

### 7.4 전처리 재실행

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

---

## 8. 제출물

| | 항목 | 위치 |
|---|---|---|
| 1 | 소스코드 및 환경 정의 | 본 저장소, `requirements.txt` |
| 2 | 기술 제안서 | `docs/proposal/` |
| 3 | End-point URL 및 API 명세 | [7.1 평가용 API](#71-평가용-api) |
| 4 | 전처리 산출물 | 아래 스토리지, `evalset/` |

전처리 산출물 스토리지

```
artifacts (1.3GB)   https://drive.google.com/file/d/12NBmEUjV1t5o3BbJK16z6rptESr9gbUH/view?usp=drive_link
data      (5.2GB)   https://drive.google.com/drive/folders/1IPNdXxBFVXq3dSCyARAzD_m1fiEOZIsJ?usp=sharing
```

평가 운영 기간은 2026년 9월 7일부터 9월 20일까지다. 해당 기간 전체에 걸쳐 서버를
가동한 상태로 유지하며, 기간 중 정지 계획은 없다.

접근 제어는 현재 구성을 유지한다. 주최 측이 별도 IP 제한 없이 현 상태로 진행 가능함을
확인했으므로, 표준 포트 80을 개방한 채 운영한다.

---

## 9. 부록

### 9.1 저장소 구조

```
src/argus/
  api/          FastAPI 서버. 다섯 필드 스키마를 고정한다
  agent/        라우팅, 검색, 집계, 검증, 처분
  corpus/       공시 파싱, 섹션 분해, 정정 관계 추출
  retrieve/     BM25 색인
  ablation.py   구성 스위치 레지스트리

evalset/        평가 문항과 정답
scripts/        색인 빌드, 측정, 채점
tests/          1,420개
docs/           설계 문서와 측정 기록
deploy/         배포 절차와 systemd 유닛
```

### 9.2 관련 문서

| 문서 | 내용 |
|---|---|
| [`docs/baseline-final-2026-08-17.md`](docs/baseline-final-2026-08-17.md) | 제출 구성 정본. 성적, 구성, 검증 기록 |
| [`docs/compliance-audit-v5-A38-2026-08-17.md`](docs/compliance-audit-v5-A38-2026-08-17.md) | 규약 준수 감사 |
| [`docs/CONVENTION.md`](docs/CONVENTION.md) | 과제 규약 정리 |
| [`docs/pipeline/`](docs/pipeline/) | 단계별 설계와 측정 |
| [`deploy/README.md`](deploy/README.md) | 배포 절차 |
| [`docs/figures/argus-algorithm-v6.drawio`](docs/figures/argus-algorithm-v6.drawio) | 파이프라인 도면 |

---

## 10. 참고 문헌

도면과 파이프라인 설계에 참조한 문헌 18편이다. 세 갈래로 나눈다. **논문에 있다는 것은
채택 사유가 아니다.** 처방마다 구성 스위치를 세워 우리 코퍼스에서 재고, 그 결과로 갈랐다.
기각도 결과이므로 함께 적는다. 측정 절차는 [`docs/pipeline/09-ablation.md`](docs/pipeline/09-ablation.md),
스위치 정의는 [`src/argus/ablation.py`](src/argus/ablation.py)에 있다.

### 10.1 최종 베이스라인이 채택한 것

| 문헌 | 가져온 것 | 구현 위치 |
|---|---|---|
| Chain-of-Thought [2] | 답변보다 근거와 계산을 먼저 쓰게 하는 순서 | `agent/prompts.py` 트리거 문장, 응답의 `think_trace` 필드 |
| Chain-of-Thought 부록 B [2] | 생성된 등식을 외부 계산기로 재계산하고 결과를 전파 | `agent/critic.py`의 `verify_answer_arithmetic`, `substitute_verified`·`regenerate_on_verify` |
| ReAct [4] | 검색과 추론을 교차하는 다단계 루프 | `agent/pipeline.py`의 hop 사다리. **행동 선택은 LLM이 아니다** — `plan_section_hops`가 만든 결정적 계획을 따른다 |
| GPT-3 [1] | 가중치를 고치지 않고 문맥만으로 과제를 수행한다는 전제 | 과제 규약상 베이스 모델 추가 학습이 불가하므로 이 전제가 접근 자체의 근거다. 다만 few-shot exemplar 기법은 쓰지 않는다 |
| Diagnosing Evidence Utilization [15] | 골든 근거를 줘도 모델이 그것을 쓰지 않는다는 진단 | `golden_oracle` 스위치로 근거 이용률을 분리 측정. 시스템 프롬프트가 attention을 크게 먹는다는 보고에 따라 규칙 수를 최소로 유지 |

### 10.2 재고 후 기각한 것

스위치는 저장소에 남아 있고 최종 구성(`A42`)에서 꺼져 있다. 재현 가능하다.

| 문헌 | 처방 | 우리 코퍼스 실측 | 스위치 |
|---|---|---|---|
| Self-Consistency [3] | k개 경로를 표집해 다수결 | 밸류체인 5/12 대 기준 6/12. 순증 0, 비용만 증가 | `sc_k=3`·`temperature=0.7` (최종은 `sc_k=1`·`0.0`) |
| Toolformer [5] | 산술을 모델이 아니라 도구 호출에 넘긴다 | 전체 74/88 대 기준 81/88 | `function_calling` |
| HyperGraphRAG [12] | n-ary 관계를 하이퍼엣지로 표현 | 코퍼스의 arity ≤ 2가 58.4%로 과반이라 **논문의 전제가 성립하지 않는다.** 전체 81/88·밸류체인 6/12로 기준과 동일 | `nary` |
| G-Retriever [7] | PCST로 서브그래프를 최적화해 토큰을 줄인다 | 근거가 이미 문맥에 들어가는 분기라 절감 대상이 없다. 논문 자신이 이 분기를 적어 두었다 | 미구현 (판정 후 미착수) |
| 밀집 검색 [9][11] | 임베딩 기반 검색을 BM25와 융합 | 밸류체인 7/12 대 최종 10/12 | `dense`. 운영 경로에서 임베딩 API를 호출하지 않는다 |

### 10.3 발상만 반영한 것

원 기제는 구현하지 않았다. PCST, PPR, Leiden 커뮤니티, 하이퍼엣지, GRPO, 역할극 대화는
저장소 코드에 각각 0곳이다.

| 문헌 | 남은 것 |
|---|---|
| Think-on-Graph 2.0 [11] | 구조 검색과 본문 검색을 번갈아 도는 루프 형태 |
| GraphReader [10] | 긴 문서를 목차 경로로 나눠 탐색하고 확인한 사실을 메모로 들고 다니는 방식 |
| Graph of Thoughts [6] | 여러 근거를 모아 하나로 집계하는 단계를 별도로 두는 것 |
| GraphRAG [8] | 전역 요약과 국소 조회를 가르는 관점. 커뮤니티 요약은 코퍼스 규모상 미적용 |
| Relink [13] | 사전 구축 그래프의 불완전성을 질의 시점에 메우는 발상 |
| GraphRAG-R1 [14] | 얕은 검색과 과잉 탐색을 함께 억제해야 한다는 관점. 보상 학습 대신 예산 가드로 구현 |
| CAMEL [16], AutoGen [17] | 역할 분리 발상. ARGUS는 단일 에이전트이며 검증자를 별도 역할로 두는 데까지만 반영 |
| SWE-Lancer [18] | 판단이 생성보다 쉽다는 결과. 생성 뒤 결정적 검증을 두는 설계의 근거 |

### 10.4 문헌 목록

프롬프팅과 에이전트 기초

1. Brown, T. B., Mann, B., Ryder, N. 외. **Language Models are Few-Shot Learners.** NeurIPS 2020. arXiv:2005.14165
2. Wei, J., Wang, X., Schuurmans, D. 외. **Chain-of-Thought Prompting Elicits Reasoning in Large Language Models.** NeurIPS 2022. arXiv:2201.11903
3. Wang, X., Wei, J., Schuurmans, D. 외. **Self-Consistency Improves Chain of Thought Reasoning in Language Models.** ICLR 2023. arXiv:2203.11171
4. Yao, S., Zhao, J., Yu, D. 외. **ReAct: Synergizing Reasoning and Acting in Language Models.** ICLR 2023. arXiv:2210.03629
5. Schick, T., Dwivedi-Yu, J., Dessì, R. 외. **Toolformer: Language Models Can Teach Themselves to Use Tools.** Meta AI Research, 2023. arXiv:2302.04761

그래프 기반 검색증강생성

6. Besta, M., Blach, N., Kubicek, A. 외. **Graph of Thoughts: Solving Elaborate Problems with Large Language Models.** ETH Zurich, 2024. arXiv:2308.09687
7. He, X., Tian, Y., Sun, Y. 외. **G-Retriever: Retrieval-Augmented Generation for Textual Graph Understanding and Question Answering.** NeurIPS 2024. arXiv:2402.07630
8. Edge, D., Trinh, H., Cheng, N. 외. **From Local to Global: A GraphRAG Approach to Query-Focused Summarization.** Microsoft Research, 2024. arXiv:2404.16130
9. Gutiérrez, B. J., Shu, Y., Gu, Y. 외. **HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models.** NeurIPS 2024. arXiv:2405.14831
10. Li, S., He, Y., Guo, H. 외. **GraphReader: Building Graph-based Agent to Enhance Long-Context Abilities of Large Language Models.** Alibaba Group, 2024. arXiv:2406.14550
11. Ma, S., Xu, C., Jiang, X. 외. **Think-on-Graph 2.0: Deep and Faithful Large Language Model Reasoning with Knowledge-guided Retrieval Augmented Generation.** ICLR 2025. arXiv:2407.10805
12. Luo, H., E, H., Chen, G. 외. **HyperGraphRAG: Retrieval-Augmented Generation via Hypergraph-Structured Knowledge Representation.** NeurIPS 2025.
13. Huang, M., Bu, C., He, Y. 외. **Relink: Constructing Query-Driven Evidence Graph On-the-Fly for GraphRAG.** AAAI-26 (The Fortieth AAAI Conference on Artificial Intelligence).
14. Yu, C., Zhao, K., Li, Y. 외. **GraphRAG-R1: Graph Retrieval-Augmented Generation with Process-Constrained Reinforcement Learning.** WWW '26. doi:10.1145/3774904.3792589

진단과 평가

15. Basak, D., Bose, D., Goswami, K., Desarkar, M. S. **Diagnosing Evidence Utilization in Multimodal Document Question Answering.** KDD '26. doi:10.1145/3770855.3817956

멀티에이전트

16. Li, G., Hammoud, H. A. A. K., Itani, H. 외. **CAMEL: Communicative Agents for "Mind" Exploration of Large Language Model Society.** NeurIPS 2023. arXiv:2303.17760
17. Wu, Q., Bansal, G., Zhang, J. 외. **AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation.** Microsoft Research, 2023. arXiv:2308.08155
18. Miserendino, S., Wang, M., Patwardhan, T., Heidecke, J. **SWE-Lancer: Can Frontier LLMs Earn $1 Million from Real-World Freelance Software Engineering?** OpenAI, ICML 2025. arXiv:2502.12115

---
