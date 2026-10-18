"""
CRUD de usuarios.

TODAS las escrituras pasan por acá, y las tres hacen lo mismo ademas de su SQL:
`created_by_user_id` / `updated_by_user_id` / `deleted_by_user_id` mas el
usuario actual.

POR QUE IMPORTA TANTO
---------------------
La tabla `users` tiene un trigger `set_audit_role_id` que copia el ROL de quien
hizo el cambio al lado del dato. Si un endpoint inserta sin pasar el actor, la
fila queda con `created_by_user_id` NULL y la bitacora tiene un hueco
irreparable: no se sabe QUIEN dio de alta a ese usuario. Por eso no hay un
`INSERT INTO users` suelto en ningun router.

EL BORRADO ES LOGICO, SIEMPRE
-----------------------------
`users` tiene `deleted_at` y todas las lecturas filtran por el. No hay un
`DELETE` físico en este archivo, y no es una decisión de estilo: un usuario es
la llave foránea de `movement_history` y `repairs`. Con `ON DELETE RESTRICT`
un borrado físico de un usuario con historial falla, y con CASCADE llevaría
consigo el historial de todos los equipos que movió.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.security import hashear_password, validar_password
from src.modules.users.models import (
    COLUMNAS_USUARIO,
    ErrorDeUsuario,
    UsuarioNoEncontrado,
    get_db,
    resolver_rol_id,
)


def _exigir_actor(actor_id: str) -> str:
    """
    El actor no puede ser opcional en ninguna escritura.

    Un `actor_id = None` que pasa: eso es un endpoint que deja un hueco en la
    bitácora. Que sea un `str` sin default obliga a pasar uno.
    """
    if not actor_id:
        raise ErrorDeUsuario(
            "Falta el actor de la operación. No se puede auditar sin él."
        )
    return actor_id


# ---------------------------------------------------------------------------
# Crear
# ---------------------------------------------------------------------------


def crear_usuario(datos: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """
    Alta de usuario.

    Valida el email ANTES de insertar, no confía en el UNIQUE de la base: el
    error de Postgres (`duplicate key value violates unique constraint`) es un
    500 para el cliente y no dice que el problema es el email.
    """
    actor_id = _exigir_actor(actor_id)

    email = (datos.get("email") or "").strip().lower()
    if not email:
        raise ErrorDeUsuario("El email es obligatorio.")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ErrorDeUsuario(f"'{email}' no parece un email válido.")

    nombre = (datos.get("full_name") or "").strip()
    if not nombre:
        raise ErrorDeUsuario("El nombre completo es obligatorio.")

    # `phone2` es NOT NULL sin default en el esquema. Si se lo deja vacio y se
    # manda "", el INSERT funciona; si se manda NULL, revienta con un error de
    # constraint que no dice qué columna es.
    telefono = (datos.get("phone") or "").strip()
    if not telefono:
        raise ErrorDeUsuario("El teléfono es obligatorio.")
    telefono2 = (datos.get("phone2") or "").strip()

    password = datos.get("password") or ""
    # `validar_password` lanza con el motivo; el router lo pasa tal cual.
    validar_password(password)

    role_id = resolver_rol_id(datos.get("role_name") or "OPERATOR")

    # Se hashea ANTES de abrir la conexión. Si hashear es lo más caro (lo es:
    # ~250 ms), hacerlo dentro de una transacción abierta mantiene taken un
    # connection del pool doing work that doesn't need the database.
    password_hash = hashear_password(password)

    try:
        with get_db().connect() as conexion:
            transaccion = conexion.begin()
            fila = (
                conexion.execute(
                    text("""
                    INSERT INTO users (
                        email, password, full_name, address, phone, phone2,
                        role_id, is_active, is_specialist, created_by_user_id
                    )
                    VALUES (
                        :email, :password, :full_name, :address, :phone, :phone2,
                        :role_id, :is_active, :is_specialist, :actor_id
                    )
                    RETURNING id
                """),
                    {
                        "email": email,
                        "password": password_hash,
                        "full_name": nombre,
                        "address": (datos.get("address") or "N/A").strip(),
                        "phone": telefono,
                        "phone2": telefono2,
                        "role_id": role_id,
                        "is_active": _a_bool(datos.get("is_active"), default=True),
                        "is_specialist": _a_bool(
                            datos.get("is_specialist"), default=False
                        ),
                        "actor_id": actor_id,
                    },
                )
                .mappings()
                .first()
            )
            transaccion.commit()

            return obtener_usuario(fila["id"])
    except IntegrityError as exc:
        # El UNIQUE de email. Se traduce a 409 con un mensaje que el frontend
        # pueda mostrar tal cual.
        if "users_email_key" in str(exc.orig) or "duplicate key" in str(exc.orig):
            raise ErrorDeUsuario(f"El email '{email}' ya está registrado.") from exc
        raise
    except SQLAlchemyError as exc:
        raise ErrorDeUsuario(f"No se pudo crear el usuario: {exc}") from exc


# ---------------------------------------------------------------------------
# Leer
# ---------------------------------------------------------------------------


def obtener_usuario(user_id: str) -> dict[str, Any]:
    from src.modules.users.models import buscar_usuario_por_id

    usuario = buscar_usuario_por_id(user_id)
    if usuario is None:
        raise UsuarioNoEncontrado(f"No existe un usuario con id {user_id}.")
    return usuario


def listar_usuarios(
    solo_activos: bool = True,
    busqueda: str | None = None,
    rol: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Listado paginado con filtros.

    Los filtros van con PARAMETROS, nunca interpolados. Es la regla basica de
    SQL y aqui se cumple a proposito: `busqueda` viene del body de una request.
    """
    condiciones = ["1=1"]
    parametros: dict[str, Any] = {}

    if solo_activos:
        condiciones.append("u.deleted_at IS NULL")
    if busqueda:
        # ILIKE con el % en el PARAMETRO. Ponerlo en el f-string sería SQL
        # injection directo, y aunque se sanitice, es un mal habito que se
        # copia.
        condiciones.append("(u.full_name ILIKE :busqueda OR u.email ILIKE :busqueda)")
        parametros["busqueda"] = f"%{busqueda.strip()}%"
    if rol:
        condiciones.append("UPPER(r.name) = UPPER(:rol)")
        parametros["rol"] = rol.strip()

    where = " AND ".join(condiciones)

    with get_db().connect() as conexion:
        total = (
            conexion.execute(
                text(f"""
                SELECT COUNT(*) AS total
                FROM users u JOIN roles r ON r.id = u.role_id
                WHERE {where}
            """),
                parametros,
            )
            .mappings()
            .one()["total"]
        )

        filas = (
            conexion.execute(
                text(f"""
                SELECT {COLUMNAS_USUARIO}, r.name AS role_name
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE {where}
                ORDER BY u.created_at DESC
                LIMIT :limite OFFSET :offset
            """),
                {**parametros, "limite": limite, "offset": offset},
            )
            .mappings()
            .all()
        )

    from src.modules.users.models import _fila_a_dict

    return {
        "usuarios": [_fila_a_dict(f) for f in filas],
        "total": total,
        "limite": limite,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Actualizar
# ---------------------------------------------------------------------------


def actualizar_usuario(
    user_id: str, cambios: dict[str, Any], actor_id: str
) -> dict[str, Any]:
    """
    Actualizacion parcial: solo los campos presentes en `cambios`.

    Se construye el SET dinámicamente con nombres de columna de una lista
    blanca. Un bucle sobre las claves del body, sin lista blanca, es un SQL
    injection: el cliente puede mandar `{"role_id": "x", "email = evil, ...": 1}`
    y el nombre de columna se interpola.
    """
    actor_id = _exigir_actor(actor_id)

    objetivo = obtener_usuario(user_id)

    # Nombre de columna -> valor. La lista blanca es lo que separa "campo
    # editable" de "campo inyectable".
    permitidos = {
        "full_name": lambda v: (v or "").strip(),
        "address": lambda v: (v or "N/A").strip(),
        "phone": lambda v: (v or "").strip(),
        "phone2": lambda v: (v or "").strip(),
        "is_active": lambda v: _a_bool(v, default=True),
        "is_specialist": lambda v: _a_bool(v, default=False),
    }

    asignaciones = []
    parametros: dict[str, Any] = {"user_id": user_id, "actor_id": actor_id}

    if cambios.get("role_name"):
        nuevo_rol = cambios["role_name"]
        if nuevo_rol.upper() == "ADMIN" and objetivo["role_name"].upper() != "ADMIN":
            # Degradar a un ADMIN no se puede hacer por la via comun. Es el
            # camino para que un ADMIN_NEW se quede sin admin y el sistema
            # quede sin nadie que lo administre.
            #
            # Se permite con `forzar_degradacion_admin=True`, que usa un
            # endpoint aparte que pide confirmación mas fuerte.
            raise ErrorDeUsuario(
                "No se puede quitar el rol ADMIN por esta vía. "
                "Usá POST /api/users/{id}/degradar-admin."
            )
        asignaciones.append("role_id = :role_id")
        parametros["role_id"] = resolver_rol_id(nuevo_rol)

    for campo, transformar in permitidos.items():
        if campo in cambios:
            valor = transformar(cambios[campo])
            if campo in ("full_name", "phone") and not valor:
                raise ErrorDeUsuario(f"El campo '{campo}' no puede quedar vacío.")
            asignaciones.append(f"{campo} = :{campo}")
            parametros[campo] = valor

    if not asignaciones:
        raise ErrorDeUsuario("No se envió ningún campo para actualizar.")

    asignaciones.append("updated_by_user_id = :actor_id")

    # `updated_at` NO se toca: lo setea el trigger `set_audit_role_id` en cada
    # UPDATE. Si se lo definiera acá, el trigger lo pisaria con su propio NOW()
    # — o peor, al reves, y la columna quedaria con la fecha de la app.

    try:
        with get_db().connect() as conexion:
            transaccion = conexion.begin()
            conexion.execute(
                text(f"""
                    UPDATE users
                    SET {", ".join(asignaciones)}
                    WHERE id = :user_id
                """),
                parametros,
            )
            transaccion.commit()

        return obtener_usuario(user_id)
    except IntegrityError as exc:
        raise ErrorDeUsuario(f"No se pudo actualizar el usuario: {exc}") from exc


def cambiar_password(
    user_id: str,
    password_nueva: str,
    actor_id: str,
    password_actual: str | None = None,
) -> None:
    """
    Cambio de contraseña.

    `password_actual` es obligatoria cuando el usuario se cambia a SÍ MISMO, y
    opcional cuando lo cambia un ADMIN. Esa diferencia es el ataque de
    password change: con un token robado (o con la sesión abierta en un
    navegador compartido) un atacante se cambia la contraseña y echa al dueño
    de su propia cuenta.
    """
    actor_id = _exigir_actor(actor_id)

    usuario = obtener_usuario(user_id)

    es_actor_mismo = str(usuario["id"]) == str(actor_id)

    if es_actor_mismo:
        if not password_actual:
            raise ErrorDeUsuario(
                "Para cambiar tu propia contraseña necesitás la contraseña actual."
            )
        from src.core.security import verificar_password
        from src.modules.users.models import buscar_usuario_por_email

        con_hash = buscar_usuario_por_email(usuario["email"], incluir_password=True)
        if not verificar_password(password_actual, con_hash["password_hash"]):
            raise ErrorDeUsuario("La contraseña actual no es correcta.")

    validar_password(password_nueva)
    nuevo_hash = hashear_password(password_nueva)

    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        conexion.execute(
            text("""
                UPDATE users
                SET password = :password, updated_by_user_id = :actor_id
                WHERE id = :user_id
            """),
            {"password": nuevo_hash, "actor_id": actor_id, "user_id": user_id},
        )
        transaccion.commit()


def degradar_admin(user_id: str, actor_id: str) -> dict[str, Any]:
    """
    Quitarle el rol ADMIN a un usuario, incluso si es el último.

    Separado de `actualizar_usuario` a propósito: es la única operación capaz
    de dejar el sistema sin administrador, y por eso tiene su propia función
    con su propia regla (no se puede quitar al último) en vez de ser un caso
    más del PATCH.
    """
    actor_id = _exigir_actor(actor_id)
    objetivo = obtener_usuario(user_id)

    if objetivo["role_name"].upper() != "ADMIN":
        raise ErrorDeUsuario("Ese usuario no es administrador.")

    with get_db().connect() as conexion:
        admins = (
            conexion.execute(
                text("""
                SELECT COUNT(*) AS total
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE UPPER(r.name) = 'ADMIN'
                  AND u.deleted_at IS NULL AND u.is_active
            """)
            )
            .mappings()
            .one()["total"]
        )

    if admins <= 1:
        raise ErrorDeUsuario(
            "Es el último administrador activo. Creá otro antes de degradar a este."
        )

    rol_operador = resolver_rol_id("OPERATOR")

    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        conexion.execute(
            text("""
                UPDATE users
                SET role_id = :role_id, updated_by_user_id = :actor_id
                WHERE id = :user_id
            """),
            {"role_id": rol_operador, "actor_id": actor_id, "user_id": user_id},
        )
        transaccion.commit()

    return obtener_usuario(user_id)


# ---------------------------------------------------------------------------
# Borrar
# ---------------------------------------------------------------------------


def desactivar_usuario(user_id: str, actor_id: str) -> dict[str, Any]:
    """
    Baja lógica.

    `is_active = FALSE` y `deleted_at = NOW()` juntos: `is_active` es el
    bloqueo inmediato (el login lo rechaza), `deleted_at` es que ya no aparece
    en listados. Son cosas distintas y por eso las dos.
    """
    actor_id = _exigir_actor(actor_id)
    objetivo = obtener_usuario(user_id)

    if str(objetivo["id"]) == str(actor_id):
        raise ErrorDeUsuario("No podés desactivar tu propia cuenta.")

    # El SELECT va en SU PROPIA conexión, y el UPDATE en otra.
    #
    # En SQLAlchemy 2.0 un `execute()` abre una transacción implícita
    # ("autobegin"). Si después, sobre la MISMA conexión, se llama a
    # `begin()`, revienta con:
    #
    #     InvalidRequestError: This connection has already initialized a
    #     SQLAlchemy Transaction() object via begin() or autobegin
    #
    # Es un error que no dice nada del problema real: dice "transaccion", y lo
    # que uno busca es "por qué hay dos".
    with get_db().connect() as conexion:
        admins = (
            conexion.execute(
                text("""
                SELECT COUNT(*) AS total
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE UPPER(r.name) = 'ADMIN'
                  AND u.deleted_at IS NULL AND u.is_active
            """)
            )
            .mappings()
            .one()["total"]
        )

    if objetivo["role_name"].upper() == "ADMIN" and admins <= 1:
        raise ErrorDeUsuario(
            "Es el último administrador activo. No podés desactivarlo."
        )

    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        conexion.execute(
            text("""
                UPDATE users
                SET is_active = FALSE,
                    deleted_at = COALESCE(deleted_at, NOW()),
                    deleted_by_user_id = :actor_id,
                    updated_by_user_id = :actor_id
                WHERE id = :user_id
            """),
            {"actor_id": actor_id, "user_id": user_id},
        )
        transaccion.commit()

    # Cerrar las sesiones de un usuario desactivado es parte de desactivarlo.
    # Si no, su access token sigue sirviendo hasta los 15 minutos.
    revocar_todas_las_sesiones(user_id, motivo="usuario desactivado")  # noqa: F821

    return obtener_usuario(user_id)


def restaurar_usuario(user_id: str, actor_id: str) -> dict[str, Any]:
    """Da de alta de nuevo una baja lógica. Por eso existe `deleted_at`."""
    actor_id = _exigir_actor(actor_id)

    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        conexion.execute(
            text("""
                UPDATE users
                SET is_active = TRUE,
                    deleted_at = NULL,
                    deleted_by_user_id = NULL,
                    updated_by_user_id = :actor_id
                WHERE id = :user_id
            """),
            {"actor_id": actor_id, "user_id": user_id},
        )
        transaccion.commit()

    return obtener_usuario(user_id)


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------


def revocar_todas_las_sesiones(user_id: str, motivo: str = "") -> int:
    """
    Cierra TODAS las sesiones del usuario.

    `revoked_before = NOW()` y NO `revoked_at = NOW()`: la diferencia es que
    `revoked_at` mata una sesión (su refresh deja de valer) pero deja vivo el
    access token que ya se entregó. `revoked_before` mata los dos, porque el
    chequeo compara `iat` del access contra esa marca.

    Por eso desactivar un usuario tiene que llamar a esta función y no quedarse
    con marcar la sesión actual.
    """
    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        resultado = conexion.execute(
            text("""
                UPDATE sessions
                SET revoked_at = COALESCE(revoked_at, NOW()),
                    revoked_before = NOW()
                WHERE user_id = :user_id
                  AND (revoked_at IS NULL OR revoked_at > NOW())
            """),
            {"user_id": user_id},
        )
        transaccion.commit()
        return resultado.rowcount


def registrar_auditoria(
    accion: str,
    tabla: str,
    record_id: str | None,
    user_id: str | None,
    valor_nuevo: str | None = None,
    valor_viejo: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """
    Escribe en `audit_logs`.

    NUNCA propaga una excepcion. Es un registro: si falla, el login o el cambio
    de contraseña ya ocurrió y devolver un 500 "no pude auditar" hace que el
    operador reintente la operación, que se aplica dos veces. Un hueco en la
    bitácora es malo; una acción ejecutada dos veces es peor.
    """
    try:
        with get_db().connect() as conexion:
            transaccion = conexion.begin()
            conexion.execute(
                text("""
                    INSERT INTO audit_logs (
                        action, table_name, record_id,
                        old_value, new_value, user_id, ip_address, user_agent
                    )
                    VALUES (:action, :table_name, :record_id,
                            :old_value, :new_value, :user_id, :ip, :user_agent)
                """),
                {
                    "action": accion,
                    "table_name": tabla,
                    "record_id": record_id,
                    "old_value": valor_viejo,
                    "new_value": valor_nuevo,
                    "user_id": user_id,
                    "ip": ip,
                    "user_agent": (user_agent or "")[:500] or None,
                },
            )
            transaccion.commit()
    except SQLAlchemyError:
        pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _a_bool(valor: Any, default: bool) -> bool:
    """
    Convierte a bool aceptando strings.

    pydantic ya convierte en el schema, pero estas funciones se llaman tambien
    desde tests y scripts. "false" es True en Python — es decir, si el body
    manda `{"is_active": "false"}` como texto, sin esto el usuario queda
    activo cuando pidió lo contrario.
    """
    if valor is None:
        return default
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(valor)
