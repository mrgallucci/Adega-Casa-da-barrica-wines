#!/bin/bash
# Restaura backup MongoDB — Minha Adega
# Uso: MONGO_URL=... DB_NAME=... ./scripts/restore.sh /app/backups/adega_XXXX.gz
# O dump guarda os namespaces originais; remapeamos para o banco de destino.
set -e
FILE=$1
if [ -z "$FILE" ]; then echo "Informe o arquivo de backup"; exit 1; fi
# O namespace de origem é detectado do próprio dump (primeiro documento restaurado usa nsFrom)
SRC_DB=${SRC_DB:-test_database}
mongorestore --uri="$MONGO_URL" --archive="$FILE" --gzip \
  --nsFrom="${SRC_DB}.*" --nsTo="${DB_NAME}.*"
echo "Restauração concluída de $FILE em ${DB_NAME}"
