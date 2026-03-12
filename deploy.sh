#!/bin/bash
set -eo pipefail

# Resolve project directory (works from cron and manual invocation)
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# Activate conda environment (use full path for cron compatibility)
eval "$(/home/tiandc/miniconda3/bin/conda shell.bash hook)"
conda activate byd-flashcharge

LOG="data/cron.log"
mkdir -p data

echo "=== $(date '+%Y-%m-%d %H:%M:%S') BYD 闪充站数据更新 ===" | tee -a "$LOG"

# Record station count before scan
BEFORE=$(python -c "
import sqlite3, os
db = 'data/stations.db'
if not os.path.exists(db): print(0); exit()
c = sqlite3.connect(db).cursor()
print(c.execute('SELECT COUNT(*) FROM stations').fetchone()[0])
")

echo "[1/2] 爬取最新数据..." | tee -a "$LOG"
python scraper.py 2>&1 | tail -5 | tee -a "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "ERROR: scraper.py failed, aborting." | tee -a "$LOG"
    exit 1
fi

# Check station count after scan
AFTER=$(python -c "
import sqlite3
c = sqlite3.connect('data/stations.db').cursor()
print(c.execute('SELECT COUNT(*) FROM stations').fetchone()[0])
")
NEW=$((AFTER - BEFORE))

if [ "$NEW" -le 0 ]; then
    echo "No new stations ($BEFORE → $AFTER), skip deploy." | tee -a "$LOG"
    exit 0
fi

echo "[2/2] +${NEW} new stations ($BEFORE → $AFTER), exporting & pushing..." | tee -a "$LOG"
python export_json.py 2>&1 | tee -a "$LOG"

git add public/api/
git commit -m "data: update $(date +%Y-%m-%d) — ${NEW} new stations (total ${AFTER})" | tee -a "$LOG"
git push 2>&1 | tee -a "$LOG"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 部署完成! ===" | tee -a "$LOG"
