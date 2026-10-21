#!/usr/bin/env bash
# ===========================================================================
# Usuarios de EJEMPLO: uno por modulo.
#
#   bash scripts/usuarios-modulo.sh
#
# QUE SON
# -------
# Dos usuarios con UN SOLO modulo cada uno:
#
#   deposito@empresa.com  (OPERATOR)    -> solo Inventario
#   taller@empresa.com    (TECHNICIAN)  -> solo Service Desk
#
# Sirven para ver el front como lo ve cada persona: el operador entra
# directo a su modulo, sin ver las incidencias; el tecnico entra a la
# bandeja, sin ver los productos ni las compras.
#
# NO destructivo: `ON CONFLICT DO UPDATE` deja la clave y el rol como
# estaban (o los deja como son si no existian). Correrlo dos veces no
# duplica nada.
#
# LAS CONTRASENAS SON CONOCIDAS
# -----------------------------
# Son cuentas de ejemplo, con la clave escrita aqui y en el repositorio.
# NO sirven para nada que no sea local: antes de cualquier despliegue que no
# sea de prueba, cambialas (o borra estos usuarios).
#
# El hash es el MISMO que usa el backend (bcrypt, costo 10) a traves de
# `crypt()` de pgcrypto: una contrasena creada por este script entra igual
# que una creada por la app.
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-smart_inventory}"
CLAVE="${CLAVE_DEMO:-ClaveSegura2026}"

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }

psql_db() {
  if docker compose ps --services 2>/dev/null | grep -qx "$DB_SERVICE"; then
    docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  elif docker ps --format '{{.Names}}' | grep -qx "$DB_SERVICE"; then
    docker exec -i "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
  else
    printf 'No hay contenedor de base de datos corriendo.\n' >&2
    exit 1
  fi
}

say "Usuarios de ejemplo (uno por modulo)"

psql_db -q -t -A -v clave="$CLAVE" <<'SQL'
-- El que trabaja en el DEPOSITO: solo Inventario.
INSERT INTO users (email, password, full_name, phone, phone2, address, role_id, is_active)
VALUES (
    'deposito@empresa.com',
    crypt(:'clave', gen_salt('bf', 10)),
    'Lucía Mendoza',
    'N/A', '', 'N/A',
    (SELECT id FROM roles WHERE name = 'OPERATOR'),
    TRUE
)
ON CONFLICT (email) DO UPDATE SET
    password = EXCLUDED.password,
    full_name = EXCLUDED.full_name,
    role_id = EXCLUDED.role_id,
    is_active = TRUE,
    deleted_at = NULL;

-- El que trabaja en el TALLER: solo Service Desk.
INSERT INTO users (email, password, full_name, phone, phone2, address, role_id, is_active)
VALUES (
    'taller@empresa.com',
    crypt(:'clave', gen_salt('bf', 10)),
    'Nicolás Ferreyra',
    'N/A', '', 'N/A',
    (SELECT id FROM roles WHERE name = 'TECHNICIAN'),
    TRUE
)
ON CONFLICT (email) DO UPDATE SET
    password = EXCLUDED.password,
    full_name = EXCLUDED.full_name,
    role_id = EXCLUDED.role_id,
    is_active = TRUE,
    deleted_at = NULL;

SELECT '   ' || u.email || '  (' || r.name || ')  ' || u.full_name
FROM users u JOIN roles r ON r.id = u.role_id
WHERE u.email IN ('deposito@empresa.com', 'taller@empresa.com')
ORDER BY u.email;
SQL

say ""
printf '  Contrasena de los dos: \033[1m%s\033[0m\n' "$CLAVE"
printf '  (son cuentas de ejemplo: cambiala antes de cualquier despliegue real)\n'
printf '\n'
