"""
Router de autenticación y usuarios.

Dos routers en un archivo porque comparten casi todo:

  /api/auth/*    público salvo donde dice lo contrario
  /api/users/*   requiere token, y permisos según el rol

CÓMO SE TRADUCEN LOS ERRORES
----------------------------
`ErrorDeUsuario`  -> 400. Es un error de negocio: el email ya existe, el rol no
                     existe, la contraseña no cumple las reglas. El mensaje va
                     al usuario tal cual porque explica qué corregir.
`UsuarioNoEncontrado` -> 404.
`CredencialesInvalidas` / `SesionInvalida` -> 401.
`ErrorDePassword` -> 400. Es subclase de ValueError, no de ErrorDeUsuario, para
                     que el router pueda diferenciarla.

Un 403 NUNCA significa "no estás autenticado". Si el usuario no tiene permiso es
porque se autenticó y le falta permiso: 403. Si no tiene quién es, 401.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.core.audit import registrar as audit_log
from src.core.deps import exigir, exigir_admin, get_current_user
from src.core.permissions import Permiso
from src.core.security import ErrorDePassword
from src.modules.users import auth as auth_service
from src.modules.users import service as user_service
from src.modules.users.models import (
    ErrorDeUsuario,
    SesionInvalida,
    UsuarioActual,
    UsuarioNoEncontrado,
    listar_roles,
)
from src.modules.users.schemas import (
    ActualizarUsuarioRequest,
    CambiarPasswordRequest,
    CrearUsuarioRequest,
    ListaUsuariosResponse,
    LoginRequest,
    LoginResponse,
    MiPerfilResponse,
    RefreshRequest,
    ResetPasswordRequest,
    SesionActiva,
    TokenResponse,
    UsuarioResumen,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
usuarios_router = APIRouter(prefix="/api/users", tags=["usuarios"])


def _traducir_error(excepcion: Exception) -> HTTPException:
    """
    Convierte una excepción de la capa de servicio en una HTTPException.

    Centralizado para que el mapeo sea IGUAL en los once endpoints. Si cada
    uno hiciera su propio try/except, uno se olvidaría de mapear `ErrorDeUsuario`
    y devolvería un 500 por un email duplicado.
    """
    if isinstance(excepcion, ErrorDePassword):
        return HTTPException(status_code=400, detail=str(excepcion))
    if isinstance(excepcion, UsuarioNoEncontrado):
        return HTTPException(status_code=404, detail=str(excepcion))
    if isinstance(excepcion, ErrorDeUsuario):
        # 409 para duplicados (el cliente debería reintentar con otro dato),
        # 400 para el resto.
        codigo = 409 if "ya está registrado" in str(excepcion) else 400
        return HTTPException(status_code=codigo, detail=str(excepcion))
    return HTTPException(status_code=500, detail="Error interno.")


def _ip(request: Request) -> str:
    """
    IP del cliente.

    Se lee `X-Forwarded-For` porque detrás de un proxy o ingress la IP real no
    está en la conexión. Se toma el PRIMER valor de la cadena, que es el
    cliente original, no el último proxy.

    ADVERTENCIA: `X-Forwarded-For` lo pone cualquiera. Sirve para auditoría
    interna; NO para decides permisos. En un despliegue donde importara, esto
    tiene que ser un header que SOLO pone el proxy de confianza.
    """
    encabezado = request.headers.get("x-forwarded-for")
    if encabezado:
        return encabezado.split(",")[0].strip()[:45]
    return request.client.host if request.client else None


def _agente(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:500]


# ===========================================================================
# /api/auth
# ===========================================================================


@router.post("/api/auth/login", response_model=LoginResponse, summary="Iniciar sesión")
def login(datos: LoginRequest, request: Request):
    """
    Devuelve access + refresh.

    Un error de login responde SIEMPRE el mismo mensaje, exista o no el email:
    distinguirlo permite enumerar qué usuarios tiene el sistema.
    """
    try:
        resultado = auth_service.login(
            email=datos.email,
            password=datos.password,
            ip=_ip(request),
            user_agent=_agente(request),
        )
        audit_log(
            user_id=getattr(getattr(resultado, "usuario", None), "id", None),
            action="auth.login",
            resource=datos.email,
            ip_address=_ip(request),
            request_id=request.state.request_id if hasattr(request, "state") else None,
        )
        return resultado
    except auth_service.CredencialesInvalidas as exc:
        audit_log(
            user_id=None,
            action="auth.login_failed",
            resource=datos.email,
            ip_address=_ip(request),
            request_id=request.state.request_id if hasattr(request, "state") else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:
        logger.exception("Error inesperado en login")
        raise HTTPException(status_code=500, detail="Error interno.") from exc


@router.get(
    "/api/auth/password-strength",
    summary="Verificar fortaleza de contraseña",
)
def verificar_fortaleza_password(password: str):
    """
    Verifica la fortaleza de una contraseña sin guardarla.
    Útil para medidores de fortaleza en tiempo real en el frontend.
    """
    from src.core.security import obtener_info_fortaleza_password
    return obtener_info_fortaleza_password(password)


@router.post(
    "/api/auth/refresh",
    response_model=TokenResponse,
    summary="Renovar el access token",
)
def refrescar(datos: RefreshRequest, request: Request):
    """
    Canjea el refresh por un access nuevo y ROTA el refresh.

    Si un refresh ya usado vuelve, se cierran todas las sesiones del usuario:
    dos usos del mismo token solo significan una cosa, y es que alguien tiene
    una copia.
    """
    try:
        return auth_service.refresh(datos.refresh_token, ip=_ip(request))
    except (auth_service.CredencialesInvalidas, SesionInvalida) as exc:
        # `SesionInvalida` es la que lanza `refresh` cuando el token esta
        # revocado, vencido o es de otro tipo. Sin esta linea, un refresh
        # reutilizado —que es la senal de que el token esta clonado— llegaba
        # al usuario como un 500 en vez de un 401 con el motivo, y el operador
        # no tenia forma de entender que paso.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/api/auth/logout", summary="Cerrar esta sesión")
def logout(
    usuario: UsuarioActual = Depends(get_current_user),
    request: Request = None,
):
    """Cierra SOLO esta sesión. Para cerrar todas: `POST /api/auth/logout-all`."""
    cerrado = auth_service.logout(usuario.session_id, usuario.id)
    return {"mensaje": "Sesión cerrada." if cerrado else "La sesión ya estaba cerrada."}


@router.post("/api/auth/logout-all", summary="Cerrar todas las sesiones")
def logout_todas(usuario: UsuarioActual = Depends(get_current_user)):
    """
    Cierra todas las sesiones, incluidos los access tokens ya entregados.

    Es el botón de "me robaron la contraseña": sin esto, un token que ya está en
    la mano de alguien sigue sirviendo hasta 15 minutos.
    """
    cantidad = user_service.revocar_todas_las_sesiones(usuario.id, motivo="logout-all")
    return {"mensaje": f"Se cerraron {cantidad} sesión(es)."}


@router.get("/api/auth/yo", response_model=MiPerfilResponse, summary="Mi perfil")
def mi_perfil(usuario: UsuarioActual = Depends(get_current_user)):
    """
    Usuario autenticado con sus permisos resueltos.

    `permisos` viene del SERVIDOR, del diccionario de roles, no del JWT: el
    token puede llevar un rol viejo de hace 15 minutos, y el frontend tiene que
    saber qué puede hacer HORA.
    """
    datos = user_service.obtener_usuario(usuario.id)
    return MiPerfilResponse(
        usuario=UsuarioResumen(**datos),
        permisos=sorted(usuario.permisos),
        sesiones_activas=len(auth_service.mis_sesiones(usuario.id)),
    )


@router.get(
    "/api/auth/sesiones",
    response_model=list[SesionActiva],
    summary="Mis sesiones activas",
)
def mis_sesiones(usuario: UsuarioActual = Depends(get_current_user)):
    """Para ver desde dónde se conectó la cuenta y cerrar accesos ajenos."""
    return auth_service.mis_sesiones(usuario.id)


@router.post("/api/auth/cambiar-password", summary="Cambiar mi contraseña")
def cambiar_mi_password(
    datos: CambiarPasswordRequest,
    request: Request,
    usuario: UsuarioActual = Depends(get_current_user),
):
    """
    Cambio por "mi cuenta". Pide la contraseña ACTUAL.

    Sin eso, cualquiera con un token robado se cambia la contraseña y echa al
    dueño de su propia cuenta.

    Además revoca todas las otras sesiones: si el cambio se hizo porque
    sospechás de una intrusión, dejarlas vivas es justo lo que no hay que hacer.
    """
    try:
        auth_service.cambiar_password_por_api(
            usuario_id=usuario.id,
            password_actual=datos.password_actual,
            password_nueva=datos.password_nueva,
            ip=_ip(request),
        )
    except (ErrorDePassword, ErrorDeUsuario) as exc:
        raise _traducir_error(exc) from exc

    return {"mensaje": "Contraseña actualizada. Todas las sesiones se cerraron."}


# ===========================================================================
# /api/users
# ===========================================================================


@usuarios_router.post(
    "",
    response_model=UsuarioResumen,
    status_code=201,
    summary="Crear usuario",
)
def crear(
    datos: CrearUsuarioRequest,
    request: Request,
    actor: UsuarioActual = Depends(exigir(Permiso.USERS_CREAR)),
):
    """
    Alta de usuario.

    Requiere `users:create`. Solo ADMIN lo tiene: un técnico que puede crear
    usuarios podría crearse a sí mismo uno con otro rol.

    No hay registro público: en un sistema interno cualquiera con acceso a la
    API podría darse de alta como operador.
    """
    try:
        nuevo = user_service.crear_usuario(datos.model_dump(), actor.id)
    except (ErrorDePassword, ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc

    user_service.registrar_auditoria(
        accion="INSERT",
        tabla="users",
        record_id=nuevo["id"],
        user_id=actor.id,
        valor_nuevo=f"{nuevo['email']} rol={nuevo['role_name']}",
        ip=_ip(request),
        user_agent=_agente(request),
    )
    audit_log(
        user_id=actor.id,
        action="users.create",
        resource=str(nuevo["id"]),
        ip_address=_ip(request),
        request_id=request.state.request_id if hasattr(request, "state") else None,
        metadata={"email": nuevo["email"], "role": nuevo["role_name"]},
    )

    return UsuarioResumen(**nuevo)


@usuarios_router.get(
    "",
    response_model=ListaUsuariosResponse,
    summary="Listar usuarios",
)
def listar(
    solo_activos: bool = True,
    busqueda: str = "",
    rol: str = "",
    limite: int = 20,
    offset: int = 0,
    _: UsuarioActual = Depends(exigir(Permiso.USERS_VER)),
):
    """
    Listado paginado con filtros.

    El `LIMIT` tiene tope en el schema para que un `?limite=999999` no se
    convierta en un SELECT de toda la tabla a la memoria del proceso.
    """
    try:
        resultado = user_service.listar_usuarios(
            solo_activos=solo_activos,
            busqueda=busqueda or None,
            rol=rol or None,
            limite=min(limite, 100),
            offset=offset,
        )
    except Exception as exc:
        logger.exception("Error listando usuarios")
        raise HTTPException(status_code=500, detail="Error interno.") from exc

    return ListaUsuariosResponse(
        usuarios=[UsuarioResumen(**u) for u in resultado["usuarios"]],
        total=resultado["total"],
        limite=resultado["limite"],
        offset=resultado["offset"],
    )


@usuarios_router.get("/roles", summary="Roles disponibles")
def roles(_: UsuarioActual = Depends(get_current_user)):
    """Catálogo de roles, para los formularios del frontend."""
    return {"roles": listar_roles()}


@usuarios_router.get(
    "/{user_id}",
    response_model=UsuarioResumen,
    summary="Obtener un usuario",
)
def obtener(
    user_id: str,
    _: UsuarioActual = Depends(exigir(Permiso.USERS_VER)),
):
    """
    Un usuario por id.

    Un ADMIN puede ver cualquiera. Un rol con `users:read` menor no debería
    poder ver la lista completa, pero hoy el permiso es único: si aparece un rol
    que deba ver solo su propia cuenta, esto necesita el chequeo de "soy yo o
    soy admin".
    """
    try:
        return UsuarioResumen(**user_service.obtener_usuario(user_id))
    except UsuarioNoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@usuarios_router.patch(
    "/{user_id}",
    response_model=UsuarioResumen,
    summary="Actualizar usuario",
)
def actualizar(
    user_id: str,
    datos: ActualizarUsuarioRequest,
    request: Request,
    actor: UsuarioActual = Depends(exigir(Permiso.USERS_EDITAR)),
):
    """Edición parcial. Solo se tocan los campos enviados."""
    cambios = datos.model_dump(exclude_none=True)

    if not cambios:
        raise HTTPException(status_code=400, detail="No se envió ningún campo.")

    try:
        anterior = user_service.obtener_usuario(user_id)
        actualizado = user_service.actualizar_usuario(user_id, cambios, actor.id)
    except (ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc

    user_service.registrar_auditoria(
        accion="UPDATE",
        tabla="users",
        record_id=user_id,
        user_id=actor.id,
        valor_viejo=f"{anterior['full_name']} rol={anterior['role_name']} activo={anterior['is_active']}",
        valor_nuevo=f"{actualizado['full_name']} rol={actualizado['role_name']} activo={actualizado['is_active']}",
        ip=_ip(request),
        user_agent=_agente(request),
    )

    return UsuarioResumen(**actualizado)


@usuarios_router.post(
    "/{user_id}/reset-password",
    summary="Un ADMIN cambia la contraseña de otro",
)
def resetear_password(
    user_id: str,
    datos: ResetPasswordRequest,
    request: Request,
    actor: UsuarioActual = Depends(exigir(Permiso.USERS_RESTABLECER_PASSWORD)),
):
    """
    Reset por ADMIN.

    NO pide la contraseña actual: el admin no la sabe y no debe poder averiguarla.
    Se cierra el caso de uso real (un técnico olvidó su clave).

    A diferencia del cambio propio, NO se revoca todo: el admin no está
    cambiando su propia cuenta, y si el usuario está trabajando, cortarle todo
    sin avisar es disruptive. Para un robo, el admin usa `/api/users/{id}/desactivar`,
    que sí revoca.
    """
    try:
        user_service.cambiar_password(
            user_id=user_id,
            password_nueva=datos.password_nueva,
            actor_id=actor.id,
        )
    except (ErrorDePassword, ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc

    user_service.registrar_auditoria(
        accion="UPDATE",
        tabla="users",
        record_id=user_id,
        user_id=actor.id,
        valor_nuevo="password reseteado por admin",
        ip=_ip(request),
        user_agent=_agente(request),
    )

    return {"mensaje": "Contraseña actualizada."}


@usuarios_router.post(
    "/{user_id}/desactivar",
    response_model=UsuarioResumen,
    summary="Desactivar usuario",
)
def desactivar(
    user_id: str,
    request: Request,
    actor: UsuarioActual = Depends(exigir(Permiso.USERS_BORRAR)),
):
    """
    Baja lógica + cierre de todas sus sesiones.

    No borra la fila: `users` es llave foránea de `movement_history` y `repairs`,
    y un borrado físico se lleva el historial de todo lo que movió esa persona.
    """
    try:
        resultado = user_service.desactivar_usuario(user_id, actor.id)
    except (ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc

    user_service.registrar_auditoria(
        accion="DELETE",
        tabla="users",
        record_id=user_id,
        user_id=actor.id,
        valor_nuevo="desactivado (baja lógica)",
        ip=_ip(request),
    )
    audit_log(
        user_id=actor.id,
        action="users.deactivate",
        resource=user_id,
        ip_address=_ip(request),
        request_id=request.state.request_id if hasattr(request, "state") else None,
    )

    return UsuarioResumen(**resultado)


@usuarios_router.post(
    "/{user_id}/restaurar",
    response_model=UsuarioResumen,
    summary="Restaurar usuario",
)
def restaurar(
    user_id: str,
    actor: UsuarioActual = Depends(exigir(Permiso.USERS_EDITAR)),
):
    """Da de alta de nuevo una baja lógica."""
    try:
        resultado = user_service.restaurar_usuario(user_id, actor.id)
    except (ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc
    audit_log(
        user_id=actor.id,
        action="users.restore",
        resource=user_id,
        ip_address=None,
        request_id=None,
    )
    return UsuarioResumen(**resultado)


@usuarios_router.post(
    "/{user_id}/degradar-admin",
    response_model=UsuarioResumen,
    summary="Quitarle el rol ADMIN (incluso al último)",
)
def degradar(
    user_id: str,
    actor: UsuarioActual = Depends(exigir_admin),
):
    """
    Degradar a OPERATOR. El unico camino para quitar ADMIN.

    Está separado de `PATCH /users/{id}` a propósito: si degradar fuera un
    campo más, bastaría con que nadie lo bloquee y un admin nuevo podría
    degradar al último admin sin querer. Acá la regla vive en el servicio.

    Tampoco se puede degradar a uno mismo: deja el sistema sin nadie que se
    degrade de vuelta.
    """
    if str(actor.id) == str(user_id):
        raise HTTPException(
            status_code=400,
            detail="No podés degradarte a vos mismo.",
        )

    try:
        return UsuarioResumen(**user_service.degradar_admin(user_id, actor.id))
    except (ErrorDeUsuario, UsuarioNoEncontrado) as exc:
        raise _traducir_error(exc) from exc
