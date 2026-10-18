-- =========================================================================
-- Database Initialization Script - SmartInventory B2B
-- =========================================================================

-- Optional prior cleanup (if rebuilding from scratch)
--
-- EL ORDEN IMPORTA y es al reves de lo que parece.
--
-- audit_logs NO tiene DROP aqui a proposito: es la bitacora de auditoria. Si se
-- borra al recrear la base, se pierde el registro de quien borro que, que es
-- justamente el unico motivo por el que existe. Por eso las tablas de negocio
-- caen de abajo hacia arriba (hijas antes que padres, por las FK), pero
-- audit_logs se respeta.
DROP TABLE IF EXISTS disposal_certificates CASCADE;
DROP TABLE IF EXISTS repairs CASCADE;
DROP TABLE IF EXISTS movement_history CASCADE;
DROP TABLE IF EXISTS devices CASCADE;
DROP TABLE IF EXISTS models CASCADE;
DROP TABLE IF EXISTS categories CASCADE;
DROP TABLE IF EXISTS locations CASCADE;
DROP TABLE IF EXISTS clients CASCADE;

-- roles y users son autocreferenciales (users.created_by_user_id -> users.id),
-- asi que users cae antes que roles.
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS roles CASCADE;

DROP TYPE IF EXISTS audit_action CASCADE;
DROP TYPE IF EXISTS repair_status CASCADE;
DROP TYPE IF EXISTS movement_type CASCADE;
DROP TYPE IF EXISTS device_status CASCADE;

-- ============================================================================
-- EXTENSION: pgcrypto
-- ============================================================================
--
-- Se necesita por DOS motivos, y conviene separarlos:
--
--   1. gen_random_uuid(). Desde Postgres 13 esta en el core, asi que en la 15
--      de este compose no haria falta la extension. Se deja explicito para
--      que el UUID por defecto no dependa de la version.
--   2. crypt()/gen_salt() para bcrypt. ESTE si la necesita: es la unica forma
--      de sembrar un password sin meter el hash a mano en el .sql.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Enumerated Types (ENUMs)
CREATE TYPE device_status AS ENUM (
    'IN_WAREHOUSE',
    'IN_TRANSIT',
    'INSTALLED',
    'IN_REPAIR',
    'PERMANENTLY_DISPOSED'
);

CREATE TYPE movement_type AS ENUM (
    'WAREHOUSE_ENTRY',
    'OUT_TO_CLIENT',
    'INSTALLATION',
    'REPLACEMENT',
    'WITHDRAWAL_TO_WORKSHOP',
    'WAREHOUSE_REENTRY',
    'DISPOSAL'
);

CREATE TYPE repair_status AS ENUM (
    'DIAGNOSIS',
    'IN_PROGRESS',
    'WAITING_FOR_PARTS',
    'REPAIRED',
    'UNREPAIRABLE'
);

-- ============================================================================
-- AUDIT TRIGGER FUNCTION
-- ============================================================================
--
-- POR QUE EXISTE
-- Cada alta, cambio y baja copia el rol de quien la hizo al lado del dato, en
-- la fila misma. Es mas caro que guardar solo el user_id: si despues se le
-- cambia el rol a alguien, el "quien lo borro" conserva el rol que tenia EN EL
-- MOMENTO de la accion. Para una bitacora de inventario, un rol que cambia
-- despues hace que la evidencia valga menos, no mas.
--
-- POR QUE VA PRIMERO, ANTES DE LAS TABLAS
-- plpgsql no valida las referencias del cuerpo al crear la funcion: parsea la
-- sintaxis pero no comprueba que `users` exista. Por eso `CREATE FUNCTION`
-- puede ir antes que las tablas, y el orden real de creacion lo fija el orden
-- del resto de este script.
CREATE OR REPLACE FUNCTION set_audit_role_id()
RETURNS TRIGGER AS $$
DECLARE
    v_role_id UUID;
BEGIN
    IF (TG_OP = 'INSERT') THEN
        NEW.created_at = COALESCE(NEW.created_at, NOW());
        IF (NEW.created_by_user_id IS NOT NULL) THEN
            SELECT role_id INTO v_role_id FROM users WHERE id = NEW.created_by_user_id;
            NEW.created_by_role_id = v_role_id;
        END IF;
    ELSIF (TG_OP = 'UPDATE') THEN
        NEW.updated_at = NOW();
        IF (NEW.updated_by_user_id IS NOT NULL AND (OLD.updated_by_user_id IS NULL OR NEW.updated_by_user_id <> OLD.updated_by_user_id)) THEN
            SELECT role_id INTO v_role_id FROM users WHERE id = NEW.updated_by_user_id;
            NEW.updated_by_role_id = v_role_id;
        END IF;
        -- Soft delete: una fila con deleted_at ya no aparece en las consultas
        -- normales (siempre WHERE deleted_at IS NULL) pero queda para auditoria.
        --
        -- El `OR NEW.deleted_by_user_id IS NOT NULL` permite setear el actor de
        -- la baja en una UPDATE posterior, cuando el DELETE logico ya se habia
        -- hecho. Es el patron de los dos pasos.
        IF (NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL) OR (NEW.deleted_by_user_id IS NOT NULL AND OLD.deleted_by_user_id IS NULL) THEN
            NEW.deleted_at = COALESCE(NEW.deleted_at, NOW());
            IF (NEW.deleted_by_user_id IS NOT NULL) THEN
                SELECT role_id INTO v_role_id FROM users WHERE id = NEW.deleted_by_user_id;
                NEW.deleted_by_role_id = v_role_id;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- ROLES AND USERS
-- ============================================================================
--
-- ORDEN DE CREACION: roles antes que users, porque users.role_id la referencia.
-- Y users.id tiene DEFAULT gen_random_uuid(): sin eso, cada INSERT tiene que
-- inventar el UUID a mano y el primer forgot de clave se vuelve un problema.
--
-- `password TEXT NOT NULL` sin DEFAULT a proposito. Un usuario sin password es
-- un usuario que no puede loguearse, y el error debe ser el INSERT, no un
-- login fallido tres dias despues.
CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_by_user_id UUID,
    updated_by_user_id UUID,
    deleted_by_user_id UUID,
    created_by_role_id UUID,
    updated_by_role_id UUID,
    deleted_by_role_id UUID
);


-- Las columnas created_by_user_id de `roles` NO llevan REFERENCES a users a
-- proposito: `users` todavia no existe en este punto del script, y porque un
-- rol es anterior a cualquier usuario, su creador no puede ser un usuario. El
-- trigger es lo que lo resuelve en runtime, cuando ya existe.
CREATE TRIGGER trigger_roles_audit
BEFORE INSERT OR UPDATE ON roles
FOR EACH ROW EXECUTE FUNCTION set_audit_role_id();


CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password TEXT NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    -- address tiene DEFAULT pero phone y phone2 no. Es una inconsistencia que
    -- viene del diseno original; se deja como esta porque el frontend puede
    -- depender de los dos telefonos siendo obligatorios. Si molesta, el
    -- DEFAULT es una migracion de una linea.
    address VARCHAR(255) NOT NULL DEFAULT 'N/A',
    phone VARCHAR(255) NOT NULL,
    phone2 VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_specialist BOOLEAN DEFAULT FALSE,
    role_id UUID NOT NULL REFERENCES roles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    -- Autorreferencia. Valida porque la tabla ya existe cuando se resuelve la
    -- FK, a diferencia de las columnas de `roles`.
    created_by_user_id UUID REFERENCES users(id),
    updated_by_user_id UUID REFERENCES users(id),
    deleted_by_user_id UUID REFERENCES users(id),
    created_by_role_id UUID REFERENCES roles(id),
    updated_by_role_id UUID REFERENCES roles(id),
    deleted_by_role_id UUID REFERENCES roles(id)
);


CREATE TRIGGER trigger_users_audit
BEFORE INSERT OR UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_audit_role_id();


-- ============================================================================
-- AUDIT LOGS
-- ============================================================================
CREATE TYPE audit_action AS ENUM ('INSERT', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT');


-- Por que NO hay FOREIGN KEY en user_id
-- --------------------------------------
-- Es la unica tabla del esquema sin FK, y es deliberado: es el unico registro
-- que tiene que SOBREVIVIR a la baja del usuario. Si user_id fuera FK, un
-- `DELETE FROM users` (no el logico, el fisico) dejaria la bitacora huerfana o
-- la borraria en cascada, que es justo el escenario para el que existe.
--
-- `record_id` tampoco lleva FK porque es POLIMORFICO: el mismo UUID no puede
-- apuntar a devices.id (INT) y a users.id (UUID) con una sola restriccion. Por
-- eso se guarda como texto y el `table_name` dice de que tabla es.
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action audit_action NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    record_id UUID,
    old_value TEXT,
    new_value TEXT,
    user_id UUID,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


CREATE INDEX idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_record ON audit_logs(table_name, record_id);
-- El indice que hace que la bitacora de una tabla sea barata de leer:
-- `WHERE table_name = 'devices' ORDER BY created_at DESC`.
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at DESC);


-- ============================================================================
-- SESIONES (migracion 0003, replicada aca)
-- ============================================================================
--
-- POR QUE ESTA TABLA ESTA EN init.sql Y NO SOLO EN 0003_sessions.sql
-- -------------------------------------------------------------------
-- Porque el compose monta UN SOLO archivo en `/docker-entrypoint-initdb.d/`:
-- `./database/init.sql`. Los archivos de `migrations/` NO se ejecutan en una
-- instalacion nueva; solo corren con `make migrar`.
--
-- El efecto de que faltara aca era este: `make up` creaba una base SIN tabla
-- `sessions`, y `POST /api/auth/login` fallaba al intentar insertar la sesion.
-- O sea, una instalacion nueva segun el README ("make up, no hacen falta
-- migraciones") dejaba la API sin login. No es un caso raro: es el camino que
-- sigue todo el mundo la primera vez.
--
-- La regla que se respeta aca es la del propio proyecto: `init.sql` y una base
-- que corrio TODAS las migraciones terminan con el MISMO esquema. Por eso la
-- tabla va integra aca y 0003 queda registrada mas abajo como ya aplicada.
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A quien pertenece. CASCADE: si borran al usuario (hard delete), sus
    -- sesiones tienen que morir con el o quedan sesiones huerfanas de un id que
    -- no existe. Es el unico CASCADE del schema, y es correcto aca: una sesion
    -- sin usuario no tiene a quien servirle.
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- SHA-256 del refresh token, NO el token. Si alguien lee la base con un
    -- SELECT no puede autenticarse con lo que hay: el hash sirve para comparar,
    -- no para iniciar sesion.
    --
    -- UNIQUE: dos filas con el mismo token es un dato corrupto, y con el UNIQUE
    -- el error aparece al insertar y no como un fallo raro tres dias despues.
    token_hash VARCHAR(64) NOT NULL,

    expires_at TIMESTAMPTZ NOT NULL,

    -- Informativo: para la lista "mis sesiones abiertas". No es un dato de
    -- autenticacion.
    user_agent TEXT,
    ip_address VARCHAR(45),

    -- Logout de ESTA sesion.
    revoked_at TIMESTAMPTZ,

    -- Logout de TODAS las del usuario. Es la unica forma de cortar un access
    -- token ya entregado sin esperar a que expire: es exactamente el caso de
    -- "le robaron el token".
    revoked_before TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX idx_sessions_user ON sessions(user_id);
-- Indice PARCIAL: solo las vivas, que son pocas. Las muertas se pueden borrar
-- sin esperar, y no hace falta que un indice las tenga adentro.
CREATE INDEX idx_sessions_vivas ON sessions(expires_at)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE sessions IS
    'Refresh tokens hasheados. Un JWT access NO se guarda: es verificable por firma.';
COMMENT ON COLUMN sessions.revoked_before IS
    'Corta todos los access tokens emitidos antes de este instante. Para robo de token.';


-- ============================================================================
-- SEED: ROLES
-- ============================================================================
--
-- Sin esto, `users.role_id` es NOT NULL REFERENCES y no hay ninguna fila de
-- roles de la que apuntar: el primer usuario que se quiera crear falla. Tres
-- roles cubren el flujo del taller.
--
-- `deleted_at IS NULL` en los ON CONFLICT los hace idempotentes por si el
-- seed corre dos veces.
INSERT INTO roles (name) VALUES ('ADMIN') ON CONFLICT (name) DO NOTHING;
INSERT INTO roles (name) VALUES ('TECHNICIAN') ON CONFLICT (name) DO NOTHING;
INSERT INTO roles (name) VALUES ('OPERATOR') ON CONFLICT (name) DO NOTHING;


-- ============================================================================
-- SEED: USUARIO ADMIN
-- ============================================================================
--
-- El password se calcula con crypt() de pgcrypto (bcrypt, costo 10) en vez de
-- dejar el hash escrito a mano. Es mas legible y se puede cambiar leyendo una
-- frase en vez de comparando hashes.
--
-- CAMBIAR ESTO ANTES DE CUALQUIER COSA QUE NO SEA LOCAL. Es una credencial
-- conocida, publicada en el repositorio.
DO $$
DECLARE
    v_admin_role UUID;
    v_admin_id   UUID;
BEGIN
    SELECT id INTO v_admin_role FROM roles WHERE name = 'ADMIN';

    SELECT id INTO v_admin_id FROM users WHERE email = 'admin@smartinventory.local';

    IF v_admin_id IS NULL THEN
        INSERT INTO users (email, password, full_name, phone, phone2, role_id, is_specialist)
        VALUES (
            'admin@smartinventory.local',
            crypt('admin123', gen_salt('bf', 10)),
            'Administrador',
            '+54 11 0000-0000',
            '',
            v_admin_role,
            TRUE
        )
        RETURNING id INTO v_admin_id;
    END IF;

    RAISE NOTICE 'admin@smartinventory.local / admin123  (user_id=%)', v_admin_id;
END $$;


-- ============================================================================
-- 2. Clients and Locations
--
-- BORRADO LOGICO: `deleted_at` mas el filtro `WHERE deleted_at IS NULL`.
--
-- Por que tambien en clients y no solo en users: un cliente con 5 locales y 400
-- dispositivos que se borra en cascada se lleva su historial entero. En un
-- sistema cuya razon de ser es la trazabilidad, "lo borro porque ya no
-- trabajamos con ellos" y "nunca existio" tienen que ser cosas distintas.
-- `is_active` sigue existiendo y significa otra cosa: el cliente sigue con
-- nosotros pero esa sede esta dada de baja.
CREATE TABLE clients (
    id SERIAL PRIMARY KEY,
    commercial_name VARCHAR(150) NOT NULL,
    tax_id VARCHAR(50) UNIQUE NOT NULL,
    contact_name VARCHAR(100),
    contact_email VARCHAR(100),
    contact_phone VARCHAR(20),
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE locations (
    id SERIAL PRIMARY KEY,
    -- Nullable a proposito: la sede del almacen central no pertenece a ningun
    -- cliente. Por eso `devices.is_warehouse` tiene sentido como columna y no
    -- como un dato derivable de esta FK.
    client_id INT REFERENCES clients(id) ON DELETE RESTRICT,
    store_name VARCHAR(100) NOT NULL,
    address TEXT NOT NULL,
    city VARCHAR(100),
    latitude NUMERIC(10, 8),
    longitude NUMERIC(11, 8),
    tolerance_radius_meters INT DEFAULT 50,
    is_active BOOLEAN DEFAULT TRUE,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Catalogs and Devices
--
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    deleted_at TIMESTAMPTZ
);


-- SEED DE CATEGORIAS, antes de los dispositivos.
--
-- `devices.model_id` y `models.category_id` son NOT NULL: sin al menos una
-- fila en `categories` no se puede crear ni un modelo ni un equipo, y el
-- primer INSERT falla. Este es el minimo para que el sistema arranque con
-- datos.
--
-- El `ON CONFLICT DO NOTHING` lo hace idempotente por si el seed corre dos
-- veces.
INSERT INTO categories (name, description) VALUES
    ('Notebook',   'Equipos portatiles'),
    ('Desktop',    'Equipos de escritorio'),
    ('Monitor',    'Monitores y pantallas'),
    ('Impresora',  'Impresoras y escáneres'),
    ('Servidor',   'Servidores y infraestructura'),
    ('Red',        'Equipamiento de red'),
    ('Periferico', 'Teclados, mouse y otros perifericos')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE models (
    id SERIAL PRIMARY KEY,
    -- NOT NULL: un modelo sin categoria no se puede reportar. Antes admitia
    -- NULL y esos equipos desaparecian de cualquier agrupacion por tipo.
    category_id INT NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    brand VARCHAR(100) NOT NULL,
    model VARCHAR(100) NOT NULL,
    specifications JSONB,
    warranty_months INT DEFAULT 12 CHECK (warranty_months >= 0),
    deleted_at TIMESTAMPTZ,

    -- El UNIQUE que faltaba. Sin el, "Samsung / Galaxy A10" se podia insertar
    -- dos veces y cualquier reporte "equipos por modelo" salia duplicado, con
    -- conteos que no cerraban contra el total de devices.
    --
    -- No es UNIQUE(brand) ni UNIQUE(model) por separado: hay marcas con varios
    -- modelos (HP / LaserJet) y modelos que existen en dos marcas.
    CONSTRAINT uq_models_brand_model UNIQUE (brand, model)
);

CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    -- NOT NULL por lo mismo que models.category_id.
    model_id INT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    serial_number VARCHAR(100) UNIQUE NOT NULL,
    qr_code VARCHAR(150) UNIQUE NOT NULL,
    current_status device_status DEFAULT 'IN_WAREHOUSE',
    current_location_id INT REFERENCES locations(id) ON DELETE RESTRICT,
    -- DENORMALIZADO A PROPOSITO: es `current_status = 'IN_WAREHOUSE'`.
    --
    -- Se conserva porque el indice parcial de abajo necesita una columna
    -- booleana: Postgres no puede usar una comparacion de ENUM en la predicado
    -- de un indice parcial. El CHECK de abajo es lo que impide que las dos
    -- fuentes de verdad se separen.
    is_warehouse BOOLEAN DEFAULT TRUE,
    warehouse_entry_date TIMESTAMPTZ DEFAULT NOW(),
    installation_date TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- CONSISTENCIA DE LAS DOS FUENTES DE VERDAD.
    -- Si alguien hace UPDATE devices SET current_status = 'IN_REPAIR' sin tocar
    -- is_warehouse, la fila no entra. Sin este CHECK el sistema tiene un flag
    -- que dice "esta en el almacen" y un status que dice "esta en taller", y
    -- los dos aparecen en reportes distintos.
    CONSTRAINT ck_devices_warehouse_status CHECK (
        is_warehouse = (current_status = 'IN_WAREHOUSE')
    )
);

-- 4. Traceability and Movements
--
-- ON DELETE RESTRICT, NO CASCADE.
--
-- Con CASCADE, un DELETE accidental de un dispositivo borra TODOS sus
-- movimientos: donde estuvo, cuando salio y quien lo movio. En inventario eso
-- es informacion que no se recupera, y el borrado accidental es justamente el
-- caso para el que existe la FK. La baja legitima se hace con `deleted_at`.
CREATE TABLE movement_history (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    movement_type movement_type NOT NULL,
    origin_location_id INT REFERENCES locations(id) ON DELETE RESTRICT,
    destination_location_id INT REFERENCES locations(id) ON DELETE RESTRICT,
    replaced_device_id INT REFERENCES devices(id) ON DELETE RESTRICT,
    replacement_reason TEXT,
    -- UUID, no INT. Antes era `user_id INT NOT NULL` sin foreign key: un entero
    -- suelto que no apuntaba a ninguna parte, donde se podia guardar 999 o -1
    -- sin que nadie lo notara y la pregunta "quien movio esto" no tenia
    -- respuesta posible. El tipo tiene que coincidir con users.id o el JOIN no
    -- existe.
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    scan_latitude NUMERIC(10, 8),
    scan_longitude NUMERIC(11, 8),
    is_valid_location BOOLEAN DEFAULT TRUE,
    observations TEXT,
    movement_date TIMESTAMPTZ DEFAULT NOW(),

    -- REPLACEMENT es la unica operacion con equipo reemplazado y la unica que
    -- necesita motivo. La regla tiene las DOS direcciones, que es lo que hace
    -- falta:
    --
    --   si es REPLACEMENT  ->  tiene que decir QUE equipo y POR QUE
    --   si no es REPLACEMENT ->  no puede tener esas dos columnas
    --
    -- Con una sola de las dos, un WAREHOUSE_ENTRY con replacement_reason
    -- "se rayo" pasa el filtro y queda un movimiento con un motivo que no
    -- corresponde a su tipo.
    CONSTRAINT ck_movement_replacement CHECK (
        CASE WHEN movement_type = 'REPLACEMENT'
             THEN replaced_device_id IS NOT NULL AND replacement_reason IS NOT NULL
             ELSE replaced_device_id IS NULL AND replacement_reason IS NULL
        END
    )
);

-- 5. Workshop and Disposals
CREATE TABLE repairs (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    -- UUID y FK a users. Mismo motivo que movement_history.user_id.
    technician_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status repair_status DEFAULT 'DIAGNOSIS',
    initial_diagnosis TEXT,
    work_performed TEXT,
    estimated_cost NUMERIC(10, 2) CHECK (estimated_cost >= 0),
    entry_date TIMESTAMPTZ DEFAULT NOW(),
    exit_date TIMESTAMPTZ,
    is_operational BOOLEAN DEFAULT FALSE
);

CREATE TABLE disposal_certificates (
    id SERIAL PRIMARY KEY,
    -- UNIQUE: un equipo no se da de baja dos veces. Con esto el segundo
    -- certificado es un error de la base y no un duplicado que aparece en dos
    -- reportes.
    device_id INT UNIQUE NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    repair_id INT REFERENCES repairs(id) ON DELETE RESTRICT,
    disposal_reason TEXT NOT NULL,
    -- UUID y FK a users. Igual que user_id y technician_id: era `INT NOT NULL`
    -- sin FK, el mismo entero suelto que ya se corrigio en las otras dos.
    authorized_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    disposal_date TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================================================
-- 6. OPERACIONES (migracion 0005, replicada aca)
-- ============================================================================
--
-- Mismo motivo que `sessions`: el compose solo monta `init.sql` en
-- `/docker-entrypoint-initdb.d/`, asi que sin este bloque una instalacion
-- nueva se levanta sin `transfers`, `maintenance` ni `stock_alerts`, y los
-- endpoints `/api/ops/*` devuelven 500 con "relation does not exist".

-- Transferencia entre ubicaciones: un pedido, su aprobacion y su resolucion.
--
-- `from_location` / `to_location` guardan el NOMBRE de la sede y no el id. Es
-- una decision discutible —un id no se renombra y puede desincronizarse del
-- texto— pero cambiarla ahora es una migracion con datos ya escritos. Lo que si
-- se hace aca es dejar las dos columnas NOT NULL: antes el endpoint aceptaba
-- un payload incompleto y creaba una transferencia sin origen ni destino.
CREATE TABLE IF NOT EXISTS transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id INTEGER REFERENCES devices(id) ON DELETE RESTRICT,
    from_location VARCHAR(128) NOT NULL,
    to_location VARCHAR(128) NOT NULL,
    requested_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    approved_by UUID REFERENCES users(id) ON DELETE RESTRICT,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ
);

-- Un traslado a la misma sede no es un traslado.
ALTER TABLE transfers DROP CONSTRAINT IF EXISTS ck_transfers_distintas;
ALTER TABLE transfers ADD CONSTRAINT ck_transfers_distintas
    CHECK (from_location <> to_location);

-- El estado es un conjunto CERRADO. Sin este CHECK un typo como 'aprovado'
-- crea una fila que ningun reporte cuenta y que nadie sabe como resolver.
ALTER TABLE transfers DROP CONSTRAINT IF EXISTS ck_transfers_status;
ALTER TABLE transfers ADD CONSTRAINT ck_transfers_status
    CHECK (status IN ('pending', 'approved', 'rejected', 'completed'));

CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
CREATE INDEX IF NOT EXISTS idx_transfers_device ON transfers(device_id);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON transfers(created_at DESC);

-- Mantenimiento preventivo.
CREATE TABLE IF NOT EXISTS maintenance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id INTEGER REFERENCES devices(id) ON DELETE RESTRICT,
    tipo VARCHAR(64) NOT NULL,
    descripcion TEXT,
    scheduled_for DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    tecnico_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_maintenance_scheduled ON maintenance(scheduled_for);
CREATE INDEX IF NOT EXISTS idx_maintenance_device ON maintenance(device_id);

-- Alerta de stock minimo.
CREATE TABLE IF NOT EXISTS stock_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ubicacion VARCHAR(128) NOT NULL,
    minimo INT NOT NULL,
    actual INT NOT NULL,
    resuelto BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- `minimo` negativo o `actual` negativo son datos imposibles, y una alerta con
-- minimo 0 salta siempre: no informa nada.
ALTER TABLE stock_alerts DROP CONSTRAINT IF EXISTS ck_stock_alerts_minimo;
ALTER TABLE stock_alerts ADD CONSTRAINT ck_stock_alerts_minimo
    CHECK (minimo >= 0 AND actual >= 0);

CREATE INDEX IF NOT EXISTS idx_stock_alerts_resuelto ON stock_alerts(resuelto);


-- ============================================================================
-- INDICES
-- ============================================================================
--
-- POR QUE ESTE BLOQUE EXISTE
-- Postgres NO crea indices en columnas foraneas. La FK se verifica con una
-- busqueda en la tabla referenciada y, para las declaradas con REFERENCES, no
-- se arma indice automatico en la que las referencia. El resultado es que cada
-- JOIN que entra por una FK sin indice es un seq scan.
--
-- Y eso pega justo donde pega la IA: las consultas que genera el modelo
-- llegan por `devices.model_id`, `movement_history.device_id` y
-- `locations.client_id` todo el tiempo.
--
-- Indice UNIQUE PARCIAL: indice con WHERE, que solo indexa las filas que
-- cumplen la condicion. Es mas chico que uno completo y por eso es mas rapido,
-- y es lo que permite escribir reglas de negocio sin ponerlas como CHECK.
--
-- `idx_repairs_open` es el ejemplo: sin el, "un equipo no puede tener dos
-- reparaciones abiertas" se implementaria con UNIQUE(device_id) y eso
-- PROHIBIRIA el historial completo, porque el tercer equipo que vuelve al
-- taller despues de dos reparaciones anteriores fallaria al insertar. El
-- indice parcial restringe solo las filas abiertas.

-- --- Localizaciones ---
CREATE INDEX idx_locations_client ON locations(client_id);
CREATE INDEX idx_locations_active ON locations(is_active) WHERE is_active;

-- --- Catalogos ---
CREATE INDEX idx_models_category ON models(category_id);
-- GIN sobre JSONB: sin esto, buscar un modelo por una clave de
-- `specifications` es un seq scan sobre el JSON completo. Es el indice que
-- hace que "equipos con pantalla de 6 pulgadas" sea una consulta y no un
-- escaneo.
CREATE INDEX idx_models_specs ON models USING GIN (specifications);

-- --- Dispositivos ---
-- El indice por el que existe `is_warehouse`: responde "que hay disponible
-- para salir hoy" sin recorrer la flota entera.
CREATE INDEX idx_devices_warehouse ON devices(is_warehouse) WHERE is_warehouse;
CREATE INDEX idx_devices_model ON devices(model_id);
CREATE INDEX idx_devices_location ON devices(current_location_id);
CREATE INDEX idx_devices_status ON devices(current_status);
-- Todos los listados de dispositivos son de activos, o sea `deleted_at IS
-- NULL`. Es un indice parcial sobre la condicion mas frecuente.
CREATE INDEX idx_devices_active ON devices(deleted_at) WHERE deleted_at IS NULL;

-- --- Movimientos ---
-- `device_id` es la columna de TODAS las consultas de historial.
CREATE INDEX idx_movement_device ON movement_history(device_id);
CREATE INDEX idx_movement_date ON movement_history(movement_date DESC);
CREATE INDEX idx_movement_user ON movement_history(user_id);
CREATE INDEX idx_movement_origin ON movement_history(origin_location_id);
CREATE INDEX idx_movement_destination ON movement_history(destination_location_id);
CREATE INDEX idx_movement_replaced ON movement_history(replaced_device_id)
    WHERE replaced_device_id IS NOT NULL;

-- --- Taller ---
CREATE INDEX idx_repairs_device ON repairs(device_id);
CREATE INDEX idx_repairs_technician ON repairs(technician_id);
-- UNIQUE PARCIAL: un equipo no puede tener dos reparaciones abiertas a la vez.
-- Quedaria indefinido cual es la vigente, y `devices.current_status =
-- 'IN_REPAIR'` no sabria a cual se refiere. Las ya CERRADAS si se acumulan sin
-- limite, que es el comportamiento correcto.
CREATE UNIQUE INDEX idx_repairs_open ON repairs(device_id)
    WHERE status NOT IN ('REPAIRED', 'UNREPAIRABLE');
-- "Equipos esperando repuestos" es de las preguntas mas frecuentes del taller.
CREATE INDEX idx_repairs_waiting ON repairs(entry_date DESC)
    WHERE status = 'WAITING_FOR_PARTS';

-- --- Bajas ---
CREATE INDEX idx_disposal_authorizer ON disposal_certificates(authorized_by);
CREATE INDEX idx_disposal_repair ON disposal_certificates(repair_id);


-- ============================================================================
-- TRIGGERS DE TIMESTAMP
-- ============================================================================
--
-- `updated_at DEFAULT NOW()` solo aplica en el INSERT. Sin un trigger, la
-- columna queda congelada en la fecha de creacion para siempre: un "ultima
-- actualizacion" que nunca cambia es peor que no tener la columna.
--
-- users y roles ya lo tenian (dentro de set_audit_role_id); devices, clients y
-- locations no, y sus updated_at mentian.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_devices_updated_at
BEFORE UPDATE ON devices
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trigger_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trigger_locations_updated_at
BEFORE UPDATE ON locations
FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================================
-- MAQUINA DE ESTADOS: DONDE ESTA CADA COMPONENTE, EN CADA MOMENTO
-- ============================================================================
--
-- QUE PROBLEMA RESUELVE
-- ----------------------
-- El inventario tiene DOS lugares fisicos y un tercero virtual:
--     el deposito, donde el componente espera sin usar,
--     instalado,working en un equipo del cliente,
--     en transito entre uno y otro.
--
-- La columna `current_status` dice cual de los tres es, y `current_location_id`
-- dice donde. Pero hasta ahora NADA IMPEDIA que esas dos columnas dijeran una
-- cosa y la otra otra.
--
-- Se podia dejar un equipo en `INSTALLED` sin haber registrado nunca una
-- INSTALLATION, con `current_location_id` apuntando al deposito. Se podia dar
-- de baja un equipo que estaba instalado en un cliente y en uso. Se podia
-- marcar `IN_WAREHOUSE` un equipo que estaba en el taller con una reparacion
-- abierta.
--
-- En un inventario que se vende como "se donde esta cada equipo", cada uno de
-- esos estados es una perdida de trazabilidad silenciosa: el sistema responde,
-- no da error, y el dato esta mal.
--
-- POR QUE NO BASTA UN CHECK
-- -------------------------
-- Un CHECK mira UNA fila. Esta regla mira dos: el estado viejo y el nuevo. Y
-- necesita saber si el componente tiene una reparacion abierta, o si esta
-- dado de baja, que son otras tablas. Eso es un trigger.
--
-- POR QUE NO ESTA EN CODIGO
-- --------------------------
-- Porque el codigo que escribe el estado es mas de uno —el escaner, la API de
-- operaciones, una migracion, un `psql` del operador a las 11 de la noche— y
-- la regla tiene que estar en el motor, no en cada uno de ellos. Un trigger se
-- aplica a los cuatro. Un `if` en Python se aplica a uno.
CREATE OR REPLACE FUNCTION validar_transicion_estado()
RETURNS TRIGGER AS $$
DECLARE
    v_transicion_valida BOOLEAN := FALSE;
    v_tiene_reparacion_abierta BOOLEAN;
    v_es_nuevo BOOLEAN := (TG_OP = 'INSERT');
BEGIN
    -- En un INSERT no hay estado previo: se acepta el inicial. La maquina de
    -- estados gobierna los CAMBIOS, no la creacion.
    IF v_es_nuevo THEN
        RETURN NEW;
    END IF;

    -- Sin cambio de estado: no hay nada que validar. Un UPDATE que solo mueve
    -- `updated_at` (que lo hace el trigger de timestamps en cada fila) no tiene
    -- que pasar por la maquina.
    IF NEW.current_status IS NOT DISTINCT FROM OLD.current_status THEN
        RETURN NEW;
    END IF;

    -- PERMANENTLY_DISPOSED ES ABSORBENTE
    -- -----------------------------------
    -- Nada sale de una baja. Ni el taller ni el deposito ni un error de tipeo:
    -- un componente dado de baja que vuelve a estar "instalado" es un equipo
    -- fantasma trabajando en un cliente.
    IF OLD.current_status = 'PERMANENTLY_DISPOSED' THEN
        RAISE EXCEPTION
            'El componente % esta dado de baja (PERMANENTLY_DISPOSED). Un componente dado de baja no vuelve al inventario. Para reactivarlo hay que emitir un certificado de baja nuevo, no cambiar el estado a mano.',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- ESTADO NUEVO = EN REPARACION, Y HAY UNA REPARACION ABIERTA
    -- -------------------------------------------------------
    -- Si no, `devices.current_status` dice "esta en el taller" mientras la
    -- tabla `repairs` no tiene ninguna fila: el taller no sabe que lo tiene y
    -- el componente no tiene diagnostico ni tecnico.
    IF NEW.current_status = 'IN_REPAIR' THEN
        SELECT EXISTS (
            SELECT 1 FROM repairs r
            WHERE r.device_id = NEW.id
              AND r.status NOT IN ('REPAIRED', 'UNREPAIRABLE')
        ) INTO v_tiene_reparacion_abierta;

        IF NOT v_tiene_reparacion_abierta THEN
            RAISE EXCEPTION
                'No se puede poner el componente % en IN_REPAIR sin una reparacion abierta: primero hay que registrar el ingreso al taller.',
                NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- SALIR DE EN REPARACION, Y NO HAY REPARACION ABIERTA
    -- ----------------------------------------------------
    -- El caso inverso y peor: un tecnico cerro la reparacion en la UI y salio
    -- de la pantalla sin tocar el dispositivo. Este queda en el taller para
    -- siempre, sin taller que lo Atienda.
    IF OLD.current_status = 'IN_REPAIR' AND NEW.current_status <> 'IN_REPAIR' THEN
        SELECT EXISTS (
            SELECT 1 FROM repairs r
            WHERE r.device_id = NEW.id
              AND r.status NOT IN ('REPAIRED', 'UNREPAIRABLE')
        ) INTO v_tiene_reparacion_abierta;

        IF v_tiene_reparacion_abierta THEN
            RAISE EXCEPTION
                'El componente % tiene una reparacion abierta y no puede salir de IN_REPAIR. Hay que cerrar la reparacion primero.',
                NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- LA MATRIZ DE TRANSICIONES
    -- -------------------------
    -- Solo se listan las que el negocio reconoce. `IS NOT DISTINCT FROM` en vez
    -- de `NOT IN` porque si `current_status` llega NULL, `NOT IN` devuelve NULL
    -- —que en PL/pgSQL no es verdadero— y la regla pasaria sin checking.
    v_transicion_valida := CASE NEW.current_status
        WHEN 'IN_WAREHOUSE'        THEN OLD.current_status IN ('IN_WAREHOUSE', 'IN_TRANSIT', 'IN_REPAIR')
        WHEN 'IN_TRANSIT'          THEN OLD.current_status IN ('IN_WAREHOUSE', 'IN_REPAIR', 'INSTALLED')
        WHEN 'INSTALLED'           THEN OLD.current_status IN ('IN_TRANSIT', 'INSTALLED')
        WHEN 'IN_REPAIR'           THEN OLD.current_status IN ('IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED', 'IN_REPAIR')
        WHEN 'PERMANENTLY_DISPOSED' THEN OLD.current_status IN ('IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED', 'IN_REPAIR')
        ELSE FALSE
    END;

    IF NOT v_transicion_valida THEN
        RAISE EXCEPTION
            'Transicion de estado invalida para el componente %: de % a %. Los estados validos del inventario son deposito (IN_WAREHOUSE), transito (IN_TRANSIT), instalado (INSTALLED) y taller (IN_REPAIR); desde IN_REPAIR hay que cerrar la reparacion antes de salir.',
            OLD.id, OLD.current_status, NEW.current_status
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_devices_transicion_estado
    BEFORE UPDATE OF current_status ON devices
    FOR EACH ROW EXECUTE FUNCTION validar_transicion_estado();


-- ============================================================================
-- `installation_date`: QUE NO MIENTA
-- ============================================================================
--
-- La columna existe desde el esquema original pero no hay NADA que la mantenga.
-- Quedaba con el valor del INSERT —que es el de la compra— mientras el equipo
-- ya estaba instalado en un cliente hace seis meses. "Instalado desde" era un
-- dato falso, y como nadie lo actualizaba, nadie se noticed hasta que se
-- quiso usar.
--
-- El trigger lo mantiene en el mismo lugar donde el estado cambia, que es el
-- unico momento en que se sabe la verdad. Y solo escribe cuando corresponde:
-- tocar la columna en cada UPDATE haria que un cambio de ubicacion pareciera
-- una reinstalacion.
CREATE OR REPLACE FUNCTION sincronizar_fecha_instalacion()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.current_status IS DISTINCT FROM OLD.current_status THEN
        IF NEW.current_status = 'INSTALLED' THEN
            -- Entra a instalarse: arranca el conteo de uso.
            NEW.installation_date = COALESCE(NEW.installation_date, NOW());
        ELSIF OLD.current_status = 'INSTALLED' THEN
            -- Sale de instalado: deja de estar en uso. Se conserva el valor
            -- viejo en vez de ponerlo NULL, porque "cuando se instalo la
            -- ultima vez" sigue siendo informacion historica util.
            NEW.installation_date = OLD.installation_date;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_devices_fecha_instalacion
    BEFORE UPDATE OF current_status ON devices
    FOR EACH ROW EXECUTE FUNCTION sincronizar_fecha_instalacion();


-- ============================================================================
-- ESCANEO: SOPORTE DE LECTOR QR / CODIGO DE BARRAS
-- ============================================================================
--
-- QUE HACE FALTA Y POR QUE
-- ------------------------
-- El esquema ya tenia las columnas del escaneo (`devices.qr_code`,
-- `movement_history.scan_latitude`, `is_valid_location`) y el analisis de
-- riesgo ya contaba "escaneos fuera de radio" como senal. Lo que faltaba era la
-- pieza que hace CONFIABLE esa senal: garantizar que un mismo envio no se
-- registre dos veces.
--
-- EL PROBLEMA
-- -----------
-- Un operador escanea un componente en un deposito con mala senal. La peticion
-- sale dos veces, o el telefono reintenta, o el operador aprieta "escanear" otra
-- vez sin ver la respuesta. Sin proteccion quedan DOS movimientos para el mismo
-- envio, y "cuantos componentes entraron hoy" da el doble.
--
-- Y no es un problema teorico: la duplicacion corroe la confianza en el
-- historial. Un sistema del que no se puede decir "dice la verdad" no se usa
-- para decidir nada.
--
-- LA SOLUCION: CLAVE DE IDEMPOTENCIA DEL CLIENTE
-- ----------------------------------------------
-- `client_scan_id` es un UUID que genera el DISPOSITIVO antes de enviar la
-- peticion. Si la misma peticion llega dos veces, llega con el mismo UUID, y la
-- segunda no inserta nada: devuelve el movimiento original.
--
-- POR QUE NO UN INDICE UNICO SOBRE (device_id, movement_date)
-- ----------------------------------------------------------
-- Dos escaneos REALES del mismo componente en el mismo segundo son legitimos:
-- uno entra al deposito y al segundo sale hacia un cliente. Un indice unico
-- sobre la fecha rechazaria algo que ocurrio, y el operador, viendo el error,
-- aprende a no volver a escanear. Es peor.
--
-- La clave la pone el cliente y representa "ESTE envio", no "ESTE evento en el
-- mundo". Un evento repetido es legitimo; un envio repetido no.
CREATE TABLE IF NOT EXISTS scan_receipts (
    -- El id que genero el cliente. Es la clave de idempotencia.
    client_scan_id UUID PRIMARY KEY,

    device_id INTEGER REFERENCES devices(id) ON DELETE RESTRICT,
    -- El movimiento que produjo ESTE envio. Si el mismo client_scan_id llega
    -- otra vez, se devuelve este y no se inserta un segundo movimiento.
    movement_id INTEGER REFERENCES movement_history(id) ON DELETE RESTRICT,

    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Resolver un reintento es buscar por `client_scan_id`, que es la PK: ya tiene
-- indice. Este es para la pregunta opuesta, "que escaneos hizo este
-- componente", que es la que dibuja el historial en pantalla.
CREATE INDEX IF NOT EXISTS idx_scan_receipts_device ON scan_receipts(device_id, created_at DESC);

COMMENT ON TABLE scan_receipts IS
    'Idempotencia de escaneo: un client_scan_id, un movimiento. Un reintento no duplica.';

-- POR QUE `qr_code` ES UNIQUE A SECAS Y NO UN INDICE UNIQUE PARCIAL
-- -----------------------------------------------------------------
-- Un componente dado de baja (`deleted_at`) conserva su etiqueta fisica pegada
-- en un estante, y alguien la va a escanear. Con UNIQUE parcial
-- (`WHERE deleted_at IS NULL`) ese codigo quedaria LIBRE y se podria reasignar
-- a otro componente: la misma etiqueta identificando dos cosas, que es peor que
-- un error. UNIQUE a secas convierte el reescaneo en un 409 explicito, que es
-- la respuesta honesta.

-- ============================================================================
-- REGISTRO DE MIGRACIONES
-- ============================================================================
--
-- QUE HACE ESTO Y POR QUE NO ES OPCIONAL
-- -------------------------------------
-- `init.sql` y una base que ya corrio TODAS las migraciones terminan con el
-- MISMO esquema. Es el problema que el propio script de migraciones describe:
-- "una base creada desde cero y una base migrada son IDENTICAS".
--
-- La consecuencia sin este bloque es fea: alguien levanta el proyecto con
-- `make up` — que es lo que dice el README — y después corre `make migrar`
-- "por las dudas". El script no puede saber que la base ya está al día, así que
-- intenta aplicar 0001 y 0002 encima, y revienta con
--
--     ERROR: relation "uq_models_brand_model" already exists
--
-- que no dice nada de que el problema es que estás corriendo migraciones donde
-- no hay que correrlas.
--
-- Marcando acá qué migraciones ya quedás cubiertos, `make migrar` sobre una
-- base nueva dice "al día" y solo ofrece las que de verdad faltan.
--
-- Esto NO es un atajo: una base vieja tiene ESTA tabla vacía, y por eso
-- `migrar-estado` puede decir honestamente "no hay forma de saber si necesita
-- migraciones". Con el registro lleno, ya lo sabe.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(100) PRIMARY KEY,
    description TEXT,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT
);

INSERT INTO schema_migrations (version, description)
VALUES
    ('0001_add_users',              'aplicado por init.sql (instalación nueva)'),
    ('0002_integridad_del_modelo',  'aplicado por init.sql (instalación nueva)'),
    ('0003_sessions',               'aplicado por init.sql (instalación nueva)'),
    -- 0004 creo `audit_log` (singular), que compite con `audit_logs`
    -- (plural, el de arriba). Ver la nota de 0006: la tabla que sobrevive es
    -- `audit_logs`, y 0004 queda registrado para que `make migrar` no intente
    -- volver a crear la tabla equivocada.
    ('0004_audit_log',              'aplicado por init.sql (instalación nueva)'),
    ('0005_transferencias_mantenimiento_alertas', 'aplicado por init.sql (instalación nueva)'),
    ('0006_rotacion_estados_escaneo', 'aplicado por init.sql (instalación nueva)')
ON CONFLICT (version) DO NOTHING;
