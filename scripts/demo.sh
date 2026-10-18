#!/usr/bin/env bash
# ===========================================================================
# Datos de DEMO para probar el escaner.
#
#   bash scripts/demo.sh
#
# NO destructivo por diseno: no borra nada. Solo agrega sedes, modelos y
# componentes que no existian, con `ON CONFLICT DO NOTHING` para que correrlo
# dos veces no duplique.
#
# Las coordenadas son reales y verificables: una dentro de la sede y otra a
# ~5 km, para poder probar los dos caminos de la geo-validacion (dentro / fuera)
# con el mismo componente.
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }

psql_db() {
  docker compose exec -T db psql -U postgres -d smart_inventory -v ON_ERROR_STOP=1 "$@"
}

say "1/3  Sedes"

psql_db -t -A <<'SQL'
-- El guard es por `store_name` y no `ON CONFLICT` porque la tabla no tiene
-- restriccion unica sobre el nombre: `ON CONFLICT DO NOTHING` sin columna de
-- conflicto no evita el duplicado, solo el error de llave primaria.
INSERT INTO locations (store_name, address, city, latitude, longitude, tolerance_radius_meters)
SELECT v.store_name, v.address, v.city, v.lat, v.lon, v.radio
FROM (VALUES
  ('Deposito Central', 'Av. Corrientes 800', 'Buenos Aires', -34.6037::numeric, -58.3816::numeric, 50),
  ('Cliente Acme SA',  'Av. Santa Fe 1200',  'Buenos Aires', -34.5960::numeric, -58.3900::numeric, 80),
  ('Taller Norte',     'Av. Cordoba 4500',   'Buenos Aires', -34.6150::numeric, -58.4300::numeric, 60)
) AS v(store_name, address, city, lat, lon, radio)
WHERE NOT EXISTS (
  SELECT 1 FROM locations l WHERE l.store_name = v.store_name AND l.deleted_at IS NULL
);
SQL

say "2/3  Modelos"

psql_db -t -A <<'SQL'
INSERT INTO models (category_id, brand, model, warranty_months)
SELECT c.id, v.brand, v.model, v.warranty
FROM (VALUES
  ('Dell',       'Latitude 5440', 36),
  ('Lenovo',     'ThinkPad T14',  36),
  ('HP',         'EliteBook 840', 36),
  ('Samsung',    'Galaxy A10',    24),
  ('Cisco',      'Catalyst 1000', 60)
) AS v(brand, model, warranty)
JOIN categories c ON c.name = 'Notebook' OR v.brand = 'Cisco'
ON CONFLICT DO NOTHING;
SQL

say "3/3  Componentes"

psql_db -t -A <<'SQL'
INSERT INTO devices (model_id, serial_number, qr_code, current_location_id, current_status, is_warehouse)
SELECT
  m.id,
  v.serial,
  v.qr,
  -- `min(id)`: sin esto, si la sede quedara duplicada, la subconsulta devuelve
  -- mas de una fila y el INSERT entero falla con "more than one row returned by
  -- a subquery used as an expression".
  (SELECT min(id) FROM locations WHERE store_name = v.sede),
  v.estado::device_status,
  v.estado = 'IN_WAREHOUSE'
FROM (VALUES
  ('SN-DEMO-0001', 'INV-a1b2c3d4', 'Deposito Central', 'IN_WAREHOUSE'),
  ('SN-DEMO-0002', 'INV-b2c3d4e5', 'Deposito Central', 'IN_WAREHOUSE'),
  ('SN-DEMO-0003', 'INV-c3d4e5f6', 'Deposito Central', 'IN_WAREHOUSE'),
  ('SN-DEMO-0004', 'INV-d4e5f6a7', 'Cliente Acme SA',  'INSTALLED'),
  ('SN-DEMO-0005', 'INV-e5f6a7b8', 'Deposito Central', 'IN_WAREHOUSE')
) AS v(serial, qr, sede, estado)
-- El id del modelo va por `min(id)` y NO por `JOIN ... ON brand = 'Dell'`:
-- con dos modelos de la misma marca (esta base ya tenia "Latitude 5540" y
-- "Latitude 5440"), el JOIN duplica cada fila del demo y el INSERT entero
-- revienta con "duplicate key ... serial_number", que no dice nada del motivo
-- real.
JOIN models m ON m.id = (
  SELECT min(id) FROM models
  WHERE brand = 'Dell' AND model = 'Latitude 5440' AND deleted_at IS NULL
)
WHERE NOT EXISTS (
  SELECT 1 FROM devices d
  WHERE d.serial_number = v.serial OR d.qr_code = v.qr
);
SQL

say "Resultado"
psql_db -c "
SELECT d.serial_number,
       d.qr_code,
       l.store_name AS sede,
       d.current_status AS estado,
       COALESCE(round(EXTRACT(EPOCH FROM (NOW() - d.installation_date)) / 86400)::int, 0)
         AS dias_instalado
FROM devices d
LEFT JOIN locations l ON l.id = d.current_location_id
WHERE d.serial_number LIKE 'SN-DEMO-%'
ORDER BY d.serial_number;"
