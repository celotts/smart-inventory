-- ===========================================================================
-- 0014 - Códigos de etiqueta por producto (QR + código de barras)
-- ===========================================================================
-- Objetivo:
--   Que cada producto del maestro tenga su propia etiqueta escaneable: un QR
--   y un código de barras, que se pueden imprimir y pegar en la caja, en el
--   rack o en el estante.
--
-- QUE ES Y QUE NO ES Está ETIQUETA
-- --------------------------------
-- Es la etiqueta del PRODUCTO ("una notebook tal modelo": hay cinco en el
-- depósito). NO es la etiqueta del EQUIPO: esa ya existe, con el `qr_code` de
-- `devices` (`INV-...`) y el serial del fabricante, y se imprime desde la
-- ficha del componente.
--
-- Las dos conviven en el mismo depósito, y por eso el código del producto
-- lleva un prefijo distinto (`PRD-`): de un vistazo, un operador sabe si
-- está mirando una caja o un equipo.
--
-- POR QUE SON NULLABLES
-- ---------------------
-- Un producto que todavía no fue etiquetado puede no tener código. La columna
-- acepta NULL, y el alta del producto genera el QR y el código de barras por
-- defecto (ver `src/modules/catalog/service.py`).
--
-- POR QUE UNIQUE
-- --------------
-- Dos productos con el mismo código hacen inutil la etiqueta: el escaner
-- no puede saber a cual de los dos pertenece. El mismo motivo que en
-- `devices.qr_code`.
-- ===========================================================================

ALTER TABLE models ADD COLUMN IF NOT EXISTS qr_code VARCHAR(150) UNIQUE;
ALTER TABLE models ADD COLUMN IF NOT EXISTS barcode VARCHAR(64) UNIQUE;

-- Respaldo de los productos que ya existian: el QR deriva del SKU y el código
-- de barras ES el SKU, que es lo que ya está escrito en la caja del
-- proveedor. Un producto sin esto se quedaria sin etiqueta, y el operador no
-- tendria como distinguir dos productos parecidos.
UPDATE models
SET qr_code = 'PRD-' || sku
WHERE qr_code IS NULL AND sku IS NOT NULL;

UPDATE models
SET barcode = sku
WHERE barcode IS NULL AND sku IS NOT NULL;
