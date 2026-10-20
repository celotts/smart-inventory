-- ===========================================================================
-- 0011 - Extender Catálogo Existente (categories + models) + Stock + Mantenimiento
-- ===========================================================================
-- Objetivo:
--   1. Extender categories: code, icon, color, sort_order
--   2. Extender models: sku, unit_cost, list_price, min/max stock, lead_time, warranty, maintenance fields
--   3. stock_levels: Stock agregado por model/ubicación (vistas rápidas)
--   4. maintenance_plans: Planes de mantenimiento por modelo
--   5. device_maintenance: Extender maintenance existente o nueva tabla detallada
--   6. Triggers para mantener stock_levels actualizado
-- ===========================================================================

-- ===========================================================================
-- 1. EXTENDER CATEGORIES
-- ===========================================================================
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