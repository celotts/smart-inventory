-- ===========================================================================
-- 0010 - Tipos de movimiento dinámicos (movement_types table)
-- ===========================================================================
-- Permite a los clientes configurar sus propios tipos de movimiento
-- manteniendo los tipos del sistema como base.

-- ===========================================================================
-- 1. Tabla movement_types
-- ===========================================================================
CREATE TABLE movement_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL = tipo del sistema (no editable). INT = tipo personalizado del cliente.
    client_id INT REFERENCES clients(id) ON DELETE CASCADE,
    name VARCHAR(50) NOT NULL,           -- Identificador único: 'WAREHOUSE_ENTRY', 'OUT_TO_CLIENT', etc.
    label VARCHAR(100) NOT NULL,         -- Texto visible para el usuario: 'Sale a cliente', 'Instalo en sitio'
    target_status device_status NOT NULL, -- Estado destino al ejecutar este movimiento
    icon VARCHAR(50),                    -- Nombre del icono (lucide/heroicons)
    color VARCHAR(20),                   -- Color hex para la UI
    sort_order INT DEFAULT 0,            -- Orden en la UI
    is_system BOOLEAN DEFAULT FALSE,     -- TRUE = tipo del sistema (no editable)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Un cliente no puede tener dos tipos con el mismo nombre
    CONSTRAINT uq_movement_type_client_name UNIQUE (client_id, name)
);

-- Trigger para updated_at en movement_types
CREATE TRIGGER trigger_movement_types_updated_at
    BEFORE UPDATE ON movement_types
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Índices
CREATE INDEX idx_movement_types_client ON movement_types(client_id);
CREATE INDEX idx_movement_types_system ON movement_types(is_system);
CREATE INDEX idx_movement_types_active ON movement_types(is_active);

-- Unique constraint for system types (partial unique index)
CREATE UNIQUE INDEX uq_movement_type_system_name ON movement_types(name) WHERE (is_system = TRUE);

-- ===========================================================================
-- 2. Seed de tipos de movimiento DEL Sistema (is_system = TRUE, client_id = NULL)
-- ===========================================================================
INSERT INTO movement_types (name, label, target_status, icon, color, sort_order, is_system, is_active) VALUES
    ('WAREHOUSE_ENTRY', 'Entrada a depósito', 'IN_WAREHOUSE', 'package', '#2e7d32', 1, TRUE, TRUE),
    ('OUT_TO_CLIENT', 'Sale a cliente', 'IN_TRANSIT', 'truck', '#b45309', 2, TRUE, TRUE),
    ('INSTALLATION', 'Instalo en sitio', 'INSTALLED', 'home', '#0277bd', 3, TRUE, TRUE),
    ('REPLACEMENT', 'Reemplazo', 'IN_TRANSIT', 'swap', '#b45309', 4, TRUE, TRUE),
    ('WITHDRAWAL_TO_WORKSHOP', 'Manda a taller', 'IN_REPAIR', 'wrench', '#6b4a86', 5, TRUE, TRUE),
    ('WAREHOUSE_REENTRY', 'Vuelve a depósito', 'IN_WAREHOUSE', 'package-2', '#2e7d32', 6, TRUE, TRUE),
    ('DISPOSAL', 'Dar de baja', 'PERMANENTLY_DISPOSED', 'trash', '#c62828', 8, TRUE, TRUE);

-- ===========================================================================
-- 2. Migración de movement_history: movement_type -> movement_type_id
-- ===========================================================================
-- Paso 1: Agregar columna movement_type_id
ALTER TABLE movement_history ADD COLUMN movement_type_id UUID;

-- Paso 2: Poblar movement_type_id desde movement_type (ENUM)
UPDATE movement_history mh
SET movement_type_id = mt.id
FROM movement_types mt
WHERE mt.name = mh.movement_type::text AND mt.is_system = TRUE;

-- Paso 3: Hacer NOT NULL y agregar FK
ALTER TABLE movement_history ALTER COLUMN movement_type_id SET NOT NULL;
ALTER TABLE movement_history ADD CONSTRAINT fk_movement_history_type
    FOREIGN KEY (movement_type_id) REFERENCES movement_types(id) ON DELETE RESTRICT;

-- Paso 4: Eliminar columna movement_type (ENUM)
ALTER TABLE movement_history DROP COLUMN movement_type;

-- ===========================================================================
-- 3. Actualizar validación de reemplazo
-- ===========================================================================
-- POR QUE NO ES UN CHECK
-- ----------------------
-- La versión original era un CHECK con un SELECT adentro:
--
--     CHECK (CASE WHEN (SELECT name FROM movement_types WHERE id = movement_type_id)
--                 = 'REPLACEMENT' THEN ... END)
--
-- Postgres RECHAZA eso con "cannot use subquery in check constraint": la regla
-- es que un CHECK se evalua dentro de la misma fila y una subconsulta rompe esa
-- garantía. La migración fallaba entera y `movement_history` quedaba sin migrar.
--
-- La regla depende de OTRA tabla (el nombre del tipo vive en movement_types), así
-- que no se puede expresar como CHECK ni con una FK. Se hace con un trigger, que
-- si puede consultar.
ALTER TABLE movement_history DROP CONSTRAINT IF EXISTS ck_movement_replacement;

CREATE OR REPLACE FUNCTION validar_reemplazo_movimiento()
RETURNS TRIGGER AS $$
DECLARE
    es_reemplazo BOOLEAN;
BEGIN
    SELECT (name = 'REPLACEMENT') INTO es_reemplazo
    FROM movement_types
    WHERE id = NEW.movement_type_id;

    IF COALESCE(es_reemplazo, FALSE) THEN
        IF NEW.replaced_device_id IS NULL OR NEW.replacement_reason IS NULL THEN
            RAISE EXCEPTION
                'Un movimiento REPLACEMENT requiere replaced_device_id y replacement_reason';
        END IF;
    ELSE
        IF NEW.replaced_device_id IS NOT NULL OR NEW.replacement_reason IS NOT NULL THEN
            RAISE EXCEPTION
                'Solo un movimiento REPLACEMENT puede tener replaced_device_id/replacement_reason';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_movement_history_reemplazo ON movement_history;
CREATE TRIGGER trigger_movement_history_reemplazo
    BEFORE INSERT OR UPDATE ON movement_history
    FOR EACH ROW EXECUTE FUNCTION validar_reemplazo_movimiento();

-- ===========================================================================
-- 4. Índices
-- ===========================================================================
CREATE INDEX idx_movement_history_type ON movement_history(movement_type_id);
-- Nota: para filtrar por cliente, se hace JOIN con movement_types
