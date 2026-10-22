#!/bin/bash
# Script de backup automatizado con rotación
# Usage: ./backup.sh

set -e

BACKUP_DIR="backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=180

mkdir -p "$BACKUP_DIR"

# Backup diario completo
echo "[$TIMESTAMP] Iniciando backup diario..."
docker compose exec -T db pg_dump -U postgres -d smart_inventory   | gzip > "$BACKUP_DIR/smart_inventory_daily_$TIMESTAMP.sql.gz"

# Rotación: eliminar backups mayores a RETENTION_DAYS
echo "[$TIMESTAMP] Aplicando rotación de $RETENTION_DAYS días..."
find "$BACKUP_DIR" -name "*.sql.gz" -type f -mtime +$RETENTION_DAYS -delete

echo "[$TIMESTAMP] Backup completado: smart_inventory_daily_$TIMESTAMP.sql.gz"
