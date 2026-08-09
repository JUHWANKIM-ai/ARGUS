# Phase D — Retriever

**policy §8** · 크레딧 **0** · 선행 [03-graph-schema](03-graph-schema.md)

> 같은 모델이라도 Chunk 전략에 따라 성능이 크게 달라진다. — policy §8

---

## D-1. 현재 상태

| 항목 | 값 |
|---|---|
| 색인 | exchange 1,469건 (`exchange_bm25.json` 0.7MB) |
| 검색 | BM25 + 메타 프리필터 |
| 필터 | `corp_name` 완전일치 · `rcept_dt` **prefix** |
| 토크나이저 | 어절 + 한글 문자 bigram |
| 실측 | Q-001 골든 **14/1,332 → 1/1** |
| **미구현** | **periodic 섹션 인덱스** — 정기공시 질의 근거 0건 |

**메타 프리필터가 가장 큰 효과였다.** `corp_name` + `ym=202301` 로 1,469 → 1건.
policy §8 의 Metadata 항목이 우리 코퍼스에서 결정적이라는 실측 근거다.

---

## D-2. periodic 섹션 인덱스 — 최우선

정기공시는 전체 토큰의 95%이고 평가 질의의 절반이 여기서 나온다. **없으면 답할 수 없다.**

```
periodic_sections.jsonl  (CONVENTION §7.3 슬라이드 스키마 그대로)
{
  "chunk_id": "periodic_20260310002820#008",
  "doc_id":   "periodic_20260310002820",
  "corp_code": "00126380", "corp_name": "삼성전자",
  "sector": "반도체·전자부품",
  "doc_subtype": "annual", "base_year": 2025, "base_month": 12,
  "rcept_dt": "20260310",
  "path": "II. 사업의 내용 > 2. 주요 제품 및 서비스",
  "n_chars": 1008, "n_tables": 3,
  "text": "..."
}
```

**설계 결정**

| 항목 | 결정 | 근거 |
|---|---|---|
| 청킹 단위 | **목차 섹션** | 설명회 권고 · GraphReader |
| 스텁 배제 | `n_chars < 100` 제외 | 주최 측 `list_sections` 명세 |
| 표 처리 | 마크다운 인라인 (분리는 D-3 실험) | 설명회 권고 |

> **비용 주의**: periodic 1,054건, 전량 텍스트 680MB. `text` 를 통째로 넣으면 인덱스가 커진다.
> **본문은 파일 오프셋만 저장**하고 `read_section` 이 필요할 때 읽는 lazy 방식을 검토한다
> (서버 메모리 상한 2.8GiB).

---

## D-3. 실험 축 — policy §8

| 축 | 후보 | 판정 근거 |
|---|---|---|
| Chunk 단위 | 레코드 / **섹션** / 고정 길이 | [01-eda](01-eda.md) 길이 분포 |
| Parent-Child | 섹션(child) → 문서(parent) | `read_section` 이 이미 이 구조 |
| 표 분리 | 인라인 / 별도 청크 | [01-eda](01-eda.md) 표 비율 |
| 토크나이저 | 어절 / **어절+bigram** | bigram 은 리콜↑ **랭킹↓** (실측) |
| 메타 필터 | corp / +기간 / +doc_subtype | 기간이 결정적 (실측) |
| 임베딩 | 없음 / 한국어 임베딩 | **G1 확정 후** |
| 하이브리드 | BM25 / BM25+Vector RRF | 동일 |

### bigram 트레이드오프 — 실측

```
'변압기' 질의 → '배전변압기' 문서
  bigram 없음 : 매칭 불가        (리콜 0)
  bigram 있음 : 매칭되나 28/28   (정확 토큰 문서가 밀어올림)
  기간 필터   : 1/1              ← 실제 해법
```

**bigram 은 프리필터가 약할 때의 보험**이지 랭킹 개선 수단이 아니다.

---

## D-4. 지표

```
Recall@1 / @5 / @20     골든 rcept_no 가 후보에 있는가
MRR                      순위 품질
후보 축소율              프리필터 전후 후보 수
지연                     검색 ms (현재 2ms)
```

**축별로 나눠 본다** — [02-golden-set](02-golden-set.md) 의 3축 그대로.
난이도 '상' 의 Recall 이 낮으면 [08](08-error-analysis.md) 의 `RETRIEVAL` 병목 예고다.

---

## 게이트 D

- [ ] `periodic_sections.jsonl` 생성, `list_sections`/`read_section` 이 error 를 반환하지 않음
- [ ] dev 60문항에서 **Recall@5 ≥ 0.85**
- [ ] 난이도 상/중/하 각각의 Recall@5 기록
- [ ] 인덱스 메모리 < 500MB
