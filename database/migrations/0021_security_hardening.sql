-- ============================================================================
-- MIGRACIÓN 0021: Security Hardening
-- ============================================================================
-- Agrega:
--   1. password_history: evita reutilizar contraseñas anteriores
--   2. Campos de rotación de contraseña en users
--   3. Device fingerprint en sessions (binding)
--   4. TOTP secret para MFA en users
--   5. Índices y constraints necesarios
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Tabla de historial de contraseñas
-- ---------------------------------------------------------------------------
CREATE TABLE password_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_user_id UUID REFERENCES users(id),
    created_by_role_id UUID REFERENCES roles(id)
);

CREATE INDEX idx_password_history_user ON password_history(user_id, created_at DESC);

COMMENT ON TABLE password_history IS
    'Historial de hashes de contraseñas para impedir reutilización. Nunca se expone por API.';


-- ---------------------------------------------------------------------------
-- 2. Campos de rotación de contraseña en users
-- ---------------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_must_change BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_rotation_days INTEGER DEFAULT 90;

-- Actualizar existing: marcar como cambiado "hoy" para no forzar rotación inmediata
UPDATE users SET password_changed_at = NOW() WHERE password_changed_at IS NULL;

COMMENT ON COLUMN users.password_changed_at IS 'Cuándo se cambió la contraseña por última vez (para rotación)';
COMMENT ON COLUMN users.password_must_change IS 'Si TRUE, obliga a cambiar en próximo login (expiración forzada, reset admin)';
COMMENT ON COLUMN users.password_rotation_days IS 'Días máximos antes de requerir cambio (0 = sin rotación)';


-- ---------------------------------------------------------------------------
-- 3. Device fingerprint en sessions (binding de refresh token)
-- ---------------------------------------------------------------------------
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_fingerprint VARCHAR(64);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_name VARCHAR(255);  -- legible: "Chrome on Windows", "iPhone Safari"

-- Índice para consultas rápidas por dispositivo
CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions(user_id, device_fingerprint) WHERE revoked_at IS NULL;

COMMENT ON COLUMN sessions.device_fingerprint IS 'SHA-256 truncado del fingerprint (UA + IP + etc.) para binding';
COMMENT ON COLUMN sessions.device_name IS 'Nombre legible del dispositivo para UI "mis sesiones"';


-- ---------------------------------------------------------------------------
-- 4. TOTP secret para MFA (2FA) en users
-- ---------------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_last_used_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_recovery_codes TEXT[];  -- códigos de recuperación de un solo uso

COMMENT ON COLUMN users.totp_secret IS 'Secreto base32 para TOTP (RFC 6238). NULL = MFA no configurado';
COMMENT ON COLUMN users.totp_enabled IS 'Si TRUE, exige TOTP en login para este usuario';
COMMENT ON COLUMN users.totp_recovery_codes IS 'Array de hashes bcrypt de códigos de recuperación (uso único)';


-- ---------------------------------------------------------------------------
-- 5. Trigger de auditoría para password_history
-- ---------------------------------------------------------------------------
CREATE TRIGGER trigger_password_history_audit
BEFORE INSERT OR UPDATE ON password_history
FOR EACH ROW EXECUTE FUNCTION set_audit_role_id();


-- ---------------------------------------------------------------------------
-- 6. Función helper: registrar cambio de password en historial
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION registrar_password_history(
    p_user_id UUID,
    p_password_hash TEXT,
    p_actor_id UUID DEFAULT NULL
) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO password_history (user_id, password_hash, created_by_user_id)
    VALUES (p_user_id, p_password_hash, p_actor_id);
END;
$$;

COMMENT ON FUNCTION registrar_password_history IS
    'Inserta el hash actual en password_history. Se llama desde la app al cambiar password.';


-- ---------------------------------------------------------------------------
-- 7. Función helper: verificar si password ya fue usada
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION password_ya_usada(
    p_user_id UUID,
    p_password_hash TEXT,
    p_historial_max INTEGER DEFAULT 12
) RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM password_history
    WHERE user_id = p_user_id
      AND password_hash = p_password_hash;

    RETURN v_count > 0;
END;
$$;

COMMENT ON FUNCTION password_ya_usada IS
    'Verifica si el hash dado ya está en el historial del usuario (últimos N cambios)';


-- ---------------------------------------------------------------------------
-- 8. Función helper: limpiar historial antiguo (mantener solo últimos N)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION limpiar_password_history(
    p_user_id UUID,
    p_mantener INTEGER DEFAULT 12
) RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_borrados INTEGER;
BEGIN
    DELETE FROM password_history
    WHERE user_id = p_user_id
      AND id NOT IN (
        SELECT id FROM password_history
        WHERE user_id = p_user_id
        ORDER BY created_at DESC
        LIMIT p_mantener
    );
    GET DIAGNOSTICS v_borrados = ROW_COUNT;
    RETURN v_borrados;
END;
$$;

COMMENT ON FUNCTION limpiar_password_history IS
    'Limpia el historial dejando solo los últimos N hashes. Se llama tras cambio exitoso.';


-- ---------------------------------------------------------------------------
-- 9. Función helper: verificar si password debe rotar
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION password_requiere_rotacion(p_user_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_rotacion_days INTEGER;
    v_changed_at TIMESTAMPTZ;
    v_must_change BOOLEAN;
BEGIN
    SELECT password_rotation_days, password_changed_at, password_must_change
    INTO v_rotacion_days, v_changed_at, v_must_change
    FROM users WHERE id = p_user_id;

    IF v_must_change THEN
        RETURN TRUE;
    END IF;

    IF v_rotacion_days IS NULL OR v_rotacion_days <= 0 THEN
        RETURN FALSE;
    END IF;

    IF v_changed_at IS NULL THEN
        RETURN TRUE;  -- nunca cambió, forzar
    END IF;

    RETURN (NOW() - v_changed_at) > (v_rotacion_days || ' days')::INTERVAL;
END;
$$;

COMMENT ON FUNCTION password_requiere_rotacion IS
    'TRUE si el usuario debe cambiar contraseña por rotación o forced change.';


-- ============================================================================
-- REGISTRO EN schema_migrations
-- ============================================================================
INSERT INTO schema_migrations (version, description, applied_at)
VALUES ('0021', 'Security hardening: password history, rotation, device binding, MFA', NOW())
ON CONFLICT (version) DO NOTHING;