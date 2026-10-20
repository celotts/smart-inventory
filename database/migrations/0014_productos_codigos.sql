-- ===========================================================================
-- 0014 - Codigos de etiqueta por producto (QR + codigo de barras)
-- ===========================================================================
-- Objetivo:
--   Que cada producto del maestro tenga su propia etiqueta escaneable: un QR
--   y un codigo de barras, que se pueden imprimir y pegar en la caja, en el
--   rack o en el estante.
--
-- QUE ES Y QUE NO ES ESTA ETIQUETA
-- --------------------------------
-- Es la etiqueta del PRODUCTO ("una notebook tal modelo": hay cinco en el
-- deposito). NO es la etiqueta del EQUIPO: esa ya existe, con el `qr_code` de
-- `devices` (`INV-...`) y el serial del fabricante, y se imprime desde la
-- ficha del componente.
--
-- Las dos conviven en el mismo deposito, y por eso el codigo del producto
-- lleva un prefijo distinto (`PRD-`): de un vistazo, un operador sabe si
-- esta mirando una caja o un equipo.
--
-- POR QUE SON NULLABLES
-- ---------------------
-- Un producto que todavia no fue etiquetado puede no tener codigo. La columna
-- acepta NULL, y el alta del producto genera el QR y el codigo de barras por
-- defecto (ver `src/modules/catalog/service.py`).
--
-- POR QUE UNIQUE
-- --------------
-- Dos productos con el mismo codigo hacen inutil la etiqueta: el escaner
-- no puede saber a cual de los dos pertenece. El mismo motivo que en
-- `devices.qr_code`.
-- ===========================================================================

ALTER TABLE models ADD COLUMN IF NOT EXISTS qr_code VARCHAR(150) UNIQUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS barcode VARCHAR(64) UNIQUE;

-- Respaldo de los productos que ya existian: el QR deriva del SKU y el codigo
-- de barras ES el SKU, que es lo que ya esta escrito en la caja del
-- proveedor. Un producto sin esto se quedaria sin etiqueta, y el operador no
-- tendria como distinguir dos productos parecidos.
UPDATE models
SET qr_code = 'PRD-' || sku
WHERE qr_code IS NULL AND sku IS NOT NULL;

UPDATE models
SET barcode = sku
WHERE barcode IS NULL AND sku IS NOT NULL;
