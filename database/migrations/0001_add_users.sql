-- ===========================================================================
-- 0001 — Roles, usuarios y bitacora de auditoria
-- ===========================================================================
--
-- Para que sirve esta migracion
-- -----------------------------
-- `init.sql` ya trae estas tablas. Esta migracion existe para la base que se
-- creo ANTES de que existieran: es la unica razon de ser de todo el mecanismo
-- de migraciones de este repo.
--
--   Instalacion nueva  ->  `make up`.    Correr esto seria redundante.
--   Base ya existente   ->  `make migrar`. Esto.
--
-- QUE HACE, EN ORDEN
-- -------------------
--   1. pgcrypto, para bcrypt y uuid.
--   2. La funcion de auditoria.
--   3. roles y users, con sus triggers.
--   4. audit_logs. SIN foreign key a proposito: es el unico registro que tiene
--      que sobrevivir a la baja del usuario.
--   5. Los dos ALTER de las columnas que hoy son enteros sueltos.
--   6. El seed de roles y del admin.
--
-- EL PUNTO 5 ES EL QUE DUELE, Y ES EL IMPORTANTE
-- -----------------------------------------------
-- `movement_history.user_id` y `repairs.technician_id` son `INT NOT NULL` sin
-- foreign key. Para que referencien `users.id` (UUID) hay que cambiar el tipo,
-- y Postgres NO lo hace solo: `ALTER COLUMN ... TYPE uuid` sobre una columna
-- con datos falla con "default for column cannot be cast automatically".
--
-- Acapanar con `USING` sobre una base VACIA es trivial. Sobre una base con
-- movimientos cargados, los enteros viejos son ids que no existen en `users`:
-- no hay a que traduciarlos. La migracion NO los borra sola — seria perder
-- trazabilidad sin avisar — y en cambio falla con un mensaje explicito y el
-- nombre de la columna, para que la decision sea del operador.
--
-- QUE HACER SI FALLA
-- ------------------
--   a) La base tiene datos de prueba: `make clean && make up`. Lo mas simple.
--   b) La base tiene datos que importan: revisar el SELECT que imprime el
--      error y decidir fila por fila. Ver el bloque de abajo.


-- 1. pgcrypto: crypt()/gen_salt() para bcrypt y gen_random_uuid().
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- 2. La funcion de auditoria. Idéntica a la de init.sql.
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


-- 3. roles. Sin DEFAULT de uuid en una migracion que corre sobre una base
--    vieja: se genera aca para que el INSERT de abajo sea explicito y el
--    operador vea de donde sale cada id.
CREATE TABLE IF NOT EXISTS roles (
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

DROP TRIGGER IF EXISTS trigger_roles_audit ON roles;
CREATE TRIGGER trigger_roles_audit
BEFORE INSERT OR UPDATE ON roles
FOR EACH ROW EXECUTE FUNCTION set_audit_role_id();


CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password TEXT NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    address VARCHAR(255) NOT NULL DEFAULT 'N/A',
    phone VARCHAR(255) NOT NULL,
    phone2 VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_specialist BOOLEAN DEFAULT FALSE,
    role_id UUID NOT NULL REFERENCES roles(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_by_user_id UUID REFERENCES users(id),
    updated_by_user_id UUID REFERENCES users(id),
    deleted_by_user_id UUID REFERENCES users(id),
    created_by_role_id UUID REFERENCES roles(id),
    updated_by_role_id UUID REFERENCES roles(id),
    deleted_by_role_id UUID REFERENCES roles(id)
);

DROP TRIGGER IF EXISTS trigger_users_audit ON users;
CREATE TRIGGER trigger_users_audit
BEFORE INSERT OR UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_audit_role_id();


-- 4. audit_logs
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'audit_action') THEN
        CREATE TYPE audit_action AS ENUM ('INSERT', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS audit_logs (
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

CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_record ON audit_logs(table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);


-- 5. LA PARTE DIFICIL:_INT -> UUID en las columnas de trazabilidad.
--
-- Se hace en dos pasos a proposito. Primero se verifica que las columnas esten
-- vacias; recien despues se cambia el tipo. Al reves, un fallo del ALTER
-- dejaria la tabla a medio camino.
DO $$
DECLARE
    v_con_movimientos INT;
    v_con_reparaciones INT;
BEGIN
    SELECT count(*) INTO v_con_movimientos FROM movement_history;
    SELECT count(*) INTO v_con_reparaciones FROM repairs;

    RAISE NOTICE 'movement_history: % filas | repairs: % filas',
        v_con_movimientos, v_con_reparaciones;

    IF v_con_movimientos = 0 THEN
        ALTER TABLE movement_history ALTER COLUMN user_id TYPE UUID USING
            NULLIF(user_id::text, '')::uuid;
        ALTER TABLE movement_history ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE movement_history ADD CONSTRAINT fk_movement_history_user
            FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;
        RAISE NOTICE 'movement_history.user_id -> UUID + FK (valida)';
    ELSE
        -- Se ABORTA, no se borra. Perder los movimientos sin avisar para que
        -- el script "pase" es peor que este error.
        RAISE EXCEPTION USING
            MESSAGE = format(
                'movement_history tiene % filas con user_id INT, que no se puede convertir a UUID automaticamente.',
                v_con_movimientos),
            HINT = 'Estos user_id son ids de un esquema anterior. Para convertir la columna hay que decidir que usuario corresponde a cada fila: exportalos, creá los usuarios, y actualizá la columna. Si son datos de prueba: make clean && make up.',
            DETAIL = format('SELECT id, device_id, movement_type, user_id FROM movement_history ORDER BY id;');
    END IF;

    IF v_con_reparaciones = 0 THEN
        ALTER TABLE repairs ALTER COLUMN technician_id TYPE UUID USING
            NULLIF(technician_id::text, '')::uuid;
        ALTER TABLE repairs ALTER COLUMN technician_id SET NOT NULL;
        ALTER TABLE repairs ADD CONSTRAINT fk_repairs_technician
            FOREIGN KEY (technician_id) REFERENCES users(id) NOT VALID;
        RAISE NOTICE 'repairs.technician_id -> UUID + FK (valida)';
    ELSE
        RAISE EXCEPTION USING
            MESSAGE = format(
                'repairs tiene % filas con technician_id INT, que no se puede convertir a UUID automaticamente.',
                v_con_reparaciones),
            HINT = 'Mismo criterio que movement_history: decidir la correspondencia fila por fila. Si son datos de prueba: make clean && make up.',
            DETAIL = format('SELECT id, device_id, technician_id FROM repairs ORDER BY id;');
    END IF;

    -- NOT VALID deja la FK creada pero sin verificar las filas existentes, que
    -- es lo que permite crearla sin bloquear la tabla. VALIDATE la comprueba
    -- en background; con la columna vacia, un solo ANALYZE alcanza.
    ALTER TABLE movement_history VALIDATE CONSTRAINT fk_movement_history_user;
    ALTER TABLE repairs VALIDATE CONSTRAINT fk_repairs_technician;
END $$;


-- 6. Seed. Idempotente: se puede correr dos veces sin duplicar.
INSERT INTO roles (name) VALUES ('ADMIN')       ON CONFLICT (name) DO NOTHING;
INSERT INTO roles (name) VALUES ('TECHNICIAN')  ON CONFLICT (name) DO NOTHING;
INSERT INTO roles (name) VALUES ('OPERATOR')    ON CONFLICT (name) DO NOTHING;

DO $$
DECLARE
    v_admin_role UUID;
    v_admin_id   UUID;
BEGIN
    SELECT id INTO v_admin_role FROM roles WHERE name = 'ADMIN';
    SELECT id INTO v_admin_id FROM users WHERE email = 'admin@smartinventory.local';

    IF v_admin_id IS NULL THEN
        INSERT INTO users (email, password, full_name, phone, phone2, role_id, is_specialist)
        VALUES ('admin@smartinventory.local', crypt('admin123', gen_salt('bf', 10)),
                'Administrador', '+54 11 0000-0000', '', v_admin_role, TRUE)
        RETURNING id INTO v_admin_id;
        RAISE NOTICE 'Usuario admin creado';
    ELSE
        RAISE NOTICE 'Usuario admin ya existia (id=%), no se toco su password', v_admin_id;
    END IF;
END $$;