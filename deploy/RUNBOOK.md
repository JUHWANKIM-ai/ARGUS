# 배포 런북 — 🔴 **붙여넣기용**

> 설계·근거는 [`README.md`](README.md). **이 문서는 실행 순서만** 담는다.
> 작성 2026-08-15 · 사전 검증 완료(아래 `§0`)

---

# §0. 이미 검증된 것 — 다시 하지 않아도 된다

```
✅ 응답 5키 규격        question_id · question · retrieved_context · think_trace · answer
                      전부 string · 주최측 규격과 정확히 일치 (로컬 실측)
✅ 라우트              GET /answer · GET /health
✅ 파라미터            question_id: str · question: str  (둘 다 Query)
✅ requirements.txt    🔴 손으로 작성됨. Windows 휠 없음. 아래 §2 에서 그대로 쓴다
✅ 선언 안 된 import   전수 스캔 완료 — pydantic 을 찾아 pyproject 에 추가했다
✅ 예외 안전성          어떤 예외에도 200 + 한계 고지 (pipeline.fault_response)
```

🔴 **`pip freeze` 를 새로 뜨지 마세요.** `requirements.txt` 는 이미 Ubuntu 용으로 손질돼 있습니다.

---

# §1. 사용자 몫 — NCP 콘솔 (20~30분)

에이전트가 못 하는 유일한 부분입니다. **순서대로:**

```
1. VPC          argus            (없으면 생성)
2. Subnet       argus-subnet     Public · 192.168.1.0/24   🔴 /24 표기 필수
3. ACG          argus-acg
     Inbound   TCP 0.0.0.0/0 → 22      (SSH)
               TCP 0.0.0.0/0 → 80      (평가용 HTTP)
     Outbound  TCP 0.0.0.0/0 → 1-65535 🔴 CLOVA 호출에 필수. 빠지면 전 문항 실패
4. 서버 생성     c2-g3a  (2 vCPU / 4 GB)
     OS         Ubuntu 24.04     🔴 Windows Server 를 고르지 마세요 — 라이선스가 시간당 붙습니다
     요금제      시간 요금제
5. 공인 IP       223.130.129.131 (리소스 144276114) 을 이 서버에 연결
6. 인증키        .pem 다운로드 → 관리자 비밀번호 확인
```

🔴 **IP 를 새로 신청하지 마세요.** `223.130.129.131` 은 이미 확보돼 있고, **09-20 까지 유지해야
제출한 URL 이 살아 있습니다.**

---

# §2. 서버 초기 구성 — 붙여넣기 (20~30분)

SSH 로 접속한 뒤 **한 덩이씩** 붙여넣습니다.

## ① 기본 패키지 · swap

```bash
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git
python3 --version        # 🔴 3.12 이상이어야 한다 (Ubuntu 24.04 는 3.12)

# 🔴 swap 2GB — 4GB RAM 에서 색인 적재가 OOM 될 수 있다
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h                  # Swap 2.0Gi 가 보이면 성공
```

## ② 코드 배치

```bash
sudo mkdir -p /opt/argus && sudo chown -R $USER:$USER /opt/argus
```

그리고 **로컬(Windows)에서** 코드를 올립니다:

```bash
# 로컬에서 실행. <KEY> 는 NCP 인증키 경로
scp -i <KEY> -r src scripts deploy pyproject.toml requirements.txt README.md \
    root@223.130.129.131:/opt/argus/
```

## ③ venv 와 의존성

```bash
cd /opt/argus
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

python -c "import argus.api.server as s; print('키', len(s.AnswerResponse.model_fields))"
# 🔴 "키 5" 가 나와야 한다
```

## ④ 🔴 `.env` — **사용자가 직접**

에이전트는 이 파일을 보지 않습니다. **직접 올리거나 서버에서 작성합니다.**

```bash
# 방법 A — 로컬에서 전송
scp -i <KEY> .env root@223.130.129.131:/opt/argus/.env

# 방법 B — 서버에서 직접 작성 (nano)
nano /opt/argus/.env
```

🔴 **재발급한 키가 들어 있어야 합니다.** 그리고 권한을 좁힙니다:

```bash
chmod 600 /opt/argus/.env
```

## ⑤ 산출물 전송 — 🔴 **시간을 지배하는 단계**

```bash
# 로컬에서. 약 1.2GB — 업로드 속도에 따라 10분~1시간+
scp -i <KEY> -r artifacts root@223.130.129.131:/opt/argus/
```

🔴 **끊기면 처음부터입니다.** 큰 파일이면 `rsync` 를 쓰는 편이 안전합니다:

```bash
rsync -avz --progress -e "ssh -i <KEY>" artifacts/ root@223.130.129.131:/opt/argus/artifacts/
```

## ⑥ systemd 등록

```bash
sudo cp /opt/argus/deploy/argus.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now argus
sudo systemctl status argus --no-pager
```

🔴 **`active (running)` 이 아니면 로그를 봅니다:**

```bash
sudo journalctl -u argus -n 50 --no-pager
```

---

# §3. 검증 — 🔴 이 셋이 통과해야 배포 완료다

## ① 헬스체크

```bash
curl -s http://223.130.129.131/health
```

## ② 🔴 5키 JSON — 주최측이 보는 그대로

```bash
curl -sG "http://223.130.129.131/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은?" | python3 -m json.tool
```

**기대**: `question_id` · `question` · `retrieved_context` · `think_trace` · `answer` 다섯 키,
전부 문자열.

## ③ 🔴 예외에도 200 이 나오는가 — 재시도 정책 대비

주최측은 **타임아웃 또는 5xx 에 최대 2회 재시도**합니다. 세 번 실패하면 그 문항은 0점이고,
비결정적 실패는 크레딧을 3배 씁니다. 우리 `fault_response` 가 그것을 막습니다.

```bash
# 존재하지 않는 문항 ID · 빈 질문 등으로 확인
curl -s -o /dev/null -w "%{http_code}\n" -G "http://223.130.129.131/answer" \
  --data-urlencode "question_id=ZZZ-999" --data-urlencode "question="
```

🔴 **`200` 이 나와야 합니다.** `500` 이 나오면 `fault_response` 경로를 확인하세요.

## ④ 지연 실측 — 300초 타임아웃 대비

```bash
for q in "삼성전자의 2025년 매출액은?" "현대차의 최근 대규모 공급계약 상대는?"; do
  /usr/bin/time -f "%e초" curl -sG "http://223.130.129.131/answer" \
    --data-urlencode "question_id=T" --data-urlencode "question=$q" -o /dev/null
done
```

🔴 **캐시가 없는 상태에서 재야 진짜 값입니다.** 로컬 실측은 최대 11.1초였습니다.

---

# §4. 배포 후 — 🔴 **잊으면 전 문항 0점**

```bash
# 검증이 끝나면 서버를 정지한다 (시간요금 절약)
# 🔴 IP 는 유지된다 — c2-g3a 는 High CPU G3 라 정지 시 시간요금 면제
```

```
🔴 09-07 이전에 반드시 서버를 시작할 것
   정지 상태로 평가기간에 들어가면 전 문항 0점이다

🔴 09-20 까지 공인 IP 를 반납하지 말 것
   반납 후 재획득하면 새 IP 가 나오고 제출한 URL 이 죽는다
```

## 루트 `README.md` 채우기

배포가 끝나면 자리표시자를 채웁니다:

```
TBD-1  공인 IP          → http://223.130.129.131/answer   (확보됨)
TBD-2  requirements.txt → ✅ 이미 작성됨 (이 런북 §0)
TBD-3  산출물 스토리지 링크 → R4-3 에서
TBD-4  주최측 발신 IP 대역  → 🔴 주최측 공지 대기
TBD-5  서버 운영 일자·시간대 → 🔴 주최측 공지 대기
```

---

# §5. 문제가 나면

| 증상 | 원인 | 조치 |
|---|---|---|
| `active (running)` 이 아니다 | `.env` 부재 | `argus.service` 의 `EnvironmentFile` 이 읽는다. `chmod 600` 확인 |
| `ModuleNotFoundError: argus` | `pip install -e .` 누락 | venv 활성 상태에서 재실행 |
| CLOVA 호출 실패 | 🔴 **ACG Outbound** | `TCP 0.0.0.0/0 → 1-65535` 확인 |
| `/bin/bash^M: bad interpreter` | CRLF | `.gitattributes` 가 LF 를 강제하지만, `scp` 대신 zip 으로 옮겼다면 발생 |
| OOM / 프로세스 죽음 | 색인 1.2GB | `§2 ①` 의 swap 2GB 를 만들었는지 확인 |
| 응답이 느리다 | 캐시 없음 | 첫 호출은 느리다. 🔴 300초 안이면 정상 |
