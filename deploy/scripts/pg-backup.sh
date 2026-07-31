#!/usr/bin/env bash
# PostgreSQL 日备脚本（ADR-0002 运维纪律）：pg_dump 全库 → gzip → deploy/backups/
# 手动执行：./scripts/pg-backup.sh
# 建议每日定时（crontab 示例）：
#   0 3 * * * cd /path/to/ai4s/deploy && ./scripts/pg-backup.sh >> backups/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p backups
TS=$(date +%Y%m%d-%H%M%S)
OUT="backups/axonhub-${TS}.sql.gz"

docker compose exec -T postgres pg_dump -U axonhub -d axonhub | gzip > "$OUT"

# 保留最近 14 份，清理更老的
ls -1t backups/axonhub-*.sql.gz 2>/dev/null | tail -n +15 | while read -r f; do rm -f "$f"; done

echo "备份完成: $OUT ($(du -h "$OUT" | cut -f1))"
echo "恢复参考: gunzip -c $OUT | docker compose exec -T postgres psql -U axonhub -d axonhub"
