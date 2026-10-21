"""
Dependencias de FastAPI: quien es el usuario de está request.

DOnde se centraliza
-------------------
Cada endpoint protected hace `Depends(exigir(Permiso.X))`. El chequeo NO está
en el router: está acá, para que un endpoint no pueda olvidarse de la regla de
negocio y solo controlar la sintaxis.

LA DEPENDENCIA DE Sesión, Y POR QUE ES OBLIGATORIA
---------------------------------------------------
`get_current_user` con solo verificar la FIRMA del JWT no alcanza para cerrar el
logout. Un access token es válido hasta 15 minutos Después de que el usuario
haga logout o de que un ADMIN le cierre las sesiones: si no se mira la base, la
revocacion no existe y "cerrar sesión" es una sugerencia.

Por eso en CADA request se toca la tabla `sessions`:
  - se verifica que la sesión no este revocada,
  - se verifica `revoked_before` contra el `iat` del token,
  - se actualiza `last_used_at`.

Eso es una query por request, y es el precio de la revocacion. Si algun día
molesta, el camino no es volver a JWT stateless sino un cache corto (Redis) de
sesiones revocadas. Lo que no se puede es creer que se puede quitar.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.permissions import Permiso, permisos_de, tiene
from src.core.security import TokenInvalido, decodificar_token
from src.modules.users.models import UsuarioActual, buscar_usuario_por_id

# auto_error=False para que el 401 sea NUESTRO y con el detalle correcto.
# Con auto_error=True (el default), FastAPI devuelve un 403 generico que no dice
# si falta el token, si está vencido o si es inválido.
esquema_bearer = HTTPBearer(auto_error=False)


def obtener_token(
    credenciales: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
) -> str:
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credenciales.credentials


def get_current_user(
    token: str = Depends(obtener_token),
) -> UsuarioActual:
    """
    Usuario autenticado, o 401.

    OJO con el status: 401 es "no sabes quien sos, autenticate". 403 es "sos
    quien sos pero no te dejo". Confundirlos hace que un frontend muestre
    "no tenes permiso" a alguien a quien solo le falta loguearse.
    """
    try:
        payload = decodificar_token(token, "access")
    except TokenInvalido as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    session_id = payload.get("sid")

    usuario = buscar_usuario_por_id(user_id)

    if usuario is None:
        # El token es válido y firmado con nuestra key, pero el usuario no
        # existe: fue borrado mientras el token estaba vivo.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El usuario de este token ya no existe.",
        )

    # El token dice ADMIN y el usuario dice OPERATOR? Se gana la base. El rol
    # viaja en el JWT para no ir a buscarlo en cada permiso, así que puede
    # estar viejo si alguien cambió de rol hace 15 minutos.
    #
    # Por eso los permisos se leen de la base, NO del token: el token solo
    # sirve para saber QUIEN es.
    rol_actual = usuario["role_name"]

    if not usuario["is_active"] or usuario["deleted_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta está desactivada. Contacta al administrador.",
        )

    if session_id and not _sesion_valida(session_id, payload.get("iat")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión fue cerrada. Volve a iniciar sesión.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return UsuarioActual(
        id=usuario["id"],
        email=usuario["email"],
        full_name=usuario["full_name"],
        role_id=usuario["role_id"],
        role_name=rol_actual,
        is_specialist=usuario["is_specialist"],
        session_id=session_id,
        permisos=permisos_de(rol_actual),
    )


def _sesion_valida(session_id: str, emitido_en: int | None) -> bool:
    """
    La sesión no fue revocada, y el token no es anterior a una revocacion
    general del usuario.

    El `emitted_en` se compara contra `revoked_before` porque un access token
    NO tiene fila propia en `sessions`: lo que se guarda es el refresh. Si el
    usuario renovo su sesión hace un minuto, su `revoked_before` viejo no lo
    tiene que matar.
    """
    try:
        with obtener_engine().connect() as conexion:
            fila = (
                conexion.execute(
                    text(
                        """
                    SELECT revoked_at, revoked_before
                    FROM sessions WHERE id = :session_id
                    """
                    ),
                    {"session_id": session_id},
                )
                .mappings()
                .first()
            )

        if fila is None:
            # El token tiene `sid` de una sesión que ya se purgo por expiracion.
            # Se permite: la fila se limpia sola y no debe tumbar el sistema.
            return True

        if fila["revoked_at"] is not None:
            return False

        if fila["revoked_before"] is not None and emitido_en is not None:
            if emitido_en < fila["revoked_before"].timestamp():
                return False

        return True
    except SQLAlchemyError:
        # Si la base no responde, NO se abre la puerta. Fallar hacia el
        # permiso es lo que hace un sistema de auth vulnerable a la caida.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo verificar la sesión. Intenta de nuevo en un momento.",
        )


def exigir(*requeridos: Permiso):
    """
    Dependencia que exige permisos. Uso:

        @router.delete("/{user_id}")
        def borrar(..., _=Depends(exigir(Permiso.USERS_BORRAR))):

    Devolver una funcion en vez de repetir el `if` en cada endpoint es lo que
    hace que un endpoint nuevo nazca con su chequeo puesto: hay que escribirlo
    para que el endpoint exista.
    """

    def _checker(usuario: UsuarioActual = Depends(get_current_user)) -> UsuarioActual:
        faltantes = [p.value for p in requeridos if not tiene(usuario.role_name, p)]

        if faltantes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "mensaje": "No tenes permiso para esta operacion.",
                    "requeridos": [p.value for p in requeridos],
                    "faltantes": faltantes,
                    "tu_rol": usuario.role_name,
                },
            )
        return usuario

    return _checker


def exigir_admin(usuario: UsuarioActual = Depends(get_current_user)) -> UsuarioActual:
    """
    Solo ADMIN.

    Existe separada de `exigir(...)` para las operaciones que son de
    administracion de ACCESO y no de un permiso concreto: dar de alta un
    administrador, por ejemplo. Que ese chequeo se escriba como "se requiere
    ADMIN" y no como un permiso más, evita que alguien lo abra con un
    permiso cualquiera en el futuro sin darse cuenta.
    """
    if usuario.role_name.upper() != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "mensaje": "Esta operacion es solo para administradores.",
                "tu_rol": usuario.role_name,
            },
        )
    return usuario


# Import diferido: `executor` importa de `config`, y `config` no importa nada
# de este módulo, así que se puede arriba. Se deja abajo para que el orden de
# lectura del archivo sea el natural.
def obtener_engine():
    from src.modules.ai.executor import get_ai_engine

    return get_ai_engine()
