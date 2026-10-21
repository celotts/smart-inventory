#!/usr/bin/env bash
# ===========================================================================
# Crear un usuario desde la terminal.
#
#   make usuario
#
# POR QUE EXISTE, Y POR QUE NO ES Solo LA PANTALLA
# ---------------------------------------------------
# La pantalla de Usuarios (en la app) es el camino normal: un ADMIN da de
# alta a los demas. Este script es para el caso en que NO hay admin todavía:
# una base recien creada, o un admin que perdio su clave.
#
# En ese caso no se puede entrar a la app (que es justamente el problema), y
# el único camino es la base.
#
# POR QUE PIDE LA CLAVE CON `read -s`
# -----------------------------------
# Un `read` normal muestra lo que se escribe: la clave queda en pantalla, en
# el historial del shell si se pasa como argumento, y en el `ps` de los otros
# procesos de la maquina. `read -s` no la muestra y no la guarda en ningún
# lado.
#
# LA CLAVE NO SE ESCRIBE EN Ningún ARCHIVO
# ----------------------------------------
# Se pasa por la entrada estándar a `psql`, que la recibe en una variable de
# la sesión (`psql -v clave=...`), no en un archivo del repo ni en el
# historial de la shell.
# ===========================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-smart_inventory}"
DB_PASSWORD="${DB_PASSWORD:-secretpassword}"

ROJO="\033[0;31m"; VERDE="\033[0;32m"; SIN="\033[0m"; GRIS="\033[0;90m"

# El `psql` del contenedor, o el del host si está instalado.
#
# Se busca por SERVICIO de compose (`docker compose ps --services`) y no por
# nombre de contenedor: el servicio se llama `db` y el contenedor
# `smart_inventory_db`, y con el nombre de contenedor el chequeo falla y el
# script cae al `psql` del host, que no está.
psql_en() {
  if docker compose ps --services 2>/dev/null | grep -qx "$DB_SERVICE"; then
    docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  elif docker ps --format '{{.Names}}' | grep -qx "$DB_SERVICE"; then
    docker exec -i "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  elif command -v psql >/dev/null 2>&1; then
    PGPASSWORD="$DB_PASSWORD" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  else
    printf "%b\n" "${ROJO}No hay contenedor '$DB_SERVICE' corriendo ni psql en el PATH.${SIN}" >&2
    exit 1
  fi
}

echo ""
echo "  Crear un usuario de la app"
echo "  ==========================="
echo ""

read -r -p "  Email (es el nombre de usuario): " EMAIL
if [ -z "$EMAIL" ]; then
  printf "%b\n" "${ROJO}  El email es obligatorio.${SIN}" >&2
  exit 1
fi

read -r -p "  Nombre y apellido: " NOMBRE
if [ -z "$NOMBRE" ]; then
  printf "%b\n" "${ROJO}  El nombre es obligatorio.${SIN}" >&2
  exit 1
fi

read -r -p "  Telefono: " TELEFONO
read -r -p "  Rol (ADMIN / TECHNICIAN / OPERATOR) [OPERATOR]: " ROL
ROL="${ROL:-OPERATOR}"
ROL="$(echo "$ROL" | tr '[:lower:]' '[:upper:]')"

case "$ROL" in
  ADMIN|TECHNICIAN|OPERATOR) ;;
  *) printf "%b\n" "${ROJO}  Rol desconocido: '$ROL'.${SIN}" >&2; exit 1 ;;
esac

echo ""
read -r -s -p "  Contrasena (no se muestra): " CLAVE
echo ""
read -r -s -p "  Repetir la contrasena: " REPETIDA
echo ""

if [ "$CLAVE" != "$REPETIDA" ]; then
  printf "%b\n" "${ROJO}  Las contrasenas no coinciden.${SIN}" >&2
  exit 1
fi

if [ "${#CLAVE}" -lt 8 ]; then
  printf "%b\n" "${ROJO}  La contrasena necesita al menos 8 caracteres.${SIN}" >&2
  exit 1
fi

# El hash es el MISMO que usa el backend (bcrypt, costo 10) a traves de
# `crypt()` de pgcrypto: una contraseña creada por el script entra igual que
# una creada por la app.
# `-q` silencia los tags de comando ("INSERT 0 1"): lo que interesa es el
# `RETURNING`, que es el email del usuario.
RESULTADO="$(psql_en -q -t -A -v email="$EMAIL" -v nombre="$NOMBRE" -v telefono="${TELEFONO:-N/A}" -v rol="$ROL" -v clave="$CLAVE" <<'SQL'
INSERT INTO users (email, password, full_name, phone, phone2, address, role_id, is_active)
VALUES (
    :'email',
    crypt(:'clave', gen_salt('bf', 10)),
    :'nombre',
    :'telefono',
    '',
    'N/A',
    (SELECT id FROM roles WHERE name = :'rol'),
    TRUE
)
ON CONFLICT (email) DO UPDATE SET
    password = EXCLUDED.password,
    full_name = EXCLUDED.full_name,
    phone = EXCLUDED.phone,
    role_id = EXCLUDED.role_id,
    is_active = TRUE,
    deleted_at = NULL
RETURNING email;
SQL
)"

if [ -z "$RESULTADO" ]; then
  printf "%b\n" "${ROJO}  No se pudo crear el usuario.${SIN}" >&2
  exit 1
fi

echo ""
printf "%b\n" "  ${VERDE}Usuario ${RESULTADO} listo.${SIN}"
printf "%b\n" "  ${GRIS}Entrá con ese email y esa contrasena.${SIN}"
echo ""
