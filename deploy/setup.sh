#!/usr/bin/env bash
# ARGUS 평가용 서버 초기화 (Ubuntu 24.04, 2 vCPU / 4 GB)
set -euo pipefail

apt-get update
apt-get install -y python3-pip python3-venv git

# 타임존 — 로그 타임스탬프를 KST 로
timedatectl set-timezone Asia/Seoul

# swap 2GB — 기본 이미지에 swap 이 0B 이라 인덱스 로딩 시 OOM 이 난다
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 애플리케이션
APP_DIR=/opt/argus
mkdir -p "$APP_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "setup 완료. .env 와 artifacts/ 를 $APP_DIR 에 배치한 뒤 argus.service 를 등록하세요."
