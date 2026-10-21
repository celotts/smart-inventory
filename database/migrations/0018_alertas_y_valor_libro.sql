-- ===========================================================================
-- 0018 - ALERTAS DE STOCK BAJO REALES Y VALOR LIBRO DE LOS ACTIVOS
-- ===========================================================================
--
-- QUE RESUELVE
-- -------------
-- Dos huecos que la evaluacion de requisitos dejo documentados:
--
-- 1. LA ALERTA DE STOCK BAJO NO EXISTIA. La tabla `stock_alerts` estaba
--    creada desde el principio, pero NADA la escribia: el operador se
--    enteraba del faltante al abrir el tablero. Una alerta que hay que ir a
--    buscar no es una alerta.
--
--    Ahora hay un BARRIDO que la llena, y la alerta guarda CUANDO aparecio
--    el faltante: con eso se sabe si la reposición está atrasada y cuando
--    va a romper el stock (lead time contra días bajo mínimo).
--
-- 2. NO HABIA VALOR LIBRO. El sistema sabia cuanto costaba un equipo, pero
--    no cuanto vale HOY. Un cliente que pide "cuanto vale mi flota" no
--    tenia respuesta, y la depreciacion es el dato que pide contabilidad.
--
-- POR QUE LAS ALERTAS SON POR PRODUCTO Y POR Ubicación
-- ---------------------------------------------------
-- La tabla original era generica (`ubicación`, `mínimo`, `actual`): un
-- número por ubicación, sin saber que producto. Para el stock bajo eso no
-- alcanza: el mínimo es POR PRODUCTO (`models.min_stock`), y dos productos
-- en el mismo depósito son dos alertas distintas con faltas distintas.
--
-- Se agregan las columnas que faltan, y se DEJA la tabla original intacta
-- para las filas que ya existieran (columnas anulables).
--
-- LA VIGENCIA DE UNA ALERTA
-- -------------------------
-- Una alerta se RESUELVE sola cuando el stock vuelve: el barrido marca
-- `resuelto = TRUE` y guarda `resolved_at`. Así se puede medir el tiempo
-- que un producto estuvo por debajo del mínimo, que es el número que
-- sirve para ajustar el punto de pedido.
--
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. ALERTAS DE STOCK BAJO: QUE PRODUCTO, DONDE, Y DESDE CUANDO
-- ---------------------------------------------------------------------------

ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS model_id INT REFERENCES models(id) ON DELETE CASCADE;
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS sku VARCHAR(50);
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS producto VARCHAR(200);
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS location_id INT REFERENCES locations(id) ON DELETE CASCADE;
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS faltante INT NOT NULL DEFAULT 0;
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS lead_time_days INT;
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE stock_alerts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Una alerta abierta por producto y ubicación. Es lo que hace que el
-- barrido sea idempotente: al correr cada minuto no duplica la misma
-- alerta, la actualiza.
--
-- `model_id IS NULL` (las filas de la versión anterior, sin producto) no
-- entran en el índice único: NULL no choca con NULL en Postgres.
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_alerts_modelo_ubicacion
    ON stock_alerts(model_id, location_id)
    WHERE resuelto = FALSE AND model_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_stock_alerts_modelo
    ON stock_alerts(model_id)
    WHERE resuelto = FALSE;

-- ---------------------------------------------------------------------------
-- 2. VALOR LIBRO: CUANTO VALE HOY CADA EQUIPO
-- ---------------------------------------------------------------------------
--
-- `purchase_date` y `purchase_value` son POR EQUIPO: dos notebooks del
-- mismo modelo pueden haberse comprado a precios distintos, y el valor
-- libro es de cada una.
--
-- `useful_life_months` es POR MODELO: la vida útil es del modelo, no de la
-- unidad.
--
-- `warranty_months` y `eol_date` ya existian en `models`.

ALTER TABLE devices ADD COLUMN IF NOT EXISTS purchase_date DATE;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS purchase_value NUMERIC(12, 2);

-- Un precio pagado no puede ser negativo.
ALTER TABLE devices DROP CONSTRAINT IF EXISTS ck_devices_purchase_value;
ALTER TABLE devices ADD CONSTRAINT ck_devices_purchase_value
    CHECK (purchase_value IS NULL OR purchase_value >= 0);

ALTER TABLE models ADD COLUMN IF NOT EXISTS useful_life_months INT;

ALTER TABLE models DROP CONSTRAINT IF EXISTS ck_models_useful_life;
ALTER TABLE models ADD CONSTRAINT ck_models_useful_life
    CHECK (useful_life_months IS NULL OR useful_life_months > 0);

-- El índice que usa el reporte de valor libro: los equipos con fecha de
-- compra (los únicos depreciables) ordenados por fecha.
CREATE INDEX IF NOT EXISTS idx_devices_purchase_date
    ON devices(purchase_date)
    WHERE purchase_date IS NOT NULL;
