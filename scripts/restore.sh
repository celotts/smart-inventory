#!/bin/bash
# Script de restauración desde backup
# Usage: ./restore.sh [ARCHIVO]
# Si no se especifica archivo, usa el más reciente.

set -e

BACKUP_DIR="/Users/carloslott/develop/python/smart-inventory/backups"
DB_NAME="smart_inventory"
DB_USER="postgres"

if [ -z "$1" ]; then
  # Usar el archivo más reciente
  ARCHIVO=$(ls -t "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
  if [ -z "$ARCHIVO" ]; then
    echo "❌ No hay backups encontrados en $BACKUP_DIR"
    exit 1
  fi
else
  ARCHIVO="$1"
fi

if [ ! -f "$ARCHIVO" ]; then
  echo "❌ El archivo $ARCHIVO no existe"
  exit 1
fi

echo "📦 Iniciando restauración desde: $ARCHIVO"

# Crear base de restauración
RESTORE_DB="smart_inventory_restore_$(date +%Y%m%d_%H%M%S)"
echo "🗄️  Creando base de restauración: $RESTORE_DB"
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -c "CREATE DATABASE $RESTORE_DB"

# Restaurar
echo "📥 Restaurando backup..."
gunzip -c "$ARCHIVO" | docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d "$RESTORE_DB"

# Conteos para comparar
echo "📊 Obteniendo conteos de la base restaurada..."
EQUIPOS=$(docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d "$RESTORE_DB" -t -c "SELECT count(*) FROM devices WHERE deleted_at IS NULL;")
MOVIMIENTOS=$(docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d "$RESTORE_DB" -t -c "SELECT count(*) FROM movement_history;")
CASOS=$(docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d "$RESTORE_DB" -t -c "SELECT count(*) FROM incidents WHERE deleted_at IS NULL;")

echo "📊 Conteo base ORIGINAL:"
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d smart_inventory -t -c "SELECT count(*) FROM devices WHERE deleted_at IS NULL;" 2>/dev/null
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d smart_inventory -t -c "SELECT count(*) FROM movement_history;" 2>/dev/null  
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d smart_inventory -t -c "SELECT count(*) FROM incidents WHERE deleted_at IS NULL;" 2>/dev/null

echo "📊 Conteo base RESTAURADA:"
echo "   Equipos: $EQUIPOS"
echo "   Movimientos: $MOVIMIENTOS"
echo "   Casos: $CASOS"

# Comparar
EQUIPOS_ORIG=$(docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d smart_inventory -t -c "SELECT count(*) FROM devices WHERE deleted_at IS NULL;" 2>/dev/null | tr -d ' ')
if [ "$EQUIPOS" = "$EQUIPOS_ORIG" ]; then
  echo "✅ **RESTORE EXITOSO**: Los conteos de equipos coinciden ($EQUIPOS = $EQUIPOS_ORIG)"
else
  echo "⚠️  **ADVERTENCIA**: Diferencia en cantidad de equipos ($EQUIPOS vs $EQUIPOS_ORIG)"
fi

# Limpiar base temporal
echo "🧹 Limpiando base temporal..."
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -c "DROP DATABASE $RESTORE_DB"

echo "✅ proceso de restauración completado"
