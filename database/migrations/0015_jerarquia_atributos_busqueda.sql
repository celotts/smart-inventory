-- ===========================================================================
-- 0015 - Jerarquia de categorias y ubicaciones, atributos por categoria y
--        busqueda de texto completo
-- ===========================================================================
-- Objetivo:
--   1. Que las categorias y las ubicaciones formen arboles (subcategorias,
--      pasillos, estantes, bins).
--   2. Que cada categoria pueda definir sus propios atributos (caducidad, lote,
--      dimensiones, garantia, especificaciones...).
--   3. Que la busqueda sea de texto COMPLETO, y no un ILIKE que no encuentra
--      "notebook dell" cuando el producto se llama "Dell Latitude 5520".
--
-- QUE ES UNA JERARQUIA Y POR QUE LA NECESITA UN INVENTARIO
-- --------------------------------------------------------
-- Sin arbol, "Notebook" y "Notebook / i5" conviven en la misma lista y el
-- operador no puede decir "todo lo que esta en el pasillo 3" ni "los
-- portatiles de 14 pulgadas". Con arbol, el filtro por "Deposito Central"
-- trae sus pasillos y estantes, y el filtro por "Portatiles" trae sus
-- subcategorias.
--
-- POR QUE `parent_id` Y NO UNA TABLA `category_paths`
-- ---------------------------------------------------
-- El arbol es de una sola padre: una categoria cuelga de otra categoria. Un
-- "path" materializado (Ltree) es para arboles de profundidad arbitraria con
-- consultas por prefijo; aqui la profundidad es de 2 o 3 niveles y el filtro
-- por descendientes se resuelve con un CTE recursivo, que es lo que hace
-- `_ids_descendientes` en el service de locations.
--
-- POR QUE LAS UBICACIONES TIENEN `location_type`
-- ----------------------------------------------
-- "Deposito Central" y "Deposito Norte" son bodegas: tienen equipos.
-- "Pasillo 3" y "Estante A" son parte de una bodega: tambien. Pero un
-- "Sitio del cliente" es otra cosa (tiene `client_id`), y un filtro por
-- "bodega" no debe traer los equipos instalados en el cliente. El tipo
-- permite que la interfaz pregunte lo correcto en cada caso.
--
-- POR QUE EL VECTOR DE BUSQUEDA ES UNA COLUMNA GENERADA
-- -----------------------------------------------------
-- Un `tsvector` GENERATED ALWAYS ... STORED se calcula UNA vez, cuando se
-- escribe la fila, y se indexa con GIN. Es lo que hace que la busqueda sea
-- instantanea con miles de equipos, y que el indice no se pueda desincronizar
-- con el dato: Postgres lo recalcula solo.
--
-- El indice va sobre el texto que el operador REALMENTE escribe en el
-- scanner: el serial y el codigo de etiqueta del equipo, y el SKU, la marca,
-- el modelo y la variante del producto. Con "spanish" porque el operador
-- escribe en castellano: sin el diccionario, "portatil" no encuentra
-- "portátil".
-- ===========================================================================

-- --- 1. Jerarquia de categorias --------------------------------------------
ALTER TABLE categories ADD COLUMN IF NOT EXISTS parent_id INT REFERENCES categories(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);

-- --- 2. Jerarquia de ubicaciones -------------------------------------------
ALTER TABLE locations ADD COLUMN IF NOT EXISTS parent_id INT REFERENCES locations(id) ON DELETE RESTRICT;
ALTER TABLE locations ADD COLUMN IF NOT EXISTS location_type VARCHAR(20) NOT NULL DEFAULT 'WAREHOUSE';
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id);

-- CHECK del tipo: los valores que la API acepta. Un typo en el codigo
-- ("ALMACEN" en vez de "WAREHOUSE") dejaba una ubicacion sin tipo y sin
-- filtro, y el error seedia en un reporte, no en el alta.
ALTER TABLE locations DROP CONSTRAINT IF EXISTS ck_locations_type;
ALTER TABLE locations ADD CONSTRAINT ck_locations_type CHECK (
    location_type IN ('WAREHOUSE', 'AISLE', 'SHELF', 'BIN', 'CLIENT')
);

-- --- 3. Atributos personalizados por categoria -----------------------------
--
-- La DEFINICION de los atributos vive en `category_attributes`; los VALORES
-- de cada producto viven en `models.specifications` (JSONB, que ya estaba y
-- ya tenia su indice GIN).
--
-- POR QUE SEPARADAS Y NO EN LA MISMA TABLA
-- -----------------------------------------
-- La definicion es de la CATEGORIA ("un monitor tiene alto y ancho") y el
-- valor es del PRODUCTO ("este monitor mide 60x40"). Juntas, cada producto
-- tendria que volver a declarar los campos de su categoria.
CREATE TABLE IF NOT EXISTS category_attributes (
    id SERIAL PRIMARY KEY,
    category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    -- La clave es el nombre del atributo en el JSONB de valores. Es la que
    -- viaja, y por eso tiene que ser estable y corta.
    key VARCHAR(60) NOT NULL,
    -- La etiqueta es lo que ve el operador: "Fecha de caducidad".
    label VARCHAR(120) NOT NULL,
    -- text | number | date | boolean | select
    data_type VARCHAR(20) NOT NULL DEFAULT 'text',
    -- Para `select`: la lista de valores permitidos, en JSON.
    options JSONB,
    unit VARCHAR(20),
    is_required BOOLEAN DEFAULT FALSE,
    -- product | device: un atributo puede ser del producto (el modelo) o de
    -- cada equipo (el serial de la etiqueta del fabricante).
    applies_to VARCHAR(20) NOT NULL DEFAULT 'product',
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_category_attributes UNIQUE (category_id, key),
    CONSTRAINT ck_category_attributes_type CHECK (
        data_type IN ('text', 'number', 'date', 'boolean', 'select')
    ),
    CONSTRAINT ck_category_attributes_applies CHECK (
        applies_to IN ('product', 'device')
    )
);

CREATE INDEX IF NOT EXISTS idx_category_attributes_category ON category_attributes(category_id);

-- --- 4. Busqueda de texto completo -----------------------------------------
--
-- El vector del EQUIPO: lo que el operador tiene en la mano, el serial y el
-- codigo de la etiqueta.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('spanish', coalesce(serial_number, '') || ' ' || coalesce(qr_code, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_devices_search ON devices USING GIN (search_vector);

-- El vector del PRODUCTO: SKU, marca, modelo, variante y sus dos codigos de
-- etiqueta.
ALTER TABLE models ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'spanish',
            coalesce(sku, '') || ' ' ||
            coalesce(brand, '') || ' ' ||
            coalesce(model, '') || ' ' ||
            coalesce(variant, '') || ' ' ||
            coalesce(qr_code, '') || ' ' ||
            coalesce(barcode, '')
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_models_search ON models USING GIN (search_vector);
