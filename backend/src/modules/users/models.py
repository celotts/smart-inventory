"""
Modelos de datos de usuarios y sesiones.

POR QUE NO HAY ORM
------------------
El resto del proyecto usa SQLAlchemy, pero el acceso a `users` esta escrito a
mano con `text()` por dos razones concretas:

  1. La tabla tiene triggers de auditoria (`set_audit_role_id`) que copian el
     rol de quien hizo el cambio. Con un ORM hay que acordarse de setear
     `created_by_user_id` a mano en cada alta; aca se centraliza en `_preparar_`.
  2. Es la tabla más sensible del sistema. SQL explicito se lee entero y se
     revisa; un `session.add(...)` esconde en que columnas se escribe.

`src.modules.ai.executor` tiene el patron equivalente para las consultas de la
IA: pool chico, helpers de fila.

EL CAMPO `password_hash` NUNCA SALE
----------------------------------
No existe un `SELECT *` en este archivo. Todos arman la lista de columnas a
mano justamente para que agregar una columna sensible despues no la exponga por
olvido. Es el mismo criterio que el denylist de columnas de `guard.py` para el
LLM: la lista se escribe a mano porque es lo que obliga a notar el cambio.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import create_engine, text

from src.core.config import get_settings

_engine = None


def get_db():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_size=5,
            max_overflow=3,
            pool_pre_ping=True,
        )
    return _engine


# Columnas que un administrador puede ver de otro usuario. `password` NO está:
# ni el admin ni nadie deberia poder leer un hash, y no exponerlo ni por
# error es la razon de escribir la lista a mano en vez de `SELECT *`.
COLUMNAS_USUARIO = """
    u.id, u.email, u.full_name, u.address, u.phone, u.phone2,
    u.is_active, u.is_specialist, u.role_id,
    u.created_at, u.updated_at, u.deleted_at,
    u.password_changed_at, u.password_must_change, u.password_rotation_days,
    u.totp_enabled, u.totp_last_used_at
"""


class ErrorDeUsuario(Exception):
    """Error de negocio: email duplicado, rol inexistente. Se traduce a 400/409."""


class UsuarioNoEncontrado(LookupError):
    """No existe un usuario activo con ese id/email."""


class SesionInvalida(Exception):
    """Refresh token desconocido, vencido o revocado. Se traduce a 401."""


@dataclass
class UsuarioActual:
    """El usuario autenticado, para el resto de la request."""

    id: str
    email: str
    full_name: str
    role_id: str
    role_name: str
    is_specialist: bool
    session_id: str | None
    # Se llena desde `core.permissions`, NO desde el token: el rol del JWT
    # puede estar viejo si alguien cambió de rol hace menos de 15 minutos.
    permisos: set[str] = field(default_factory=set)

    def puede(self, permiso: str) -> bool:
        return permiso in self.permisos


def _normalizar_fila(fila: dict[str, Any]) -> dict[str, Any]:
    """
    Convierte lo que devuelve psycopg2 a lo que declaran los schemas.

    `UUID` -> `str`. TODOS los schemas de este módulo declaran los ids como
    `str`, y el driver devuelve `uuid.UUID`. Sin está conversión, pydantic
    rechaza la respuesta con

        ResponseValidationError: {'type': 'string_type', 'input': UUID(...)}

    que es un 500 en un login que ya verifico la password. O sea: el fallo
    aparece en el último paso y no dice nada de por que.

    La conversión va Acá y no en cada schema porque el problema es del driver,
    no de un endpoint. Convertir en cada schema significa que el próximo
    endpoint que se escriba se olvide, y el error vuelve a aparecer.
    """
    import datetime
    import uuid

    resultado: dict[str, Any] = {}
    for clave, valor in fila.items():
        if isinstance(valor, uuid.UUID):
            resultado[clave] = str(valor)
        elif isinstance(valor, (datetime.datetime, datetime.date)):
            # pydantic los maneja bien, pero dejarlos como datetime da
            # respuestas con formato distinto según el endpoint.
            resultado[clave] = valor
        elif isinstance(valor, dict):
            resultado[clave] = _normalizar_fila(valor)
        else:
            resultado[clave] = valor
    return resultado


def _fila_a_dict(fila) -> dict[str, Any]:
    if fila is None:
        return {}
    return _normalizar_fila(dict(fila))


def buscar_usuario_por_id(user_id: str) -> dict[str, Any] | None:
    """
    Usuario por id, con su rol. Devuelve `deleted_at` a propósito: la dependencia
    de autenticación lo necesita para rechazar cuentas dadas de baja.

    Se busca por id y NO por email: el email es un dato que el usuario puede
    cambiar, y usar un dato mutable como identidad hace que "cambiarme el mail"
    rompa la sesión de un usuario que en realidad es el mismo.
    """
    if not user_id:
        return None

    with get_db().connect() as conexion:
        fila = (
            conexion.execute(
                text(f"""
                SELECT {COLUMNAS_USUARIO}, r.name AS role_name
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE u.id = :user_id
            """),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )

    return _fila_a_dict(fila) if fila else None


def buscar_usuario_por_email(email: str, incluir_password: bool = False):
    """
    Usuario por email.

    `incluir_password=True` Solo para el login. Es un parámetro explicito y no
    un flag global porque el default seguro tiene que ser "sin hash".
    """
    columnas = COLUMNAS_USUARIO
    if incluir_password:
        columnas += ", u.password AS password_hash"

    with get_db().connect() as conexion:
        fila = (
            conexion.execute(
                text(f"""
                SELECT {columnas}, r.name AS role_name
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE LOWER(u.email) = LOWER(:email)
            """),
                {"email": (email or "").strip()},
            )
            .mappings()
            .first()
        )

    return _fila_a_dict(fila) if fila else None


def listar_roles() -> list[dict[str, Any]]:
    with get_db().connect() as conexion:
        filas = (
            conexion.execute(
                text("""
                SELECT id, name FROM roles
                WHERE deleted_at IS NULL ORDER BY name
            """)
            )
            .mappings()
            .all()
        )
    return [_fila_a_dict(f) for f in filas]


def resolver_rol_id(role_name: str) -> str:
    """
    Nombre de rol -> id. Lanza si no existe.

    NO se acepta un role_id suelto del cliente: si lo aceptara, cualquiera que
    pueda crear usuarios podria inventar un UUID y asignar un rol que no existe,
    y el usuario quedaria con cero permisos sin que nadie lo note. El nombre
    es lo que se válida contra la tabla.
    """
    with get_db().connect() as conexion:
        fila = (
            conexion.execute(
                text("""
                SELECT id FROM roles
                WHERE UPPER(name) = UPPER(:name) AND deleted_at IS NULL
            """),
                {"name": (role_name or "").strip()},
            )
            .mappings()
            .first()
        )

    if fila is None:
        with get_db().connect() as conexion:
            existentes = [
                r["name"]
                for r in conexion.execute(
                    text(
                        "SELECT name FROM roles WHERE deleted_at IS NULL ORDER BY name"
                    )
                )
                .mappings()
                .all()
            ]
        raise ErrorDeUsuario(
            f"El rol '{role_name}' no existe. Roles válidos: {', '.join(existentes)}."
        )

    return fila["id"]


def roles_de_usuario(user_id: str) -> list[str]:
    with get_db().connect() as conexion:
        filas = (
            conexion.execute(
                text("""
                SELECT r.name
                FROM users u
                JOIN roles r ON r.id = u.role_id
                WHERE u.id = :user_id
            """),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    return [f["name"] for f in filas]
