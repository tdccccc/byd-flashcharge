#!/bin/bash
set -e

# Resolve project directory (works from cron and manual invocation)
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate byd-flashcharge

LOG="data/cron.log"
mkdir -p data

echo "=== $(date '+%Y-%m-%d %H:%M:%S') BYD 闪充站数据更新 ===" | tee -a "$LOG"

echo "[1/3] 爬取最新数据..." | tee -a "$LOG"
python scraper.py 2>&1 | tail -5 | tee -a "$LOG"

echo "[2/3] 导出静态 JSON..." | tee -a "$LOG"
python export_json.py 2>&1 | tee -a "$LOG"

echo "[3/3] 提交并推送..." | tee -a "$LOG"
git add public/api/
git diff --cached --quiet && echo "No changes to commit." | tee -a "$LOG" && exit 0
git commit -m "data: update $(date +%Y-%m-%d_%H:%M)" | tee -a "$LOG"
git push 2>&1 | tee -a "$LOG"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 部署完成! ===" | tee -a "$LOG"
