-- ===========================================================================
-- 0016 - Gestión de incidencias y control de mantenimiento
-- ===========================================================================
-- Objetivo:
--   1. `incidents`: el ticket de una incidencia (un equipo que falla, una
--      falla de red, un cliente sin servicio), con SLA, historial y cierre.
--   2. `incident_events`: la línea de tiempo INMUTABLE de cada incidencia.
--   3. `incident_sla_policies`: los tiempos de respuesta y resolucion por
--      severidad. Son DATO, no código: el SLA cambia con el contrato, y no
--      puede requerir un deploy.
--   4. Vincular el correctivo (`repairs`) y el preventivo (`maintenance`) con
--      la incidencia que los origina.
--   5. Vistas de metricas: SLA en curso y salud de mantenimiento por modelo.
--
-- QUE ES UNA INCIDENCIA Y CUAL ES EL MANTENIMIENTO
-- -------------------------------------------------
-- Son dos caras del mismo problema, y por eso van juntas:
--
--   - INCIDENCIA: el REPORTE. "Este equipo no prende", "el cliente no tiene
--     internet". Tiene severidad, responsable, SLA y un historial. Es la
--     unidad de trabajo del día a día.
--   - MANTENIMIENTO: la EJECUCION técnica. Preventivo (un plan que vence) o
--     correctivo (arreglar lo que reporto una incidencia). Ya existian
--     `maintenance_plans` y `maintenance`; lo que faltaba era el vinculo con
--     la incidencia y las metricas.
--
-- POR QUE UN Código LEGIBLE Y NO UN UUID
-- --------------------------------------
-- `incidents.id` es un UUID (estable, sin filtrar). `incidents.code` es
-- `INC-2026-000001`: es lo que el operador lee por teléfono, lo que va en la
-- orden de trabajo y lo que se busca en el reporte. Un UUID en unticket de
-- soporte es un número que nadie puede dictar por teléfono.
--
-- EL Número ES POR ANIO
-- ---------------------
-- El correlativo se reinicia cada ano: `INC-2026-000001` vuelve a ser el
-- primer caso del 2027. Un número global creciente por año (el 000001 de 2027
-- después del 000900 de 2026) obliga a leer el año para saber la antiguedad.
--
-- POR QUE `client_ref` ES UNIQUE Y NULL
-- ---------------------------------------
-- Es la idempotencia: la app (una PWA, con red mala) manda la misma alta dos
-- veces y el backend tiene que devolver LA MISMA incidencia, no crear dos.
-- `NULL` se puede repetir todas las veces que haga falta: en Postgres,
-- `NULL` nunca choca con `NULL` en un índice único.
-- ===========================================================================

-- ===========================================================================
-- 1. TIPOS
-- ===========================================================================
--
-- ENUM y no VARCHAR con CHECK: el ENUM no acepta un valor que no este en la
-- lista, y Postgres lo garantiza sin que ningún endpoint tenga que validar.
-- El orden del ENUM es el de la severidad: `S1` es más grave que `S4`, y un
-- `ORDER BY severity` ordena de más grave a menos.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_severity') THEN
        -- SEVERIDAD: el impacto, no la urgencia. "S1" es el sistema caido.
        CREATE TYPE incident_severity AS ENUM ('S1', 'S2', 'S3', 'S4');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_status') THEN
        -- ESTADO: el ciclo de vida de la incidencia.
        --   OPEN       -> reportada, sin tocar
        --   IN_PROGRESS-> alguien la está trabajando
        --   WAITING    -> esperando una parte (repuesto, aprobacion, el cliente)
        --   RESOLVED   -> el problema está resuelto, falta cerrar
        --   CLOSED     -> cerrada y verificada
        --   CANCELLED  -> no era una incidencia (duplicado, error de uso)
        CREATE TYPE incident_status AS ENUM (
            'OPEN', 'IN_PROGRESS', 'WAITING', 'RESOLVED', 'CLOSED', 'CANCELLED'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'incident_category') THEN
        -- Categoría: que tipo de problema es. Es lo que permite agrupar para
        -- detectar patrones ("siempre falla la misma cosa").
        CREATE TYPE incident_category AS ENUM (
            'HARDWARE', 'SOFTWARE', 'NETWORK', 'PERIPHERAL',
            'PHYSICAL', 'ACCESS', 'OTHER'
        );
    END IF;
END
$$;

-- ===========================================================================
-- 2. Políticas DE SLA
-- ===========================================================================
--
-- El SLA es un COMPROMISO con el cliente, no una constante del código: los
-- tiempos cambian por contrato y por cliente. Por eso es una tabla, con sus
-- valores sembrados.
--
-- `business_hours_only` está preparado para el horario laboral, pero la
-- calculadora de la aplicación Todavía no lo aplica (ver la nota del trigger
-- más abajo): el campo existe para que la decisión quede tomada y no para
-- cambiar el calculo a escondidas.

CREATE TABLE IF NOT EXISTS incident_sla_policies (
    severity incident_severity PRIMARY KEY,
    description TEXT NOT NULL,
    -- Minutos hasta la PRIMERA RESPUESTA (una persona mira el caso).
    response_minutes INT NOT NULL CHECK (response_minutes > 0),
    -- Minutos hasta la RESOLUCION.
    resolution_minutes INT NOT NULL CHECK (resolution_minutes > 0),
    business_hours_only BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO incident_sla_policies (severity, description, response_minutes, resolution_minutes, business_hours_only)
VALUES
    ('S1', 'Critico: servicio detenido o perdida de datos. Sin workaround.',        15,   240, FALSE),
    ('S2', 'Alto: degradacion seria con impacto en el trabajo del cliente.',      60,  1440, FALSE),
    ('S3', 'Medio: un equipo o una funcion con problema, con alternativa.',       480,  4320, FALSE),
    ('S4', 'Bajo: consulta, mejora o pedido de informacion.',                    1440, 20160, FALSE)
ON CONFLICT (severity) DO UPDATE SET
    description = EXCLUDED.description,
    response_minutes = EXCLUDED.response_minutes,
    resolution_minutes = EXCLUDED.resolution_minutes,
    updated_at = NOW();

-- ===========================================================================
-- 3. CONTADOR DE Códigos (correlativo por ano)
-- ===========================================================================
--
-- Una tabla con una fila por ano y un UPSERT atomico. Es el patron clasico
-- del correlativo por ejercicio: el `ON CONFLICT DO UPDATE` con `RETURNING`
-- devuelve el número YA INCREMENTADO, y el UPSERT es atomico, así que dos
-- altas simultaneas no pueden obtener el mismo número.

CREATE TABLE IF NOT EXISTS incident_code_counters (
    year INT PRIMARY KEY,
    last_number INT NOT NULL DEFAULT 0 CHECK (last_number >= 0)
);

CREATE OR REPLACE FUNCTION incident_next_code(p_year INT)
RETURNS VARCHAR(24) AS $$
DECLARE
    v_next INT;
BEGIN
    INSERT INTO incident_code_counters (year, last_number)
    VALUES (p_year, 1)
    ON CONFLICT (year)
    DO UPDATE SET last_number = incident_code_counters.last_number + 1
    RETURNING last_number INTO v_next;

    RETURN 'INC-' || p_year::TEXT || '-' || lpad(v_next::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 4. INCIDENCIAS
-- ===========================================================================

CREATE TABLE IF NOT EXISTS incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- El código legible. UNIQUE: dos incidencias no pueden llamarse igual.
    code VARCHAR(24) NOT NULL UNIQUE,

    -- Idempotencia del alta (la app es una PWA con red mala). NULL se
    -- repite sin problema.
    client_ref VARCHAR(64) UNIQUE,

    title VARCHAR(200) NOT NULL,
    description TEXT,

    category incident_category NOT NULL DEFAULT 'OTHER',
    severity incident_severity NOT NULL DEFAULT 'S3',
    status incident_status NOT NULL DEFAULT 'OPEN',

    -- QUE SE AFECTA. Los tres son opcionales: una incidencia puede ser de un
    -- producto entero (un falla de red que afecta a todos) sin ser de un
    -- equipo en particular.
    device_id INT REFERENCES devices(id) ON DELETE RESTRICT,
    model_id INT REFERENCES models(id) ON DELETE RESTRICT,
    location_id INT REFERENCES locations(id) ON DELETE RESTRICT,

    -- QUIEN. `reported_by` es obligatorio: una incidencia sin origen es
    -- imposible de rastrear. `assigned_to` puede ser NULL (abierta, sin
    -- responsible asignado).
    reported_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_to UUID REFERENCES users(id) ON DELETE RESTRICT,

    -- SLA: los plazos se CALCULAN al crear, desde la política de la
    -- severidad. Se guardan, no se calculan al vuelo: una política que
    -- cambia después no puede reescribir el pasado.
    response_due_at TIMESTAMPTZ NOT NULL,
    resolution_due_at TIMESTAMPTZ NOT NULL,
    first_response_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,

    -- CIERRE
    resolution TEXT,
    root_cause TEXT,
    downtime_minutes INT CHECK (downtime_minutes >= 0),
    cost NUMERIC(12, 2) CHECK (cost >= 0),

    -- BLOQUEO OPTIMISTA
    -- Dos operadores pueden editar la misma incidencia (el del depósito y el
    -- de soporte). Con `versión`, el segundo que guarda recibe un conflicto
    -- explicito en vez de pisar el trabajo del primero.
    version INT NOT NULL DEFAULT 1,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- Un título de una palabra no es un título: es un error de tipeo, y una
-- incidencia sin descripción no se puede trabajar.
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS ck_incidents_title;
ALTER TABLE incidents ADD CONSTRAINT ck_incidents_title
    CHECK (length(btrim(title)) >= 5 AND length(title) <= 200);

-- Cerrar una incidencia sin resolverla es un estado imposible.
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS ck_incidents_cierre;
ALTER TABLE incidents ADD CONSTRAINT ck_incidents_cierre
    CHECK (closed_at IS NULL OR resolved_at IS NOT NULL);

-- Las marcas de tiempo tienen que ir en orden. Sin esto, un `resolved_at`
-- anterior al `created_at` pasa el chequeo y rompe todos los calculos de SLA.
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS ck_incidents_tiempos;
ALTER TABLE incidents ADD CONSTRAINT ck_incidents_tiempos CHECK (
    (first_response_at IS NULL OR first_response_at >= created_at) AND
    (resolved_at IS NULL OR resolved_at >= created_at) AND
    (closed_at IS NULL OR closed_at >= created_at)
);

-- El plazo de resolucion no puede ser anterior al de respuesta: es
-- fisicamente imposible responder después de resolver.
ALTER TABLE incidents DROP CONSTRAINT IF EXISTS ck_incidents_sla;
ALTER TABLE incidents ADD CONSTRAINT ck_incidents_sla
    CHECK (resolution_due_at >= response_due_at);

-- ===========================================================================
-- 5. Línea DE TIEMPO (append-only)
-- ===========================================================================
--
-- TODO lo que pasa en una incidencia queda acá: el alta, cada cambió de
-- estado, las asignaciones, los cambios de severidad y los comentarios.
--
-- UNA TABLA Y NO DOS
-- -----------------
-- Una tabla de comentarios y otra de cambios de estado obligan a unir dos
-- listas ordenadas por tiempo para armar "la historia" de un caso, y las dos
-- se desalinean. Con una sola tabla, la historia es un `ORDER BY created_at`.
--
-- INMUTABLE
-- ---------
-- Un trigger prohibe UPDATE y DELETE. El historial de un caso es la prueba de
-- que paso: si se puede editar, no es una prueba. (En una app PWA, un
-- reintento con el mismo `client_ref` no debe duplicar la línea: por eso el
-- UNIQUE de abajo.)

CREATE TABLE IF NOT EXISTS incident_events (
    id BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,

    -- Que paso: CREATED, STATUS_CHANGED, ASSIGNED, SEVERITY_CHANGED, COMMENT,
    -- LINKED_REPAIR, LINKED_MAINTENANCE.
    event_type VARCHAR(32) NOT NULL,

    from_status incident_status,
    to_status incident_status,

    -- Quien lo hizo. `ON DELETE SET NULL`: si se borra el usuario, el
    -- historial sigue siendo válido (dice "lo hizo alguien que ya no está").
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,

    note TEXT,

    -- Datos extra del evento (cambió de severidad, de responsable, costo).
    payload JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Idempotencia del evento: el mismo evento con la misma clave no se
    -- duplica. Un reintento de la app no puede escribir dos veces lo mismo.
    event_key VARCHAR(80) UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_incident_events_incident
    ON incident_events(incident_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_incident_events_actor
    ON incident_events(actor_id)
    WHERE actor_id IS NOT NULL;

-- El trigger que hace el historial inmutable.
CREATE OR REPLACE FUNCTION incident_event_inmutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'La linea de tiempo de una incidencia es inmutable: no se modifica ni se borra (incidencia %). Para retirar el caso hay que usar el borrado logico (deleted_at).',
        OLD.incident_id
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_incident_event_inmutable ON incident_events;
CREATE TRIGGER trigger_incident_event_inmutable
    BEFORE UPDATE OR DELETE ON incident_events
    FOR EACH ROW EXECUTE FUNCTION incident_event_inmutable();

-- ===========================================================================
-- 6. SLA AL CREAR Y Transiciones VALIDADAS
-- ===========================================================================

-- El SLA se calcula al crear, desde la política de la severidad.
--
-- POR QUE UN TRIGGER Y NO LA Aplicación
-- ------------------------------------
-- Si el calculo estuviera en el código, cada cliente que llega con un SLA
-- distinto necesitaria un deploy, y dos caminos distintos de calcular el
-- mismo plazo (el del trigger y el del reporte) podrian dar números
-- diferentes. Con el trigger, el plazo es un DATO: se guarda, y todos los
-- reportes leen el mismo número.
CREATE OR REPLACE FUNCTION incident_calcula_sla()
RETURNS TRIGGER AS $$
DECLARE
    v_response INT;
    v_resolution INT;
BEGIN
    SELECT response_minutes, resolution_minutes
      INTO v_response, v_resolution
      FROM incident_sla_policies
     WHERE severity = NEW.severity AND is_active = TRUE;

    IF v_response IS NULL THEN
        -- Sin política para esa severidad, el alta tiene que fallar: es
        -- preferible un error claro a una incidencia sin plazo de respuesta.
        RAISE EXCEPTION
            'No hay politica de SLA activa para la severidad %', NEW.severity
            USING ERRCODE = 'check_violation';
    END IF;

    NEW.response_due_at := NEW.created_at + make_interval(mins => v_response);
    NEW.resolution_due_at := NEW.created_at + make_interval(mins => v_resolution);
    NEW.code := COALESCE(NEW.code, incident_next_code(EXTRACT(YEAR FROM NEW.created_at)::INT));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_incident_calcula_sla ON incidents;
CREATE TRIGGER trigger_incident_calcula_sla
    BEFORE INSERT ON incidents
    FOR EACH ROW EXECUTE FUNCTION incident_calcula_sla();

-- La maquina de estados de la incidencia.
--
-- LAS Transiciones PERMITIDAS
-- ---------------------------
--   OPEN        -> IN_PROGRESS | WAITING | CANCELLED
--   IN_PROGRESS -> WAITING | RESOLVED | CANCELLED
--   WAITING     -> IN_PROGRESS | RESOLVED | CANCELLED
--   RESOLVED    -> CLOSED | IN_PROGRESS      (reabrir: el problema volvio)
--   CLOSED      -> (terminal)
--   CANCELLED   -> (terminal)
--
-- POR QUE EN LA BASE Y NO EN EL Código
-- -------------------------------------
-- La maquina de estados de los movimientos ya vive en un trigger de Postgres,
-- y la razón es la misma: si la validación está en el código, el que
-- escribe en la base (un script, una migración, un `psql`) la esquiva. Con
-- el trigger, una transición imposible es un error de la BASE.
--
-- Además, EL TRIGGER ESCRIBE EL EVENTO
-- ------------------------------------
-- El cambió de estado y la línea de tiempo se escriben en la misma
-- transaccion. Si fueran dos operaciones, un error entre ellas dejaria una
-- incidencia cambiada de estado SIN su evento en el historial: el histórico
-- mentiria, que es lo único que no puede pasar.
CREATE OR REPLACE FUNCTION incident_transicion_valida()
RETURNS TRIGGER AS $$
DECLARE
    v_permitida BOOLEAN := FALSE;
    v_actor UUID;
    v_nota TEXT;
BEGIN
    -- QUIEN Y POR QUE
    --
    -- El trigger no sabe quien está operando: lo lee del CONTEXTO de la
    -- transaccion, que la API setea con `SET LOCAL app.actor_id = ...`.
    --
    -- Por que no desde la app: la columna `actor_id` lo podria mandar
    -- cualquiera, y un historial de un caso de soporte es una prueba: tiene
    -- que decir QUIEN lo toco, no lo que el cliente dijo que lo toco. El
    -- contexto de la transaccion lo setea el servidor, no el cliente.
    --
    -- `current_setting(..., true)` con el `true` devuelve NULL si no está
    -- seteado, en vez de tirar el error " unrecognized configuration parameter".
    v_actor := NULLIF(current_setting('app.actor_id', true), '')::UUID;
    v_nota := NULLIF(current_setting('app.actor_note', true), '');

    IF NEW.status = OLD.status THEN
        -- Mismo estado: no es una transición. Se deja pasar (por ejemplo, un
        -- guardado que no cambia el estado) y no se escribe evento.
        NEW.updated_at := NOW();
        RETURN NEW;
    END IF;

    v_permitida := CASE OLD.status
        WHEN 'OPEN'        THEN NEW.status IN ('IN_PROGRESS', 'WAITING', 'CANCELLED')
        WHEN 'IN_PROGRESS' THEN NEW.status IN ('WAITING', 'RESOLVED', 'CANCELLED')
        WHEN 'WAITING'     THEN NEW.status IN ('IN_PROGRESS', 'RESOLVED', 'CANCELLED')
        WHEN 'RESOLVED'    THEN NEW.status IN ('CLOSED', 'IN_PROGRESS')
        ELSE FALSE
    END;

    IF NOT v_permitida THEN
        RAISE EXCEPTION
            'Transicion de incidencia no valida: % -> %',
            OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;

    -- Las marcas de tiempo las pone la base, no el cliente: un `resolved_at`
    -- que manda el navegador puede estar en cualquier zona horaria.
    IF NEW.status = 'RESOLVED' AND NEW.resolved_at IS NULL THEN
        NEW.resolved_at := NOW();
    END IF;
    IF NEW.status = 'CLOSED' AND NEW.closed_at IS NULL THEN
        NEW.closed_at := NOW();
    END IF;
    IF NEW.status <> 'RESOLVED' AND NEW.status <> 'CLOSED' THEN
        -- Se reabre: el caso vuelve a estar vivo y pierde su cierre.
        NEW.resolved_at := NULL;
        NEW.closed_at := NULL;
    END IF;

    -- La primera respuesta: la primera vez que alguien toca el caso.
    IF NEW.first_response_at IS NULL AND NEW.status <> 'OPEN' THEN
        NEW.first_response_at := NOW();
    END IF;

    NEW.updated_at := NOW();

    -- El evento, en la misma transaccion.
    INSERT INTO incident_events (
        incident_id, event_type, from_status, to_status, actor_id, note
    ) VALUES (
        NEW.id, 'STATUS_CHANGED', OLD.status, NEW.status, v_actor, v_nota
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_incident_transicion ON incidents;
CREATE TRIGGER trigger_incident_transicion
    BEFORE UPDATE OF status ON incidents
    FOR EACH ROW EXECUTE FUNCTION incident_transicion_valida();

-- `updated_at` automático.
DROP TRIGGER IF EXISTS trigger_incidents_updated_at ON incidents;
CREATE TRIGGER trigger_incidents_updated_at
    BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Auditoría de la tabla (el mismo disparador que el resto del sistema).
DROP TRIGGER IF EXISTS audit_incidents ON incidents;
CREATE TRIGGER audit_incidents
    AFTER INSERT OR DELETE OR UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

-- ===========================================================================
-- 7. Índices
-- ===========================================================================
--
-- Los índices que importan son los de las consultas que se hacen TODAS LAS
-- VECES: las abiertas, las vencidas, las asignadas a mi, y las del equipo.

-- Las abiertas, ordenadas por severidad (la más grave primero).
CREATE INDEX IF NOT EXISTS idx_incidents_abiertas
    ON incidents(severity, created_at DESC)
    WHERE deleted_at IS NULL AND status NOT IN ('RESOLVED', 'CLOSED', 'CANCELLED');

-- Las que ya violaron el SLA de resolucion: la consulta del tablero.
CREATE INDEX IF NOT EXISTS idx_incidents_vencidas
    ON incidents(resolution_due_at)
    WHERE deleted_at IS NULL AND status NOT IN ('RESOLVED', 'CLOSED', 'CANCELLED');

-- Las asignadas a una persona.
CREATE INDEX IF NOT EXISTS idx_incidents_asignadas
    ON incidents(assigned_to, status)
    WHERE deleted_at IS NULL AND assigned_to IS NOT NULL;

-- Las de un equipo: "que le pasa a este equipo".
CREATE INDEX IF NOT EXISTS idx_incidents_equipo
    ON incidents(device_id, created_at DESC)
    WHERE deleted_at IS NULL AND device_id IS NOT NULL;

-- Busqueda por texto (el título y la descripción).
CREATE INDEX IF NOT EXISTS idx_incidents_titulo
    ON incidents USING GIN (to_tsvector('spanish', coalesce(title, '') || ' ' || coalesce(description, '')));

-- ===========================================================================
-- 8. VINCULO CON EL MANTENIMIENTO Y LAS Reparaciones
-- ===========================================================================
--
-- La incidencia es el REPORTE; la reparación y el mantenimiento son la
-- EJECUCION. El vinculo va desde la ejecucion hacia la incidencia: así una
-- fila de `repairs` no tiene que conocer la tabla de incidencias más que por
-- su id.
--
-- `ON DELETE SET NULL`: si la incidencia se borra (borrado logico, pero
-- puede pasar), la reparación queda viva. Una orden de trabajo no puede
-- desaparecer porque al ticket le dio por borrado.

-- Primero la columna, después la FK: al reves, la FK no tiene a que
-- apuntar y la sentencia falla.
ALTER TABLE repairs ADD COLUMN IF NOT EXISTS incident_id UUID;
ALTER TABLE repairs DROP CONSTRAINT IF EXISTS fk_repairs_incident;
ALTER TABLE repairs ADD CONSTRAINT fk_repairs_incident
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE SET NULL;

ALTER TABLE maintenance ADD COLUMN IF NOT EXISTS incident_id UUID;
ALTER TABLE maintenance DROP CONSTRAINT IF EXISTS fk_maintenance_incident;
ALTER TABLE maintenance ADD CONSTRAINT fk_maintenance_incident
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_repairs_incident ON repairs(incident_id) WHERE incident_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_maintenance_incident ON maintenance(incident_id) WHERE incident_id IS NOT NULL;

-- ===========================================================================
-- 9. VISTAS
-- ===========================================================================

-- El SLA en curso, con los plazos que faltan y si ya se violaron.
--
-- `min(0, ...)` para que "restante" sea NEGATIVO cuando ya se paso: así el
-- tablero puede ordenar por lo más vencido sin tener que comparar con NOW()
-- en cada fila.
CREATE OR REPLACE VIEW v_incident_sla AS
SELECT
    i.id,
    i.code,
    i.title,
    i.severity,
    i.status,
    i.category,
    i.device_id,
    i.assigned_to,
    i.created_at,
    i.response_due_at,
    i.resolution_due_at,
    i.first_response_at,
    i.resolved_at,
    i.closed_at,
    EXTRACT(EPOCH FROM (i.response_due_at - NOW())) / 60.0 AS minutos_para_responder,
    EXTRACT(EPOCH FROM (i.resolution_due_at - NOW())) / 60.0 AS minutos_para_resolver,
    (i.first_response_at IS NOT NULL AND i.first_response_at > i.response_due_at) AS respuesta_tardia,
    (i.resolved_at IS NOT NULL AND i.resolved_at > i.resolution_due_at) AS resolucion_tardia,
    (i.resolved_at IS NULL AND i.resolution_due_at < NOW()) AS vencido,
    (i.resolved_at IS NOT NULL AND i.resolved_at > i.resolution_due_at) AS fuera_de_sla
FROM incidents i
WHERE i.deleted_at IS NULL;

-- La salud del mantenimiento por modelo: MTBF (tiempo medio entre fallas),
-- MTTR (tiempo medio de reparación), y el cumplimiento del plan preventivo.
--
-- MTBF: el promedio de los días entre dos incidencias del mismo modelo. Es el
-- número que dice "cada cuanto se rompe".
--
-- MTTR: el promedio de las horas entre que se reporto y se resolvio. Es el
-- número que dice "cuanto tarda el taller".
CREATE OR REPLACE VIEW v_maintenance_metrics AS
SELECT
    m.id AS model_id,
    m.sku,
    m.brand,
    m.model,
    COALESCE(f.cantidad_fallas, 0) AS cantidad_fallas,
    f.mtbf_dias,
    r.mttr_horas,
    p.cantidad_planes,
    p.mantenimientos_programados,
    p.mantenimientos_completados,
    p.mantenimientos_pendientes,
    CASE
        WHEN COALESCE(p.mantenimientos_programados, 0) = 0 THEN NULL
        ELSE ROUND(
            100.0 * COALESCE(p.mantenimientos_completados, 0) / p.mantenimientos_programados,
            1
        )
    END AS cumplimiento_plan_pct
FROM models m
LEFT JOIN (
    -- Fallas por modelo, con el tiempo medio entre ellas.
    --
    -- El `LAG` va en un subquery y el `AVG` por FUERA: Postgres no permite
    -- una función de ventana dentro de un agregado ("aggregate function calls
    -- cannot contain window function calls"). Por eso son dos niveles.
    SELECT
        d.model_id,
        COUNT(*) AS cantidad_fallas,
        ROUND(AVG(d.dias_entre)::NUMERIC, 1) AS mtbf_dias
    FROM (
        SELECT
            i.model_id,
            EXTRACT(EPOCH FROM (
                i.created_at - LAG(i.created_at) OVER (
                    PARTITION BY i.model_id ORDER BY i.created_at
                )
            )) / 86400.0 AS dias_entre
        FROM incidents i
        WHERE i.deleted_at IS NULL
          AND i.model_id IS NOT NULL
          AND i.status NOT IN ('CANCELLED')
    ) d
    GROUP BY d.model_id
) f ON f.model_id = m.id
LEFT JOIN (
    -- Tiempo medio de resolucion.
    SELECT
        i.model_id,
        ROUND(AVG(EXTRACT(EPOCH FROM (i.resolved_at - i.created_at)) / 3600.0)::NUMERIC, 1) AS mttr_horas
    FROM incidents i
    WHERE i.deleted_at IS NULL
      AND i.model_id IS NOT NULL
      AND i.resolved_at IS NOT NULL
    GROUP BY i.model_id
) r ON r.model_id = m.id
LEFT JOIN (
    -- Cumplimiento del plan preventivo.
    SELECT
        mp.model_id,
        (SELECT COUNT(*) FROM maintenance_plans p2
          WHERE p2.model_id = mp.model_id AND p2.is_active) AS cantidad_planes,
        COUNT(mt.id) AS mantenimientos_programados,
        COUNT(mt.id) FILTER (WHERE mt.status = 'COMPLETED') AS mantenimientos_completados,
        COUNT(mt.id) FILTER (WHERE mt.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')) AS mantenimientos_pendientes
    FROM maintenance_plans mp
    LEFT JOIN maintenance mt ON mt.plan_id = mp.id
    GROUP BY mp.model_id
) p ON p.model_id = m.id
WHERE m.deleted_at IS NULL;
