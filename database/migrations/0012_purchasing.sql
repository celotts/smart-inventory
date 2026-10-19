-- ===========================================================================
-- 0012 - Compras: purchase_orders + purchase_order_lines
-- ===========================================================================
-- Tablas para gestión de órdenes de compra y recepción.
-- ===========================================================================

-- ===========================================================================
-- 1. Proveedores (vendors)
-- ===========================================================================
CREATE TABLE vendors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    code VARCHAR(50) UNIQUE,
    email VARCHAR(200),
    phone VARCHAR(50),
    address TEXT,
    contact_person VARCHAR(100),
    payment_terms VARCHAR(100),        -- ej: '30 días', 'contado'
    currency VARCHAR(3) DEFAULT 'USD',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trigger_vendors_updated_at
    BEFORE UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_vendors_active ON vendors(is_active);

-- Seed proveedor por defecto
INSERT INTO vendors (name, code, email, currency, is_active)
VALUES ('Proveedor Genérico', 'GEN', 'compras@ejemplo.com', 'USD', TRUE)
ON CONFLICT (code) DO NOTHING;

-- ===========================================================================
-- 2. Órdenes de Compra (purchase_orders)
-- ===========================================================================
CREATE TABLE purchase_orders (
    id SERIAL PRIMARY KEY,
    vendor_id INT NOT NULL REFERENCES vendors(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'BORRADOR',  -- BORRADOR, ENVIADA, CONFIRMADA, RECIBIDA, CANCELADA
    expected_date DATE,
    observations TEXT,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    received_at TIMESTAMPTZ
);

CREATE TRIGGER trigger_purchase_orders_updated_at
    BEFORE UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX idx_po_status ON purchase_orders(status);
CREATE INDEX idx_po_expected ON purchase_orders(expected_date);

-- ===========================================================================
-- 3. Líneas de Orden de Compra (purchase_order_lines)
-- ===========================================================================
CREATE TABLE purchase_order_lines (
    id SERIAL PRIMARY KEY,
    po_id INT NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    model_id INT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    cantidad INT NOT NULL CHECK (cantidad > 0),
    cantidad_recibida INT NOT NULL DEFAULT 0 CHECK (cantidad_recibida >= 0),
    unit_cost NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT chk_received_not_exceed CHECK (cantidad_recibida <= cantidad)
);

CREATE TRIGGER trigger_purchase_order_lines_updated_at
    BEFORE UPDATE ON purchase_order_lines
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_pol_po ON purchase_order_lines(po_id);
CREATE INDEX idx_pol_model ON purchase_order_lines(model_id);

-- ===========================================================================
-- 4. Función para actualizar status de PO al recibir
-- ===========================================================================
CREATE OR REPLACE FUNCTION update_po_status_on_receive()
RETURNS TRIGGER AS $$
BEGIN
    -- Verificar si todas las líneas están completamente recibidas
    IF NOT EXISTS (
        SELECT 1 FROM purchase_order_lines
        WHERE po_id = NEW.po_id AND cantidad_recibida < cantidad
    ) THEN
        UPDATE purchase_orders
        SET status = 'RECIBIDA', received_at = NOW(), updated_at = NOW()
        WHERE id = NEW.po_id;
    ELSE
        UPDATE purchase_orders
        SET status = 'PARCIAL', updated_at = NOW()
        WHERE id = NEW.po_id AND status NOT IN ('RECIBIDA', 'CANCELADA');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_po_status_on_receive
    AFTER UPDATE ON purchase_order_lines
    FOR EACH ROW
    WHEN (OLD.cantidad_recibida IS DISTINCT FROM NEW.cantidad_recibida)
    EXECUTE FUNCTION update_po_status_on_receive();

-- ===========================================================================
-- 5. Índices adicionales
-- ===========================================================================
CREATE INDEX idx_purchase_orders_created ON purchase_orders(created_at DESC);
CREATE INDEX idx_purchase_orders_vendor_status ON purchase_orders(vendor_id, status);