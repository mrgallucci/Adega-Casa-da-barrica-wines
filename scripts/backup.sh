#!/bin/bash
# Backup MongoDB — Minha Adega
# Uso: ./scripts/backup.sh   (cria dump comprimido em /app/backups/YYYYmmdd_HHMMSS.gz)
# Restauração: ./scripts/restore.sh <arquivo.gz>
set -e
mkdir -p /app/backups
TS=$(date +%Y%m%d_%H%M%S)
mongodump --uri="$MONGO_URL" --db="$DB_NAME" --archive="/app/backups/adega_$TS.gz" --gzip
echo "Backup criado: /app/backups/adega_$TS.gz"
