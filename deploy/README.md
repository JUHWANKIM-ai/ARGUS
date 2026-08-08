# 배포 절차

## 서버 준비 (NCP, Ubuntu 24.04, c2-g3a 2vCPU/4GB)
1. VPC `argus` / Subnet `argus-subnet`(Public, 192.168.1.0/24) / **시간 요금제** / 공인 IP 신청
2. ACG Inbound: 22(관리자 IP) · 80 · 443(0.0.0.0/0) — 3389 불필요
   ACG Outbound: TCP 0.0.0.0/0 → 1-65535 (**CLOVA API 호출에 필수**)

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

- `artifacts/` 는 `.gitignore` 대상이라 저장소에 없다. **Dockerfile 의 `COPY artifacts/`
  는 로컬에서 `python scripts/build_index.py --group exchange` 를 먼저 돌려 둔 상태에서만
  성공한다.** 코퍼스 원본(5.2GB)은 서버에 올리지 않고 이 가공 산출물만 올린다.
- `.env` 는 저장소에 없다. 별도로 `scp` 한다. `argus.service` 의 `EnvironmentFile` 이
  이 파일을 읽으므로 없으면 서비스가 기동하지 않는다.
- 셸 스크립트·유닛 파일은 `.gitattributes` 로 LF 개행이 강제된다. Windows 작업 사본에서
  그대로 `scp` 해도 `/bin/bash^M: bad interpreter` 가 나지 않는다.
