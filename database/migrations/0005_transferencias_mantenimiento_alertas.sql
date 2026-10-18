-- Transferencias entre ubicaciones
CREATE TABLE IF NOT EXISTS transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES devices(id),
    from_location VARCHAR(128) NOT NULL,
    to_location VARCHAR(128) NOT NULL,
    requested_by UUID REFERENCES users(id),
    approved_by UUID REFERENCES users(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ
);

-- Mantenimiento preventivo
CREATE TABLE IF NOT EXISTS maintenance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID REFERENCES devices(id),
    tipo VARCHAR(64) NOT NULL,
    descripcion TEXT,
    scheduled_for DATE NOT NULL,
    completed_at TIMESTAMPTZ,
    tecnico_id UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Alertas de stock mínimo
CREATE TABLE IF NOT EXISTS stock_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ubicacion VARCHAR(128) NOT NULL,
    minimo INT NOT NULL,
    actual INT NOT NULL,
    resuelto BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
CREATE INDEX IF NOT EXISTS idx_maintenance_scheduled ON maintenance(scheduled_for);
CREATE INDEX IF NOT EXISTS idx_stock_alerts_resuelto ON stock_alerts(resuelto);