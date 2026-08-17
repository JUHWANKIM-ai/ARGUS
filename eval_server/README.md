# 배포 서버 응답 기록

평가용 API 서버(`http://223.130.129.131/answer`)가 실제로 내놓은 답을 그대로 담는다.
로컬 러너 산출물은 `artifacts/` 아래에 쌓이는데 그 폴더는 추적되지 않으므로, 서버가
무엇이라 답했는지 저장소만 보고 확인할 수 있게 여기에 따로 남긴다.

## 파일

| 파일 | 문항 | 출처 |
| --- | --- | --- |
| `full.json` | 88 | `evalset/questions.jsonl` 78 + `evalset/regression.jsonl` 10 |
| `safety.json` | 12 | `evalset/safety.jsonl` |
| `valuechain.json` | 12 | `evalset/valuechain.jsonl` |

## 형식

```jsonc
{
  "host": "223.130.129.131",
  "cohort": "full",
  "arm": "A42",              // 운영 기본 arm
  "fetched": "2026-08-17T..",
  "n": 88,
  "upstream_failures": 0,    // 상류 한도로 답을 못 받은 문항 수
  "answers": {
    "Q-001": {
      "question": "...",
      "question_id": "Q-001",
      "answer": "...",
      "retrieved_context": [ ... ],
      "think_trace": "route=... | hop=0 ... | generate tokens=..."
    }
  }
}
```

`answers` 의 값은 서버 응답 본문 그대로다. 키를 고르거나 이름을 바꾸지 않는다.

## 다시 받는 법

```
python scratch/server_sweep.py 223.130.129.131 [코호트 ...]
```

요청은 직렬로 보내고 사이에 쉰다. 상류(CLOVA)가 분당 요청 한도를 걸고 있어서 몰아치면
429 가 나고, 그때 파이프라인은 답 대신 한계 안내문을 `upstream_failure` 표식과 함께
돌려준다. 그 응답을 오답으로 세면 측정이 통째로 틀리므로, 표식이 붙은 문항은 대기 시간을
늘려 가며 다시 묻는다. `upstream_failures` 가 0 이 아닌 파일은 측정에 쓰지 않는다.

같은 응답이 `artifacts/ablation/v6/server_<코호트>/A42/responses/` 에도 러너 레코드
형식으로 저장되므로, 채점은 로컬 실행과 똑같은 코드 경로로 돌릴 수 있다.

```
python scratch/score_server.py
```
