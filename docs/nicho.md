# El nicho: qué es esta app y qué no es

Una frase, para que cada pedido nuevo tenga contra qué medirse:

> **Control de activos de TI y Service Desk para empresas de servicios.**
> Equipos que se instalan en clientes, se mueven, se rompen, se reparan y
> a veces se dan de baja — con fecha, con responsable y con plazo.

---

## 1. Por qué está escrita (y por qué importa)

Una app sin un nicho declarado se diluye: cada pedido nuevo entra como "y
también necesitamos..." y al final la app hace de todo regular. Este
documento es el filtro. Cuando entre una feature nueva, la primera pregunta
es: **¿esto lo pide el nicho o se parece a un ERP?**

---

## 2. Los tres trabajos de la app

### 2.1 Depósito (`módulo Inventario`, azul)

El activo está en la base, en el taller o en el cliente. Se entra, se sale,
se transfiere, se cuenta. Todo con serial, con ubicación y con responsable.

- SKU y etiquetas propias (QR + Code 128) por producto.
- Serial del fabricante por equipo, y una etiqueta interna (`INV-…`) para los
  proveedores que reciclan seriales.
- Jerarquía de ubicaciones: almacén → pasillo → estante → bin → cliente.
- Movimientos con tipología configurable, idempotencia y geo-validación.
- Conteo físico con ajuste (lo que el depositero cuenta contra lo que el
  sistema dice).

### 2.2 Taller y Service Desk (`módulo Service Desk`, ámbar)

El activo se rompió y alguien tiene que resolverlo **en un plazo**.

- Bandeja de resolutores: los casos reportados que nadie tomó.
- SLA por severidad, con avisos de vencimiento y escalamiento al
  supervisor.
- Aviso al que reportó, cuando el caso se toma y cuando se resuelve.
- Cierre con **destino del equipo**: taller, reparado o daño total. Un caso
  no se cierra con un texto: se cierra diciendo qué pasó con el equipo.
- Orden de trabajo por equipo, con la baja certificada cuando el daño es
  total.

### 2.3 El activo y su valor

- Mantenimiento preventivo por modelo, con checklist y vencimientos.
- Garantía y fin de vida.
- **Valor libro**: lo que el equipo vale hoy, no lo que costó.

---

## 3. Lo que esta app NO es (y no va a ser)

Esto no es una lista de pendientes: son decisiones.

| No es | Por qué |
|---|---|
| Un ERP | No factura, no emite comprobantes, no hace contabilidad, no tiene RRHH. |
| Un WMS de depósito | No maneja lotes, ni caducidad por unidad, ni FEFO/LIFO. Un equipo de TI no caduca: se amortiza o se da de baja. |
| Un e-commerce | No hay carrito, ni pasarela de pago, ni catálogo público. |
| Un sistema de tickets genérico | No hay chat, ni adjuntos, ni portal del cliente. El caso es de un equipo roto. |
| Un help desk de software | El objeto del caso es un **activo**, y el cierre tiene un efecto en el stock. |

**La regla práctica**: si un pedido habla de *mercadería* (lotes, caducidad,
FEFO, EAN-13 propio, pesaje o lectura de codigos de barras), es de un ERP y va a otro lado. Si habla
de *activos y de quien los responde*, es de esta app.

---

## 4. Cómo se compara con lo comercial

| Si lo que se busca es… | La alternativa de mercado | Por qué |
|---|---|---|
| Facturación, contabilidad, e-commerce, integrations | Odoo, Zoho Inventory, InveTree | Son ERP: hacen de todo, y por eso hacen de todo regular. |
| Activo fijo + tickets, y nada más | **Snipe-IT** (simple, activo) | Comparable de verdad. Snipe es más chico y más simple. |
| Activo + tickets + inventario + contratos | **GLPI** (con FusionInventory) | Es la alternativa más completa de la categoría. |
| WMS de depósito con conteo cíclico | Un WMS dedicado | Esta app no va a ser un WMS. |

**Cuándo dejar de construir y mirar alternativas**: si el nicho termina
siendo e-commerce o restaurantes, este código no es un buen punto de
partida (está modelado alrededor de un activo instalado en un cliente, no
alrededor de una mercadería que entra y sale). Si el nicho sigue siendo
"activos de TI para empresas de servicios", seguir construyendo es lo
correcto: las alternativas de la categoría no tienen ni taller, ni SLA, ni
cierre con destino de equipo.

---

## 5. El filtro de pedidos

Tres preguntas antes de aceptar un pedido nuevo:

1. **¿Es del nicho?** (sección 3). Si es de mercancía, es de otro producto.
2. **¿El dato existe hoy en la app?** Si hay que inventar una tabla nueva,
   el costo es real y la decisión tiene que ser consciente.
3. **¿Puedo probarlo en el navegador hoy?** Una feature que no se puede
   ver funcionando no entra.

---

## 6. La hoja de ruta, en el nicho

Lo que falta, ordenado por lo que este nicho pide primero (no por
dificultad):

| # | Qué | Por qué, en este nicho |
|---|---|---|
| 1 | Conteo físico con ajuste | El depositero cuenta contra el sistema; sin sesión de conteo, la diferencia se corrige a mano y se rompe la trazabilidad. |
| 2 | Depreciación y valor libro | "¿Cuánto vale hoy esta flota?" es la pregunta que hace el cliente, y es la que la contabilidad pide. |
| 3 | Aviso automático de stock bajo | Hoy la alerta se ve al abrir el tablero; el aviso tiene que llegar solo. |
| 4 | Cola offline de escaneos | El depósito sin cobertura es el caso real, y la idempotencia ya está lista. |
| 5 | Atributos por equipo en la ficha | El modelo de datos ya los soporta; falta la pantalla. |
| 6 | EAN-13 | Solo cuando el cliente traiga un GTIN real de GS1. |
| — | LIFO, FEFO, lotes por unidad | **Fuera**: son de mercancía perecedera. |
