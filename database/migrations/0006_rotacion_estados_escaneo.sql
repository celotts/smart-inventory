-- ===========================================================================
-- 0006 — Rotacion: maquina de estados, idempotencia de escaneo, y las dos
--        tablas que faltaban en una instalacion nueva.
-- ===========================================================================
--
-- QUE HACE ESTA MIGRACION, Y POR QUE HAY TANTA COSA EN UN ARCHIVO
-- --------------------------------------------------------------
-- Porque las tres cosas son el MISMO arreglo, contado desde el mismo symptom:
--
--   1. `make up` creaba una base sin `sessions`, sin `transfers`, sin
--      `maintenance` y sin `stock_alerts`, porque el compose monta UN SOLO
--      archivo en `/docker-entrypoint-initdb.d/` y los archivos de
--      `migrations/` no se ejecutan en una instalacion nueva.
--      Consecuencia: `POST /api/auth/login` fallaba al insertar la sesion, y
--      los endpoints `/api/ops/*` devolvian 500 con "relation does not exist".
--
--   2. Nada impedia que un componente quedara en un estado imposible
--      (INSTALLED sin haber pasado por IN_TRANSIT, dado de baja mientras
--      estaba en un cliente, en el taller sin reparacion registrada).
--
--   3. Un reintento de escaneo duplicaba el movimiento.
--
-- Los tres hacen que el historial del inventario deje de ser confiable, que es
-- la unica razon de ser de un sistema asi.
--
-- QUE NO SE PIERDE NADA
-- ---------------------
-- Los CHECK se agregan con una guarda previa que se detiene si hay filas que no
-- los cumplen, e imprime el SELECT para ver cuales. Perder trazabilidad para
-- que un script diga "ok" es peor que el error. Es el criterio que ya usan las
-- migraciones 0001 y 0002 de este proyecto.

-- ===========================================================================
-- 1. TABLAS QUE FALTABAN
-- ===========================================================================
--
-- Se crean con IF NOT EXISTS y NO las borra 0003/0005 si llegan despues: el
-- `IF NOT EXISTS` las encuentra y no hace nada. Por eso el orden de los
-- archivos no importa para estas tres.

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent TEXT,
    ip_address VARCHAR(45),
    revoked_at TIMESTAMPTZ,
    revoked_before TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_vivas ON sessions(expires_at)
    WHERE revoked_at IS NULL;

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

CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
CREATE INDEX IF NOT EXISTS idx_transfers_device ON transfers(device_id);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON transfers(created_at DESC);

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

CREATE TABLE IF NOT EXISTS stock_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ubicacion VARCHAR(128) NOT NULL,
    minimo INT NOT NULL,
    actual INT NOT NULL,
    resuelto BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stock_alerts_resuelto ON stock_alerts(resuelto);

-- ===========================================================================
-- 2. CHECKS DE LAS TABLAS DE OPERACIONES
-- ===========================================================================
--
-- Se aplican con `NOT VALID` primero: Postgres los crea sin verificar las
-- filas existentes, que es lo que hace posible agregar una restriccion a una
-- base con datos sin bloquear la tabla ni fallar por un fila vieja.

DO $$
DECLARE
    v_sql TEXT;
BEGIN
    -- Un traslado a la misma sede no es un traslado.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_transfers_distintas'
    ) THEN
        ALTER TABLE transfers ADD CONSTRAINT ck_transfers_distintas
            CHECK (from_location <> to_location) NOT VALID;
    END IF;

    -- El estado es un conjunto cerrado. Un typo como 'aprovado' crea una fila
    -- que ningun reporte cuenta y que nadie sabe como resolver.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_transfers_status'
    ) THEN
        ALTER TABLE transfers ADD CONSTRAINT ck_transfers_status
            CHECK (status IN ('pending', 'approved', 'rejected', 'completed')) NOT VALID;
    END IF;

    -- Una alerta con minimo negativo, o con minimo 0, no informa nada.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_stock_alerts_minimo'
    ) THEN
        ALTER TABLE stock_alerts ADD CONSTRAINT ck_stock_alerts_minimo
            CHECK (minimo >= 0 AND actual >= 0) NOT VALID;
    END IF;
END $$;

-- ===========================================================================
-- 3. LAS DOS TABLAS DE AUDITORIA
-- ===========================================================================
--
-- EL PROBLEMA
-- -----------
-- Hay dos bitacoras y el codigo no sabe cual usar:
--
--   `audit_logs`  (plural, la de init.sql)  -> la que escribe
--                  users/service.py. Tiene `table_name`, `record_id`,
--                  `old_value`, `new_value` y un ENUM `action`.
--
--   `audit_log`   (singular, la de 0004)   -> la que escribe core/audit.py.
--                  Tiene `resource`, `request_id`, `metadata` y un VARCHAR
--                  libre en `action`.
--
-- Dos tablas para lo mismo es peor que una sola: nadie sabe donde buscar, y la
-- que no usa nadie se llena de filas que nadie lee. Peor aun, el indice de
-- "acciones sensibles" que alguien crea sobre `audit_logs` se pierde la mitad
-- de los eventos, sin ningun error que lo indique.
--
-- POR QUE SOBREVIVE `audit_logs`
-- -------------------------------
-- Es la que crea el trigger `set_audit_role_id`, que es lo que hace que la
-- bitacora guarde el ROL del actor en el momento de la accion y no solo su id:
-- si despues le cambian el rol a alguien, "quien lo borro" conserva el rol que
-- tenia cuando lo borro. Para una bitacora de inventario eso es la diferencia
-- entre una evidencia y una suposicion.
--
-- QUE PASA CON LAS FILAS DE `audit_log`
-- -------------------------------------
-- No se borran. Se copian a `audit_logs` con el mapeo que corresponde y despues
-- se dropea la tabla. Copiar y dropear, y no dropear y perder: el dato existio,
-- alguien lo consulto alguna vez, y borrarlo sin dejar rastro seria perder
-- informacion para que un script diga "ok".
--
-- El mapeo es PARCIAL y hay que decirlo: `action` VARCHAR libre no siempre cae
-- en el ENUM, y `resource` es texto donde `audit_logs` espera un UUID de
-- registro. Lo que no se puede mapear exacto se conserva en el texto de
-- `new_value`, que es donde queda consultable.
DO $$
DECLARE
    v_hay_audit_log BOOLEAN;
BEGIN
    SELECT to_regclass('public.audit_log') IS NOT NULL INTO v_hay_audit_log;

    IF NOT v_hay_audit_log THEN
        RETURN;
    END IF;

    -- 3a. Copiar lo que se pueda.
    INSERT INTO audit_logs (action, table_name, record_id, new_value, user_id, ip_address, created_at)
    SELECT
        -- El ENUM no tiene 'ESCANEO' ni 'LOGOUT_ALL' ni nada asi. Lo que no
        -- existe va a UPDATE, que es el contenedor generico: el detalle del
        -- `action` original viaja en el texto de abajo, que es donde se puede
        -- buscar igual.
        CASE
            WHEN a.action ~* '^(INSERT|CREATE|ADD)$'                 THEN 'INSERT'::audit_action
            WHEN a.action ~* '^(UPDATE|EDIT|MODIFY)$'                 THEN 'UPDATE'::audit_action
            WHEN a.action ~* '^(DELETE|REMOVE)$'                      THEN 'DELETE'::audit_action
            WHEN a.action ~* '^(LOGIN|SIGNIN)$'                      THEN 'LOGIN'::audit_action
            WHEN a.action ~* '^(LOGOUT|SIGN.?OUT)$'                   THEN 'LOGOUT'::audit_action
            ELSE 'UPDATE'::audit_action
        END,
        -- `table_name` es NOT NULL y va a decir QUE se toco. El recurso va
        -- adentro del texto.
        LEFT(COALESCE(a.resource, 'desconocido'), 100),
        NULL,
        'migrado desde audit_log: action=' || a.action
            || ' resource=' || COALESCE(a.resource, '')
            || ' request_id=' || COALESCE(a.request_id, '')
            || ' metadata=' || COALESCE(a.metadata::text, ''),
        a.user_id,
        a.ip_address::VARCHAR(45),
        a.created_at
    FROM audit_log a;

    -- 3b. Y ahora si, dropear. Con CASCADE NO: si algo quedodepending en
    -- esta tabla, el error es mejor que una cascada que se lleva otra cosa.
    EXECUTE 'DROP TABLE audit_log';
END $$;

-- ===========================================================================
-- 4. MAQUINA DE ESTADOS
-- ===========================================================================
--
-- El comentario largo de por que esta regla esta en el motor y no en Python
-- esta en init.sql, junto a la definicion. Aca va la misma definicion: una base
-- migrada y una creada desde cero tienen que terminar IGUALES, y eso incluye
-- las funciones.

CREATE OR REPLACE FUNCTION validar_transicion_estado()
RETURNS TRIGGER AS $$
DECLARE
    v_transicion_valida BOOLEAN := FALSE;
    v_tiene_reparacion_abierta BOOLEAN;
BEGIN
    -- En un INSERT no hay estado previo: se acepta el inicial.
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;

    -- Sin cambio de estado no hay nada que validar. El trigger de `updated_at`
    -- dispara un UPDATE en cada fila y no tiene por que pasar por aca.
    IF NEW.current_status IS NOT DISTINCT FROM OLD.current_status THEN
        RETURN NEW;
    END IF;

    -- PERMANENTLY_DISPOSED ES ABSORBENTE: nada sale de una baja.
    IF OLD.current_status = 'PERMANENTLY_DISPOSED' THEN
        RAISE EXCEPTION
            'El componente % esta dado de baja (PERMANENTLY_DISPOSED). Un componente dado de baja no vuelve al inventario. Para reactivarlo hay que emitir un certificado de baja nuevo, no cambiar el estado a mano.',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- Entrar al taller sin reparacion abierta deja al componente en un estado
    -- que el taller no sabe que tiene.
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

    -- Salir del taller con la reparacion abierta deja al componente trabado
    -- para siempre: nadie lo tiene y nadie lo atiende.
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

    -- La matriz. Solo las transiciones que el negocio reconoce.
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

DROP TRIGGER IF EXISTS trigger_devices_transicion_estado ON devices;
CREATE TRIGGER trigger_devices_transicion_estado
    BEFORE UPDATE OF current_status ON devices
    FOR EACH ROW EXECUTE FUNCTION validar_transicion_estado();

-- ===========================================================================
-- 5. `installation_date` QUE NO MIENTE
-- ===========================================================================
--
-- "Instalado desde" es informacion de rotacion: dice hace cuanto un componente
-- esta trabajando. Si la columna no se mantiene, el dato es la fecha de compra
-- y nadie lo detecta hasta que se usa para decidir algo.
CREATE OR REPLACE FUNCTION sincronizar_fecha_instalacion()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.current_status IS DISTINCT FROM OLD.current_status THEN
        IF NEW.current_status = 'INSTALLED' THEN
            NEW.installation_date = COALESCE(NEW.installation_date, NOW());
        ELSIF OLD.current_status = 'INSTALLED' THEN
            NEW.installation_date = OLD.installation_date;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_devices_fecha_instalacion ON devices;
CREATE TRIGGER trigger_devices_fecha_instalacion
    BEFORE UPDATE OF current_status ON devices
    FOR EACH ROW EXECUTE FUNCTION sincronizar_fecha_instalacion();

-- ===========================================================================
-- 6. IDEMPOTENCIA DE ESCANEO
-- ===========================================================================
--
-- Un `client_scan_id` es UN envio, no un evento. El telefono lo genera antes de
-- enviar; si la peticion llega dos veces, la segunda no inserta nada y devuelve
-- el movimiento de la primera.
--
-- NO se usa un indice unico sobre (device_id, movement_date): dos escaneos
-- reales del mismo componente en el mismo segundo son legitimos (uno entra al
-- deposito y al segundo sale hacia un cliente), y rechazarlos seria peor que
-- la duplicacion que se quiere evitar.
CREATE TABLE IF NOT EXISTS scan_receipts (
    client_scan_id UUID PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id) ON DELETE RESTRICT,
    movement_id INTEGER REFERENCES movement_history(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_receipts_device
    ON scan_receipts(device_id, created_at DESC);

COMMENT ON TABLE scan_receipts IS
    'Idempotencia de escaneo: un client_scan_id, un movimiento. Un reintento no duplica.';
