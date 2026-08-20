#!/usr/bin/env bash
# 제출물 업로드 — NCP Object Storage (S3 호환).
#
# 🔴 자격증명은 이 파일에 적지 않는다. 환경변수로만 받는다.
#     export NCP_ACCESS_KEY=... NCP_SECRET_KEY=... NCP_BUCKET=argus-submission
#     bash deploy/upload-artifacts.sh
#
# 🔴 **서버에서 돌리는 편이 낫다.** artifacts/ 1.2G 가 이미 서버에 있고 같은 리전이라
# 집 회선으로 올리는 것보다 훨씬 빠르다. 다만 raw/ 는 서버에 없으므로 그 5.57GB 만
# 로컬에서 올려야 한다.
set -euo pipefail

: "${NCP_ACCESS_KEY:?export NCP_ACCESS_KEY 가 필요합니다}"
: "${NCP_SECRET_KEY:?export NCP_SECRET_KEY 가 필요합니다}"
: "${NCP_BUCKET:?export NCP_BUCKET 이 필요합니다}"
ENDPOINT="${NCP_ENDPOINT:-https://kr.object.ncloudstorage.com}"

command -v aws >/dev/null || { echo "aws CLI 가 없습니다: pip install awscli"; exit 1; }

export AWS_ACCESS_KEY_ID="$NCP_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$NCP_SECRET_KEY"
S3="aws --endpoint-url $ENDPOINT s3"

echo "== ① 전처리 산출물 (1.22 GB · 20개) =="
$S3 cp artifacts/                 "s3://$NCP_BUCKET/artifacts/" --recursive \
    --exclude "*" \
    --include "*.json" --include "*.jsonl" --include "*.npz" --include "*.txt" \
    --exclude "ablation/*" --exclude "audit/*" --exclude "attic/*" \
    --exclude "eda/*" --exclude "eval/*" --exclude "embeddings/*"

echo "== ② 대장·기업목록 (2.4 MB · 런타임 필수) =="
for f in manifest.jsonl universe.csv universe.xlsx README.md data_filter.md; do
    $S3 cp "data/dart-disclosures/$f" "s3://$NCP_BUCKET/data/dart-disclosures/$f"
done

echo "== ③ 원본 공시 raw (5.57 GB · 4,902개) =="
# 🔴 주최측이 제공한 원본이라 '전처리 산출물' 은 아니다. 백업·재현용으로 함께 올린다.
$S3 cp data/dart-disclosures/raw/ "s3://$NCP_BUCKET/data/dart-disclosures/raw/" --recursive

echo "== ④ 무결성 대조표 =="
$S3 cp deploy/preprocessing_manifest.json "s3://$NCP_BUCKET/preprocessing_manifest.json"

echo
echo "완료. 확인:"
echo "  $S3 ls s3://$NCP_BUCKET/ --recursive --human-readable --summarize | tail -20"
echo "  공개 링크로 낼 경우: https://$NCP_BUCKET.kr.object.ncloudstorage.com/<key>"
