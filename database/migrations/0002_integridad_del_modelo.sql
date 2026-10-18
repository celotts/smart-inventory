-- ===========================================================================
-- 0002 — Integridad del modelo relacional
-- ===========================================================================
--
-- Para que sirve esta migracion
-- -----------------------------
-- `init.sql` ya trae estos cambios. Esta migracion es para la base que se creo
-- con la version anterior del esquema: agrega indices, CHECKs, borrado logico y
-- corrige el tipo de `disposal_certificates.authorized_by`.
--
--   Instalacion nueva  ->  `make up`.     Correr esto seria redundante.
--   Base ya existente   ->  `make migrar`. Esto.
--
-- QUE CORRIGE, Y POR QUE CADA COSA EXISTE
-- ----------------------------------------
--  1. authorized_by: INT -> UUID + FK. Era el mismo entero suelto que ya se
--     corrigio en movement_history.user_id y repairs.technician_id.
--  2. NOT NULL en devices.model_id y models.category_id. Un equipo sin modelo
--     desaparece de todo reporte por tipo.
--  3. UNIQUE(brand, model). Sin el, "Samsung / Galaxy A10" se podia duplicar y
--     los conteos por modelo no cerraban contra el total de devices.
--  4. CHECK de consistencia entre is_warehouse y current_status. Son la misma
--     informacion en dos columnas; sin el CHECK pueden divergir en silencio.
--  5. CHECK de REPLACEMENT en las dos direcciones.
--  6. Borrado logico (deleted_at) en las tablas de negocio.
--  7. TIMESTAMP -> TIMESTAMPTZ. users/roles ya usaban TIMESTAMPTZ y el resto
--     TIMESTAMP sin zona: con datos de dos paises, dos timestamps a la misma
--     hora caen en instantes distintos y comparar fechas deja de funcionar.
--  8. ON DELETE CASCADE -> RESTRICT en las tablas de historial. Con CASCADE, un
--     DELETE accidental de un dispositivo se lleva TODOS sus movimientos.
--  9. Trigger de updated_at en devices/clients/locations. Tenian DEFAULT pero
--     ningun trigger: la columna nunca cambiaba despues del INSERT.
-- 10. Indices en todas las columnas foraneas. Postgres no los crea solo.
--
-- EL ORDEN IMPORTA
-- -----------------
-- Las columnas NOT NULL se agregan SOLO si la tabla esta vacia o si todas las
-- filas ya tienen valor. Es el mismo criterio que la 0001: no se borra data para
-- que una migracion "pase". Si la tabla tiene huerfanos, esto falla con el
-- SELECT de abajo como ayuda, y el operador decide.


-- ---------------------------------------------------------------------------
-- 0. Chequeo previo: informa que habria que corregir antes de seguir.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_huerfanos INT;
BEGIN
    SELECT count(*) INTO v_huerfanos FROM devices WHERE model_id IS NULL;
    IF v_huerfanos > 0 THEN
        RAISE WARNING 'devices: % filas con model_id NULL. La migracion va a fallar al agregar el NOT NULL.', v_huerfanos;
        RAISE WARNING '  Para verlas: SELECT id, serial_number, qr_code FROM devices WHERE model_id IS NULL;';
    END IF;

    SELECT count(*) INTO v_huerfanos FROM models WHERE category_id IS NULL;
    IF v_huerfanos > 0 THEN
        RAISE WARNING 'models: % filas con category_id NULL. La migracion va a fallar al agregar el NOT NULL.', v_huerfanos;
        RAISE WARNING '  Para verlas: SELECT id, brand, model FROM models WHERE category_id IS NULL;';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 0b. Borrado logico — PRIMERO, y no por capricho
-- ---------------------------------------------------------------------------
--
-- Va antes que todo lo demas porque `ADD COLUMN IF NOT EXISTS deleted_at` es
-- lo UNICO de esta migracion que no puede fallar: agregar una columna nullable
-- no rompe datos ni puede chocar con un constraint.
--
-- Va primero porque los pasos que siguen la consultan. El chequeo de duplicados
-- en `models` usa `WHERE deleted_at IS NULL`, y si se corre antes de que la
-- columna exista, Postgres dice "column does not exist" y la migracion se frena
-- a mitad de camino. Ya paso.
--
-- `ADD COLUMN IF NOT EXISTS` la hace re-ejecutable.
ALTER TABLE clients    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE locations  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE models     ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE devices    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- `locations` NO tenia `updated_at` en el esquema viejo (solo `created_at`).
-- Sin agregarlo aca, el paso 7 falla con 'column updated_at does not exist':
-- no se puede convertir un tipo de una columna que no existe.
ALTER TABLE clients    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE locations  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();


-- ---------------------------------------------------------------------------
-- 1. authorized_by: INT -> UUID
-- ---------------------------------------------------------------------------
-- Igual que en la 0001: si hay certificados de baja con un entero que no es un
-- UUID de users, no hay traduccion posible y no se borra nada por el camino.
DO $$
DECLARE
    v_filas INT;
BEGIN
    SELECT count(*) INTO v_filas FROM disposal_certificates;

    IF v_filas = 0 THEN
        ALTER TABLE disposal_certificates
            ALTER COLUMN authorized_by TYPE UUID USING NULLIF(authorized_by::text, '')::uuid;
        ALTER TABLE disposal_certificates ADD CONSTRAINT fk_disposal_authorized_by
            FOREIGN KEY (authorized_by) REFERENCES users(id) NOT VALID;
        ALTER TABLE disposal_certificates VALIDATE CONSTRAINT fk_disposal_authorized_by;
        RAISE NOTICE 'disposal_certificates.authorized_by -> UUID + FK';
    ELSE
        RAISE EXCEPTION USING
            MESSAGE = format('disposal_certificates tiene % filas con authorized_by INT, que no se puede convertir a UUID.', v_filas),
            HINT = 'Cada certificado tiene que quedar asociado a un usuario real de la tabla users. Exportar, crear los usuarios, actualizar la columna. Si son datos de prueba: make clean && make up.',
            DETAIL = 'SELECT id, device_id, disposal_reason, authorized_by FROM disposal_certificates ORDER BY id;';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 2. NOT NULL en las columnas de catalogo
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_huerfanos INT;
BEGIN
    SELECT count(*) INTO v_huerfanos FROM devices WHERE model_id IS NULL;
    IF v_huerfanos = 0 THEN
        ALTER TABLE devices ALTER COLUMN model_id SET NOT NULL;
        RAISE NOTICE 'devices.model_id NOT NULL';
    ELSE
        RAISE EXCEPTION USING
            MESSAGE = format('devices tiene % filas sin model_id.', v_huerfanos),
            HINT = 'Cada dispositivo necesita un modelo: UPDATE devices SET model_id = <id> WHERE model_id IS NULL; antes de volver a correr la migracion.',
            DETAIL = 'SELECT id, serial_number, qr_code FROM devices WHERE model_id IS NULL;';
    END IF;

    SELECT count(*) INTO v_huerfanos FROM models WHERE category_id IS NULL;
    IF v_huerfanos = 0 THEN
        ALTER TABLE models ALTER COLUMN category_id SET NOT NULL;
        RAISE NOTICE 'models.category_id NOT NULL';
    ELSE
        RAISE EXCEPTION USING
            MESSAGE = format('models tiene % filas sin category_id.', v_huerfanos),
            HINT = 'UPDATE models SET category_id = <id> WHERE category_id IS NULL; antes de volver a correr la migracion.',
            DETAIL = 'SELECT id, brand, model FROM models WHERE category_id IS NULL;';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 3. UNIQUE(brand, model)
-- ---------------------------------------------------------------------------
-- Se agrega SIN VALIDAR si ya hay duplicados: el UNIQUE es un indice, se crea
-- siempre, pero el CHECK de duplicados avisa antes de romper nada.
DO $$
DECLARE
    v_dupes INT;
BEGIN
    SELECT count(*) INTO v_dupes FROM (
        SELECT brand, model FROM models WHERE deleted_at IS NULL
        GROUP BY brand, model HAVING count(*) > 1
    ) duplicados;

    IF v_dupes > 0 THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Hay % marcas/modelo repetidos. El UNIQUE(brand, model) no se puede agregar.', v_dupes),
            HINT = 'Consolidar: UPDATE devices SET model_id = <el que se queda> WHERE model_id IN (SELECT id FROM models WHERE brand = ... AND model = ...); y marcar el duplicado con deleted_at.',
            DETAIL = 'SELECT brand, model, count(*), array_agg(id) FROM models GROUP BY brand, model HAVING count(*) > 1;';
    END IF;

    ALTER TABLE models ADD CONSTRAINT uq_models_brand_model UNIQUE (brand, model);
    RAISE NOTICE 'models: UNIQUE(brand, model) agregado';
END $$;


-- ---------------------------------------------------------------------------
-- 4. CHECK de consistencia is_warehouse / current_status
-- ---------------------------------------------------------------------------
-- Primero se normalizan las filas que ya esten desincronizadas: si el status
-- dice IN_WAREHOUSE, el flag tiene que decir true. Se hace ANTES de agregar el
-- CHECK porque si no, la sentencia falla y la migracion se frena.
--
-- La normalizacion va en la direccion que manda `current_status`, porque es la
-- columna de la que derivan los reportes y los ENUM.
UPDATE devices
SET is_warehouse = (current_status = 'IN_WAREHOUSE')
WHERE is_warehouse IS DISTINCT FROM (current_status = 'IN_WAREHOUSE');

ALTER TABLE devices DROP CONSTRAINT IF EXISTS ck_devices_warehouse_status;
ALTER TABLE devices ADD CONSTRAINT ck_devices_warehouse_status CHECK (
    is_warehouse = (current_status = 'IN_WAREHOUSE')
);
-- (Sin RAISE NOTICE: fuera de un bloque DO $$...$$ no es una sentencia
-- valida. El aviso va en el NOTICIO del paso siguiente.)


-- ---------------------------------------------------------------------------
-- 5. CHECK de REPLACEMENT
-- ---------------------------------------------------------------------------
-- Al reves del 4: NO se normalizan filas, se rechazan. Un movimiento con
-- replacement_reason pero que no es REPLACEMENT tiene datos que no se pueden
-- inventar: no sabemos si el motivo era real o un error de carga, ni a que
-- equipo reemplazaba. Borrarlo seria perder trazabilidad; inventar el
-- reemplazo seria fabricar un evento que no ocurrio.
DO $$
DECLARE
    v_invalidas INT;
BEGIN
    SELECT count(*) INTO v_invalidas FROM movement_history
    WHERE (movement_type = 'REPLACEMENT'
           AND (replaced_device_id IS NULL OR replacement_reason IS NULL))
       OR (movement_type <> 'REPLACEMENT' AND replaced_device_id IS NOT NULL);

    IF v_invalidas > 0 THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Hay % movimientos con datos de reemplazo inconsistentes con su movement_type.', v_invalidas),
            HINT = 'Revisar uno por uno. Son eventos que no se pueden corregir sin saber la realidad de lo que paso.',
            DETAIL = 'SELECT id, device_id, movement_type, replaced_device_id, replacement_reason FROM movement_history WHERE (movement_type = ''REPLACEMENT'' AND (replaced_device_id IS NULL OR replacement_reason IS NULL)) OR (movement_type <> ''REPLACEMENT'' AND replaced_device_id IS NOT NULL);';
    END IF;

    ALTER TABLE movement_history DROP CONSTRAINT IF EXISTS ck_movement_replacement;
    ALTER TABLE movement_history ADD CONSTRAINT ck_movement_replacement CHECK (
        CASE WHEN movement_type = 'REPLACEMENT'
             THEN replaced_device_id IS NOT NULL AND replacement_reason IS NOT NULL
             ELSE replaced_device_id IS NULL AND replacement_reason IS NULL
        END
    );
    RAISE NOTICE 'movement_history: CHECK de REPLACEMENT agregado';
END $$;


-- ---------------------------------------------------------------------------
-- 6. CHECK de una sola reparacion abierta
-- ---------------------------------------------------------------------------
-- Se agrega SOLO si no hay equipos con dos reparaciones abiertas. Si los hay, no
-- se elige una arbitrariamente: se aborta.
DO $$
DECLARE
    v_duplicados INT;
BEGIN
    SELECT count(*) INTO v_duplicados FROM (
        SELECT device_id FROM repairs
        WHERE status NOT IN ('REPAIRED', 'UNREPAIRABLE')
        GROUP BY device_id HAVING count(*) > 1
    ) dup;

    IF v_duplicados > 0 THEN
        RAISE EXCEPTION USING
            MESSAGE = format('Hay % equipos con mas de una reparacion abierta.', v_duplicados),
            HINT = 'Cerrar las que no corresponden: UPDATE repairs SET status = ''REPAIRED'', exit_date = NOW() WHERE id IN (...); antes de volver a correr.',
            DETAIL = 'SELECT device_id, count(*), array_agg(id) FROM repairs WHERE status NOT IN (''REPAIRED'',''UNREPAIRABLE'') GROUP BY device_id HAVING count(*) > 1;';
    END IF;

    CREATE UNIQUE INDEX IF NOT EXISTS idx_repairs_open ON repairs(device_id)
        WHERE status NOT IN ('REPAIRED', 'UNREPAIRABLE');
    RAISE NOTICE 'repairs: UNIQUE parcial de reparaciones abiertas';
END $$;


-- 7. TIMESTAMP -> TIMESTAMPTZ
-- ---------------------------------------------------------------------------
-- El cambio de tipo es seguro e implicitamente reescribe las filas. OJO: el
-- valor se interpreta con la zona horaria de la SESION en el momento de correr.
-- Por eso el `SET TIME ZONE` explicito: correr esto en un servidor en UTC
-- cuando los datos se cargaron en Argentina desplaza todas las fechas.
-- `models` NO aparece en esta lista a proposito: no tiene created_at ni
-- updated_at, ni en el esquema viejo ni en init.sql. Es una tabla de catalogo
-- y su fecha de creacion no se consulta nunca. Agregarla al Conversion fue un
-- error que solo se vio corriendo la migracion contra una base real.
SET TIME ZONE 'UTC';

ALTER TABLE clients    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE clients    ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';
ALTER TABLE locations  ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE locations  ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';
ALTER TABLE devices    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
ALTER TABLE devices    ALTER COLUMN updated_at TYPE TIMESTAMPTZ USING updated_at AT TIME ZONE 'UTC';
ALTER TABLE devices    ALTER COLUMN warehouse_entry_date TYPE TIMESTAMPTZ USING warehouse_entry_date AT TIME ZONE 'UTC';
ALTER TABLE devices    ALTER COLUMN installation_date    TYPE TIMESTAMPTZ USING installation_date AT TIME ZONE 'UTC';
ALTER TABLE movement_history ALTER COLUMN movement_date TYPE TIMESTAMPTZ USING movement_date AT TIME ZONE 'UTC';
ALTER TABLE repairs    ALTER COLUMN entry_date TYPE TIMESTAMPTZ USING entry_date AT TIME ZONE 'UTC';
ALTER TABLE repairs    ALTER COLUMN exit_date  TYPE TIMESTAMPTZ USING exit_date AT TIME ZONE 'UTC';
ALTER TABLE disposal_certificates ALTER COLUMN disposal_date TYPE TIMESTAMPTZ USING disposal_date AT TIME ZONE 'UTC';

-- Sin RAISE NOTICE por lo mismo que arriba: esto corre como SQL plano, no
-- dentro de un DO. El resultado se ve en el exito o el error del script.


-- ---------------------------------------------------------------------------
-- 8. ON DELETE RESTRICT en las tablas de historial
-- ---------------------------------------------------------------------------
-- Postgres no tiene "cambiar la FK": hay que BORRAR la constraint y crearla de
-- nuevo. Los nombres AUTO-generados no son estables entre instalaciones, asi
-- que se localiza cada FK POR COLUMNA y no por nombre.
--
-- OJO CON `DROP CONSTRAINT v_conname`: PL/pgSQL NO interpola variables en
-- DDL. Hay que usar EXECUTE format('...', v_conname). Escribido directo, dice
-- `constraint "v_conname" does not exist`, que es un error que no dice nada.
--
-- Y el nombre de la columna NO alcanza para deducir a que tabla apunta:
-- `locations.client_id` va a clients y `devices.current_location_id` va a
-- locations, y las dos se deducen igual de "una columna que termina en _id".
-- Por eso cada bloque declara a que tabla referencia.
DO $$
DECLARE
    v_conname TEXT;
BEGIN
    SELECT con.conname INTO v_conname
    FROM pg_constraint con
    JOIN pg_class src ON src.oid = con.conrelid
    WHERE con.contype = 'f' AND con.confdeltype = 'c'   -- 'c' = CASCADE
      AND src.relname = 'locations'
      AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute
             WHERE attrelid = 'locations'::regclass AND attname = 'client_id')];

    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE locations DROP CONSTRAINT %I', v_conname);
        ALTER TABLE locations ADD CONSTRAINT fk_locations_client
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE RESTRICT;
        RAISE NOTICE 'locations: FK client_id -> RESTRICT';
    END IF;

    SELECT con.conname INTO v_conname
    FROM pg_constraint con
    JOIN pg_class src ON src.oid = con.conrelid
    WHERE con.contype = 'f' AND con.confdeltype = 'c'
      AND src.relname = 'movement_history'
      AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute
             WHERE attrelid = 'movement_history'::regclass AND attname = 'device_id')];

    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE movement_history DROP CONSTRAINT %I', v_conname);
        ALTER TABLE movement_history ADD CONSTRAINT fk_movement_device
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE RESTRICT;
        RAISE NOTICE 'movement_history: FK device_id -> RESTRICT';
    END IF;

    SELECT con.conname INTO v_conname
    FROM pg_constraint con
    JOIN pg_class src ON src.oid = con.conrelid
    WHERE con.contype = 'f' AND con.confdeltype = 'c'
      AND src.relname = 'repairs'
      AND con.conkey = ARRAY[(SELECT attnum FROM pg_attribute
             WHERE attrelid = 'repairs'::regclass AND attname = 'device_id')];

    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE repairs DROP CONSTRAINT %I', v_conname);
        ALTER TABLE repairs ADD CONSTRAINT fk_repairs_device
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE RESTRICT;
        RAISE NOTICE 'repairs: FK device_id -> RESTRICT';
    END IF;
END $$;

-- Estas tres se crean de cero porque en el esquema viejo NO existian:
-- `authorized_by` era un INT suelto y `user_id` / `technician_id` todavia no
-- tienen FK (la 0001 los convierte a UUID y les pone nombre propio).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_disposal_authorized_by') THEN
        ALTER TABLE disposal_certificates ADD CONSTRAINT fk_disposal_authorized_by
            FOREIGN KEY (authorized_by) REFERENCES users(id) ON DELETE RESTRICT;
        RAISE NOTICE 'disposal_certificates: FK authorized_by creada';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_movement_origin') THEN
        ALTER TABLE movement_history ADD CONSTRAINT fk_movement_origin
            FOREIGN KEY (origin_location_id) REFERENCES locations(id) ON DELETE RESTRICT;
        ALTER TABLE movement_history ADD CONSTRAINT fk_movement_destination
            FOREIGN KEY (destination_location_id) REFERENCES locations(id) ON DELETE RESTRICT;
        ALTER TABLE movement_history ADD CONSTRAINT fk_movement_replaced
            FOREIGN KEY (replaced_device_id) REFERENCES devices(id) ON DELETE RESTRICT;
        RAISE NOTICE 'movement_history: FKs de trazabilidad creadas';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_disposal_device') THEN
        ALTER TABLE disposal_certificates ADD CONSTRAINT fk_disposal_device
            FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE RESTRICT;
        ALTER TABLE disposal_certificates ADD CONSTRAINT fk_disposal_repair
            FOREIGN KEY (repair_id) REFERENCES repairs(id) ON DELETE RESTRICT;
        RAISE NOTICE 'disposal_certificates: FKs creadas';
    END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 9. Trigger de updated_at
-- ---------------------------------------------------------------------------
-- `updated_at` con DEFAULT solo aplica en el INSERT. Sin trigger, la columna
-- queda congelada en la fecha de creacion para siempre.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_devices_updated_at ON devices;
CREATE TRIGGER trigger_devices_updated_at
BEFORE UPDATE ON devices
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trigger_clients_updated_at ON clients;
CREATE TRIGGER trigger_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trigger_locations_updated_at ON locations;
CREATE TRIGGER trigger_locations_updated_at
BEFORE UPDATE ON locations
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ---------------------------------------------------------------------------
-- 10. Indices
-- ---------------------------------------------------------------------------
-- Postgres NO crea indices en columnas foraneas. Sin esto, cada JOIN que entra
-- por una FK es un seq scan — y las consultas que genera la IA llegan
-- justamente por estas columnas.
CREATE INDEX IF NOT EXISTS idx_locations_client ON locations(client_id);
CREATE INDEX IF NOT EXISTS idx_locations_active ON locations(is_active) WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_models_category ON models(category_id);
-- GIN sobre JSONB: sin esto, buscar por una clave de `specifications` es un
-- seq scan sobre el JSON completo.
CREATE INDEX IF NOT EXISTS idx_models_specs ON models USING GIN (specifications);

CREATE INDEX IF NOT EXISTS idx_devices_warehouse ON devices(is_warehouse) WHERE is_warehouse;
CREATE INDEX IF NOT EXISTS idx_devices_model ON devices(model_id);
CREATE INDEX IF NOT EXISTS idx_devices_location ON devices(current_location_id);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(current_status);
CREATE INDEX IF NOT EXISTS idx_devices_active ON devices(deleted_at) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_movement_device ON movement_history(device_id);
CREATE INDEX IF NOT EXISTS idx_movement_date ON movement_history(movement_date DESC);
CREATE INDEX IF NOT EXISTS idx_movement_user ON movement_history(user_id);
CREATE INDEX IF NOT EXISTS idx_movement_origin ON movement_history(origin_location_id);
CREATE INDEX IF NOT EXISTS idx_movement_destination ON movement_history(destination_location_id);
CREATE INDEX IF NOT EXISTS idx_movement_replaced ON movement_history(replaced_device_id)
    WHERE replaced_device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_repairs_device ON repairs(device_id);
CREATE INDEX IF NOT EXISTS idx_repairs_technician ON repairs(technician_id);
CREATE INDEX IF NOT EXISTS idx_repairs_waiting ON repairs(entry_date DESC)
    WHERE status = 'WAITING_FOR_PARTS';

CREATE INDEX IF NOT EXISTS idx_disposal_authorizer ON disposal_certificates(authorized_by);
CREATE INDEX IF NOT EXISTS idx_disposal_repair ON disposal_certificates(repair_id);


-- ---------------------------------------------------------------------------
-- 11. Seed de categorias
-- --------------------------------------------------------------------------
-- devices.model_id y models.category_id ahora son NOT NULL: sin al menos una
-- categoria, no se puede crear ni un modelo ni un dispositivo. Este es el
-- minimo indispensable para que el sistema arranque con datos.
INSERT INTO categories (name, description) VALUES
    ('Notebook',    'Equipos portatiles'),
    ('Desktop',     'Equipos de escritorio'),
    ('Monitor',     'Monitores y pantallas'),
    ('Impresora',   'Impresoras y escáneres'),
    ('Servidor',    'Servidores y infraestructura'),
    ('Red',         'Equipamiento de red'),
    ('Periferico',  'Teclados, mouse y otros periféricos')
ON CONFLICT (name) DO NOTHING;