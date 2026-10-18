"""
Permisos por rol.

QUE HAY ACA Y QUE NO
--------------------
Un diccionario `rol -> set de permisos`. No una tabla `permissions`.

La tabla/configuracion por permisos es mas flexible y es lo que se arma cuando
los roles son DEFINIDOS POR EL PRODUCTO: "el administrador puede hacer todo",
"el tecnico solo trabaja en el taller". Cambiar eso requiere un deploy, y eso
esta bien: el control de acceso es codigo, no un dato que alguien pueda editar
en pantalla y abrirse un hueco.

Lo que NO esta resuelto: si algun dia un ADMIN necesita crear roles nuevos sin
deploy, ahi si va tabla. Ese salto es un problema para cuando aparezca, y hoy
seria una tabla dinamica de mas que mantener.

REGLA DE DISENO
---------------
ADMIN tiene `TODOS`. Los permisos se evaluan con `tiene()`, nunca comparando
el rol a mano en los endpoints. Si el chequeo lo hace cada router por su cuenta,
la proxima funcionalidad se olvida del chequeo y queda abierta.
"""

from enum import Enum


class Permiso(str, Enum):
    """
    Permisos como `str, Enum` para que serialicen a JSON tal cual y el
    frontend pueda hacer `permisos.includes("users:create")` sin mapear.
    """

    # --- Usuarios ---
    USERS_VER = "users:read"
    USERS_CREAR = "users:create"
    USERS_EDITAR = "users:edit"
    USERS_BORRAR = "users:delete"
    USERS_RESTABLECER_PASSWORD = "users:reset_password"

    # --- Roles ---
    ROLES_VER = "roles:read"
    ROLES_EDITAR = "roles:edit"

    # --- Inventario ---
    DISPOSITIVOS_VER = "devices:read"
    DISPOSITIVOS_EDITAR = "devices:edit"
    MOVIMIENTOS_VER = "movements:read"
    MOVIMIENTOS_CREAR = "movements:create"

    # --- Taller ---
    REPARACIONES_VER = "repairs:read"
    REPARACIONES_EDITAR = "repairs:edit"
    REPARACIONES_ASIGNAR = "repairs:assign"

    # --- IA ---
    IA_PREGUNTAR = "ai:query"
    IA_ANALIZAR = "ai:analyze"
    IA_REPORTE = "ai:report"

    # --- Auditoria ---
    AUDITORIA_VER = "audit:read"


# Los tres roles que siembra init.sql. Los nombres TIENEN que coincidir con los
# de la tabla `roles`, y si no coincide el permiso es `sin permisos`, que es lo
# correcto: un rol desconocido no es un rol administrador.
PERMISOS_POR_ROL: dict[str, set[str]] = {
    "ADMIN": set(Permiso),  # todo
    "TECHNICIAN": {
        # El tecnico trabaja en el taller: ve la flota para contexto y opera
        # las reparaciones. NO ve la lista de usuarios ni el panel de auditoría:
        # no son datos que necesite para trabajar.
        Permiso.DISPOSITIVOS_VER,
        Permiso.REPARACIONES_VER,
        Permiso.REPARACIONES_EDITAR,
        Permiso.IA_PREGUNTAR,
        Permiso.IA_ANALIZAR,
        Permiso.IA_REPORTE,
    },
    "OPERATOR": {
        # El operador mueve equipos: escanea, registra movimientos, consulta el
        # inventario. No toca reparaciones ni usuarios.
        Permiso.DISPOSITIVOS_VER,
        Permiso.MOVIMIENTOS_VER,
        Permiso.MOVIMIENTOS_CREAR,
        Permiso.IA_PREGUNTAR,
        Permiso.IA_REPORTE,
    },
}

# Usuario sin rol, o con un rol que no existe en el diccionario. Sin permisos:
# falla cerrado. Es la decisión importante — si un rol desconocido devolviera
# todos los permisos, bastaría con escribir mal un nombre en la base para
# tener acceso total.
PERMISOS_NINGUNO: set[str] = set()


def permisos_de(rol: str) -> set[str]:
    """Permisos del rol. Rol desconocido -> ninguno."""
    if not rol:
        return PERMISOS_NINGUNO
    return PERMISOS_POR_ROL.get(rol.upper(), PERMISOS_NINGUNO)


def tiene(rol: str, permiso: Permiso) -> bool:
    """El unico chequeo de permisos del sistema."""
    return permiso.value in permisos_de(rol)


def es_admin(rol: str) -> bool:
    return (rol or "").upper() == "ADMIN"
