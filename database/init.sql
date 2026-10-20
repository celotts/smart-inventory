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
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS stock_items CASCADE;
DROP TABLE IF EXISTS installation_periods CASCADE;
DROP TABLE IF EXISTS movement_types CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
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


-- ============================================================================
-- FUNCTION: set_updated_at
-- ============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: set_updated_at
-- ============================================================================



-- 1. Enumerated Types (ENUMs)
CREATE TYPE device_status AS ENUM (
    'IN_WAREHOUSE',
    'IN_TRANSIT',
    'INSTALLED',
    'IN_REPAIR',
    'PERMANENTLY_DISPOSED'
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
--
-- `record_id` es TEXT y no UUID por eso mismo: de las nueve tablas auditadas,
-- siete tienen id entero y solo dos lo tienen uuid. Un unico tipo uuid no
-- puede guardar las dos cosas; el texto si. (La migracion 0013 corrige el
-- DDL de las bases que ya tenian la columna como uuid.)
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action audit_action NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    record_id TEXT,
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

-- ============================================================================
-- 3b. Tipos de movimiento personalizables (configurables por cliente)
-- ============================================================================
--
-- ANTES esto era un ENUM (`movement_type`) con los valores fijos en el codigo.
-- Un cliente cuyo flujo no calzaba — "prestado en sitio", "en garantia del
-- proveedor" — no podia registrar su operacion: el sistema no tenia nombre para
-- ese movimiento, y un movimiento sin nombre no se puede reportar.
--
-- Ahora los tipos base viven como filas con `is_system = TRUE` (client_id NULL,
-- no editables ni borrables) y cada cliente agrega los suyos con su client_id.
-- La FK de movement_history pasa a apuntar al id, no a un string.
--
-- POR QUE ESTA AQUI Y NO SOLO EN 0010: init.sql es el instalador de una base
-- nueva. Si esta tabla viviera solo en la migracion, una instalacion limpia
-- -- que monte init.sql sin aplicar migraciones -- crearia movement_history con
-- una FK a una tabla inexistente y fallaria.
CREATE TABLE movement_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL = tipo del sistema (no editable). INT = tipo personalizado del cliente.
    client_id INT REFERENCES clients(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,           -- Identificador unico: 'WAREHOUSE_ENTRY', 'OUT_TO_CLIENT', etc.
    label VARCHAR(100) NOT NULL,         -- Texto visible: 'Sale a cliente', 'Instalo en sitio'
    target_status device_status NOT NULL, -- Estado destino al ejecutar este movimiento
    icon VARCHAR(50),
    color VARCHAR(20),                   -- Hex para la UI
    sort_order INT DEFAULT 0,
    is_system BOOLEAN DEFAULT FALSE,     -- TRUE = tipo del sistema (no editable)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_movement_type_client_name UNIQUE (client_id, name)
);

CREATE TRIGGER trigger_movement_types_updated_at
    BEFORE UPDATE ON movement_types
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_movement_types_client ON movement_types(client_id);
CREATE INDEX idx_movement_types_active ON movement_types(is_active);

-- Dos tipos del sistema con el mismo nombre romperian el catalogo global.
CREATE UNIQUE INDEX uq_movement_type_system_name ON movement_types(name) WHERE (is_system = TRUE);

-- Seed de los tipos del sistema. Los nombres coinciden 1:1 con los valores del
-- ENUM que reemplazaron, asi que el mapeo nombre -> estado no se negocia.
INSERT INTO movement_types (name, label, target_status, icon, color, sort_order, is_system, is_active) VALUES
    ('WAREHOUSE_ENTRY', 'Entrada a depósito', 'IN_WAREHOUSE', 'package', '#2e7d32', 1, TRUE, TRUE),
    ('OUT_TO_CLIENT', 'Sale a cliente', 'IN_TRANSIT', 'truck', '#b45309', 2, TRUE, TRUE),
    ('INSTALLATION', 'Instalo en sitio', 'INSTALLED', 'home', '#0277bd', 3, TRUE, TRUE),
    ('REPLACEMENT', 'Reemplazo', 'IN_TRANSIT', 'swap', '#b45309', 4, TRUE, TRUE),
    ('WITHDRAWAL_TO_WORKSHOP', 'Manda a taller', 'IN_REPAIR', 'wrench', '#6b4a86', 5, TRUE, TRUE),
    ('WAREHOUSE_REENTRY', 'Vuelve a depósito', 'IN_WAREHOUSE', 'package-2', '#2e7d32', 6, TRUE, TRUE),
    ('DISPOSAL', 'Dar de baja', 'PERMANENTLY_DISPOSED', 'trash', '#c62828', 8, TRUE, TRUE);

-- ============================================================================
-- 4. Traceability and Movements
-- ============================================================================
CREATE TABLE movement_history (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    movement_type_id UUID NOT NULL REFERENCES movement_types(id) ON DELETE RESTRICT,
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
    -- SIN coma al final. La tabla no tiene ninguna columna mas despues de
    -- esta: el CHECK de REPLACEMENT que venia aca se movio al trigger de mas
    -- abajo, y al sacarlo se dejo la coma colgante. Postgres la rechaza con
    -- "syntax error at or near )" y el init.sql entero muere en esa linea:
    -- una base nueva no se creaba, y el contenedor de postgres salia con
    -- codigo 3 sin que el error llegara a la consola.
    movement_date TIMESTAMPTZ DEFAULT NOW()

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
-- La validacion REPLACEMENT NO puede ser un CHECK: un CHECK no admite
    -- subconsultas, y la regla necesita preguntar el nombre del tipo a
    -- movement_types. Por eso vive en el trigger de abajo.
);

-- ============================================================================
-- TRIGGER: ck_movement_replacement_trigger
-- ============================================================================
--
-- Implementa la misma regla que antes era el CHECK ck_movement_replacement,
-- pero con la diferencia que obliga: el tipo ahora es una FK, no un ENUM, asi
-- que "es REPLACEMENT" ya no se puede responder leyendo la fila -- hay que
-- preguntar a movement_types.
--
-- Sin esto, la regla no existiria: un REPLACEMENT sin motivo, o un
-- WAREHOUSE_ENTRY con motivo, entrarian igual y el reporte de reemplazos
-- contaria operacoes que no fueron reemplazos.
CREATE OR REPLACE FUNCTION ck_movement_replacement()
RETURNS TRIGGER AS $$
DECLARE
    tipo_nombre VARCHAR(50);
BEGIN
    SELECT name INTO tipo_nombre FROM movement_types WHERE id = NEW.movement_type_id;

    IF tipo_nombre = 'REPLACEMENT' THEN
        IF NEW.replaced_device_id IS NULL OR NEW.replacement_reason IS NULL THEN
            RAISE EXCEPTION
                'Un movimiento REPLACEMENT exige replaced_device_id y replacement_reason';
        END IF;
    ELSE
        IF NEW.replaced_device_id IS NOT NULL OR NEW.replacement_reason IS NOT NULL THEN
            RAISE EXCEPTION
                'Solo un movimiento REPLACEMENT puede tener replaced_device_id o replacement_reason';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ck_movement_replacement_trigger
    BEFORE INSERT OR UPDATE ON movement_history
    FOR EACH ROW EXECUTE FUNCTION ck_movement_replacement();

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

CREATE INDEX IF NOT EXISTS idx_scan_receipts_device ON scan_receipts(device_id, created_at DESC);

COMMENT ON TABLE scan_receipts IS
    'Idempotencia de escaneo: un client_scan_id, un movimiento. Un reintento no duplica.';


-- ============================================================================
-- PERIODOS DE INSTALACION (acumula tiempo de uso aunque se mueva el equipo)
-- ============================================================================
--
-- POR QUE ESTA TABLA
-- --------------------
-- El campo `devices.installation_date` se resetea cada vez que el equipo se
-- instala. Si el equipo va a reparación y vuelve al mismo sitio, se pierde el
-- tiempo acumulado anterior. Esta tabla guarda cada periodo de instalación
-- como una fila independiente, permitiendo consultar:
--   - Cuantos dias TOTALES lleva el equipo instalado en su vida util
--   - Cuantos dias en CADA sede especifica
--   - El historico completo de instalaciones sin perder datos por reparaciones
--
-- Se alimenta automaticamente por los triggers de cambio de estado:
--   - Entrada a INSTALLED  -> INSERT nueva fila (started_at = NOW)
--   - Salida de INSTALLED   -> UPDATE ended_at = NOW, days_installed cached
CREATE TABLE installation_periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    -- El movimiento que INICIO la instalacion (escaneo INSTALLATION)
    movement_id_in INTEGER REFERENCES movement_history(id) ON DELETE RESTRICT,
    -- El movimiento que TERMINO la instalacion (escaneo que saca de INSTALLED)
    movement_id_out INTEGER REFERENCES movement_history(id) ON DELETE RESTRICT,
    -- Dias instalados en ESTE periodo (cached para consultas rapidas)
    -- Se calcula al cerrar el periodo: EXTRACT(DAY FROM ended_at - started_at)
    days_installed INTEGER,

    -- Un equipo no puede tener dos periodos abiertos en la misma sede
    CONSTRAINT uq_install_period_open UNIQUE (device_id, location_id, ended_at)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX idx_install_periods_device ON installation_periods(device_id, started_at DESC);
CREATE INDEX idx_install_periods_location ON installation_periods(location_id, started_at DESC);
CREATE INDEX idx_install_periods_open ON installation_periods(device_id) WHERE ended_at IS NULL;

COMMENT ON TABLE installation_periods IS
    'Periodos de instalación por sede. Acumula tiempo de uso aunque el equipo vaya a reparación y vuelva.
     Cada fila = un periodo continuo en una sede. days_installed se cachea al cerrar.';


-- ============================================================================
-- INVENTARIO POR CANTIDAD (stock por SKU + ubicacion + lote)
-- ============================================================================
--
-- POR QUE ESTA TABLA Y NO REUTILIZAR devices
-- -------------------------------------------
-- devices = 1 fila = 1 EQUIPO UNICO (serial unico, vida util, reparacion)
-- stock_items = 1 fila = 1 SKU en 1 ubicacion + lote (cantidad, vencimiento)
--
-- devices = "este portatil SN-123" (identidad unica, trazabilidad individual)
-- stock_items = "15 cables USB-C lote L-2024-03 en Deposito Central" (cantidad)
--
-- NO se pueden unificar: devices exige serial unico; stock_items agrega por SKU+ubicacion+lote.
-- Se usan flujos distintos:
--   devices -> escaneo QR/serial -> movimiento individual -> trazabilidad completa
--   stock_items -> escaneo codigo barras + cantidad -> entrada/salida/ajuste -> stock
CREATE TABLE stock_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Mismo catalogo que devices (models -> categories)
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    -- Lote de fabricacion (trazabilidad para recalls, vencimientos)
    lot_number VARCHAR(50) NOT NULL DEFAULT '',
    -- Fecha de vencimiento (baterias, medicamentos, consumibles con caducidad)
    expiry_date DATE,
    -- Stock actual disponible
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    -- Cantidad reservada para ordenes/ordenes de trabajo
    reserved_quantity INTEGER NOT NULL DEFAULT 0 CHECK (reserved_quantity >= 0),
    -- Punto de reposicion (alerta cuando quantity - reserved <= min_stock)
    min_stock INTEGER NOT NULL DEFAULT 0 CHECK (min_stock >= 0),
    -- Capacidad maxima (opcional, para control de capacidad)
    max_stock INTEGER CHECK (max_stock IS NULL OR max_stock >= 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Un SKU + ubicacion + lote = una fila de stock
    CONSTRAINT uq_stock_item_unique UNIQUE (model_id, location_id, lot_number)
);

-- Trigger para updated_at
CREATE TRIGGER trigger_stock_items_updated_at
    BEFORE UPDATE ON stock_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Indices para consultas de stock
CREATE INDEX idx_stock_items_model ON stock_items(model_id);
CREATE INDEX idx_stock_items_location ON stock_items(location_id);
CREATE INDEX idx_stock_items_expiry ON stock_items(expiry_date) WHERE expiry_date IS NOT NULL;
CREATE INDEX idx_stock_items_low ON stock_items(model_id, location_id)
    WHERE (quantity - reserved_quantity) <= min_stock AND min_stock > 0;

COMMENT ON TABLE stock_items IS
    'Inventario por cantidad (SKU + ubicacion + lote). Para consumibles, repuestos genericos,
     insumos con vencimiento. NO para equipos con serial unico (eso es devices).';


-- ============================================================================
-- MOVIMIENTOS DE STOCK (entrada, salida, traslado, ajuste, vencimiento)
-- ============================================================================
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_item_id UUID NOT NULL REFERENCES stock_items(id) ON DELETE RESTRICT,
    -- Tipo de movimiento
    movement_type VARCHAR(20) NOT NULL CHECK (movement_type IN (
        'IN',              -- Entrada (compra, recepcion, devolucion)
        'OUT',             -- Salida (consumo, venta, asignacion a dispositivo)
        'TRANSFER_IN',     -- Entrada por traslado desde otra ubicacion
        'TRANSFER_OUT',    -- Salida por traslado a otra ubicacion
        'ADJUSTMENT',      -- Ajuste de inventario (conteo ciclico, merma)
        'EXPIRED',         -- Vencimiento / baja por caducidad
        'RETURN'           -- Devolucion de prestamo / garantia
    )),
    quantity INTEGER NOT NULL CHECK (quantity != 0),
    -- Trazabilidad del origen/destino del movimiento
    reference_type VARCHAR(30),   -- 'purchase_order', 'work_order', 'sale', 'adjustment', 'transfer'
    reference_id UUID,
    -- Para TRANSFER: ubicacion origen/destino
    from_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    to_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    -- Lote afectado (si el stock_item tiene lote, se hereda; si no, se declara)
    lot_number VARCHAR(50),
    expiry_date DATE,
    -- Usuario que registra el movimiento
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    -- Idempotencia: client_scan_id para que reintentos no dupliquen
    client_scan_id UUID UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Validaciones logicas
    CONSTRAINT chk_stock_movement_qty_positive CHECK (
        (movement_type IN ('IN', 'TRANSFER_IN', 'RETURN') AND quantity > 0) OR
        (movement_type IN ('OUT', 'TRANSFER_OUT', 'EXPIRED', 'ADJUSTMENT') AND quantity != 0)
    ),
    CONSTRAINT chk_transfer_locations CHECK (
        (movement_type IN ('TRANSFER_IN', 'TRANSFER_OUT') AND from_location_id IS NOT NULL AND to_location_id IS NOT NULL) OR
        (movement_type NOT IN ('TRANSFER_IN', 'TRANSFER_OUT'))
    )
);

CREATE INDEX idx_stock_movements_item ON stock_movements(stock_item_id, created_at DESC);
CREATE INDEX idx_stock_movements_type ON stock_movements(movement_type, created_at DESC);
CREATE INDEX idx_stock_movements_ref ON stock_movements(reference_type, reference_id);
CREATE INDEX idx_stock_movements_client_scan ON stock_movements(client_scan_id) WHERE client_scan_id IS NOT NULL;

COMMENT ON TABLE stock_movements IS
    'Movimientos de stock por cantidad. Cada fila = una entrada/salida/traslado/ajuste.
     client_scan_id permite idempotencia: reintentar el mismo escaneo no duplica el movimiento.';


-- ============================================================================
-- INDICES Y TRIGGERS ADICIONALES
-- ============================================================================

-- Trigger updated_at para stock_items (ya existe set_updated_at)
-- Trigger updated_at para stock_movements si se permite UPDATE (no recomendado, es append-only)


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
    ('0006_rotacion_estados_escaneo', 'aplicado por init.sql (instalación nueva)'),
    -- 0010 es la que reemplaza el ENUM `movement_type` por la tabla
    -- `movement_types`. Arriba, init.sql YA crea `movement_history` con
    -- `movement_type_id` y el trigger `ck_movement_replacement`, o sea el estado
    -- final de 0010, no el punto de partida. Sin este registro, `make migrar`
    -- sobre una base recien instalada intentaria correr los ALTER de 0010 y
    -- fallarian: `ADD COLUMN movement_type_id` sobre una columna que ya existe.
    -- Este registro dice "no la necesitas, tu base ya esta en ese estado".
    ('0010_movement_types_dynamic',  'aplicado por init.sql (instalación nueva)'),
    -- 0011 y 0012 estan replicadas mas abajo en este mismo archivo, con sus
    -- tablas, funciones y vistas. Sin estos registros, `make migrar` sobre una
    -- base recien instalada las volveria a correr: 0011 es idempotente, pero
    -- 0012 hace `CREATE TABLE vendors` a secas y fallaria porque la tabla ya
    -- existe.
    ('0011_extend_catalog_stock_maintenance', 'aplicado por init.sql (instalación nueva)'),
    ('0012_purchasing',           'aplicado por init.sql (instalación nueva)'),
    -- 0013 (el trigger de auditoria y el `record_id` en TEXT) tambien esta
    -- replicada mas abajo, al final, cuando todas las tablas ya existen.
    ('0013_audit_record_id_texto', 'aplicado por init.sql (instalación nueva)')
ON CONFLICT (version) DO NOTHING;


-- ============================================================================
-- 0011 — Extender catalogo + stock + mantenimiento (migracion replicada aca)
-- ============================================================================
-- Por que esta replicada: el compose monta UN SOLO archivo en
-- `/docker-entrypoint-initdb.d/`, y los archivos de `migrations/` no corren en
-- una instalacion nueva. Sin esta seccion, una base creada desde cero se
-- quedaba sin `stock_levels`, sin `maintenance_plans` y sin las vistas de
-- stock: la app arrancaba, pero `/api/stock/summary` y `/api/maintenance`
-- no tenian de donde leer.
--
-- Es idempotente (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`),
-- asi que tambien se puede correr encima de esta base sin romper nada.
--
-- CONTENIDO DE 0011_extend_catalog_stock_maintenance.sql
-- ============================================================================

ALTER TABLE categories ADD COLUMN IF NOT EXISTS code VARCHAR(20) UNIQUE;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS icon VARCHAR(50);
ALTER TABLE categories ADD COLUMN IF NOT EXISTS color VARCHAR(20);
ALTER TABLE categories ADD COLUMN IF NOT EXISTS sort_order INT DEFAULT 0;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- Actualizar categorías existentes con códigos e iconos
--
-- POR QUE EL CASE MIRA LOS DOS IDIOMAS
-- ------------------------------------
-- La version original solo contemplaba nombres en INGLES ('Notebook',
-- 'Networking', 'Server'...). La base real los tiene en ESPANOL ('Impresora',
-- 'Red', 'Servidor', 'Periferico'), asi que caian todas en el `ELSE 'OT'` y el
-- UPDATE moria con:
--
--     duplicate key value violates unique constraint "categories_code_key"
--
-- porque cuatro categorias distintas recibian el mismo codigo. Se agregan los
-- alias en espanol y el `ELSE` deja de ser una constante: genera un codigo unico
-- a partir del nombre para cualquier categoria que no este en la lista.
UPDATE categories SET 
    code = CASE
        WHEN name IN ('Notebook', 'Notebook / Laptop')  THEN 'NB'
        WHEN name IN ('Desktop', 'Desktop / Workstation') THEN 'DT'
        WHEN name IN ('Monitor', 'Monitor / Pantalla')  THEN 'MN'
        WHEN name IN ('Networking', 'Red')              THEN 'NW'
        WHEN name IN ('Server', 'Servidor', 'Servidor / Storage') THEN 'SV'
        WHEN name IN ('UPS', 'UPS / Energía')           THEN 'UP'
        WHEN name IN ('Peripherals', 'Periferico', 'Periférico') THEN 'PF'
        WHEN name IN ('Printer', 'Impresora')           THEN 'PR'
        WHEN name IN ('Phone', 'Telefono', 'Teléfono')  THEN 'PH'
        ELSE UPPER(LEFT(REGEXP_REPLACE(name, '[^a-zA-Z0-9]', '', 'g'), 2)) || id
    END,
    icon = CASE
        WHEN name IN ('Notebook', 'Notebook / Laptop')  THEN 'laptop'
        WHEN name IN ('Desktop', 'Desktop / Workstation') THEN 'desktop'
        WHEN name IN ('Monitor', 'Monitor / Pantalla')  THEN 'monitor'
        WHEN name IN ('Networking', 'Red')              THEN 'wifi'
        WHEN name IN ('Server', 'Servidor', 'Servidor / Storage') THEN 'server'
        WHEN name IN ('UPS', 'UPS / Energía')           THEN 'battery'
        WHEN name IN ('Peripherals', 'Periferico', 'Periférico') THEN 'mouse'
        WHEN name IN ('Printer', 'Impresora')           THEN 'printer'
        WHEN name IN ('Phone', 'Telefono', 'Teléfono')  THEN 'phone'
        ELSE 'package'
    END,
    color = CASE
        WHEN name IN ('Notebook', 'Notebook / Laptop')  THEN '#0277bd'
        WHEN name IN ('Desktop', 'Desktop / Workstation') THEN '#2e7d32'
        WHEN name IN ('Monitor', 'Monitor / Pantalla')  THEN '#b45309'
        WHEN name IN ('Networking', 'Red')              THEN '#6b4a86'
        WHEN name IN ('Server', 'Servidor', 'Servidor / Storage') THEN '#c62828'
        WHEN name IN ('UPS', 'UPS / Energía')           THEN '#795548'
        WHEN name IN ('Peripherals', 'Periferico', 'Periférico') THEN '#37474f'
        WHEN name IN ('Printer', 'Impresora')           THEN '#5d4037'
        WHEN name IN ('Phone', 'Telefono', 'Teléfono')  THEN '#00695c'
        ELSE '#616161'
    END,
    sort_order = CASE
        WHEN name IN ('Notebook', 'Notebook / Laptop')  THEN 1
        WHEN name IN ('Desktop', 'Desktop / Workstation') THEN 2
        WHEN name IN ('Monitor', 'Monitor / Pantalla')  THEN 3
        WHEN name IN ('Networking', 'Red')              THEN 4
        WHEN name IN ('Server', 'Servidor', 'Servidor / Storage') THEN 5
        WHEN name IN ('UPS', 'UPS / Energía')           THEN 6
        WHEN name IN ('Peripherals', 'Periferico', 'Periférico') THEN 7
        WHEN name IN ('Printer', 'Impresora')           THEN 8
        WHEN name IN ('Phone', 'Telefono', 'Teléfono')  THEN 9
        ELSE 99
    END
WHERE code IS NULL;

-- ===========================================================================
-- 2. EXTENDER MODELS (agregar campos de SKU, stock, costos, mantenimiento)
-- ===========================================================================
ALTER TABLE models ADD COLUMN IF NOT EXISTS sku VARCHAR(50) UNIQUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS variant VARCHAR(100);           -- 'i5-1335U/16GB/512GB/14"'
ALTER TABLE models ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(12,2);       -- Costo unitario promedio
ALTER TABLE models ADD COLUMN IF NOT EXISTS list_price NUMERIC(12,2);      -- Precio de lista
ALTER TABLE models ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'USD';
ALTER TABLE models ADD COLUMN IF NOT EXISTS min_stock INT DEFAULT 0;       -- Punto de reposición
ALTER TABLE models ADD COLUMN IF NOT EXISTS max_stock INT DEFAULT 100;     -- Tope máximo
ALTER TABLE models ADD COLUMN IF NOT EXISTS reorder_multiple INT DEFAULT 1; -- Múltiplo de compra
ALTER TABLE models ADD COLUMN IF NOT EXISTS lead_time_days INT DEFAULT 14; -- Días entrega proveedor
ALTER TABLE models ADD COLUMN IF NOT EXISTS preferred_vendor_id INT;       -- Proveedor preferido (FK future)
ALTER TABLE models ADD COLUMN IF NOT EXISTS warranty_months INT DEFAULT 36; -- Garantía (ya existe pero default 12)
ALTER TABLE models ADD COLUMN IF NOT EXISTS eol_date DATE;                 -- Fin de vida anunciado
ALTER TABLE models ADD COLUMN IF NOT EXISTS replacement_cycle_months INT;  -- Ciclo renovación sugerido
ALTER TABLE models ADD COLUMN IF NOT EXISTS requires_maintenance BOOLEAN DEFAULT FALSE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS maintenance_interval_months INT; -- Intervalo base mantenimiento
ALTER TABLE models ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS is_serialized BOOLEAN DEFAULT TRUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS track_by_lot BOOLEAN DEFAULT FALSE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id);

-- Generar SKU automático para modelos existentes: MARCA-MODELO-ID
UPDATE models 
SET sku = UPPER(brand || '-' || REGEXP_REPLACE(model, '\s+', '', 'g') || '-' || id)
WHERE sku IS NULL;

-- ===========================================================================
-- 2b. CODIGOS DE ETIQUETA POR PRODUCTO (QR + codigo de barras)
-- ===========================================================================
-- La etiqueta del PRODUCTO (la caja, el rack, el estante), distinta de la
-- etiqueta del EQUIPO, que ya existe en `devices.qr_code` (`INV-...`).
--
-- El QR lleva el prefijo `PRD-` para que se distinga de un codigo de equipo
-- de un vistazo: los dos conviven en el mismo deposito. El codigo de barras
-- es el SKU, que es lo que ya esta escrito en la caja del proveedor.
--
-- Los dos son UNIQUE: dos productos con el mismo codigo hacen inutil la
-- etiqueta, porque el escaner no puede saber a cual pertenece.
ALTER TABLE models ADD COLUMN IF NOT EXISTS qr_code VARCHAR(150) UNIQUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS barcode VARCHAR(64) UNIQUE;

UPDATE models
SET qr_code = 'PRD-' || sku
WHERE qr_code IS NULL AND sku IS NOT NULL;

UPDATE models
SET barcode = sku
WHERE barcode IS NULL AND sku IS NOT NULL;

-- ===========================================================================
-- 3. STOCK LEVELS (stock agregado por modelo / ubicación)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS stock_levels (
    model_id INT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    location_id INT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    
    -- Contadores por estado (dispositivos individuales)
    available_count INT DEFAULT 0,      -- IN_WAREHOUSE (disponible para asignar)
    in_transit_count INT DEFAULT 0,     -- IN_TRANSIT (en camino a cliente)
    installed_count INT DEFAULT 0,      -- INSTALLED (en sitio del cliente)
    in_repair_count INT DEFAULT 0,      -- IN_REPAIR (en taller)
    
    -- Valor de inventario
    total_value NUMERIC(14,2) DEFAULT 0,
    
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (model_id, location_id)
);

CREATE INDEX IF NOT EXISTS idx_stock_levels_location ON stock_levels(location_id);
CREATE INDEX IF NOT EXISTS idx_stock_levels_available ON stock_levels(available_count);

-- ===========================================================================
-- 4. FUNCIÓN RECALCULAR STOCK
-- ===========================================================================
CREATE OR REPLACE FUNCTION recalculate_stock_levels(p_model_id INT, p_location_id INT)
RETURNS VOID AS $$
DECLARE
    v_available INT;
    v_in_transit INT;
    v_installed INT;
    v_in_repair INT;
    v_unit_cost NUMERIC(12,2);
BEGIN
    SELECT COUNT(*) FILTER (WHERE current_status = 'IN_WAREHOUSE'),
           COUNT(*) FILTER (WHERE current_status = 'IN_TRANSIT'),
           COUNT(*) FILTER (WHERE current_status = 'INSTALLED'),
           COUNT(*) FILTER (WHERE current_status = 'IN_REPAIR')
    INTO v_available, v_in_transit, v_installed, v_in_repair
    FROM devices
    WHERE model_id = p_model_id AND current_location_id = p_location_id;
    
    SELECT unit_cost INTO v_unit_cost FROM models WHERE id = p_model_id;
    
    INSERT INTO stock_levels (model_id, location_id, available_count, in_transit_count, installed_count, in_repair_count, total_value, updated_at)
    VALUES (p_model_id, p_location_id, v_available, v_in_transit, v_installed, v_in_repair, v_available * COALESCE(v_unit_cost, 0), NOW())
    ON CONFLICT (model_id, location_id) DO UPDATE SET
        available_count = EXCLUDED.available_count,
        in_transit_count = EXCLUDED.in_transit_count,
        installed_count = EXCLUDED.installed_count,
        in_repair_count = EXCLUDED.in_repair_count,
        total_value = EXCLUDED.total_value,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 5. TRIGGERS PARA MANTENER STOCK LEVELS
-- ===========================================================================
CREATE OR REPLACE FUNCTION trigger_device_stock_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Recalcular para ubicación anterior (si cambió)
    IF OLD.current_location_id IS NOT NULL AND OLD.model_id IS NOT NULL THEN
        PERFORM recalculate_stock_levels(OLD.model_id, OLD.current_location_id);
    END IF;
    -- Recalcular para ubicación nueva
    IF NEW.current_location_id IS NOT NULL AND NEW.model_id IS NOT NULL THEN
        PERFORM recalculate_stock_levels(NEW.model_id, NEW.current_location_id);
    END IF;
    -- Si cambió modelo
    IF OLD.model_id IS DISTINCT FROM NEW.model_id THEN
        IF OLD.model_id IS NOT NULL AND OLD.current_location_id IS NOT NULL THEN
            PERFORM recalculate_stock_levels(OLD.model_id, OLD.current_location_id);
        END IF;
        IF NEW.model_id IS NOT NULL AND NEW.current_location_id IS NOT NULL THEN
            PERFORM recalculate_stock_levels(NEW.model_id, NEW.current_location_id);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_device_stock_change ON devices;
CREATE TRIGGER trigger_device_stock_change
    AFTER INSERT OR UPDATE OR DELETE ON devices
    FOR EACH ROW EXECUTE FUNCTION trigger_device_stock_change();

-- ===========================================================================
-- 6. POBLAR STOCK_LEVELS INICIAL
-- ===========================================================================
DO $$
DECLARE
    v_rec RECORD;
BEGIN
    FOR v_rec IN SELECT DISTINCT model_id, current_location_id FROM devices WHERE model_id IS NOT NULL AND current_location_id IS NOT NULL
    LOOP
        PERFORM recalculate_stock_levels(v_rec.model_id, v_rec.current_location_id);
    END LOOP;
END;
$$;

-- ===========================================================================
-- 7. PLANES DE MANTENIMIENTO POR MODELO
-- ===========================================================================
CREATE TABLE IF NOT EXISTS maintenance_plans (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,                 -- 'Mantenimiento Anual UPS', 'Revisión Baterías'
    description TEXT,
    type VARCHAR(30) NOT NULL DEFAULT 'PREVENTIVE', -- 'PREVENTIVE', 'PREDICTIVE', 'CALIBRATION', 'INSPECTION'
    interval_months INT NOT NULL,               -- Cada cuántos meses (1, 3, 6, 12, 24)
    interval_basis VARCHAR(20) DEFAULT 'INSTALL_DATE', -- 'INSTALL_DATE' | 'MANUFACTURE_DATE' | 'LAST_MAINTENANCE'
    lead_time_days INT DEFAULT 30,              -- Avisar con X días antelación
    checklist JSONB DEFAULT '[]',               -- [{"step": "Verificar filtros", "required": true}]
    estimated_duration_minutes INT,
    requires_specialist BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_maintenance_plans_updated_at
    BEFORE UPDATE ON maintenance_plans
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_maintenance_plans_model ON maintenance_plans(model_id);

-- ===========================================================================
-- 8. EXTENDER TABLA MAINTENANCE EXISTENTE (agregar campos de plan + resultados)
-- ===========================================================================
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS plan_id INT REFERENCES maintenance_plans(id) ON DELETE SET NULL;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS due_date DATE GENERATED ALWAYS AS (scheduled_for + INTERVAL '7 days') STORED;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'PENDING'; -- 'PENDING', 'DUE_SOON', 'OVERDUE', 'COMPLETED', 'SKIPPED', 'CANCELLED'
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS duration_minutes INT;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS result VARCHAR(20); -- 'OK', 'NEEDS_REPAIR', 'NEEDS_REPLACEMENT', 'OBSERVATION'
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS observations TEXT;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS checklist_results JSONB DEFAULT '[]';
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS next_scheduled_for DATE;
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS labor_cost NUMERIC(10,2);
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS parts_cost NUMERIC(10,2);
ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS total_cost NUMERIC(10,2) GENERATED ALWAYS AS (COALESCE(labor_cost,0) + COALESCE(parts_cost,0)) STORED;

CREATE INDEX IF NOT EXISTS idx_maintenance_plan ON maintenance(plan_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_status ON maintenance(status);
CREATE INDEX IF NOT EXISTS idx_maintenance_due ON maintenance(due_date) WHERE status IN ('PENDING', 'DUE_SOON', 'OVERDUE');

-- Actualizar estados existentes
UPDATE maintenance SET status = 'COMPLETED' WHERE completed_at IS NOT NULL AND status IS NULL;
UPDATE maintenance SET status = 'PENDING' WHERE status IS NULL;

-- ===========================================================================
-- 9. FUNCIÓN: GENERAR MANTENIMIENTOS PENDIENTES (batch nocturno)
-- ===========================================================================
CREATE OR REPLACE FUNCTION generate_pending_maintenances()
RETURNS TABLE(device_id INT, plan_id INT, scheduled_for DATE, message TEXT) AS $$
DECLARE
    v_record RECORD;
    v_last_maint DATE;
    v_next_date DATE;
    v_base_date DATE;
BEGIN
    FOR v_record IN
        SELECT d.id AS device_id, d.model_id, d.installation_date,
               mp.id AS plan_id, mp.interval_months, mp.interval_basis, mp.lead_time_days
        FROM devices d
        JOIN maintenance_plans mp ON mp.model_id = d.model_id AND mp.is_active
        WHERE d.current_status IN ('INSTALLED', 'IN_WAREHOUSE')
          AND d.model_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM maintenance m
              WHERE m.device_id = d.id
                AND m.plan_id = mp.id
                AND m.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')
          )
    LOOP
        -- Calcular fecha base según interval_basis
        CASE v_record.interval_basis
            WHEN 'INSTALL_DATE' THEN v_base_date := v_record.installation_date;
            WHEN 'MANUFACTURE_DATE' THEN v_base_date := NULL; -- No tenemos manufacture_date en devices
            WHEN 'LAST_MAINTENANCE' THEN
                SELECT MAX(completed_at)::DATE INTO v_last_maint
                FROM maintenance
                WHERE device_id = v_record.device_id AND plan_id = v_record.plan_id AND status = 'COMPLETED';
                v_base_date := v_last_maint;
            ELSE v_base_date := v_record.installation_date;
        END CASE;
        
        IF v_base_date IS NULL THEN
            -- Sin fecha base, programar para dentro del intervalo desde hoy
            v_next_date := CURRENT_DATE + (v_record.interval_months || ' months')::INTERVAL;
        ELSE
            -- Calcular próxima ocurrencia
            v_next_date := v_base_date + (v_record.interval_months || ' months')::INTERVAL;
            WHILE v_next_date <= CURRENT_DATE LOOP
                v_next_date := v_next_date + (v_record.interval_months || ' months')::INTERVAL;
            END LOOP;
        END IF;
        
        -- Insertar mantenimiento pendiente
        INSERT INTO maintenance (device_id, plan_id, scheduled_for, status, tipo, descripcion)
        VALUES (v_record.device_id, v_record.plan_id, v_next_date, 'PENDING', 
                (SELECT type FROM maintenance_plans WHERE id = v_record.plan_id),
                (SELECT name FROM maintenance_plans WHERE id = v_record.plan_id))
        ON CONFLICT DO NOTHING;
        
        RETURN QUERY SELECT v_record.device_id, v_record.plan_id, v_next_date, 
            'Mantenimiento programado para ' || to_char(v_next_date, 'DD/MM/YYYY');
    END LOOP;
    
    -- Actualizar estados PENDING -> DUE_SOON / OVERDUE
    UPDATE maintenance
    SET status = CASE 
        WHEN due_date < CURRENT_DATE THEN 'OVERDUE'
        WHEN due_date <= CURRENT_DATE + INTERVAL '7 days' THEN 'DUE_SOON'
        ELSE 'PENDING'
    END
    WHERE status IN ('PENDING', 'DUE_SOON', 'OVERDUE');
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 10. VISTA: EQUIPOS CON MANTENIMIENTO PENDIENTE
-- ===========================================================================
CREATE OR REPLACE VIEW v_devices_maintenance_due AS
SELECT 
    m.id AS maintenance_id,
    d.id AS device_id,
    d.serial_number,
    d.qr_code,
    mo.model AS model_name,
    mo.sku,
    mp.name AS plan_name,
    mp.type AS plan_type,
    m.scheduled_for,
    m.due_date,
    m.status,
    l.store_name AS location_name,
    l.client_id,
    CASE 
        WHEN m.due_date < CURRENT_DATE THEN CURRENT_DATE - m.due_date
        ELSE 0
    END AS days_overdue,
    m.result,
    m.next_scheduled_for
FROM maintenance m
JOIN devices d ON d.id = m.device_id
JOIN models mo ON mo.id = d.model_id
JOIN maintenance_plans mp ON mp.id = m.plan_id
JOIN locations l ON l.id = d.current_location_id
WHERE m.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')
ORDER BY m.due_date ASC;

-- ===========================================================================
-- 11. VISTA: STOCK BAJO (para alertas de reposición)
-- ===========================================================================
CREATE OR REPLACE VIEW v_low_stock AS
SELECT 
    sl.model_id,
    mo.sku,
    mo.model AS model_name,
    mo.brand,
    mo.model,
    mo.min_stock,
    mo.max_stock,
    mo.reorder_multiple,
    mo.lead_time_days,
    sl.location_id,
    loc.store_name AS location_name,
    sl.available_count,
    sl.available_count <= mo.min_stock AS is_below_min,
    GREATEST(mo.min_stock - sl.available_count, 0) AS deficit,
    -- Cantidad sugerida para PO: llevar a max_stock (redondeado a múltiplo)
    CASE 
        WHEN mo.max_stock > sl.available_count 
        THEN CEIL((mo.max_stock - sl.available_count) / mo.reorder_multiple::NUMERIC) * mo.reorder_multiple
        ELSE 0
    END AS suggested_reorder_qty
FROM stock_levels sl
JOIN models mo ON mo.id = sl.model_id AND mo.is_active
JOIN locations loc ON loc.id = sl.location_id
WHERE sl.available_count <= mo.min_stock
   OR (mo.max_stock > 0 AND sl.available_count >= mo.max_stock)
ORDER BY sl.available_count ASC;

-- ===========================================================================
-- 12. VISTA: STOCK COMPLETO (para dashboard de inventario)
-- ===========================================================================
CREATE OR REPLACE VIEW v_stock_summary AS
SELECT 
    mo.id AS model_id,
    mo.sku,
    mo.model AS model_name,
    mo.brand,
    mo.model,
    c.name AS category_name,
    c.code AS category_code,
    mo.min_stock,
    mo.max_stock,
    mo.unit_cost,
    sl.location_id,
    loc.store_name AS location_name,
    sl.available_count,
    sl.in_transit_count,
    sl.installed_count,
    sl.in_repair_count,
    (sl.available_count + sl.in_transit_count + sl.installed_count + sl.in_repair_count) AS total_units,
    sl.total_value,
    CASE 
        WHEN sl.available_count <= mo.min_stock THEN 'LOW'
        WHEN mo.max_stock > 0 AND sl.available_count >= mo.max_stock THEN 'HIGH'
        ELSE 'OK'
    END AS stock_status
FROM stock_levels sl
JOIN models mo ON mo.id = sl.model_id AND mo.is_active
JOIN categories c ON c.id = mo.category_id
JOIN locations loc ON loc.id = sl.location_id
ORDER BY mo.brand, mo.model, loc.store_name;


-- ============================================================================
-- 0012 — Compras: purchase_orders + purchase_order_lines (replicada aca)
-- ============================================================================
-- Mismo motivo que 0011: sin esta seccion, una base nueva no tenia
-- `vendors`, `purchase_orders` ni `purchase_order_lines`, y toda la pestana
-- de Compras devolvia un 500.
--
-- CONTENIDO DE 0012_purchasing.sql
-- ============================================================================

CREATE TABLE vendors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    code VARCHAR(50) UNIQUE,
    email VARCHAR(200),
    phone VARCHAR(50),
    address TEXT,
    contact_person VARCHAR(100),
    payment_terms VARCHAR(100),        -- ej: '30 días', 'contado'
    currency VARCHAR(3) DEFAULT 'USD',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_vendors_active ON vendors(is_active);

-- Seed proveedor por defecto
INSERT INTO vendors (name, code, email, currency, is_active)
VALUES ('Proveedor Genérico', 'GEN', 'compras@ejemplo.com', 'USD', TRUE)
ON CONFLICT (code) DO NOTHING;

-- ===========================================================================
-- 2. Órdenes de Compra (purchase_orders)
-- ===========================================================================
CREATE TABLE purchase_orders (
    id SERIAL PRIMARY KEY,
    vendor_id INT NOT NULL REFERENCES vendors(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'BORRADOR',  -- BORRADOR, ENVIADA, CONFIRMADA, RECIBIDA, CANCELADA
    expected_date DATE,
    observations TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    received_at TIMESTAMPTZ
);

CREATE TRIGGER trigger_purchase_orders_updated_at
    BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX idx_po_status ON purchase_orders(status);
CREATE INDEX idx_po_expected ON purchase_orders(expected_date);

-- ===========================================================================
-- 3. Líneas de Orden de Compra (purchase_order_lines)
-- ===========================================================================
CREATE TABLE purchase_order_lines (
    id SERIAL PRIMARY KEY,
    po_id INT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    model_id INT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    cantidad INT NOT NULL CHECK (cantidad > 0),
    cantidad_recibida INT NOT NULL DEFAULT 0 CHECK (cantidad_recibida >= 0),
    unit_cost NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT chk_received_not_exceed CHECK (cantidad_recibida <= cantidad)
);

CREATE TRIGGER trigger_purchase_order_lines_updated_at
    BEFORE UPDATE ON purchase_order_lines
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_pol_po ON purchase_order_lines(po_id);
CREATE INDEX idx_pol_model ON purchase_order_lines(model_id);

-- ===========================================================================
-- 4. Función para actualizar status de PO al recibir
-- ===========================================================================
CREATE OR REPLACE FUNCTION update_po_status_on_receive()
RETURNS TRIGGER AS $$
BEGIN
    -- Verificar si todas las líneas están completamente recibidas
    IF NOT EXISTS (
        SELECT 1 FROM purchase_order_lines
        WHERE po_id = NEW.po_id AND cantidad_recibida < cantidad
    ) THEN
        UPDATE purchase_orders
        SET status = 'RECIBIDA', received_at = NOW(), updated_at = NOW()
        WHERE id = NEW.po_id;
    ELSE
        UPDATE purchase_orders
        SET status = 'PARCIAL', updated_at = NOW()
        WHERE id = NEW.po_id AND status NOT IN ('RECIBIDA', 'CANCELADA');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_po_status_on_receive
    AFTER UPDATE ON purchase_order_lines
    FOR EACH ROW
    WHEN (OLD.cantidad_recibida IS DISTINCT FROM NEW.cantidad_recibida)
    EXECUTE FUNCTION update_po_status_on_receive();

-- ===========================================================================
-- 5. Índices adicionales
-- ===========================================================================
CREATE INDEX idx_purchase_orders_created ON purchase_orders(created_at DESC);
CREATE INDEX idx_purchase_orders_vendor_status ON purchase_orders(vendor_id, status);

-- ============================================================================
-- TRIGGERS DE AUDITORIA (migracion 0013, replicada aca)
-- ============================================================================
--
-- POR QUE ESTAN AL FINAL Y NO AL LADO DE `audit_logs`
-- -----------------------------------------------------
-- Un trigger necesita que la tabla que audita ya exista. `audit_logs` se crea
-- al principio del script, pero `devices`, `models`, `vendors` y
-- `purchase_orders` se crean mucho despues: poner los triggers aca daba
-- "relation does not exist" y mataba el init.sql entero.
--
-- POR QUE ESTAN ACAS Y NO SOLO EN 0013
-- -----------------------------------
-- Por la misma razon que `sessions`: el compose monta UN SOLO archivo en
-- `/docker-entrypoint-initdb.d/`, y los archivos de `migrations/` no corren en
-- una instalacion nueva. Sin esto, una base creada desde cero quedaba SIN
-- auditar: el cambio se hacia pero nadie dejaba rastro.
--
-- POR QUE LA FUNCION ES GENERICA
-- -------------------------------
-- La misma funcion audita las nueve tablas, y no pueden ser la misma: siete
-- tienen id entero (devices, models, clients, vendors, categories,
-- locations, purchase_orders) y dos lo tienen uuid (roles, users). Por eso
-- `record_id` es TEXT y no UUID: guarda '42' y el texto del uuid, y
-- `table_name` dice de que tabla es cada fila.
--
-- `v_user_id` sale de `deleted_by` / `updated_by` / `created_by`, columnas que
-- no todas las tablas tienen. El bloque con EXCEPTION convierte ese error en
-- NULL en vez de tumbar la operacion.
CREATE OR REPLACE FUNCTION audit_soft_delete()
RETURNS TRIGGER AS $$
DECLARE
    v_record_id TEXT;
    v_user_id UUID;
BEGIN
    -- Determinar record_id
    IF TG_OP = 'DELETE' THEN
        BEGIN
            v_record_id := OLD.id::text;
        EXCEPTION WHEN OTHERS THEN
            v_record_id := NULL;
        END;
    ELSE
        BEGIN
            v_record_id := NEW.id::text;
        EXCEPTION WHEN OTHERS THEN
            v_record_id := NULL;
        END;
    END IF;

    -- Determinar user_id si existe
    IF TG_OP != 'DELETE' THEN
        BEGIN
            IF NEW.deleted_by IS NOT NULL THEN
                v_user_id := NEW.deleted_by::uuid;
            ELSIF NEW.updated_by IS NOT NULL THEN
                v_user_id := NEW.updated_by::uuid;
            ELSIF NEW.created_by IS NOT NULL THEN
                v_user_id := NEW.created_by::uuid;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_user_id := NULL;
        END;
    END IF;

    IF (TG_OP = 'UPDATE') THEN
        IF (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL) THEN
            -- Soft delete
            INSERT INTO audit_logs (action, table_name, record_id, old_value, new_value, user_id, created_at)
            VALUES (
                'DELETE',
                TG_TABLE_NAME,
                v_record_id,
                json_build_object('deleted_at', OLD.deleted_at),
                json_build_object('deleted_at', NEW.deleted_at, 'action', 'SOFT_DELETE'),
                v_user_id,
                NOW()
            );
            RETURN NEW;
        END IF;
        -- Actualización
        INSERT INTO audit_logs (action, table_name, record_id, old_value, new_value, user_id, created_at)
        VALUES (
            'UPDATE',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(OLD),
            row_to_json(NEW),
            v_user_id,
            NOW()
        );
        RETURN NEW;
    ELSIF (TG_OP = 'INSERT') THEN
        INSERT INTO audit_logs (action, table_name, record_id, new_value, user_id, created_at)
        VALUES (
            'INSERT',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(NEW),
            v_user_id,
            NOW()
        );
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        -- Hard delete - loguear
        INSERT INTO audit_logs (action, table_name, record_id, old_value, created_at)
        VALUES (
            'DELETE',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(OLD),
            NOW()
        );
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;


CREATE TRIGGER audit_devices
    AFTER INSERT OR DELETE OR UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_roles
    AFTER INSERT OR DELETE OR UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_models
    AFTER INSERT OR DELETE OR UPDATE ON models
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_clients
    AFTER INSERT OR DELETE OR UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_vendors
    AFTER INSERT OR DELETE OR UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_categories
    AFTER INSERT OR DELETE OR UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_locations
    AFTER INSERT OR DELETE OR UPDATE ON locations
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_purchase_orders
    AFTER INSERT OR DELETE OR UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE TRIGGER audit_users
    AFTER INSERT OR DELETE OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();
