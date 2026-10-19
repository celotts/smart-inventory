-- ===========================================================================
-- 0011 - Catálogo de Productos (SKU) + Stock Agregado + Mantenimiento
-- ===========================================================================
-- Objetivo:
--   1. products: Maestro de SKUs (qué se compra, vende, mantiene)
--   2. stock_levels: Stock agregado por producto/ubicación (vistas rápidas)
--   3. product_maintenance: Planes de mantenimiento por producto
--   4. device_maintenance: Mantenimientos programados/realizados por equipo
--   5. Relacionar devices -> products para heredar reglas de mantenimiento
-- ===========================================================================

-- ===========================================================================
-- 1. CATEGORÍAS DE PRODUCTO
-- ===========================================================================
CREATE TABLE product_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,          -- 'Notebook', 'Switch', 'UPS', 'Monitor'
    code VARCHAR(20) NOT NULL UNIQUE,           -- 'NB', 'SW', 'UP', 'MN' (para SKUs legibles)
    description TEXT,
    icon VARCHAR(50),                           -- Icono para UI
    color VARCHAR(20),                          -- Color hex
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_product_categories_updated_at
    BEFORE UPDATE ON product_categories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Seed categorías base
INSERT INTO product_categories (name, code, description, icon, color, sort_order) VALUES
    ('Notebook / Laptop', 'NB', 'Portátiles y estaciones de trabajo', 'laptop', '#0277bd', 1),
    ('Desktop / Workstation', 'DT', 'Torres, All-in-One, mini PCs', 'desktop', '#2e7d32', 2),
    ('Monitor / Pantalla', 'MN', 'Monitores, pantallas táctiles, señalización', 'monitor', '#b45309', 3),
    ('Networking', 'NW', 'Switches, routers, access points, firewalls', 'wifi', '#6b4a86', 4),
    ('Servidor / Storage', 'SV', 'Servidores rack/tower, NAS, SAN', 'server', '#c62828', 5),
    ('UPS / Energía', 'UP', 'UPS, PDUs, generadores, baterías', 'battery', '#795548', 6),
    ('Periféricos', 'PF', 'Teclados, mice, docks, webcams, headsets', 'mouse', '#37474f', 7),
    ('Impresoras / Escáneres', 'PR', 'Impresoras láser/inkjet, escáneres documentales', 'printer', '#5d4037', 8),
    ('Telefonía / UC', 'PH', 'Teléfonos IP, DECT, auriculares, conferencias', 'phone', '#00695c', 9),
    ('Otros', 'OT', 'Cables, racks, herramientas, consumibles', 'package', '#616161', 99);

-- ===========================================================================
-- 2. PRODUCTOS (SKU MAESTRO)
-- ===========================================================================
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(50) NOT NULL UNIQUE,            -- 'DELL-LAT-5440', 'CISCO-2960-48'
    name VARCHAR(200) NOT NULL,                 -- 'Dell Latitude 5440 i5/16GB/512GB'
    category_id INT NOT NULL REFERENCES product_categories(id),
    brand VARCHAR(100),                         -- 'Dell', 'Cisco', 'HP', 'APC'
    model VARCHAR(100),                         -- 'Latitude 5440', 'Catalyst 2960-48'
    variant VARCHAR(100),                       -- 'i5-1335U/16GB/512GB/14"'
    
    -- Especificaciones técnicas (JSON flexible)
    specs JSONB DEFAULT '{}',                   -- {"cpu":"i5-1335U","ram":"16GB","storage":"512GB SSD","screen":"14\""}
    
    -- Costos y precios
    unit_cost NUMERIC(12,2),                    -- Costo unitario promedio (USD o moneda local)
    list_price NUMERIC(12,2),                   -- Precio de lista / venta
    currency VARCHAR(3) DEFAULT 'USD',
    
    -- Gestión de inventario
    min_stock INT DEFAULT 0,                    -- Punto de reposición (alertar si stock <= min)
    max_stock INT DEFAULT 100,                  -- Tope máximo (evitar overstock)
    reorder_multiple INT DEFAULT 1,             -- Múltiplo de compra (cajas de 5, 10, etc.)
    lead_time_days INT DEFAULT 14,              -- Días de entrega proveedor
    preferred_vendor_id INT,                    -- Proveedor preferido (FK future)
    
    -- Ciclo de vida
    warranty_months INT DEFAULT 36,             -- Garantía estándar (meses)
    eol_date DATE,                              -- Fin de vida (End of Life) anunciado
    replacement_cycle_months INT,               -- Ciclo de renovación sugerido (ej: 48 meses)
    
    -- Mantenimiento
    requires_maintenance BOOLEAN DEFAULT FALSE, -- ¿Tiene plan de mantenimiento?
    maintenance_interval_months INT,            -- Intervalo base (ej: 12 = anual)
    
    -- Control
    is_active BOOLEAN DEFAULT TRUE,             -- Se puede comprar/usar
    is_serialized BOOLEAN DEFAULT TRUE,         -- TRUE = cada unidad tiene serial único (asset tracking)
    track_by_lot BOOLEAN DEFAULT FALSE,         -- FALSE = FIFO por serial, TRUE = lotes
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID REFERENCES users(id),
    
    CONSTRAINT chk_stock_bounds CHECK (min_stock >= 0 AND max_stock >= min_stock)
);

CREATE TRIGGER trigger_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_active ON products(is_active);
CREATE INDEX idx_products_sku_prefix ON products(sku); -- Para búsquedas por prefijo

-- ===========================================================================
-- 3. RELACIONAR DEVICES CON PRODUCTS
-- ===========================================================================
ALTER TABLE devices ADD COLUMN product_id INT REFERENCES products(id) ON DELETE SET NULL;

CREATE INDEX idx_devices_product ON devices(product_id);

-- ===========================================================================
-- 4. STOCK AGREGADO POR PRODUCTO / UBICACIÓN
-- ===========================================================================
-- Vista materializada refrescada por triggers (rápida para dashboards)
CREATE TABLE stock_levels (
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    location_id INT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    
    -- Contadores por estado (dispositivos individuales)
    available_count INT DEFAULT 0,      -- IN_WAREHOUSE (disponible para asignar)
    in_transit_count INT DEFAULT 0,     -- OUT_TO_CLIENT (en camino a cliente, no instalado aún)
    installed_count INT DEFAULT 0,      -- INSTALLED (en sitio del cliente)
    in_repair_count INT DEFAULT 0,      -- IN_REPAIR (en taller)
    quarantine_count INT DEFAULT 0,     -- CUARENTENA (pendiente revisión, opcional futuro)
    
    -- Valor de inventario (opcional, para contabilidad)
    total_value NUMERIC(14,2) DEFAULT 0, -- available_count * unit_cost promedio
    
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (product_id, location_id)
);

CREATE INDEX idx_stock_levels_location ON stock_levels(location_id);
CREATE INDEX idx_stock_levels_low ON stock_levels(available_count) WHERE available_count <= (
    SELECT min_stock FROM products WHERE id = stock_levels.product_id
);

-- ===========================================================================
-- 5. FUNCIÓN PARA RECALCULAR STOCK (se llama desde triggers)
-- ===========================================================================
CREATE OR REPLACE FUNCTION recalculate_stock_levels(p_product_id INT, p_location_id INT)
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
    WHERE product_id = p_product_id AND ubicacion_id = p_location_id;
    
    SELECT unit_cost INTO v_unit_cost FROM products WHERE id = p_product_id;
    
    INSERT INTO stock_levels (product_id, location_id, available_count, in_transit_count, installed_count, in_repair_count, total_value, updated_at)
    VALUES (p_product_id, p_location_id, v_available, v_in_transit, v_installed, v_in_repair, v_available * COALESCE(v_unit_cost, 0), NOW())
    ON CONFLICT (product_id, location_id) DO UPDATE SET
        available_count = EXCLUDED.available_count,
        in_transit_count = EXCLUDED.in_transit_count,
        installed_count = EXCLUDED.installed_count,
        in_repair_count = EXCLUDED.in_repair_count,
        total_value = EXCLUDED.total_value,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 6. TRIGGERS PARA MANTENER STOCK LEVELS ACTUALIZADO
-- ===========================================================================
-- Cuando cambia estado o ubicación de un device
CREATE OR REPLACE FUNCTION trigger_device_stock_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Recalcular para ubicación anterior (si cambió)
    IF OLD.ubicacion_id IS NOT NULL AND OLD.product_id IS NOT NULL THEN
        PERFORM recalculate_stock_levels(OLD.product_id, OLD.ubicacion_id);
    END IF;
    -- Recalcular para ubicación nueva
    IF NEW.ubicacion_id IS NOT NULL AND NEW.product_id IS NOT NULL THEN
        PERFORM recalculate_stock_levels(NEW.product_id, NEW.ubicacion_id);
    END IF;
    -- Si cambió producto (raro, pero posible al corregir datos)
    IF OLD.product_id IS DISTINCT FROM NEW.product_id THEN
        IF OLD.product_id IS NOT NULL AND OLD.ubicacion_id IS NOT NULL THEN
            PERFORM recalculate_stock_levels(OLD.product_id, OLD.ubicacion_id);
        END IF;
        IF NEW.product_id IS NOT NULL AND NEW.ubicacion_id IS NOT NULL THEN
            PERFORM recalculate_stock_levels(NEW.product_id, NEW.ubicacion_id);
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
-- 7. PLANES DE MANTENIMIENTO POR PRODUCTO
-- ===========================================================================
CREATE TABLE product_maintenance_plans (
    id SERIAL PRIMARY KEY,
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,                 -- 'Mantenimiento Anual', 'Revisión Baterías UPS'
    description TEXT,
    type VARCHAR(30) NOT NULL DEFAULT 'PREVENTIVE', -- 'PREVENTIVE', 'PREDICTIVE', 'CALIBRATION', 'INSPECTION'
    interval_months INT NOT NULL,               -- Cada cuántos meses (1, 3, 6, 12, 24)
    interval_basis VARCHAR(20) DEFAULT 'INSTALL_DATE', -- 'INSTALL_DATE' | 'MANUFACTURE_DATE' | 'LAST_MAINTENANCE'
    lead_time_days INT DEFAULT 30,              -- Avisar con X días de antelación
    checklist JSONB DEFAULT '[]',               -- [{ "step": "Verificar filtros", "required": true }, ...]
    estimated_duration_minutes INT,             -- Duración estimada
    requires_specialist BOOLEAN DEFAULT FALSE,  -- ¿Requiere técnico certificado?
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_product_maintenance_plans_updated_at
    BEFORE UPDATE ON product_maintenance_plans
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_product_maintenance_product ON product_maintenance_plans(product_id);

-- ===========================================================================
-- 8. MANTENIMIENTOS POR EQUIPO (device_maintenance)
-- ===========================================================================
CREATE TABLE device_maintenance (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    plan_id INT REFERENCES product_maintenance_plans(id) ON DELETE SET NULL,
    
    -- Programación
    scheduled_date DATE NOT NULL,               -- Cuándo toca
    due_date DATE GENERATED ALWAYS AS (scheduled_date + INTERVAL '7 days') STORED, -- Ventana de 7 días
    completed_date DATE,                        -- Cuándo se hizo realmente
    
    -- Estado
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'DUE_SOON', 'OVERDUE', 'COMPLETED', 'SKIPPED', 'CANCELLED'
    
    -- Ejecución
    performed_by UUID REFERENCES users(id),     -- Quién lo hizo
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_minutes INT,
    
    -- Resultados
    result VARCHAR(20),                         -- 'OK', 'NEEDS_REPAIR', 'NEEDS_REPLACEMENT', 'OBSERVATION'
    observations TEXT,
    checklist_results JSONB DEFAULT '[]',       -- [{"step": "Verificar filtros", "result": "OK", "notes": "Limpios"}]
    next_scheduled_date DATE,                   -- Próxima fecha calculada
    
    -- Costos
    labor_cost NUMERIC(10,2),
    parts_cost NUMERIC(10,2),
    total_cost NUMERIC(10,2) GENERATED ALWAYS AS (COALESCE(labor_cost,0) + COALESCE(parts_cost,0)) STORED,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_device_maintenance_updated_at
    BEFORE UPDATE ON device_maintenance
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_device_maintenance_device ON device_maintenance(device_id);
CREATE INDEX idx_device_maintenance_status ON device_maintenance(status);
CREATE INDEX idx_device_maintenance_scheduled ON device_maintenance(scheduled_date);
CREATE INDEX idx_device_maintenance_due ON device_maintenance(due_date) WHERE status IN ('PENDING', 'DUE_SOON', 'OVERDUE');

-- ===========================================================================
-- 9. FUNCIÓN: GENERAR PRÓXIMOS MANTENIMIENTOS (batch nocturno)
-- ===========================================================================
CREATE OR REPLACE FUNCTION generate_pending_maintenances()
RETURNS TABLE(device_id INT, plan_id INT, scheduled_date DATE, message TEXT) AS $$
DECLARE
    v_record RECORD;
    v_last_maint DATE;
    v_next_date DATE;
    v_install_date DATE;
BEGIN
    FOR v_record IN
        SELECT d.id AS device_id, d.product_id, d.installation_date, d.manufacture_date,
               pmp.id AS plan_id, pmp.interval_months, pmp.interval_basis, pmp.lead_time_days
        FROM devices d
        JOIN product_maintenance_plans pmp ON pmp.product_id = d.product_id AND pmp.is_active
        WHERE d.current_status IN ('INSTALLED', 'IN_WAREHOUSE') -- Solo equipos instalados o en stock
          AND d.product_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM device_maintenance dm
              WHERE dm.device_id = d.id
                AND dm.plan_id = pmp.id
                AND dm.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')
          )
    LOOP
        -- Calcular fecha base según interval_basis
        CASE v_record.interval_basis
            WHEN 'INSTALL_DATE' THEN v_install_date := v_record.installation_date;
            WHEN 'MANUFACTURE_DATE' THEN v_install_date := v_record.manufacture_date;
            WHEN 'LAST_MAINTENANCE' THEN
                SELECT MAX(completed_date) INTO v_last_maint
                FROM device_maintenance
                WHERE device_id = v_record.device_id AND plan_id = v_record.plan_id AND status = 'COMPLETED';
                v_install_date := v_last_maint;
            ELSE v_install_date := v_record.installation_date;
        END CASE;
        
        IF v_install_date IS NULL THEN
            -- Sin fecha base, programar para dentro del intervalo desde hoy
            v_next_date := CURRENT_DATE + (v_record.interval_months || ' months')::INTERVAL;
        ELSE
            -- Calcular próxima ocurrencia
            v_next_date := v_install_date + (v_record.interval_months || ' months')::INTERVAL;
            WHILE v_next_date <= CURRENT_DATE LOOP
                v_next_date := v_next_date + (v_record.interval_months || ' months')::INTERVAL;
            END LOOP;
        END IF;
        
        -- Insertar mantenimiento pendiente
        INSERT INTO device_maintenance (device_id, plan_id, scheduled_date, status)
        VALUES (v_record.device_id, v_record.plan_id, v_next_date, 'PENDING')
        ON CONFLICT DO NOTHING; -- Si ya existe por carrera, ignorar
        
        RETURN QUERY SELECT v_record.device_id, v_record.plan_id, v_next_date, 
            'Mantenimiento programado para ' || to_char(v_next_date, 'DD/MM/YYYY');
    END LOOP;
    
    -- Actualizar estados PENDING -> DUE_SOON / OVERDUE
    UPDATE device_maintenance
    SET status = CASE 
        WHEN due_date < CURRENT_DATE THEN 'OVERDUE'
        WHEN due_date <= CURRENT_DATE + INTERVAL '7 days' THEN 'DUE_SOON'
        ELSE 'PENDING'
    END
    WHERE status IN ('PENDING', 'DUE_SOON', 'OVERDUE');
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- 10. VISTA: EQUIPOS CON MANTENIMIENTO PENDIENTE (para dashboard)
-- ===========================================================================
CREATE OR REPLACE VIEW v_devices_maintenance_due AS
SELECT 
    dm.id AS maintenance_id,
    d.id AS device_id,
    d.serial_number,
    d.qr_code,
    p.name AS product_name,
    p.sku,
    pmp.name AS plan_name,
    pmp.type AS plan_type,
    dm.scheduled_date,
    dm.due_date,
    dm.status,
    l.name AS location_name,
    l.client_id,
    CASE 
        WHEN dm.due_date < CURRENT_DATE THEN CURRENT_DATE - dm.due_date
        ELSE 0
    END AS days_overdue,
    dm.result,
    dm.next_scheduled_date
FROM device_maintenance dm
JOIN devices d ON d.id = dm.device_id
JOIN products p ON p.id = d.product_id
JOIN product_maintenance_plans pmp ON pmp.id = dm.plan_id
JOIN locations l ON l.id = d.ubicacion_id
WHERE dm.status IN ('PENDING', 'DUE_SOON', 'OVERDUE')
ORDER BY dm.due_date ASC;

-- ===========================================================================
-- 11. VISTA: STOCK BAJO (para alertas de reposición)
-- ===========================================================================
CREATE OR REPLACE VIEW v_low_stock AS
SELECT 
    sl.product_id,
    p.sku,
    p.name AS product_name,
    p.min_stock,
    p.max_stock,
    p.reorder_multiple,
    p.lead_time_days,
    sl.location_id,
    loc.name AS location_name,
    sl.available_count,
    sl.available_count <= p.min_stock AS is_below_min,
    GREATEST(p.min_stock - sl.available_count, 0) AS deficit,
    -- Cantidad sugerida para PO: llevar a max_stock (redondeado a múltiplo)
    CASE 
        WHEN p.max_stock > sl.available_count 
        THEN CEIL((p.max_stock - sl.available_count) / p.reorder_multiple::NUMERIC) * p.reorder_multiple
        ELSE 0
    END AS suggested_reorder_qty
FROM stock_levels sl
JOIN products p ON p.id = sl.product_id AND p.is_active
JOIN locations loc ON loc.id = sl.location_id
WHERE sl.available_count <= p.min_stock
   OR (p.max_stock > 0 AND sl.available_count >= p.max_stock)
ORDER BY sl.available_count ASC;

-- ===========================================================================
-- 12. POBLAR STOCK_LEVELS INICIAL (para datos existentes)
-- ===========================================================================
DO $$
DECLARE
    v_rec RECORD;
BEGIN
    FOR v_rec IN SELECT DISTINCT product_id, ubicacion_id FROM devices WHERE product_id IS NOT NULL AND ubicacion_id IS NOT NULL
    LOOP
        PERFORM recalculate_stock_levels(v_rec.product_id, v_rec.ubicacion_id);
    END LOOP;
END;
$$;