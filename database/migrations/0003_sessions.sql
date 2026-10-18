-- ===========================================================================
-- 0003 — Sesiones: revocación de tokens
-- ===========================================================================
--
-- QUE NECESITA ESTA TABLA
-- -----------------------
-- Un JWT es stateless por naturaleza: el servidor no guarda nada y no puede
-- darlo por vencido. Para dar de baja a un usuario hay que guardar el estado.
--
-- El problema de "guardar todo token emitido" es que la tabla crece sin
-- límite y cada request tiene que escribir. La solución de dos tokens:
--
--   access token  JWT, 15 minutos. Viaja en cada request. NO se guarda: es
--                  verificable por firma sola, sin tocar la base.
--   refresh token JWT, 30 días. Solo se usa en POST /api/auth/refresh, y es el
--                  ÚNICO que queda guardado (hasheado) en esta tabla.
--
-- Verificar un access token es una firma: microsegundos. Revocar es borrar la
-- fila de la sesión, o marcar la de todos los access de ese usuario con una
-- `revoked_before`. Por eso esta tabla existe y por eso tiene dos funciones.
--
-- EL HASH DEL REFRESH TOKEN
-- -------------------------
-- Se guarda `SHA-256` del token, no el token. Si alguien lee la base con un
-- SELECT, no puede iniciar sesión con lo que hay: el hash no sirve para
-- autenticarse, solo para comparar. Es el mismo criterio que las passwords.
--
-- `token_hash` es UNIQUE: dos refresh del mismo token es un dato corrupto, y
-- con el UNIQUE el error aparece al insertar y no como un fallo raro de
-- autenticación tres días después.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- A quién pertenece. CASCADE: si borran al usuario (hard delete), sus
    -- sesiones tienen que morir con él o quedan sesiones huérfanas de un id
    -- que no existe. Es el único CASCADE del schema, y es correcto acá: una
    -- sesión sin usuario no tiene a quién servirle.
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Ver /auth/refresh. Ver la nota de arriba sobre por qué es un SHA-256.
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,

    -- Para la lista "mis sesiones abiertas" y para el cierre de sesión en otro
    -- dispositivo. No es un dato de autenticación: es informativo.
    user_agent TEXT,
    ip_address VARCHAR(45),

    -- Logout de ESTA sesion.
    revoked_at TIMESTAMPTZ,

    -- Logout de TODAS las sesiones del usuario. `revoked_before` es la marca
    -- de tiempo: cualquier access token emitido antes de ese instante es
    -- invalido, aunque su sesion siga "viva".
    --
    -- Es la unica forma de cortar un access token ya entregado sin esperar a
    -- que expire, que es justamente el caso de "le robaron el token".
    revoked_before TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);

-- El indice que usa `revoked_before`: revocar todo el usuario tiene que
-- encontrar sus sesiones, no recorrer la tabla.
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Limpieza de sesiones vencidas. Es un indice parcial: solo las vivas, que
-- son pocas; las muertas se pueden borrar sin esperar.
CREATE INDEX IF NOT EXISTS idx_sessions_vivas ON sessions(expires_at)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE sessions IS
    'Refresh tokens hasheados. Un JWT access NO se guarda: es verificable por firma.';
COMMENT ON COLUMN sessions.revoked_before IS
    'Corta todos los access tokens emitidos antes de este instante. Para robo de token.';