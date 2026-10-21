-- ===========================================================================
-- MIGRACION 0019: LA BITACORA DE TRANSACCIONES
-- ===========================================================================
--
-- QUE ES Y QUE NO ES
-- ------------------
-- `audit_logs` ya existe y registra las ESCRITURAS: quien creo, modifico o
-- borro un registro, con el valor viejo y el nuevo. Es la bitacora de LOS
-- DATOS.
--
-- Esta tabla es la bitacora de LAS OPERACIONES: cada peticion que entra a la
-- API, con quien la hizo, sobre que recurso, que le devolvio y cuanto
-- tardo. Es la bitacora de LO QUE PASO.
--
-- POR QUE HACE FALTA LA DE LAS DOS
-- --------------------------------
-- "Cambio el precio de este producto" (audit_logs) y "el administrador
-- consulto el inventario a las 14:32" (transacciones) son preguntas
-- distintas, y un cliente con datos de empleados suele necesitar las dos:
-- no alcanza con saber QUE se toco, hay que saber QUIEN LO VIO.
--
-- UNA DE LAS DOS NO SUSTITUYE A LA OTRA
-- -------------------------------------
-- `audit_logs` tiene el detalle del CAMBIO (old_value/new_value) y el rol
-- que tenia el actor en el momento, gracias a un trigger de base. Esta tabla
-- tiene el CONTEXTO de la operacion (la ruta, la duracion, el resultado) y
-- cubre las LECTURAS, que `audit_logs` no registra (no hay nada que
-- cambiar).
--
-- LO QUE NO SE GUARDA
-- --------------------
-- El CUERPO de la peticion no se guarda: puede traer una contraseña (la de
-- "cambiar mi clave" o la de "crear usuario") y una contraseña en una
-- bitacora es una contraseña escrita en un archivo que cualquiera con
-- acceso al servidor lee.
--
-- Lo que si se guarda es el `datos` JSONB, que el codigo arma a mano con lo
-- que cada operacion decide que es relevante, y nunca con el cuerpo crudo.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS transacciones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- CUANDO
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- QUIEN
    --
    -- `user_id` con ON DELETE SET NULL: si el usuario se borra, la
    -- transaccion se queda (que es lo que tiene que pasar: el registro de
    -- lo que hizo no desaparece con la persona).
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    -- El email y el rol SE COPIAN. Si el usuario cambia de email o de rol,
    -- la transaccion dice como estaba en el momento, no como esta ahora:
    -- una auditoria que cambia con el tiempo no es una auditoria.
    user_email TEXT,
    role_name TEXT,

    -- QUE
    --
    -- `operacion` es la coarsely: LECTURA, ESCRITURA, AUTENTICACION o
    -- SISTEMA. Es un TEXTO y no un ENUM a proposito: agregar un tipo de
    -- operacion no puede requerir una migracion, y el filtro se hace por
    -- indice.
    operacion TEXT NOT NULL,

    -- COMO Y SOBRE QUE
    metodo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    -- El recurso logico ('devices', 'incidents', 'users'...). Se deduce de
    -- la ruta.
    recurso TEXT,
    -- El identificador del registro afectado, si lo hay ('42').
    recurso_id TEXT,

    -- COMO SALIO
    --
    -- `resultado` es mas legible que el codigo HTTP, y el codigo igual
    -- esta. 'ok' | 'error' | 'denegado' (un 403).
    resultado TEXT NOT NULL,
    status_code INT,
    duracion_ms INT,

    -- DE DONDE
    ip_address VARCHAR(45),
    user_agent TEXT,

    -- El `request_id` que ya viaja en la respuesta y en los logs: une la
    -- transaccion con el log del servidor sin buscala por fecha.
    request_id UUID,
    -- La sesion: permite ver TODO lo que hizo una sesion, que es lo que
    -- se pregunta cuando una cuenta fue compromise.
    sesion_id UUID,

    -- El detalle, en JSON. Lo arma el codigo de cada operacion, nunca con
    -- el cuerpo crudo de la peticion.
    datos JSONB
);

-- Los indices que usan las consultas de la pantalla de auditoria.
--
-- El primero es el de la pantalla: "lo mas reciente primero", con filtro
-- opcional por usuario, operacion o recurso.
CREATE INDEX IF NOT EXISTS idx_transacciones_occurred
    ON transacciones(occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_transacciones_user
    ON transacciones(user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_transacciones_operacion
    ON transacciones(operacion, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_transacciones_recurso
    ON transacciones(recurso, recurso_id, occurred_at DESC);

-- El `request_id` es UNICO: una peticion es una transaccion. Si una
-- peticion se registrara dos veces, el indice lo impide (y no es una
-- excepcion: es un bug).
CREATE UNIQUE INDEX IF NOT EXISTS idx_transacciones_request
    ON transacciones(request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transacciones_sesion
    ON transacciones(sesion_id, occurred_at DESC);

-- La busqueda por texto libre (el operador busca un serial, un email).
CREATE INDEX IF NOT EXISTS idx_transacciones_datos
    ON transacciones USING GIN(datos jsonb_path_ops);

-- ---------------------------------------------------------------------------
-- LA RETENCION
-- ---------------------------------------------------------------------------
--
-- Una bitacora de operaciones crece sin limite: es una tabla de SOLO
-- escritura, con una fila por peticion. Sin una politica de retencion, en
-- un año son millones de filas y las consultas de la pantalla se ponen
-- lentas.
--
-- El plazo es de 180 dias por defecto (medio año: alcanza para investigar
-- un incidente y para una auditoria anual) y se puede cambiar por entorno.
--
-- POR QUE NO HAY UN JOB QUE LA LIMPIE
-- ------------------------------------
-- Porque un job es una cosa mas que puede no estar corriendo. La limpieza
-- se hace AL ESCRIBIR: cada transaccion nueva mira las viejas y borra las
-- que ya vencieron. Es auto-contenida: si el job no corre, la tabla
-- sigue limpia.
--
-- Y es barato: el indice por fecha hace que borrar lo viejo sea un
-- recorrido de indice, no un escaneo de tabla.
--
-- La funcion es la que se puede llamar desde un job (por si despues se
-- quiere tener).
CREATE OR REPLACE FUNCTION limpiar_transacciones(
    p_dias INT DEFAULT NULL
) RETURNS BIGINT AS $$
DECLARE
    dias INT;
    borradas BIGINT;
BEGIN
    dias := COALESCE(p_dias, 180);

    DELETE FROM transacciones
    WHERE occurred_at < NOW() - make_interval(days => dias);

    GET DIAGNOSTICS borradas = ROW_COUNT;
    RETURN borradas;
END;
$$ LANGUAGE plpgsql;
