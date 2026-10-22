-- ===========================================================================
-- MIGRACION 0020: LOS ATRIBUTOS POR EQUIPO
-- ===========================================================================
--
-- EL HUECO QUE ESTA TABLA CIERRA
-- -------------------------------
-- Hoy el modelo de datos tiene DOS cosas y le falta la tercera:
--
--   1. `category_attributes`: la DEFINICION de los campos de una categoria
--      ("Monitor" define "alto" y "ancho"; "Notebook" define "procesador" y
--      "ram"). Con su tipo, sus opciones, si es obligatorio y su unidad.
--
--   2. `models.specifications`: los VALORES de esos campos, pero POR
--      MODELO. Dos notebooks del mismo modelo comparten la especificacion.
--
--   3. FALTA: los valores POR EQUIPO.
--
-- Y esa tercera cosa importa. "La notebook de la reception tiene 16 GB de
-- RAM" y "la notebook del laboratorio tiene 8 GB" son el mismo modelo con
-- valores distintos. Con los valores solo por modelo, la segunda no se
-- puede guardar: o se miente en la ficha, o se pierde el dato.
--
-- Tambien es lo que hace falta para los equipos que NO son de TI: una
-- camara IP con su direccion de gestion, un NVR con su cantidad de
-- canales, un UPS con su autonomia. Todos comparten modelo (la camara X de
-- la marca Y) y ninguno comparte valores.
--
-- POR QUE UNA TABLA Y NO UN JSONB EN `devices`
-- -------------------------------------------
-- Un JSONB en `devices` seria mas rapido de escribir. Pero:
--
--   - no se puede indexar (y la busqueda por atributo es justo lo que se
--     va a querer: "todas las camaras con la IP 10.0.0.5"),
--   - no se puede validar el tipo (el valor de "ram" tiene que ser un
--     numero, y el JSONB no lo sabe),
--   - y el valor repetido en 5000 filas se guarda 5000 veces.
--
-- Con una tabla, el indice es un indice, el tipo se valida contra la
-- definicion de la categoria, y el valor repetido se deduplica con la
-- tabla de valores posibles.
--
-- EL TIPO
-- -------
-- `valor` es JSONB y no una columna por tipo: el tipo de cada atributo lo
-- define `category_attributes.data_type` (que ya existe), y el valor se
-- guarda en la forma que corresponde a ese tipo (numero, texto, booleano,
-- fecha, lista). Asi agregar un tipo de atributo nuevo no requiere una
-- migracion.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS device_attributes (
    id SERIAL PRIMARY KEY,
    device_id INT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,

    -- La clave del atributo: la misma que en `category_attributes.key`
    -- ("ram", "ip_gestion", "canales").
    key VARCHAR(60) NOT NULL,

    -- El valor, en la forma que corresponde al tipo del atributo.
    valor JSONB,

    -- Cuando se midio o se cargo. Es lo que permite decir "el valor de la
    -- RAM es de marzo, no de hoy".
    medido_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Un atributo, una vez por equipo.
    CONSTRAINT uq_device_attributes UNIQUE (device_id, key)
);

-- La busqueda: "todos los equipos con el atributo X" y "el equipo con el
-- valor V del atributo X".
CREATE INDEX IF NOT EXISTS idx_device_attributes_key
    ON device_attributes(key, device_id);

-- La busqueda por VALOR. Es un GIN sobre el JSONB, que es lo que permite
-- preguntar "todas las camaras con la IP 10.0.0.5" sin que sea un
-- escaneo de la tabla.
CREATE INDEX IF NOT EXISTS idx_device_attributes_valor
    ON device_attributes USING GIN(valor jsonb_path_ops);

-- El equipo: sus atributos, en una sola consulta.
CREATE INDEX IF NOT EXISTS idx_device_attributes_device
    ON device_attributes(device_id);

-- ---------------------------------------------------------------------------
-- LOS VALORES POSIBLES
-- ---------------------------------------------------------------------------
--
-- Un atributo con opciones ("estado: Nuevo / Usado / Roto") tiene una
-- lista corta de valores posibles. Escribirlos en la definicion del
-- atributo (el JSONB `options` que ya existe) es comodo, pero no permite
-- saber DEJA LOS EQUIPOS usan cada opcion ("cuantos equipos estan rotos").
--
-- Con una tabla, esa pregunta es un GROUP BY.
--
-- El `key` de la opcion es el MISMO que el del atributo, y el valor es el
-- que se guarda en `device_attributes.valor`.
CREATE TABLE IF NOT EXISTS attribute_values (
    id SERIAL PRIMARY KEY,
    category_attribute_id INT NOT NULL REFERENCES category_attributes(id) ON DELETE CASCADE,

    -- El valor, como texto. Es la forma en que se muestra y en la que se
    -- busca.
    value VARCHAR(120) NOT NULL,

    -- Un codigo estable, para el caso de que el texto cambie ("Usado" pasa
    -- a "Usado (reacondicionado)"): el valor guardado en el equipo es el
    -- codigo, no el texto.
    code VARCHAR(60) NOT NULL,

    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,

    CONSTRAINT uq_attribute_values UNIQUE (category_attribute_id, code)
);

CREATE INDEX IF NOT EXISTS idx_attribute_values_attr
    ON attribute_values(category_attribute_id, sort_order);
