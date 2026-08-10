# 🔴 이 폴더를 튜닝 입력으로 쓰지 마세요

`evalset/heldout.jsonl` 18문항의 **최종 1회 측정** 산출물입니다 (2회째 사용 · 최종 구성 `A8` ·
2026-08-10 · 673.8원). `artifacts/` 는 gitignore 대상인데 **이 폴더만 `git add -f` 로 넣었습니다** —
heldout 은 **재측정이 불가능**하므로 로컬에만 두면 잃으면 끝이기 때문입니다.

## 그 대가 — 검색 골든 라벨이 노출됐습니다

`A8_heldout_run/report.json` 의 `rows` 에 이런 것이 들어 있습니다.

```
question_id  Q-021
question     (질문 전문)
golden       ['20230224800251', '20231013800121', '20231227800229']
golden_rank  {'20230224800251': 1, '20231013800121': 2, '20231227800229': 3}
```

**정답값 자체는 없습니다.** 그러나 `golden`·`golden_rank`·`golden_in_candidates`·
`golden_in_evidence` 는 **검색을 튜닝할 수 있는 형태의 라벨**이고, 18문항 질문 전문도 함께
있습니다. 즉 *"`heldout.jsonl` 을 열지 말라"* 는 규율이 이제 `artifacts/` 경유로 **우회 가능**합니다.
v1 `artifacts/ablation/A6_heldout_run/` 은 gitignore 밖이었으므로 **이번에 새로 생긴 노출**입니다.

## 그래서 지켜야 할 것

1. **이 폴더의 `golden`·`golden_rank` 를 검색·랭킹 튜닝의 입력으로 쓰지 마세요.**
   BM25 가중치·hop 순서·프롬프트를 이 18문항에 맞추면 heldout 은 개발셋이 되고,
   그것은 **되돌릴 수 없습니다** (다시 잴 heldout 이 없습니다).
2. **재측정도 하지 마세요.** 러너가 코드로 거부합니다 —
   `scripts/ablation.py::assert_heldout_unused` 가 `artifacts/` 전체에서 `heldout_run.json` 을
   찾으므로 **`--out` 을 바꿔도 풀리지 않습니다.** `--offline` 재집계(LLM 0회)만 허용됩니다.
3. **성능 확인이 필요하면** dev 그리드 72 · 회귀 10 · 밸류체인 12 로 하세요.
   그 수를 heldout 값과 나란히 적지 마세요 (분모와 성격이 다릅니다).

## 이 폴더가 무엇인가

| 파일 | 내용 |
|---|---|
| `heldout_run.json` | 🔴 **사용 원장.** 몇 회째 · 어느 구성 · 낙폭 세 축 · 판정. 이 파일이 제동의 근거다 |
| `A8_heldout_run/report.json` | 문항별 귀속 (위 경고 대상) |
| `A8_heldout_run/responses/` · `judge/` | 응답·심사 원문. 재집계(LLM 0회)의 입력 |
| `A8_heldout_run/run.json` | 실지출 673.8원 기록 |
| `index_snapshot.json` | 측정 시점 인덱스 해시 |

해석은 `artifacts/ablation/v2/gate_I.md` §14 · `docs/pipeline/09-ablation.md` §I-6 ·
`docs/total_plan.md` §0 에 있습니다.
