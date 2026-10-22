#!/bin/bash
# Onboarding de cliente nuevo — smart-inventory (no interactivo)
# Usage: ./onboarding.sh "Nombre Empresa" "admin@empresa.com" "password"

set -e

NOMBRE_EMPRESA="${1:-Transportes Ejemplo}"
ADMIN_EMAIL="${2:-admin@empresa.com}"
ADMIN_PASSWORD="${3:-Empresa2024*}"

BASE_DIR="/Users/carloslott/develop/python/smart-inventory"

echo "🏢 Onboarding: $NOMBRE_EMPRESA"
echo "📧 Admin: $ADMIN_EMAIL"

# 1. Crear/admin si no existe (la función crea-usuario ya valida si existe)
bash /Users/carloslott/develop/python/smart-inventory/scripts/crear-usuario.sh "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "admin" 2>/dev/null || true

# 2. Cargar seed electrónica
bash /Users/carloslott/develop/python/smart-inventory/scripts/seed-electronica.sh 2>/dev/null || true

# 3. Backup inicial de seguridad
BACKUP_TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p /Users/carloslott/develop/python/smart-inventory/backups
docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db pg_dump -U postgres -d smart_inventory | gzip > /Users/carloslott/develop/python/smart-inventory/backups/onboarding_initiale_$BACKUP_TIMESTAMP.sql.gz

# 3. Confirmación final
echo "========================================="
echo "  ONBOARDING COMPLETADO"
echo "========================================="
echo "  Empresa: $NOMBRE_EMPRESA"
echo "  Admin: $ADMIN_EMAIL"
echo "  Base: $(docker compose -f /Users/carloslott/develop/python/smart-inventory/docker-compose.yml exec -T db psql -U postgres -d smart_inventory -t -c 'SELECT count(*) FROM devices WHERE deleted_at IS NULL;' 2>/dev/null | tr -d ' ' ) equipos"
echo "  Dashboard: http://localhost:5175"
echo "  API:       http://localhost:5002/api"
echo "========================================="
echo ""
echo "Próximos pasos:"
echo "  1. Cambiar la contraseña del admin"
echo "  2. Visitar /api/auth/yo para ver perfil"
echo "  3. Revisar que los modulos corresponden al rol"
