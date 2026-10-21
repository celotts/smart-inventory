-- ===========================================================================
-- 0017 - SLA operable: horario laboral, escalamiento y avisos
-- ===========================================================================
-- Objetivo:
--   1. Que el SLA se mida en HORARIO LABORAL, y no en 24 horas.
--   2. Que un caso que se pasa de plazo ESCALE solo.
--   3. Que el operador SEA AVISADO: hoy el sistema calcula quien violo el
--      SLA, pero no avisa. Un SLA que nadie recibe es un SLA de papel.
--
-- QUE ES "HORARIO LABORAL" Y POR QUE NO ES 24H
-- ----------------------------------------------
-- Un SLA de 4 horas para una S1 (sistema caido) a las 3 de la madrugada
-- significa "a las 7 de la mañana". Con 24h, significa "a las 3 de la
-- madrugada", cuando no hay nadie. El cliente que firma el SLA cuenta horas
-- de trabajo, y el sistema tiene que contar lo mismo.
--
-- LO QUE FALTA Y NO SE INVENTA
-- ------------------------------
-- Los FERIADOS. Un calendario de feriados de un pais es un dato que cambia
-- todos los años y que nadie mantiene en un archivo de configuración. Por eso
-- hay una lista `holidays` editable: lo que no este cargado, el sistema no lo
-- sabe. Es una limitacion CONOCIDA y visible, no un olvido.
--
-- LA ZONA HORARIA
-- ---------------
-- IANA (`America/Argentina/Buenos_Aires`), guardada en la base y no en el
-- código: el cliente puede estar en otro pais, y la regla del SLA es de el.
-- Postgres trae la base de zonas horarias, así que el calculo se hace con
-- `AT TIME ZONE`, que respeta la hora de verano.
-- ===========================================================================

-- ===========================================================================
-- 1. CALENDARIO LABORAL
-- ===========================================================================
--
-- Una sola fila (id = 1): la configuración del negocio. Si un día hacen
-- falta varias zonas, la tabla está preparada para crecer por `client_id`.

CREATE TABLE IF NOT EXISTS business_calendar_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    -- Zona horaria IANA. Postgres la conoce: `AT TIME ZONE` la usa.
    timezone VARCHAR(64) NOT NULL DEFAULT 'America/Argentina/Buenos_Aires',
    -- Ventana de trabajo de cada día laborable.
    day_start TIME NOT NULL DEFAULT '09:00',
    day_end TIME NOT NULL DEFAULT '18:00',
    -- Días laborables: 0 = domingo, 6 = sabado. Postgres cuenta así.
    workdays SMALLINT[] NOT NULL DEFAULT '{1,2,3,4,5}',
    -- Feriados. Los que no esten cargados, el sistema no los sabe.
    holidays DATE[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_business_calendar_ventana CHECK (day_start < day_end)
);

INSERT INTO business_calendar_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- ===========================================================================
-- 2. Políticas DE ESCALAMIENTO
-- ===========================================================================
--
-- Que pasa cuando un caso se pasa de plazo sin respuesta.
--
-- `escalate_to_role` y `escalate_to_user` son ALTERNATIVAS: si hay un
-- responsable fijo, se escala a el; si no, al primer ADMIN (o al rol que se
-- indique). La eleccion es del dato, no del código: "escalar a soporte" o
-- "escalar al gerente de turno" es una decisión de cada cliente.

CREATE TABLE IF NOT EXISTS incident_escalation_policies (
    severity incident_severity PRIMARY KEY,
    -- Minutos después del plazo de RESPUESTA en los que se escala.
    -- 0 significa "escala en cuanto se pasa el plazo": es lo que corresponde
    -- a una S1 (sistema caido), donde esperar 15 minutos para avisarle a
    -- alguien es esperar 15 minutos con el cliente sin servicio.
    escalate_after_minutes INT NOT NULL CHECK (escalate_after_minutes >= 0),
    escalate_to_role VARCHAR(40),
    escalate_to_user UUID REFERENCES users(id) ON DELETE SET NULL,
    -- Cuantas veces se puede escalar el mismo caso. Un caso que se escala
    -- tres veces no está escalando: está en un loop.
    max_escalations INT NOT NULL DEFAULT 1 CHECK (max_escalations >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO incident_escalation_policies (severity, escalate_after_minutes, escalate_to_role, max_escalations)
VALUES
    ('S1', 0,   'ADMIN', 2),
    ('S2', 60,  'ADMIN', 1),
    ('S3', 480, 'ADMIN', 1)
ON CONFLICT (severity) DO UPDATE SET
    escalate_after_minutes = EXCLUDED.escalate_after_minutes,
    escalate_to_role = EXCLUDED.escalate_to_role,
    max_escalations = EXCLUDED.max_escalations,
    updated_at = NOW();

-- ===========================================================================
-- 3. Notificaciones
-- ===========================================================================
--
-- El aviso que le llega al operador. Es una TABLA y no un `avisar()` del
-- front: un aviso flotante se va solo a los 4 segundos y no se puede
-- auditar. Un aviso de SLA tiene que quedar, y tiene que saber a quien se le
-- dio y cuando.
--
-- `user_id` es el destinatario. NULL significa "para todos los que pueden
-- ver el caso" (un aviso general de la operación).
--
-- `dedup_key` es la IDEMPOTENCIA: el mismo aviso no se duplica. Sin eso, un
-- barrido cada minuto crearia 60 avisos por caso y la bandeja seria un
-- desastre.

CREATE TABLE IF NOT EXISTS incident_notifications (
    id BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    -- A quien le llega. NULL = aviso general.
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    type VARCHAR(32) NOT NULL,
    title VARCHAR(200) NOT NULL,
    message TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- La clave de deduplicacion. Un aviso por caso y por tipo y por plazo.
    dedup_key VARCHAR(160) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_incident_notifications_user
    ON incident_notifications(user_id, created_at DESC)
    WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_incident_notifications_incident
    ON incident_notifications(incident_id);

-- ===========================================================================
-- 4. EL SLA EN HORARIO LABORAL
-- ===========================================================================
--
-- La función que suma "minutos habiles" a un instante.
--
-- EL ALGORITMO
-- ------------
-- Día por día, no minuto por minuto: se calcula la ventana laboral de cada
-- día (de `day_start` a `day_end`), se le resta al remanente lo que entre,
-- y cuando el remanente entra en una ventana, el plazo cae adentro de ella.
--
-- `FOR i IN 1..366` es el tope: un remanente de más de un ano de trabajo no
-- se calcula (y sale por el fallback de abajo).
CREATE OR REPLACE FUNCTION incident_agrega_minutos_habiles(
    p_desde TIMESTAMPTZ,
    p_minutos INT,
    p_horario_laboral BOOLEAN
)
RETURNS TIMESTAMPTZ AS $$
DECLARE
    v_tz VARCHAR(64);
    v_day_start TIME;
    v_day_end TIME;
    v_workdays SMALLINT[];
    v_holidays DATE[];
    v_restante INT := p_minutos;
    v_cursor TIMESTAMPTZ := p_desde;
    v_fecha DATE;
    v_inicio TIMESTAMPTZ;
    v_fin TIMESTAMPTZ;
    v_duracion_min NUMERIC;
    v_dia INT;
BEGIN
    -- Sin horario laboral, es una suma de minutos de reloj.
    IF NOT p_horario_laboral OR p_minutos <= 0 THEN
        RETURN p_desde + make_interval(mins => p_minutos);
    END IF;

    SELECT timezone, day_start, day_end, workdays, holidays
      INTO v_tz, v_day_start, v_day_end, v_workdays, v_holidays
      FROM business_calendar_settings
     WHERE id = 1;

    IF NOT FOUND THEN
        RETURN p_desde + make_interval(mins => p_minutos);
    END IF;

    v_fecha := (p_desde AT TIME ZONE v_tz)::DATE;

    FOR i IN 1..366 LOOP
        v_dia := EXTRACT(DOW FROM v_fecha)::INT;

        IF v_dia = ANY(v_workdays) AND NOT (v_fecha = ANY(v_holidays)) THEN
            v_inicio := (v_fecha + v_day_start) AT TIME ZONE v_tz;
            v_fin := (v_fecha + v_day_end) AT TIME ZONE v_tz;

            -- Si todavía no empezo la ventana del día, el cursor arranca en su
            -- inicio: un reporte de las 3 de la mañana no cuenta las horas
            -- hasta las 9.
            IF v_cursor < v_inicio THEN
                v_cursor := v_inicio;
            END IF;

            IF v_cursor < v_fin THEN
                v_duracion_min := EXTRACT(EPOCH FROM (v_fin - v_cursor)) / 60.0;

                IF v_restante <= v_duracion_min THEN
                    RETURN v_cursor + make_interval(mins => v_restante);
                END IF;

                v_restante := v_restante - v_duracion_min::INT;
            END IF;
        END IF;

        v_fecha := v_fecha + 1;
        -- El cursor arranca en la ventana del día siguiente; el `IF` de arriba
        -- lo ajusta si hace falta.
        v_cursor := (v_fecha + v_day_start) AT TIME ZONE v_tz;
    END LOOP;

    -- Más de un ano de horario laboral: no se calcula, se cae al reloj. Es
    -- un caso patologico (un SLA de dos años), y es preferible un plazo
    -- aproximado a un error.
    RETURN p_desde + make_interval(mins => p_minutos);
END;
$$ LANGUAGE plpgsql;

-- El trigger del SLA ahora usa la función.
CREATE OR REPLACE FUNCTION incident_calcula_sla()
RETURNS TRIGGER AS $$
DECLARE
    v_response INT;
    v_resolution INT;
    v_horario BOOLEAN;
BEGIN
    SELECT response_minutes, resolution_minutes, business_hours_only
      INTO v_response, v_resolution, v_horario
      FROM incident_sla_policies
     WHERE severity = NEW.severity AND is_active = TRUE;

    IF v_response IS NULL THEN
        -- Sin política para esa severidad, el alta tiene que fallar: es
        -- preferible un error claro a una incidencia sin plazo de respuesta.
        RAISE EXCEPTION
            'No hay politica de SLA activa para la severidad %', NEW.severity
            USING ERRCODE = 'check_violation';
    END IF;

    NEW.response_due_at := incident_agrega_minutos_habiles(
        NEW.created_at, v_response, v_horario
    );
    NEW.resolution_due_at := incident_agrega_minutos_habiles(
        NEW.created_at, v_resolution, v_horario
    );
    NEW.code := COALESCE(NEW.code, incident_next_code(EXTRACT(YEAR FROM NEW.created_at)::INT));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 5. EL EVENTO DE ALTA, LO ESCRIBE LA BASE
-- ===========================================================================
--
-- El alta se registra en la línea de tiempo con un trigger AFTER INSERT, y no
-- desde la API.
--
-- POR QUE EN LA BASE Y NO EN LA API
-- ---------------------------------
-- Por la misma razón que los cambios de estado: si el evento lo escribe la
-- API, una fila creada por otro camino (un script, una migración, un `psql`)
-- queda SIN su evento de alta, y el historial queda incompleto sin que nadie
-- lo note. Un historial incompleto es peor que ninguno: parece confiable.
--
-- El actor sale del contexto de la transaccion, como en los cambios de estado.

CREATE OR REPLACE FUNCTION incident_registra_creacion()
RETURNS TRIGGER AS $$
DECLARE
    v_actor UUID;
BEGIN
    v_actor := NULLIF(current_setting('app.actor_id', true), '')::UUID;

    INSERT INTO incident_events (incident_id, event_type, to_status, actor_id, note)
    VALUES (NEW.id, 'CREATED', NEW.status, v_actor, NEW.description);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_incident_registra_creacion ON incidents;
CREATE TRIGGER trigger_incident_registra_creacion
    AFTER INSERT ON incidents
    FOR EACH ROW EXECUTE FUNCTION incident_registra_creacion();

-- ===========================================================================
-- 6. Índices
-- ===========================================================================

-- Los casos que hay que mirar en el barrido: sin respuesta, con el plazo
-- cerca o vencido.
CREATE INDEX IF NOT EXISTS idx_incidents_sla_barrido
    ON incidents(response_due_at)
    WHERE deleted_at IS NULL
      AND first_response_at IS NULL
      AND status IN ('OPEN', 'IN_PROGRESS', 'WAITING');
