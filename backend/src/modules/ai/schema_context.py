"""
El esquema que ve el modelo, y solo el esquema.

POR QUE NO HAY FILAS EN EL PROMPT
---------------------------------
La pregunta era si el modelo podia ver datos crudos. La respuesta es no, y no
por prudencia abstracta: el prompt viaja al host que corre Ollama, que puede
ser la laptop del desarrollador o un nodo compartido. Mandarle filas de
`clients` —razones sociales, emails, telefonos, coordenadas GPS de las tiendas—
es una exfiltracion hacia un tercero en cada pregunta.

Con esquema + agregados el modelo responde igual de bien para lo que se le
pide ("cuantos hay por estado", "que clientes tienen mas repaired") porque esas
preguntas son de DISTRIBUCION, y las distribuciones se calculan con GROUP BY
en la base, no en el modelo.

El precio de esta decision: el modelo no puede inventar joins raros porque no
conoce los datos, y no puede responder "cual es el cliente de Pedro" sin
preguntarselo a la base. Es una restriction aceptable a cambio de que no
existan datos de clientes fuera del servidor.
"""

from src.modules.ai.guard import tablas_permitidas

# Descripcion del esquema en el formato que los modelos entendieron bien: DDL
# real, con los ENUM como CHECK, porque el modelo tiene el DDL de Postgres en
# sus datos de entrenamiento y lo parsea mejor que una descripcion en prosa.
#
# Nota: `models` es el nombre de la tabla de catalogos. Choca con el `BaseModel`
# de pydantic si alguien importa el modulo con `from models import ...`, pero
# aca no pasa: todo el SQL se genera como TEXTO y lo valida `guard.py`.
ESQUEMA_SQL = """
CREATE TYPE device_status AS ENUM (
    'IN_WAREHOUSE', 'IN_TRANSIT', 'INSTALLED', 'IN_REPAIR', 'PERMANENTLY_DISPOSED'
);
CREATE TYPE movement_type AS ENUM (
    'WAREHOUSE_ENTRY', 'OUT_TO_CLIENT', 'INSTALLATION', 'REPLACEMENT',
    'WITHDRAWAL_TO_WORKSHOP', 'WAREHOUSE_REENTRY', 'DISPOSAL'
);
CREATE TYPE repair_status AS ENUM (
    'DIAGNOSIS', 'IN_PROGRESS', 'WAITING_FOR_PARTS', 'REPAIRED', 'UNREPAIRABLE'
);

CREATE TABLE clients (
    id SERIAL PRIMARY KEY,
    commercial_name VARCHAR(150) NOT NULL,
    tax_id VARCHAR(50) UNIQUE NOT NULL,
    contact_name VARCHAR(100),
    contact_email VARCHAR(100),
    contact_phone VARCHAR(20),
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE locations (
    id SERIAL PRIMARY KEY,
    client_id INT REFERENCES clients(id) ON DELETE CASCADE,
    store_name VARCHAR(100) NOT NULL,
    address TEXT NOT NULL,
    city VARCHAR(100),
    latitude NUMERIC(10, 8),
    longitude NUMERIC(11, 8),
    tolerance_radius_meters INT,
    is_active BOOLEAN,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE models (
    id SERIAL PRIMARY KEY,
    category_id INT REFERENCES categories(id),
    brand VARCHAR(100) NOT NULL,
    model VARCHAR(100) NOT NULL,
    specifications JSONB,
    warranty_months INT,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    model_id INT REFERENCES models(id),
    serial_number VARCHAR(100) UNIQUE NOT NULL,
    qr_code VARCHAR(150) UNIQUE NOT NULL,
    current_status device_status,
    current_location_id INT REFERENCES locations(id),
    is_warehouse BOOLEAN,
    warehouse_entry_date TIMESTAMPTZ,
    installation_date TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

CREATE TABLE movement_history (
    id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(id) ON DELETE CASCADE,
    movement_type movement_type NOT NULL,
    origin_location_id INT REFERENCES locations(id),
    destination_location_id INT REFERENCES locations(id),
    replaced_device_id INT REFERENCES devices(id),
    replacement_reason TEXT,
    user_id INT NOT NULL,
    scan_latitude NUMERIC(10, 8),
    scan_longitude NUMERIC(11, 8),
    is_valid_location BOOLEAN,
    observations TEXT,
    movement_date TIMESTAMPTZ
);

CREATE TABLE repairs (
    id SERIAL PRIMARY KEY,
    device_id INT REFERENCES devices(id) ON DELETE CASCADE,
    technician_id INT NOT NULL,
    status repair_status,
    initial_diagnosis TEXT,
    work_performed TEXT,
    estimated_cost NUMERIC(10, 2),
    entry_date TIMESTAMPTZ,
    exit_date TIMESTAMPTZ,
    is_operational BOOLEAN
);

CREATE TABLE disposal_certificates (
    id SERIAL PRIMARY KEY,
    device_id INT UNIQUE REFERENCES devices(id),
    repair_id INT REFERENCES repairs(id),
    disposal_reason TEXT NOT NULL,
    authorized_by UUID REFERENCES users(id),
    disposal_date TIMESTAMPTZ
);

CREATE TABLE roles (
    id UUID PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    address VARCHAR(255),
    phone VARCHAR(255),
    phone2 VARCHAR(255),
    is_active BOOLEAN,
    is_specialist BOOLEAN,
    role_id UUID NOT NULL REFERENCES roles(id),
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    action audit_action NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    record_id UUID,
    user_id UUID,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMPTZ
);
"""

# Lo que el modelo va a inventar igual si no se le dice. Un LLM con esquema
# guarda memoria de tablas que no hay (`suppliers`, `inventory`, `warranties`,
# `sessions`) y las usa en el SQL. Decirselo explicitamente es mas barato que
# el SQL invalido que devuelve.
#
# `users.password` NO aparece en el ESQUEMA_SQL de arriba a proposito: el modelo
# ni siquiera sabe que la columna existe. Es la version mas fuerte de "no podes
# leerla" — la de no dizerle que existe — y va respaldada por el chequeo de
# columna en guard.py, porque el prompt solo no es una garantia.
NOTAS_ESQUEMA = """
NOTAS IMPORTANTES
- La tabla de catalogos se llama `models` (no `product_models`), y su PK es `id`.
- Para ubicar un dispositivo: devices.model_id -> models.id, y
  devices.current_location_id -> locations.id -> locations.client_id -> clients.id.
- Para saber quien hizo algo: movement_history.user_id -> users.id,
  repairs.technician_id -> users.id, disposal_certificates.authorized_by -> users.id.
  Son UUID, no enteros.
- Para el nombre del rol: users.role_id -> roles.id, o directamentero.name = 'ADMIN'.
- `users` NO tiene columna `password` consultable. No la busques ni la pidas.
- BORRADO LOGICO: las tablas con `deleted_at` no se borran, se marcan. SIEMPRE
  filtrá `WHERE deleted_at IS NULL` en users y roles. En audit_logs el registro
  es historico y no lleva deleted_at.
- `audit_logs.action` es un ENUM: 'INSERT', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT'.
- Los valores de los ENUM van con comillas simples y en MAYUSCULAS.
- `specifications` es JSONB en la tabla `models` (NO en `devices`). 
  Para leerlo: JOIN models m ON d.model_id = m.id, luego m.specifications->>'campo'.
  ERROR COMUN: NO uses `d.specifications` directamente — NO EXISTE en devices.
- `is_warehouse` en devices es BOOLEAN derivado de current_status = 'IN_WAREHOUSE'.
  Usá `current_status = 'IN_WAREHOUSE'` en vez de is_warehouse para consistencia.

EJEMPLOS DE JOINS CORRECTOS:
-- Cuantos Latitude 5440 en deposito central:
SELECT COUNT(*) 
FROM devices d 
JOIN models m ON m.id = d.model_id 
JOIN locations l ON l.id = d.current_location_id 
WHERE d.current_status = 'IN_WAREHOUSE' 
  AND m.model ILIKE '%latitude%5440%' 
  AND l.store_name ILIKE '%deposito central%';

-- Stock de modelos Dell en depósito:
SELECT m.model, COUNT(*) 
FROM devices d 
JOIN models m ON m.id = d.model_id 
JOIN locations l ON l.id = d.current_location_id 
WHERE d.current_status = 'IN_WAREHOUSE' 
  AND m.brand = 'Dell'
GROUP BY m.model;

-- Coordenadas están en locations (latitude, longitude), NO en devices.
-- Coordenadas del escaneo están en movement_history (scan_latitude, scan_longitude).
"""

POCISOSOS = (
    f"""Podes consultar SOLO estas tablas: {", ".join(sorted(tablas_permitidas()))}."""
)


def descripcion_esquema() -> str:
    """El bloque que se inyecta en el system prompt. Sin filas, siempre."""
    return f"{ESQUEMA_SQL}\n{NOTAS_ESQUEMA}\n{POCISOSOS}"
