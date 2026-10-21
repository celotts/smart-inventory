/**
 * El idioma de la app: espanol (el de por defecto) o ingles.
 *
 * QUE NO TOCA
 * -----------
 * La base de datos. El idioma es de la INTERFAZ: los datos (los SKUs, los
 * nombres de los equipos, los títulos de los casos) son los que son, en el
 * idioma en que están. Traducir un nombre de producto seria inventar
 * información que no existe.
 *
 * COMO FUNCIONA
 * -------------
 * El código está escrito en espanol, que es el idioma de origen. El
 * diccionario traduce las FRASES COMPLETAS: la clave es el texto en
 * espanol y el valor es el ingles.
 *
 *   traducir('Guardar producto')  ->  'Save product'   (en ingles)
 *   traducir('Guardar producto')  ->  'Guardar producto' (en espanol)
 *
 * La clave es la frase, y no un código artificial ("g.save"), por una razón
 * concreta: si el código cambia, el texto queda en el idioma viejo y nadie
 * se entera. Con la frase como clave, un texto nuevo (que no está en el
 * diccionario) se ve en espanol y avisa en la consola: el operador nunca
 * ve un texto a medio traducir, y el que falte se nota.
 *
 * DONDE SE APLICA
 * ---------------
 * - `crear()` (el helper que pinta los nodos): traduce el texto, el
 *   placeholder, el title y el aria-label.
 * - `conTooltip()`: traduce el texto del tooltip.
 * - Los textos estaticos del `index.html`: se recorren al arrancar.
 * - Los mensajes del backend: se traducen al mostrarlos.
 */

const CLAVE = 'si.idioma';

/** Los idiomas disponibles. El primero es el de por defecto. */
export const IDIOMAS = [
  { clave: 'es', etiqueta: 'Español', corto: 'ES' },
  { clave: 'en', etiqueta: 'English', corto: 'EN' },
];

/**
 * El diccionario: texto en espanol -> texto en ingles.
 *
 * Las claves son las frases EXACTAS que pinta la app. Una frase que no
 * este acá se muestra en espanol (que es el idioma de origen) y avisa en la
 * consola: es un texto sin traducir, y mejor que se note.
 */
export const TRADUCCIONES = {
  // --- Los roles ---
  'Administrador': 'Administrator',
  'Técnico': 'Technician',
  'Operador': 'Operator',
  'Auditor': 'Auditor',

  'Equipos, stock y movimientos': 'Devices, stock and movements',
  'Incidencias, SLA y taller': 'Incidents, SLA and workshop',
  'Escanea, registra movimientos y reporta incidencias.': 'Scan, record movements and report incidents.',
  'Dónde está cada equipo, cuanto vale y que hay que reponer.': 'Where each device is, what it is worth and what to reorder.',
  'Dónde está cada equipo, cuanto vale, que hay que reponer y que se movio hoy.': 'Where each device is, what it is worth, what to reorder and what moved today.',
  'Los problemas reportados, el SLA y el taller.': 'The reported problems, the SLA and the workshop.',
  'Los problemas reportados, los que se vencen y el trabajo del taller: mantenimiento y compras atrasadas.': 'The reported problems, the ones about to expire and the workshop work: overdue maintenance and purchases.',
  'Los problemas reportados y su estado. El plazo de cada caso sale del SLA de su severidad.': 'The reported problems and their status. Each case deadline comes from the SLA of its severity.',
  'Maestro de SKUs. Acá se define que se puede cargar al inventario.': 'SKU master. Here you define what can be loaded into the inventory.',
  'Maestro de clientes. Se asocian a las salidas de productos.': 'Client master. They are linked to the product deliveries.',
  'Entrada, salida, transferencia, devolución y baja de mercancía por producto.': 'Inbound, outbound, transfers, returns and disposal of goods by product.',
  'Historial de movimientos': 'Movement history',
  'Historial de Auditoría': 'Audit history',
  'Auditoría - Trazabilidad Completa': 'Audit - Full traceability',
  'Ficha del componente': 'Device details',
  'Eventos del sistema': 'System events',
  'Movimientos de inventario': 'Inventory movements',
  'Mis casos': 'My cases',
  'Escaneos de la sesión': 'Session scans',
  'Estado de la IA': 'AI status',
  'Estado desconocido': 'Unknown status',
  'Fuera de sede': 'Out of site',
  'Escanear etiqueta': 'Scan label',
  'Ingreso manual': 'Manual entry',
  'Encender cámara': 'Turn on camera',
  'Cámara apagada': 'Camera off',
  'Entrada a depósito': 'Inbound to warehouse',
  'Devolución a depósito': 'Return to warehouse',
  'Baja definitiva': 'Permanent disposal',
  'Instalación en cliente': 'Install at client',
  'Mover mercancía': 'Move goods',
  'Crear orden': 'Create order',
  'Guardar cliente': 'Save client',
  'Guardar cambios': 'Save changes',
  'Descartar los cambios': 'Discard changes',
  'Cambiar contraseña': 'Change password',
  'Cambiar estado': 'Change status',
  'Cambiar estado a…': 'Change status to…',
  'Agregar atributo': 'Add attribute',
  'Atributos de la categoría': 'Category attributes',
  'Accesos / permisos': 'Access / permissions',
  'Abrir ficha completa del dispositivo': 'Open the full device details',
  'Nada para mostrar acá. Todo en orden.': 'Nothing to show here. All good.',
  'Nadie las tomó todavía.': 'Nobody has taken them yet.',
  'Nadie las todavía: son las que se van a perder.': 'Nobody has answered them yet: they are the ones that get lost.',
  'No hay categorías.': 'There are no categories.',
  'No hay eventos que coincidan con los filtros.': 'No events match the filters.',
  'No hay existencia con esos filtros.': 'No stock matches those filters.',
  'No hay movimientos con esa búsqueda.': 'No movements match that search.',
  'Ningún componente coincide con la busqueda.': 'No device matches the search.',
  'Todos los productos están por encima de su mínimo.': 'All the products are above their minimum.',
  'Casos abiertos que NADIE respondió todavía. Es el trabajo sin dueño: se van a perder si no se toman.': 'Open cases that NOBODY has answered yet. The work with no owner: they get lost if nobody takes them.',
  'Casos abiertos que NADIE respondió todavía: el trabajo sin dueño. Es lo primero que hay que tomar.': 'Open cases that NOBODY has answered yet: the work with no owner. It is the first thing to take.',
  'Casos por vencer, vencidos y escalados. El plazo sale del SLA de cada severidad.': 'Cases about to expire, expired and escalated. The deadline comes from the SLA of each severity.',
  'Casos sin resolver, en cualquiera de sus estados.': 'Unresolved cases, in any state.',
  'Componentes activos en el inventario, sin los dados de baja.': 'Active devices in the inventory, excluding the disposed ones.',
  'Control total: usuarios, roles, catálogo, precios y auditoría.': 'Full control: users, roles, catalog, prices and audit.',
  'Completa los datos para dar de alta un equipo en el inventario.': 'Fill in the details to register a device in the inventory.',
  'Equipos rotos, esperando el taller.': 'Broken devices, waiting for the workshop.',
  'Equipos trabajando en el cliente.': 'Devices working at the client.',
  'Disponibles para salir. Es el stock que se puede entregar hoy.': 'Available to ship. This is the stock that can be delivered today.',
  'El equipo no cambia de estado': 'The device does not change status',
  'El equipo no se arregla: se da de baja y sale del inventario, con su certificado.': 'The device is not repairable: it is disposed and leaves the inventory, with its certificate.',
  'El equipo pasa a "en taller" y deja de contar como disponible.': 'The device goes to "in repair" and stops counting as available.',
  'El equipo vuelve al depósito y vuelve a estar disponible.': 'The device returns to the warehouse and becomes available again.',
  'El equipo sale del depósito hacia el cliente': 'The device leaves the warehouse for the client',
  'El equipo se da de baja permanentemente': 'The device is permanently disposed',
  'El equipo se instala en sitio del cliente': 'The device is installed at the client site',
  'El equipo va a reparación': 'The device goes to repair',
  'El equipo vuelve al depósito desde cliente': 'The device returns to the warehouse from the client',
  'El cliente devuelve los equipos y vuelven al depósito.': 'The client returns the devices and they go back to the warehouse.',
  'El equipo hereda marca, modelo y categoría del producto del maestro.': 'The device inherits brand, model and category from the master product.',
  'El equipo deja de aparecer en el inventario y no se puede volver a usar la etiqueta. El historial de movimientos SE CONSERVA para la auditoría.': 'The device stops appearing in the inventory and its label cannot be used again. The movement history IS KEPT for the audit.',
  'El caso se resuelve, pero el equipo sigue donde está.': 'The case is resolved, but the device stays where it is.',
  'El SKU es único. Es la clave con la que se compra y se cuenta el stock.': 'The SKU is unique. It is the key used to buy and to count stock.',
  'El SKU no se puede modificar: es la clave histórica del producto.': 'The SKU cannot be modified: it is the historical key of the product.',
  'El serial del fabricante. Es único en toda la base.': 'The manufacturer serial. It is unique across the whole database.',
  'El tipo de problema. Sirve para ver patrones.': 'The type of problem. It helps to spot patterns.',
  'El tope de stock: arriba de este número, el producto está por sobre stockeado.': 'The stock ceiling: above this number, the product is overstocked.',
  'El valor leído se pone en el campo, donde se puede corregir antes de guardar.': 'The value read is put in the field, where it can be corrected before saving.',
  'La clave del producto: es con la que se compra, se cuenta el stock y se etiqueta. No se puede cambiar después.': 'The product key: it is used to buy, to count stock and to label. It cannot be changed later.',
  'La contraseña inicial. Comunicasela al usuario por un canal seguro.': 'The initial password. Share it with the user through a secure channel.',
  'La contraseña se guarda hasheada: no se puede volver a leer. Comunicasela al usuario.': 'The password is stored hashed: it cannot be read back. Share it with the user.',
  'La nueva contraseña del usuario. Se cierran sus sesiones abiertas.': 'The new password of the user. Their open sessions are closed.',
  'La que usas hoy. Se pide para confirmar que sos vos.': 'The one you use today. It is asked to confirm it is you.',
  'Las dos tienen que coincidir.': 'Both must match.',
  'Lo que costo una unidad. Con esto se calcula el valor del inventario.': 'What one unit cost. This is used to calculate the inventory value.',
  'Lo que se hizo. Es lo que lee el que reporto el problema.': 'What was done. This is what the person who reported the problem reads.',
  'La diferencia entre dos productos de la misma marca y modelo: capacidad, color, tamaño.': 'The difference between two products of the same brand and model: capacity, color, size.',
  'La etiqueta del producto: la de la caja, no la del equipo.': 'The product label: the one for the box, not for the device.',
  'La marca del fabricante. Es parte del nombre del producto.': 'The manufacturer brand. It is part of the product name.',
  'El modelo exacto. Con la marca, identifica el producto.': 'The exact model. Together with the brand, it identifies the product.',
  'El código de barras del producto: el del proveedor (un GTIN) o el SKU. Es lo que se imprime en la caja.': 'The product barcode: the provider one (a GTIN) or the SKU. This is what is printed on the box.',
  'El código de la etiqueta del producto. Si lo dejas vacío se genera uno propio (PRD-<sku>).': 'The product label code. If left empty, one is generated (PRD-<sku>).',
  'El detalle: cuando pasa, que se probó, que dice el cliente.': 'The details: when it happens, what was tried, what the client says.',
  'El contacto del usuario. Es obligatorio para poder avisarle.': 'The user contact. It is required to be able to notify them.',
  'El equipo elige los equipos que llevan más tiempo sin moverse (FIFO).': 'The system picks the devices that have been sitting the longest (FIFO).',
  'El tax_id es único. Identifica al cliente ante impuestos.': 'The tax_id is unique. It identifies the client for taxes.',
  'El plazo de respuesta y de resolución se calculan con el SLA de la severidad.': 'The response and resolution deadlines are calculated with the severity SLA.',
  'El plazo sale del SLA de la severidad de cada caso.': 'The deadline comes from the SLA of each case severity.',
  'El sistema elige los equipos que llevan más tiempo sin moverse (FIFO).': 'The system picks the devices that have been sitting the longest (FIFO).',
  'Horas promedio entre el reporte y la resolución. El número que le importa al responsable.': 'Average hours between the report and the resolution. The number the manager cares about.',
  'Horas promedio entre el reporte y la resolución. El número que le importa al responsable del taller.': 'Average hours between the report and the resolution. The number the workshop manager cares about.',
  'Este componente todavía no fue movido.': 'This device has not been moved yet.',
  'Este producto no tiene ninguna unidad dada de alta todavía.': 'This product has no unit registered yet.',
  'Está categoría no define atributos. Un producto de está categoría no tendrá campos extra.': 'This category defines no attributes. A product of this category will have no extra fields.',
  'Los atributos son los campos extra que pide un producto de está categoría.': 'The attributes are the extra fields a product of this category asks for.',
  'Los equipos salen del depósito hacia una sede o cliente.': 'The devices leave the warehouse for a site or client.',
  'Los equipos se dan de baja. No vuelven al inventario.': 'The devices are disposed. They do not return to the inventory.',
  'Los equipos se instalan en el sitio del cliente.': 'The devices are installed at the client site.',
  'Los equipos van a reparación.': 'The devices go to repair.',
  'Mercancía que ya está en el sistema ingresa al depósito.': 'Goods already in the system enter the warehouse.',
  'Apunta el código dentro del marco. El escaneo es automático.': 'Point the code inside the frame. The scan is automatic.',
  'Alternativa si la cámara no lee la etiqueta.': 'Alternative if the camera cannot read the label.',
  'Código leído (contiene caracteres no habituales).': 'Code read (it contains unusual characters).',
  'Genera reporte ejecutivo: stock, movimientos, mantenimientos, alertas. LLM redacta en prosa.': 'Generates the executive report: stock, movements, maintenance, alerts. The LLM writes it up.',
  'Mínimo 8 caracteres': 'At least 8 characters',
  'Mínimo 8 caracteres, con mayúscula, minúscula y número.': 'At least 8 characters, with an uppercase letter, a lowercase one and a digit.',
  'Definí QUE PUEDE HACER la persona que entra.': 'Defines WHAT THE PERSON WHO LOGS IN CAN DO.',
  'Definí el plazo de respuesta y de resolución (el SLA).': 'Defines the response and resolution deadlines (the SLA).',
  'Delega la tarea a otro agente especializado. Usa cuando la query requiera expertise fuera de tu dominio.': 'Delegates the task to another specialized agent. Use it when the query needs expertise outside your domain.',
  'Ejecuta SELECT arbitrario (solo lectura) via LLM. Para queries complejas que no cubren otras tools.': 'Runs an arbitrary SELECT (read only) via the LLM. For complex queries the other tools do not cover.',
  'Ejecuta batch nocturno: crea mantenimientos pendientes según planes + installation_date.': 'Runs the nightly batch: creates the pending maintenances from the plans and the installation date.',
  'Lista dispositivos con filtros. Para consultas de conteo, stock, ubicación.': 'Lists devices with filters. For count, stock and location queries.',
  'Lista mantenimientos PENDING, DUE_SOON, OVERDUE. Para dashboard de técnico.': 'Lists the PENDING, DUE_SOON and OVERDUE maintenances. For the technician dashboard.',
  'Lista órdenes de compra con filtros (estado, proveedor, fechas).': 'Lists the purchase orders with filters (status, vendor, dates).',
  'KPIs numéricos: total dispositivos, por estado, por ubicación, valor inventario, rotación.': 'Numeric KPIs: total devices, by status, by location, inventory value, turnover.',
  'Marca mantenimiento como COMPLETED con resultados, checklist, costos.': 'Marks a maintenance as COMPLETED with the results, checklist and costs.',
  'Modelos con más movimientos (entradas/salidas) en período. Para detectar fast-movers.': 'Models with the most movements (in/out) in a period. To spot fast movers.',
  'Modelos con stock por debajo del mínimo. Para generar órdenes de compra.': 'Models with stock below the minimum. To generate purchase orders.',
  'Historial completo de movimientos de un dispositivo (instalaciones, reparaciones, traslados).': 'Full movement history of a device (installations, repairs, transfers).',
  'Busca un dispositivo por código (QR, serial, parcial). Devuelve info completa + stock.': 'Searches a device by code (QR, serial, partial). Returns the full info plus stock.',
  'IA: verificando…': 'AI: checking…',
  'Idioma / Language': 'Language / Idioma',
  'Cambiar el idioma / Change language': 'Change the language / Cambiar el idioma',
  'Sin datos': 'No data',
  'Sin conexión': 'No connection',
  'Reintentar': 'Retry',
  'Ver el stock': 'View stock',
  'Ver la bandeja': 'View the queue',
  'Ver el inventario': 'View the inventory',
  'Ver los movimientos': 'View the movements',
  'Ver las incidencias': 'View the incidents',
  'Ver por severidad': 'View by severity',
  'Ver por categoría': 'View by category',
  'Ver por ubicación': 'View by location',
  'Ver el historial': 'View the history',
  'Ver el equipo': 'View the device',
  'Ver todos': 'View all',
  'Todo': 'All',
  'Todos': 'All',
  'Ninguno': 'None',
  'Sin filtros': 'No filters',
  'Limpiar filtros': 'Clear filters',
  'Aplicar': 'Apply',
  'Aplicar filtros': 'Apply filters',
  'Cargar más': 'Load more',
  'Ir': 'Go',
  'Volver': 'Back',
  'Volver al inventario': 'Back to the inventory',
  'Volver al listado': 'Back to the list',
  'Nuevo equipo': 'New device',
  'Registrar equipo': 'Register device',
  'Registrar un equipo': 'Register a device',
  'Histórico de equipos': 'Device history',
  'Historial de equipos': 'Device history',
  'Ubicaciones': 'Locations',
  'Depósitos': 'Warehouses',
  'Talleres': 'Workshops',
  'Clientes': 'Clients',
  'Proveedores': 'Vendors',
  'Ordenes de compra': 'Purchase orders',
  'Recepción': 'Receiving',
  'Recibir': 'Receive',
  'Recibir todo': 'Receive all',
  'Estado de la orden': 'Order status',
  'Nro. de orden': 'Order number',
  'Fecha estimada': 'Estimated date',
  'Fecha de entrega': 'Delivery date',
  'Agregar línea': 'Add line',
  'Costo estimado': 'Estimated cost',
  'Sin líneas': 'No lines',
  'Líneas': 'Lines',
  'Producto': 'Product',
  'Cantidad': 'Quantity',
  'Costo unitario': 'Unit cost',
  'Subtotal': 'Subtotal',
  'Total': 'Total',
  'Confirmar': 'Confirm',
  'Recibir orden': 'Receive order',
  'Cancelar orden': 'Cancel order',
  'La orden se recibió completa.': 'The order was received in full.',
  'Se registró la recepción.': 'The reception was recorded.',
  'El equipo': 'The device',
  'Serie': 'Serial',
  'Ubicación actual': 'Current location',
  'Fecha de instalación': 'Installation date',
  'Días instalado': 'Days installed',
  'Garantía': 'Warranty',
  'Vence': 'Expires',
  'Sin garantía': 'No warranty',
  'Abrir': 'Open',
  'Cerrar': 'Close',
  'Editar': 'Edit',
  'Eliminar': 'Delete',
  'Describir': 'Describe',
  'Añadir': 'Add',
  'Quitar': 'Remove',
  'Guardar': 'Save',
  'Cancelar': 'Cancel',
  'Ver': 'View',
  'Siguiente': 'Next',
  'Anterior': 'Previous',

  // --- La cabecera ---
  'Smart Inventory': 'Smart Inventory',
  'Inicio': 'Home',
  'Escanear': 'Scan',
  'Inventario': 'Inventory',
  'Existencia': 'Stock on hand',
  'Movimientos': 'Movements',
  'Incidencias': 'Incidents',
  'Consultas': 'Assistant',
  'Productos': 'Products',
  'Compras': 'Purchasing',
  'Auditoría': 'Audit',
  'Usuarios': 'Users',
  'Nuevo': 'New',
  'Service Desk': 'Service Desk',
  'Saltar al contenido': 'Skip to content',
  'Secciones': 'Sections',
  'Módulo de trabajo': 'Workspace',
  'Cerrar sesión': 'Sign out',
  'Avisos de SLA': 'SLA alerts',
  'Dar de alta un equipo (nuevo)': 'Register a device (new)',
  'Dar de alta un equipo nuevo': 'Register a new device',
  'Alternar modo exterior (alto contraste)': 'Toggle outdoor mode (high contrast)',
  'Modo exterior / alto contraste (para sol directo)': 'Outdoor mode / high contrast (for direct sunlight)',
  'Activos, inventario e incidencias': 'Assets, inventory and incidents',

  // --- La pantalla de entrada ---
  'Elegí con que módulo vas a trabajar. Podés cambiarlo desde la cabecera.':
    'Choose the workspace you are working on. You can change it from the header.',
  'Smart Inventory · Activos e incidencias': 'Smart Inventory · Assets and incidents',

  // --- El login ---
  'Iniciar sesión': 'Sign in',
  'Accede con tu cuenta de la empresa.': 'Sign in with your company account.',
  'Email': 'Email',
  'Contraseña': 'Password',
  'Entrar': 'Sign in',
  'Cargando…': 'Loading…',
  'Está aplicación necesita JavaScript para funcionar.': 'This application needs JavaScript to work.',

  // --- Los estados vacíos y los errores ---
  'Sin movimientos': 'No movements',
  'Sin avisos': 'No alerts',
  'Sin registros': 'No records',
  'Acceso denegado': 'Access denied',
  'No hay avisos. Todos los casos están en plazo.': 'No alerts. Every case is on time.',
  'Error al cargar productos': 'Error loading products',
  'Error al cargar ubicaciones': 'Error loading locations',
  'Está aplicación necesita JavaScript para funcionar.': 'This application needs JavaScript to work.',
  'Intentar de nuevo': 'Try again',

  // --- Los menus ---
  'Menú': 'Menu',
  'Configuración': 'Settings',
  'Mi cuenta': 'My account',
  'Ayuda': 'Help',
  'Soporte': 'Support',
  'Términos': 'Terms',
  'Privacidad': 'Privacy',
  'Enlaces legales': 'Legal links',
  'Info técnica': 'Technical info',

  // --- Los estados ---
  'En curso': 'In progress',
  'En espera': 'Waiting',
  'Resuelta': 'Resolved',
  'Cerrada': 'Closed',
  'Cancelada': 'Cancelled',
  'Abierta': 'Open',
  'En depósito': 'In warehouse',
  'En tránsito': 'In transit',
  'Instalados': 'Installed',
  'En taller': 'In repair',
  'Dado de baja': 'Disposed',
  'Dados de baja': 'Disposed',
  'En tránsito': 'In transit',
  'Pendiente': 'Pending',
  'Recibido': 'Received',
  'Confirmado': 'Confirmed',
  'Borrador': 'Draft',
  'Enviada': 'Sent',
  'Recibida': 'Received',
  'Cancelada': 'Cancelled',

  // --- Los botones ---
  'Guardar': 'Save',
  'Guardar producto': 'Save product',
  'Guardar cambios': 'Save changes',
  'Crear': 'Create',
  'Crear componente': 'Create device',
  'Crear orden': 'Create order',
  'Crear usuario': 'Create user',
  'Crear componente': 'Create device',
  'Editar': 'Edit',
  'Eliminar': 'Delete',
  'Cancelar': 'Cancel',
  'Cerrar': 'Close',
  'Confirmar': 'Confirm',
  'Confirmar baja': 'Confirm disposal',
  'Descartar los cambios': 'Discard changes',
  'Seguir editando': 'Keep editing',
  'Buscar': 'Search',
  'Limpiar': 'Clear',
  'Actualizar': 'Refresh',
  'Exportar CSV': 'Export CSV',
  'Ver': 'View',
  'Ver todo': 'View all',
  'Ver todos': 'View all',
  'Ver más': 'See more',
  'Ver el stock': 'View stock',
  'Ver la bandeja': 'View the queue',
  'Tomar': 'Take',
  'Tomar el caso': 'Take the case',
  'Reportar incidencia': 'Report incident',
  'Resolver el caso': 'Resolve the case',
  'Nuevo producto': 'New product',
  'Nuevo usuario': 'New user',
  'Nueva orden': 'New order',
  'Nueva categoría': 'New category',
  'Dar de baja': 'Dispose',
  'Descontinuar': 'Discontinue',
  'Reactivar': 'Reactivate',
  'Imprimir': 'Print',
  'Etiqueta': 'Label',
  'Duplicar': 'Duplicate',
  'Copiar': 'Copy',
  'Abrir': 'Open',
  'Salir': 'Sign out',
  'Entrar': 'Sign in',
  'Atrás': 'Back',
  'Siguiente': 'Next',
  'Anterior': 'Previous',

  // --- Los campos ---
  'Nombre': 'Name',
  'Marca': 'Brand',
  'Modelo': 'Model',
  'SKU': 'SKU',
  'Variante': 'Variant',
  'Categoría': 'Category',
  'Categoría padre': 'Parent category',
  'Costo unitario': 'Unit cost',
  'Stock mínimo': 'Minimum stock',
  'Stock máximo': 'Maximum stock',
  'Código de la etiqueta': 'Label code',
  'Código QR': 'QR code',
  'Código de barras': 'Barcode',
  'Serial': 'Serial',
  'Número de serie': 'Serial number',
  'Estado': 'Status',
  'Ubicación': 'Location',
  'Ubicación inicial': 'Initial location',
  'Producto': 'Product',
  'Descripción': 'Description',
  'Observaciones': 'Notes',
  'Motivo': 'Reason',
  'Nota': 'Note',
  'Comentario': 'Comment',
  'Teléfono': 'Phone',
  'Email': 'Email',
  'Rol': 'Role',
  'Contraseña': 'Password',
  'Contraseña nueva': 'New password',
  'Nombre y apellido': 'Full name',
  'Dirección': 'Address',
  'Ciudad': 'City',
  'Costo': 'Cost',
  'Precio': 'Price',
  'Cantidad': 'Quantity',
  'Fecha': 'Date',
  'Título': 'Title',
  'Severidad': 'Severity',
  'Resolución': 'Resolution',
  'Cómo se resolvió': 'How it was resolved',

  // --- Los mensajes ---
  'Guardando…': 'Saving…',
  'Consultando…': 'Querying…',
  'Consultando el stock…': 'Querying stock…',
  'Consultando la existencia…': 'Querying stock on hand…',
  'Cargando productos…': 'Loading products…',
  'Cargando incidencias…': 'Loading incidents…',
  'Cargando usuarios…': 'Loading users…',
  'Cargando órdenes…': 'Loading orders…',
  'Cargando stock…': 'Loading stock…',
  'Cargando movimientos…': 'Loading movements…',
  'Cargando auditoría…': 'Loading audit…',
  'Cargando categorías…': 'Loading categories…',
  'Cargando clientes…': 'Loading clients…',
  'Cargando ubicaciones…': 'Loading locations…',
  'Cargando atributos…': 'Loading attributes…',
  'Cargando ficha…': 'Loading details…',
  'Generando reporte…': 'Generating report…',
  'No hay incidencias con esos filtros.': 'There are no incidents with those filters.',
  'No hay productos. Crea uno con "Nuevo producto" para poder cargar mercadería.':
    'There are no products. Create one with "New product" to be able to load stock.',
  'No hay movimientos con esa búsqueda.': 'There are no movements with that search.',
  'No hay usuarios con esos filtros.': 'There are no users with those filters.',
  'Tu sesión terminó. Iniciá sesión para continuar.': 'Your session ended. Sign in to continue.',
  'Se guardó correctamente.': 'Saved successfully.',
  'Se eliminó correctamente.': 'Deleted successfully.',
  '¿Está seguro?': 'Are you sure?',
  '¿Descartar los cambios?': 'Discard the changes?',
  'Hay cambios sin guardar': 'Unsaved changes',
  'Cerrar ahora descarta lo que escribiste.': 'Closing now discards what you wrote.',
  'Si querés conservarlo, volvé a abrir la edición.':
    'If you want to keep it, open the edit again.',
  'No se pudo guardar.': 'Could not save.',
  'No se pudo conectar con el servidor.': 'Could not connect to the server.',

  // --- Los avisos de stock ---
  'Por reponer': 'To reorder',
  'Productos con el stock igual o por debajo del mínimo.':
    'Products with stock at or below the minimum.',
  'Faltan': 'Missing',
  'Disponibles': 'Available',
  'Mínimo': 'Minimum',
  'Máximo': 'Maximum',
  'Días bajo mínimo': 'Days below minimum',
  'La reposición llega tarde': 'The reorder is late',

  // --- El tablero ---
  'Equipos': 'Devices',
  'En depósito': 'In warehouse',
  'Por reponer': 'To reorder',
  'Instalados': 'Installed',
  'En tránsito': 'In transit',
  'Movimientos (30 d)': 'Movements (30 d)',
  'Estado de la IA': 'AI status',
  'Operativa': 'Operational',
  'Con problemas': 'With problems',
  'Proveedor': 'Provider',
  'Modelo': 'Model',
  'Latencia': 'Latency',
  'Consultar al inventario': 'Ask the inventory',
  'Pregunta en español. La respuesta sale de los datos reales.':
    'Ask in English. The answer comes from the real data.',
  'Preguntame por el inventario. Puedo contar equipos por estado, listar los que llevan mucho instalados y buscar los que más visitas al taller.':
    'Ask me about the inventory. I can count devices by status, list the ones installed for a long time and find the ones with the most workshop visits.',
  'Sugerencias': 'Suggestions',
  'Reporte': 'Report',
  'Cifras de la base de datos, redactadas por el modelo.':
    'Figures from the database, written up by the model.',
  'Generar reporte operativo': 'Generate the operational report',
  'Movimientos de hoy': 'Movements today',
  'Está semana': 'This week',
  'Valor de la flota': 'Fleet value',
  'Equipos por estado': 'Devices by status',
  'Últimos movimientos': 'Latest movements',
  'Trabajo pendiente': 'Pending work',
  'Casos que vencen': 'Cases about to expire',
  'Sin atender': 'Unattended',

  // --- La existence ---
  'Existencia': 'Stock on hand',
  'Cuánto hay de cada rubro y donde. El disponible es lo que se puede entregar hoy.':
    'How much of each item there is and where. Available is what can be delivered today.',
  'Disponible': 'Available',
  'Solo por debajo del mínimo': 'Only below the minimum',
  'Solo los productos con el stock igual o por debajo del mínimo.':
    'Only the products with stock at or below the minimum.',
  'por página': 'per page',
  'Anterior': 'Previous',
  'Siguiente': 'Next',
  'Página': 'Page',
  'de': 'of',
  'registros': 'records',
  'existencias': 'stock entries',
  'componentes': 'devices',
  'casos': 'cases',
  'productos': 'products',
  'usuarios': 'users',
  'órdenes': 'orders',
  'clientes': 'clients',
  'eventos': 'events',
  'movimientos': 'movements',
};

/**
 * Las etiquetas de los roles.
 *
 * El backend manda el rol como un código (`ADMIN`, `TECHNICIAN`, `OPERATOR`).
 * Mostrar el código translates a "Admin", que en ingles está bien pero en
 * espanol no: el rol de un técnico tiene que decir "Técnico", no
 * "Technician".
 *
 * La clave es el código y el valor es la etiqueta en espanol; en ingles se
 * traduce con el diccionario comun.
 */
export const ETIQUETAS_DE_ROL = {
  ADMIN: 'Admin',
  TECHNICIAN: 'Técnico',
  OPERATOR: 'Operador',
  AUDITOR: 'Auditor',
};

/**
 * El prefijo de la línea del rol.
 *
 * "Admin" solo, al lado del nombre, no dice que es: parece un segundo
 * nombre. "Rol: Admin" lo dice.
 */
export function prefijoDeRol() {
  return idioma() === 'en' ? 'Role: ' : 'Rol: ';
}

/**
 * La etiqueta de un rol, en el idioma de la app.
 *
 * @param {string} código  El código del rol (por ejemplo, `TECHNICIAN`).
 * @returns {string} La etiqueta, o el código si no se conoce.
 */
export function etiquetaDeRol(codigo) {
  if (!codigo) return '';
  const clave = String(codigo).toUpperCase();
  const espanol = ETIQUETAS_DE_ROL[clave];
  if (espanol) return traducir(espanol);

  // Un rol que no conoce la app (uno propio del cliente): se muestra el
  // código prettificado. "TRANSCRIPTOR" -> "Transcriptor".
  const palabras = clave.toLowerCase().replace(/_/g, ' ');
  return palabras.charAt(0).toUpperCase() + palabras.slice(1);
}

let idiomaActual = 'es';

/** El idioma en uso. */
export function idioma() {
  return idiomaActual;
}

/** Cambia el idioma y lo guarda para la próxima visita. */
export function setIdioma(nuevo) {
  const existe = IDIOMAS.some((i) => i.clave === nuevo);
  idiomaActual = existe ? nuevo : 'es';
  try {
    localStorage.setItem(CLAVE, idiomaActual);
  } catch {
    // Sin storage (modo privado): el idioma dura lo que dura la pestaña.
  }
  return idiomaActual;
}

/** El idioma guardado (o el de por defecto). */
export function leerIdiomaGuardado() {
  try {
    const guardado = localStorage.getItem(CLAVE);
    if (guardado && IDIOMAS.some((i) => i.clave === guardado)) return guardado;
  } catch {
    // Sin storage: el de por defecto.
  }
  return 'es';
}

/**
 * Traduce un texto.
 *
 * En espanol devuelve el texto tal cual (es el idioma de origen). En ingles
 * busca la frase en el diccionario; si no está, devuelve el original y avisa
 * en la consola: un texto sin traducir tiene que notarse, no pasar
 * desapercibido.
 */
export function traducir(texto) {
  if (idiomaActual !== 'en' || typeof texto !== 'string' || !texto) return texto;
  const traduccion = TRADUCCIONES[texto];
  if (traducción) return traducción;
  // Un texto con datos ("${nombre}") no se puede traducir: el diccionario
  // tiene las frases, no las frases con datos. No avisa, porque no es un
  // texto sin traducir: es un texto que no se puede traducir.
  if (texto.includes('${')) return texto;
  if (!textosSinTraducir.has(texto)) {
    textosSinTraducir.add(texto);
    console.warn(`[i18n] Falta la traduccion de: "${texto}"`);
  }
  return texto;
}

// Los textos ya avisados: la consola no se llena del mismo aviso 200 veces.
const textosSinTraducir = new Set();

/** Traduce una lista de textos. */
export function traducirTodos(textos) {
  return textos.map((t) => traducir(t));
}
