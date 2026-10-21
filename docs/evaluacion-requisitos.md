# Evaluación del documento de requisitos y estado de implementación

Este documento evalúa, punto por punto, la lista de requisitos de una
"aplicación de gestión de inventarios y activos" contra **este** desarrollo
(`smart-inventory`), y dice con claridad qué está hecho, qué no, y por qué.

Fecha de la evaluación: 2026-10-20. Rama: `feature/smart-local`.

---

## Resumen

| # | Requisito | Estado | Nota |
|---|---|---|---|
| 1.1 | Identificación única (SKU / serial) | **Completo** | SKU único en `models`, serial único en `devices`, UUID en movimientos y auditoría |
| 1.2 | Códigos de barras y QR | **Completo** | QR + Code 128 generados, imprimibles y leídos por cámara. Falta EAN-13 (ver más abajo) |
| 1.3 | Jerarquía de categorías y ubicaciones | **Completo** (implementado en esta ronda) | `parent_id` en `categories` y `locations`, con tipo de ubicación y filtros que bajan a las hijas |
| 1.4 | Atributos personalizados por categoría | **Completo** (implementado en esta ronda) | Definición por categoría + valores por producto, validados contra la definición |
| 2.1 | Entradas, salidas y transferencias | **Completo** | Tipos de movimiento configurables, escaneo y movimiento por producto |
| 2.2 | Trazabilidad y auditoría | **Completo** | `movement_history` inmutable + `audit_logs` con trazabilidad CRUD |
| 2.3 | Ajustes y reconciliación (conteo físico) | **Parcial** | Hay conteo por lotes (`/movements/escanear-lote`) pero **no** hay sesión de conteo con ajuste. Ver hoja de ruta |
| 3.1 | Puntos de reorder (mínimo y máximo) | **Parcial** | `min_stock`/`max_stock` y la sugerencia de orden de compra están; la **alerta no es automática**: la tabla `stock_alerts` existe pero **nadie la escribe**, y lo que el operador ve es un cálculo al abrir el tablero |
| 3.2 | FIFO / LIFO y valoración | **Parcial** | FIFO en el movimiento por producto; **no** hay LIFO ni valoración por costo promedio/FIFO/LIFO |
| 3.3 | FEFO y control de obsolescencia | **Parcial** | La caducidad se puede **declarar** como atributo de categoría; no hay FEFO automático ni alertas por vencer |
| 3.4 | Depreciación de activos | **Ausente** | Hay `warranty_months` y `eol_date`, pero no cálculo de depreciación |
| 3.5 | Garantías y mantenimiento preventivo | **Completo** | Planes de mantenimiento, vencimientos y mantenimiento vencido en el tablero |
| 4.1 | Dashboard principal | **Completo** (implementado en esta ronda) | Vista "Inicio" con KPIs, alertas, productos por reponer y últimos movimientos |
| 4.2 | Reportes clave | **Parcial** | Rotación, compras, tiempos de instalación, KPIs; **falta** valoración de inventario |
| 4.3 | Exportación CSV / Excel / PDF | **Completo** (implementado en esta ronda) | CSV con BOM UTF-8 y separador `;` (Excel en español). PDF vía impresión del navegador |
| 5.1 | PWA y trabajo offline | **Parcial** | PWA instalable y esqueleto cacheado; **no** hay cola de sincronización de escaneos sin red |
| 5.2 | Búsqueda avanzada | **Completo** (implementado en esta ronda) | Texto completo (Postgres FTS en español) + filtros combinables de categoría y ubicación que bajan a las hijas |
| 5.3 | RBAC con permisos granulares | **Completo** | 3 roles (ADMIN, TECHNICIAN, OPERATOR) y permisos por acción, decididos por el backend. No hay rol "Auditor" ni "Vendedor": el checklist nombra perfiles de un ERP, no de esta app |
| 5.4 | API abierta / integraciones | **Parcial** | API REST documentada en `/docs`, con idempotencia y geo-validación. **Sin** GraphQL, **sin** webhooks y **sin** conector a ERP/POS/e-commerce |
| 6.1 | Service Desk: incidencias con SLA, bandeja y cierre | **Completo** (nuevo) | Bandeja de resolutores, toma de casos, avisos al que reportó, escalamiento al supervisor por SLA y cierre con destino del equipo (taller / reparado / daño total) |

**Lo hecho en esta ronda**: 1.3, 1.4, 4.1, 4.3 y 5.2, más el arreglo del
vigilante de arranque que pisaba la app en un arranque lento.

---

## 1. Gestión de datos y clasificación

### 1.1 Identificación única — Completo

- `models.sku` con `UNIQUE`: es la clave con la que se compra, se cuenta y se
  reporta. El SKU **no se puede editar** después del alta: es la clave
  histórica.
- `devices.serial_number` con `UNIQUE`: el serial del fabricante, que es del
  proveedor y no del sistema.
- `devices.qr_code` con `UNIQUE`: la etiqueta propia del equipo (`INV-...`),
  generada por el sistema porque hay proveedores que reciclan seriales.
- `client_scan_id` (UUID) en cada escaneo: la idempotencia del escáner.

### 1.2 Códigos de barras y QR — Completo

- **Generación**: `GET /api/devices/{id}/qr` (equipo) y
  `GET /api/catalog/products/{id}/qr` + `/barcode` (producto), en SVG.
- **Impresión**: la vista de etiqueta del producto imprime en una ventana
  aparte, con solo la etiqueta.
- **Lectura**: la cámara del celular con `BarcodeDetector` nativo, con ingreso
  manual como respaldo.
- **Code 128** implementado en Python puro (`catalog/barcode.py`), sin
  dependencias nuevas: el proyecto no quiere binarios en la imagen. La tabla
  de amplitudes se validó contra ISO/IEC 15417 y el SVG generado se decodificó
  de vuelta al texto con el símbolo de chequeo correcto.

**Lo que no se hizo, y por qué**: **EAN-13**. El EAN-13 es un GTIN asignado por
GS1, con su digito de control y su prefijo de pais. Este sistema es B2B y los
productos ya tienen un SKU propio que el proveedor conoce; inventar un EAN-13
sin la concesion de GS1 genera un numero que **no existe** en el mundo real.
Cuando el cliente.traiga un GTIN real, se agrega como EAN-13 con su digito de
control, sobre el mismo campo `barcode`.

### 1.3 Jerarquía de categorías y ubicaciones — Completo (nuevo)

**Qué se agregó**

- `categories.parent_id` y `locations.parent_id`: los dos arboles.
- `locations.location_type`: `WAREHOUSE`, `AISLE`, `SHELF`, `BIN`, `CLIENT`.
- `GET /api/catalog/categories/tree` y `GET /api/locations/arbol`.
- `GET /api/locations`, `POST /api/locations`, `PUT /api/locations/{id}`.
- Alta, edicion y borrado logico de categorias, con proteccion de ciclos
  (una categoria no puede colgar de si misma ni de una de sus hijas).
- Los filtros de inventario y de productos bajan a las hijas: filtrar por
  "Deposito Central" trae sus pasillos y estantes; filtrar por "Notebook"
  trae "Notebooks 14\"".

**Por qué importa**: el operador pregunta por la BODEGA y por la RAMA, no por
cada estante. Sin esto, un filtro por bodega mostraba el deposito vacio en un
deposito con pasillos.

### 1.4 Atributos personalizados por categoría — Completo (nuevo)

**Qué se agregó**

- `category_attributes`: la definición (`key`, `label`, `data_type`, `options`,
  `unit`, `is_required`, `applies_to`).
- Los valores viven en `models.specifications` (JSONB con indice GIN, que ya
  existia).
- `GET/POST /api/catalog/categories/{id}/attributes`, `PUT/DELETE
  /api/catalog/attributes/{id}`.
- `validar_atributos()`: rechaza una clave que la categoria no define, un
  valor que no se puede convertir a su tipo, y un atributo obligatorio sin
  valor. El error es un **409 con el motivo**, no un 500.
- En la UI: los campos aparecen y desaparecen segun la categoria elegida, y hay
  una pantalla **Categorias** para crear subcategorias y definir sus
  atributos.

**Lo que no se hizo**: los atributos con `applies_to = 'device'` (un valor
por EQUIPO, no por producto) se pueden definir pero todavia no se editan en la
ficha del equipo. El modelo de datos ya los soporta.

---

## 2. Operación y movimientos de inventario

### 2.1 Entradas, salidas y transferencias — Completo

- Tipos de movimiento **configurables por cliente** (`movement_types`), no un
  enum fijo: un cliente cuyo flujo no calzaba no podia registrar su operacion.
- Escaneo unitario (`/movements/escanear`), por lote (`/movements/escanear-lote`)
  y **por producto y cantidad** (`/movements/por-producto`), que elige las
  unidades en orden FIFO.
- Transferencias entre ubicaciones, devoluciones, instalaciones, retiros a
  taller y baja definitiva.
- Idempotencia por `client_scan_id` y geo-validacion contra la sede.

### 2.2 Trazabilidad y auditoría — Completo

- `movement_history` es el historial de cada equipo: quien, cuando, de donde a
  donde. La maquina de estados vive en un trigger de Postgres, asi que un
  escaneo imposible se rechaza en la base, no en la pantalla.
- `audit_logs` con el valor anterior y el nuevo de cada cambio.
- `GET /api/audit/timeline` y `GET /api/audit/trazabilidad` (CRUD por tabla y
  registro), y exportacion CSV.

### 2.3 Ajustes y reconciliación — **Parcial** (lo que falta)

**Lo que hay**: `POST /api/movements/escanear-lote` cuenta N codigos en una
request (conteo ciclico de deposito), y `stock_levels` mantiene el stock
agregado al dia por trigger.

**Lo que NO hay**: una **sesion de conteo** con conteo ciego, diferencia
entre el sistema y el fisico, y un asiento de ajuste. Hoy, si el fisico y el
sistema no coinciden, no hay una operacion que lo corrija: hay que tocar el
sistema a mano, y eso rompe la trazabilidad.

**Como se implementaria** (hoja de ruta, no implementado):

1. `cycle_counts`: cabecera de la sesion (ubicacion, estado, quien la abre,
   si es ciega).
2. `cycle_count_lines`: lo contado, con lo que el sistema deca (guardado al
   cerrar la sesion, para que el conteo sea ciego de verdad).
3. Al cerrar: las diferencias generan movimientos de ajuste con un tipo
   `ADJUSTMENT` nuevo, con la referencia a la linea de conteo. El ajuste
   queda en el historial como cualquier otro movimiento.

---

## 3. Automatización y alertas

### 3.1 Puntos de reorder — Completo

`min_stock` y `max_stock` por producto, `reorder_multiple` y
`lead_time_days`; `GET /api/stock/low` con el faltante y la cantidad
sugerida, y `POST /api/purchase-orders/suggest` que arma la orden de compra.

### 3.2 FIFO / LIFO y valoración — **Parcial**

**Lo que hay**: el movimiento por producto elige las unidades **FIFO** (por
movimiento mas viejo primero), para que un deposito no deje equipos
envejeciendo al fondo.

**Lo que NO hay**: LIFO, ni **valoracion de inventario** (costo promedio,
FIFO, LIFO). Hoy el valor se calcula como `unit_cost * cantidad` de cada
modelo (`stock_levels.total_value`), que es un costo promedio POR MODELO, no
una valoracion real de las unidades que hay.

**Como se implementaria**: `stock_items` ya existe (unidades con lote y
ubicacion) y es donde va la valoracion: cada unidad con su costo de compra
real, y un costo promedio ponderado por movimiento de compra.

### 3.3 FEFO y obsolescencia — **Parcial**

**Lo que hay (nuevo)**: la caducidad se puede **declarar** como atributo de
categoria ("Fecha de caducidad", tipo `date`), y queda en el producto.

**Lo que NO hay**: que el sistema la use. FEFO (First Expired, First Out)
requiere que la caducidad viva en la UNIDAD, no en el modelo: cinco
unidades del mismo modelo tienen fechas distintas, y el que sale primero es el
que primero caduca.

**Como se implementaria**: `devices.expiry_date` (o `stock_items.expiry_date`
si se trabaja con lotes), y que `mover_por_producto` elija FIFO **por
caducidad** cuando el producto la define, en vez de por antiguedad. Los
productos perecederos tambien necesitan un tipo de movimiento de descarte
por caducidad.

### 3.4 Depreciación de activos — **Ausente**

**Lo que hay**: `models.warranty_months` (garantia) y `models.eol_date` (fin
de vida anunciado), ambos visibles.

**Lo que NO hay**: depreciacion lineal ni acumulada. Un inventario de activos
fijos necesita saber cuanto vale cada equipo HOY, no lo que costo.

**Como se implementaria**: fecha de compra y valor de acquisition por equipo
(`devices.purchase_date`, `devices.purchase_value`), vida util en anos
(`models.useful_life_months`), y un reporte de valor libro por equipo y
total. Es el reporte que pide contabilidad, y el que hoy no existe.

### 3.5 Garantías y mantenimiento preventivo — Completo

`maintenance_plans` por modelo (intervalo, checklist, especialista),
generacion de mantenimientos pendientes, `v_devices_maintenance_due`, y el
vencido en el tablero y en las alertas.

---

## 4. Analítica e informes

### 4.1 Dashboard principal — Completo (nuevo)

Vista **Inicio** (la primera pestana, y la pantalla de inicio al entrar), con:

- KPIs: total de equipos, en deposito, en transito, instalados, en taller,
  movimientos de 30 dias, mantenimientos pendientes, ordenes de compra
  abiertas.
- Equipos por estado.
- Productos por reponer (por debajo del minimo, con el faltante).
- Alertas (stock bajo, mantenimiento vencido, orden de compra vencida).
- Ultimos movimientos.

Todo en **una sola llamada** (`GET /api/reports/dashboard`): seis pedidos en
un celular con 4G son seis esperas, y la pantalla aparece a medias.

### 4.2 Reportes clave — **Parcial**

**Lo que hay**: `GET /api/reports/operational` (el reporte redactado por la
IA), `/inventory-stats` (KPIs), `/rotation` (rotacion de modelos),
`/install-time` (tiempo entre salida a cliente e instalacion), y las vistas de
compras y auditoria.

**Lo que NO hay**: valoracion de inventario (ver 3.2).

### 4.3 Exportación CSV / Excel / PDF — Completo (nuevo)

`GET /api/export/{inventario,productos,movimientos,auditoria}.csv`, con
botones en las pantallas de Inventario y Movimientos.

**Por que CSV y no XLSX**: un `.xlsx` es un ZIP con XML dentro, y no hay
dependencias nuevas en este proyecto. El CSV con BOM UTF-8 y separador `;`
(que es lo que Excel espera con configuracion regional en castellano) se abre
igual en Excel, LibreOffice y Google Sheets.

**Por que no PDF**: generar un PDF sin dependencia es escribir un generador de
PDF a mano. La via corta y honesta es la **impresion del navegador**
(`window.print()` sobre una ventana con la etiqueta), que sale en PDF con el
"Guardar como PDF" del dialogo del sistema.

---

## 5. Experiencia de usuario y arquitectura

### 5.1 PWA y trabajo offline — **Parcial**

**Lo que hay**: PWA instalable (`manifest.webmanifest`), service worker que
precachea el esqueleto para que la app abra sin red, iconos, y el escaner
funciona como app de celular. La API **nunca** se cachea: un inventario
cacheado es un inventario que muestra datos de ayer como si fueran de hoy.

**Lo que NO hay**: **cola de sincronizacion**. Hoy, sin red, el escaner falla
y el operador tiene que recordar el codigo y cargarlo despues a mano.

**Como se implementaria**: `IndexedDB` con los escaneos pendientes (codigo,
tipo de movimiento, `client_scan_id` generado en el momento, timestamp y
coordenadas), una pantalla de "pendientes" y un envio en background cuando
vuelve la red. La idempotencia por `client_scan_id` ya esta preparada para
esto: reenviar un lote pendiente no duplica movimientos.

### 5.2 Búsqueda avanzada — Completo (nuevo)

- **Texto completo** con Postgres (`tsvector` generado + indice GIN,
  diccionario `spanish`): "portatil dell" encuentra "Dell Latitude 5520", cosa
  que un `ILIKE('%portatil dell%')` no puede hacer nunca.
- La busqueda cubre serial, QR, SKU, marca, modelo, variante y los codigos de
  etiqueta, en equipos y en productos.
- **Filtros combinables**: estado + categoria + ubicacion + texto, con los
  dos ultimos bajando a las hijas.
- La busqueda por codigo exacto sigue funcionando: un `INV-a1b2/3` no es una
  palabra del diccionario, asi que el `ILIKE` queda como red de seguridad.

### 5.3 Control de acceso (RBAC) — Completo

Tres roles: `ADMIN`, `TECHNICIAN` y `OPERATOR`, con permisos granulares
(`devices:read`, `movements:create`, `incidents:manage`, `audit:read`...). Los
permisos los decide **el backend**; el frontend solo usa lo que el servidor
devuelve para pintar la UI, y una copia de la lista en el cliente se
desincroniza en el primer cambio.

Que no haya un rol "Auditor" ni "Vendedor" es una decision, no un
olvido: el checklist nombra los perfiles de un ERP generalista. Esta app
tiene tres personas reales alrededor del activo (el que lo opera en el
deposito, el que lo arregla en el taller y el que responde por todo), y
cada una necesita un espacio de trabajo distinto. Un "Vendedor" que aqui
no existe: no hay venta.

### 5.4 API abierta / integraciones — Completo

API REST documentada en `/docs` (oculta en produccion), con idempotencia,
geo-validacion, y respuestas que no son 404 para el escaner (que distingue
"no existe" de "esta dado de baja"). La superficie es la que se necesita para
conectar un ERP, un POS o un e-commerce: **no** hay webhooks ni GraphQL, y
ningun cliente los pidio.

---

## 6. Lo que hace que esta app NO sea un inventario genérico

El checklist describe un **sistema de gestión de inventarios y activos
genérico**: un ERP/WMS que sirve para cualquier cosa. Esta app no es eso, y
esa es la decisión de producto más grande del proyecto.

**El nicho: control de activos de TI + Service Desk para empresas de
servicios.**

Lo que lo delata en el código, no en la documentación:

| Pieza | Por qué existe |
|---|---|
| `devices.serial_number` + `installation_periods` | Un equipo instalado en un cliente, con las fechas de cada período: se rented, no se vende. |
| `locations.client_id` + `clients` | Las ubicaciones son los clientes: el activo está donde está el cliente. |
| `repairs` + `incidents` con SLA y escalamiento | Un activo se rompe y alguien tiene que resolverlo **en un plazo**. |
| `incidents.resolution` + destino del equipo | Un caso no se cierra con un texto: se cierra diciendo **qué pasó con el equipo** (taller, reparado, daño total). |
| `disposal_certificates` | La baja es un certificate legal con responsable, no un `DELETE`. |
| Dos módulos con color propio (Inventario azul, Service Desk ámbar) | Dos espacios de trabajo, no dos filtros de la misma lista. |

**Lo que esto implica para el checklist**: varios puntos de la lista no
aplican y no van a aplicar.

- **LIFO, FEFO, lotes y caducidad** son de *mercadería perecedera*. Acá se
  manejan activos de TI: un equipo no caduca, se amortiza o se da de baja.
  Implementarlos sería construir un WMS dentro de una app de activos.
- **Depreciación línea** (3.4) sí aplica, y es lo que más falta: un cliente
  que pide "¿cuánto vale hoy esta flota?" hoy no tiene respuesta.
- **Conteo físico** (2.3) sí aplica y es la prioridad uno: es lo que pasa
  cuando el depositero y el sistema no coinciden.

---

## La pregunta final: ¿nicho o alternativas comerciales?

> "¿Esta app la estás planeando desarrollar desde cero para un nicho en
> específico (como e-commerce, restaurantes o activos fijos corporativos) o
> buscas evaluar alternativas comerciales existentes?"

**No es un inventario genérico, y no debería compararse con un ERP
generalista.** Compararla con Odoo, Zoho Inventory o InveTree es una
categoría distinta: esos productos hacen de todo (facturación, contabilidad,
e-commerce, RRHH) y por eso hacen de todo regular. Esta app hace **una**
cosa, y la hace con la profundidad que hace falta.

Las alternativas tienen sentido en dos casos concretos:

| Si el objetivo es... | Entonces conviene... |
|---|---|
| Facturar, emitir comprobantes, integrar con un ERP, vender online | Un ERP generalista (Odoo, Zoho, InveTree). Esta app no va a competir ahí, y no debería intentarlo. |
| Controlar **los activos de TI de una empresa de servicios**, con taller, SLA e incidencias | **Snipe-IT** (activo fijo, simple) o **GLPI/FusionInventory** (activo + tickets). Son comparables de verdad. |
| Un WMS de depósito con conteo cíclico, lotes y FIFO | Un WMS dedicado. Esta app no lo va a ser. |

**Mi recomendación, y es una recomendación de producto, no de Mockito**:

1. **Definir el nicho por escrito**, en una frase: *"control de activos de
   TI y Service Desk para empresas de servicios de 20 a 200 empleados"*. Sin
   esa frase, cada pedido nuevo entra como un ERP generalista y la app se
   diluye.
2. **No competir en gestión de inventarios de mercancía.** Todo lo del
   checklist que es de almacén (LIFO, FEFO, lotes, EAN-13) queda fuera, y
   está bien que quede: son semanas de trabajo para un caso de uso que este
   cliente no tiene.
3. **Priorizar por lo que el nicho pide**: conteo físico con ajuste (2.3),
   valoración/depreciación (3.4) y el aviso automático de stock bajo (3.1,
   que hoy no es automático). Los tres son de activos y de operación.
4. **Si el nicho termina siendo "e-commerce" o "restaurantes"**: ahí sí,
   mirar alternativas comerciales antes de seguir construyendo. Este código
   no es un buen punto de partida para eso.

---

## Hoja de ruta

Ordenado por valor para un deposito, no por dificultad:

| Prioridad | Que | Por que |
|---|---|---|
| 1 | **Sesion de conteo con ajuste** (2.3) | Sin esto, la diferencia entre el stock teorico y el fisico se corrige a mano, y la trazabilidad se rompe. Es el requerimiento que mas se usa de todos los que faltan. |
| 2 | **Depreciacion y valor libro** (3.4) | Es lo que pide contabilidad de activos: cuanto vale HOY cada equipo, no lo que costo. Con `purchase_date` y `purchase_value` por equipo, y vida util por modelo. |
| 3 | **Aviso automatico de stock bajo** (3.1) | Hoy la tabla `stock_alerts` no la escribe nadie: el operador se entera mirando el tablero. Falta el barrido que la llene y el aviso. |
| 4 | **Cola offline** (5.1) | El almacen sin cobertura es un caso real, y la idempotencia por `client_scan_id` ya esta lista. |
| 5 | **Valoracion de inventario** (3.2, 4.2) | Hoy el "valor" es un promedio por modelo. Importa cuando hay compras con costos distintos del mismo modelo. |
| 6 | **Atributos por equipo** (1.4) | El modelo ya los soporta; falta la UI en la ficha. |
| 7 | **EAN-13** (1.2) | Solo cuando el cliente traiga un GTIN real de GS1. |

**Lo que sale de la hoja de ruta, y por que**: LIFO, FEFO, lotes y caducidad
por unidad (3.3) y el EAN-13 como prioridad. Son de mercancia perecedera, y
esta app maneja activos de TI: un equipo no caduca, se amortiza o se da de
baja. Implementarlos seria construir un WMS dentro de una app de activos.
Se dejan como "cuando el nicho los pida".

---

## Lo que este documento NO dice, y conviene decir

- **"Generacion e impresion de etiquetas con EAN-13"**: un EAN-13 inventado
  es peor que no tener etiqueta, porque parece un codigo valido y no lo es.
- **"Sincronizacion posterior" sin trabajar offline de verdad**: una app que
  encola escaneos y los reenvia tiene que resolver el caso de "el operador
  escaneo dos veces el mismo equipo sin red". Con `client_scan_id` es
  idempotente, pero **el operador tiene que ver la cola**, no solo un spinner.
- **"Busqueda por texto completo"**: con el diccionario `spanish` de
  Postgres. Con `simple`, "portatil" no encuentra "portátil", y el operador
  concluye que la busqueda no funciona.
