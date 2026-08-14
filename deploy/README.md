# 배포 절차

> 🔴 **완주 기록이 없다.** 이 절차서는 설계이고, 실측으로 검증되는 것은
> [ablation v4 `R4-2`](../docs/superpowers/plans/2026-08-14-ablation-v4.md) 다.

## 🔴 엔드포인트 — 이 절이 정본이다

```
공인 IP     223.130.129.131          NCP 리소스 144276114
확보일       2026-08-14
제출 URL     http://223.130.129.131/answer?question_id=…&question=…
```

**🔴 이 IP 를 2026-09-20 까지 유지해야 한다.** 반납 후 재획득하면 **새 IP 가 나오고 제출한
URL 이 죽어 전 문항 0점**이 된다. 서버를 정지해도 IP 는 유지된다(`c2-g3a` 는 High CPU G3 라
정지 시 시간요금 면제). 유지 비용 약 4,032원/월.

**🔴 2026-09-07 이전에 서버를 반드시 시작할 것.** 정지 상태로 평가기간에 들어가면 전 문항 0점이다.
평가는 09-07~09-20 중 별도 공지되는 기간(주제별 최대 1주)에 이뤄진다.

## 서버 준비 (NCP, Ubuntu 24.04, c2-g3a 2vCPU/4GB)
1. VPC `argus` / Subnet `argus-subnet`(Public, 192.168.1.0/24) / **시간 요금제** /
   공인 IP `223.130.129.131` 을 이 서버에 연결
   🔴 **Windows Server 를 고르지 마라** — 라이선스가 시간당 추가돼 비용 계산이 무효가 된다
2. ACG Inbound: 22(관리자 IP) · 80 · 443(0.0.0.0/0) — 3389 불필요
   ACG Outbound: TCP 0.0.0.0/0 → 1-65535 (**CLOVA API 호출에 필수**)
   🔴 주최측 발신 IP 대역은 **아직 공지되지 않았다.** 공지 전까지 80 은 `0.0.0.0/0` 이다

## 설치
```bash
scp -r . root@<IP>:/opt/argus
ssh root@<IP> 'bash /opt/argus/deploy/setup.sh'
scp .env root@<IP>:/opt/argus/.env
scp -r artifacts root@<IP>:/opt/argus/
ssh root@<IP> 'cp /opt/argus/deploy/argus.service /etc/systemd/system/ && \
  systemctl daemon-reload && systemctl enable --now argus'
```

## 확인
```bash
curl -s "http://<IP>/answer?question_id=Q-001&question=테스트"
```

5키 JSON(`question_id` · `question` · `retrieved_context` · `think_trace` · `answer`)이
돌아오면 정상이다.

## Docker 대안
```bash
docker build -f deploy/Dockerfile -t argus .
docker run -d --env-file .env -p 80:80 argus
```

## 주의

- 🔴 **`requirements.txt` 가 없다.** `pyproject.toml` 만 있고, **Windows/conda 에서 뜬
  `pip freeze` 는 Windows 전용 휠이 섞여 Ubuntu 에서 못 쓴다.** 반드시 **서버 venv 에서** 뽑는다:
  ```bash
  python3 -m venv .venv && . .venv/bin/activate
  pip install -e . && pip freeze > requirements.txt
  ```
  이것이 제출물 1번(소스코드)의 미결 항목이다 — 루트 `README.md` 의 `TBD-2` 가 여기를 기다린다.
- `artifacts/` 는 `.gitignore` 대상이라 저장소에 없다. **Dockerfile 의 `COPY artifacts/`
  는 로컬에서 `python scripts/build_index.py --group exchange` 를 먼저 돌려 둔 상태에서만
  성공한다.** 코퍼스 원본(**5.55GB** — `01-eda.md` 실측)은 서버에 올리지 않고 이 가공 산출물만
  올린다. 🔴 **`Dockerfile` 은 이제 필수가 아니다**(주최측 2026-08-12 공지 #11) — systemd 경로가 정본이다.
- 🔴 **인덱스 전송이 시간을 지배한다** (약 1.2GB). swap 2GB 를 먼저 만든다 — 4GB RAM 에서
  색인 적재가 OOM 될 수 있다.
- `.env` 는 저장소에 없다. 별도로 `scp` 한다. `argus.service` 의 `EnvironmentFile` 이
  이 파일을 읽으므로 없으면 서비스가 기동하지 않는다.
- 셸 스크립트·유닛 파일은 `.gitattributes` 로 LF 개행이 강제된다. Windows 작업 사본에서
  그대로 `scp` 해도 `/bin/bash^M: bad interpreter` 가 나지 않는다.
