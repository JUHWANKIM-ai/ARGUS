# 구글 드라이브 업로드 목록 (2026-08-17)

> 대조표: artifacts/audit/preprocessing_manifest.json (20개 전부의 SHA256)
> 업로드 뒤 링크를 주시면 README 의 TBD-3 을 채우겠습니다.

## A. 전처리 산출물 — 20개 · 1.22 GB  🔴 제출 필수

| # | 파일 | 크기 | 무엇인가 |
|---|---|---|---|
| 1 | `artifacts/counterparty_graph.json` | 48 KB | 계약상대 그래프 145노드 |
| 2 | `artifacts/eval_report.json` | 0 KB | 평가 리포트 |
| 3 | `artifacts/exchange_bm25.json` | 1.0 MB | 거래소공시 BM25 색인 |
| 4 | `artifacts/exchange_records.jsonl` | 4.8 MB | 거래소공시 정형 레코드 |
| 5 | `artifacts/holding_bm25.json` | 0.9 MB | 지분공시 BM25 색인 |
| 6 | `artifacts/holding_records.jsonl` | 6.1 MB | 지분공시 정형 레코드 |
| 7 | `artifacts/major_bm25.json` | 0.5 MB | 주요사항보고 BM25 색인 |
| 8 | `artifacts/major_records.jsonl` | 4.4 MB | 주요사항보고 정형 레코드 |
| 9 | `artifacts/nary_records.jsonl` | 0.1 MB | n-ary 추출 레코드 |
| 10 | `artifacts/nary_records.meta.json` | 1 KB | n-ary 메타 |
| 11 | `artifacts/periodic_sections.jsonl` | 55.2 MB | 섹션 레코드 — 경로·오프셋·길이 |
| 12 | `artifacts/periodic_sections.txt` | 926.4 MB | 정기공시 섹션 본문 블롭 (오프셋으로 읽는다) |
| 13 | `artifacts/references.jsonl` | 0.6 MB | 관련공시 참조 엣지 |
| 14 | `artifacts/section_index.json` | 19.2 MB | 섹션 BM25 색인 (메타) |
| 15 | `artifacts/section_index.npz` | 197.8 MB | 섹션 BM25 색인 (벡터) |
| 16 | `data/dart-disclosures/manifest.jsonl` | 2.4 MB | 🔴 전체 문서 대장 — 런타임 필수 |
| 17 | `data/dart-disclosures/universe.csv` | 10 KB | 🔴 70개사 목록 — 런타임 필수 |
| 18 | `data/dart-disclosures/universe.xlsx` | 13 KB | 70개사 원본 표 |
| 19 | `data/dart-disclosures/README.md` | 6 KB | 코퍼스 설명 |
| 20 | `data/dart-disclosures/data_filter.md` | 3 KB | 수집 필터 기준 |

## B. 원본 공시 — 4,902개 · 5.57 GB

`data/dart-disclosures/raw/` 전체.

🔴 **주최측이 제공한 원본**이라 규약 §9 가 말하는 "전처리 산출물" 은 아니다.
백업·재현용으로만 올린다. 제출 링크에 넣을지는 선택이다.

🔴 파일이 4,902개라 낱개 업로드는 비현실적이다 — **zip 하나로 묶어** 올리시라.

## 권장 드라이브 구조

```
ARGUS-제출물/
  preprocessing_manifest.json      ← 무결성 대조표 (먼저 올린다)
  artifacts/                       ← A 의 1~15번 (15개 · 1.21 GB)
  data/dart-disclosures/           ← A 의 16~20번 (5개 · 2.4 MB)
  raw.zip                          ← B (선택 · 5.57 GB)
```

## 올린 뒤

공유 링크("링크가 있는 모든 사용자" 보기 권한)를 주시면:
- README 의 `TBD-3` 을 채운다
- `baseline-final` · 제출 대장에 링크를 반영한다
- 파일 몇 개는 받아서 SHA256 을 대조해 무결성을 확인한다
