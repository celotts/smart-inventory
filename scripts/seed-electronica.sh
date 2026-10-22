#!/usr/bin/env bash
# ===========================================================================
# Catalogo de ELECTRONICA y SEGURIDAD: categorias y sus atributos.
#
#   bash scripts/seed-electronica.sh
#
# QUE SIEMBRA
# -----------
# Las categorias de los equipos que NO son de escritorio y que una empresa
# de servicios tiene en el campo:
#
#   Camara, NVR/DVR, UPS, Switch, Tablet, Telefono IP, Sensor, Cerradura
#
# Y los ATRIBUTOS de cada una, que es lo que hace que la app sirva para
# ellos y no solo para notebooks.
#
# EL PORQUE DE LOS ATRIBUTOS
# --------------------------
# Un atributo con `applies_to = 'device'` es un valor POR EQUIPO (la IP de
# gestion de esta camara, la cantidad de canales de este NVR). Un atributo
# con `applies_to = 'product'` es un valor del MODELO (la resolucion maxima
# de la camara, que es la misma para todas).
#
# La diferencia importa: "la camara de la reception tiene la IP
# 10.0.0.5" es un dato del EQUIPO. Con el valor solo por modelo, esa
# informacion no se puede guardar.
#
# NO DESTRUCTIVO
# --------------
# Es idempotente: correrlo dos veces no duplica nada.
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-smart_inventory}"

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

say "Categorias de electronica y seguridad"

psql_db -q <<'SQL'
-- Las CATEGORIAS. `ON CONFLICT DO NOTHING` con la clave natural (el nombre).
INSERT INTO categories (name) VALUES
    ('Camara'),
    ('NVR/DVR'),
    ('UPS'),
    ('Switch'),
    ('Tablet'),
    ('Telefono IP'),
    ('Sensor'),
    ('Cerradura electronica')
ON CONFLICT (name) DO NOTHING;
SQL

say "Atributos de cada categoria"

psql_db -q <<'SQL'
-- Los ATRIBUTOS.
--
-- `applies_to = 'device'` es un valor POR EQUIPO (la IP de esta camara).
-- `applies_to = 'product'` es un valor del MODELO (la resolucion de la
-- camara, que es la misma en todas).
--
-- El INSERT es idempotente por la restriccion unica (categoria, key).
INSERT INTO category_attributes
    (category_id, key, label, data_type, options, unit, is_required, applies_to, sort_order)
SELECT c.id, v.key, v.label, v.data_type, v.options::jsonb, v.unit, v.is_required, v.applies_to, v.sort_order
FROM (VALUES
    -- CAMARA
    ('Camara', 'resolucion',        'Resolucion maxima',   'text',    '[]',        'MP',   false, 'product', 1),
    ('Camara', 'tipo_lente',        'Tipo de lente',       'text',    '[]',        NULL,   false, 'product', 2),
    ('Camara', 'ip_gestion',        'IP de gestion',       'text',    '[]',        NULL,   false, 'device',  3),
    ('Camara', 'canales',           'Canales de video',    'number',  '[]',        NULL,   false, 'device',  4),
    ('Camara', 'poe',               'Alimentacion PoE',    'boolean', '[]',        NULL,   false, 'device',  5),
    ('Camara', 'ubicacion_instalacion', 'Donde esta instalada', 'text', '[]',        NULL,   false, 'device',  6),
    ('Camara', 'dias_grabacion',    'Dias de grabacion',   'number',  '[]',        'dias', false, 'device',  7),

    -- NVR / DVR
    ('NVR/DVR', 'canales',          'Canales',             'number',  '[]',        NULL,   false, 'device',  1),
    ('NVR/DVR', 'canales_usados',   'Canales en uso',      'number',  '[]',        NULL,   false, 'device',  2),
    ('NVR/DVR', 'resolucion',       'Resolucion de grabacion', 'text', '[]',       'MP',   false, 'product', 3),
    ('NVR/DVR', 'ip_gestion',       'IP de gestion',       'text',    '[]',        NULL,   false, 'device',  4),
    ('NVR/DVR', 'almacenamiento',   'Almacenamiento',       'number',  '[]',        'TB',   false, 'device',  5),
    ('NVR/DVR', 'dias_grabacion',   'Dias de grabacion',   'number',  '[]',        'dias', false, 'device',  6),

    -- UPS
    ('UPS', 'potencia',            'Potencia',            'number',  '[]',        'VA',   false, 'product', 1),
    ('UPS', 'autonomia',           'Autonomia',           'number',  '[]',        'min',  false, 'product', 2),
    ('UPS', 'baterias',            'Baterias',            'number',  '[]',        NULL,   false, 'device',  3),
    ('UPS', 'ultima_prueba',       'Ultima prueba de bateria', 'date', '[]',      NULL,   false, 'device',  4),

    -- SWITCH
    ('Switch', 'puertos',           'Puertos',             'number',  '[]',        NULL,   false, 'product', 1),
    ('Switch', 'puertos_usados',    'Puertos en uso',      'number',  '[]',        NULL,   false, 'device',  2),
    ('Switch', 'poe',               'Alimentacion PoE',    'boolean', '[]',        NULL,   false, 'device',  3),
    ('Switch', 'ip_gestion',       'IP de gestion',       'text',    '[]',        NULL,   false, 'device',  4),
    ('Switch', 'vlan',              'VLAN',                'text',    '[]',        NULL,   false, 'device',  5),

    -- TABLET
    ('Tablet', 'sistema',           'Sistema operativo',   'text',    '[]',        NULL,   false, 'product', 1),
    ('Tablet', 'pantalla',          'Pulgadas',            'number',  '[]',        NULL,   false, 'product', 2),
    ('Tablet', 'bateria',           'Bateria',             'number',  '[]',        'mAh',  false, 'device',  3),
    ('Tablet', 'imei',              'IMEI',                'text',    '[]',        NULL,   false, 'device',  4),
    ('Tablet', 'mdm',               'Inscrito en MDM',     'boolean', '[]',        NULL,   false, 'device',  5),

    -- TELEFONO IP
    ('Telefono IP', 'extension',     'Extension',           'text',    '[]',        NULL,   false, 'device',  1),
    ('Telefono IP', 'mac',           'Direccion MAC',       'text',    '[]',        NULL,   false, 'device',  2),
    ('Telefono IP', 'ip_gestion',    'IP de gestion',       'text',    '[]',        NULL,   false, 'device',  3),

    -- SENSOR
    ('Sensor', 'tipo',              'Tipo de sensor',      'text',    '[]',        NULL,   false, 'product', 1),
    ('Sensor', 'protocolo',         'Protocolo',           'text',    '[]',        NULL,   false, 'device',  2),
    ('Sensor', 'bateria',           'Bateria',             'number',  '[]',        '%',    false, 'device',  3),
    ('Sensor', 'ultima_lectura',    'Ultima lectura',      'date',    '[]',        NULL,   false, 'device',  4),

    -- CERRADURA ELECTRONICA
    ('Cerradura electronica', 'tipo',    'Tipo de cierre',  'text',    '[]',        NULL,   false, 'product', 1),
    ('Cerradura electronica', 'bateria', 'Bateria',         'number',  '[]',        '%',    false, 'device',  2),
    ('Cerradura electronica', 'ultima_apertura', 'Ultima apertura', 'date', '[]',     NULL,   false, 'device',  3),
    ('Cerradura electronica', 'accesos', 'Accesos registrados', 'number', '[]',      NULL,   false, 'device',  4)
) AS v(categoria, key, label, data_type, options, unit, is_required, applies_to, sort_order)
JOIN categories c ON c.name = v.categoria
ON CONFLICT (category_id, key) DO UPDATE SET
    label = EXCLUDED.label,
    data_type = EXCLUDED.data_type,
    unit = EXCLUDED.unit,
    applies_to = EXCLUDED.applies_to,
    sort_order = EXCLUDED.sort_order;
SQL

say "Resumen"

psql_db -t -A <<'SQL'
SELECT '   ' || c.name || ': ' || count(ca.id) || ' atributo(s), '
       || count(*) FILTER (WHERE ca.applies_to = 'device') || ' por equipo'
FROM categories c
LEFT JOIN category_attributes ca ON ca.category_id = c.id AND ca.is_active
WHERE c.name IN ('Camara','NVR/DVR','UPS','Switch','Tablet','Telefono IP','Sensor','Cerradura electronica')
GROUP BY c.name
ORDER BY c.name;
SQL

printf '\n'
printf '  Los atributos "por equipo" se guardan en la ficha de cada equipo.\n'
printf '  Los demas van en el producto (el modelo).\n\n'
