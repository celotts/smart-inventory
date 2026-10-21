"""
Login, refresh y logout.

EL FLUJO COMPLETO
-----------------
    POST /api/auth/login
      -> access token  (JWT 15 min, NO se guarda)
      -> refresh token (JWT 30 días, se guarda HASHEADO en `sessions`)

    GET /api/...           Authorization: Bearer <access>
    POST /api/auth/refresh  Authorization: Bearer <refresh>  -> access nuevo
    POST /api/auth/logout   -> mata Está sesión

POR QUE HAY DOS TOKENS
----------------------
Uno solo obliga a elegir: corto (y hay que pedir uno nuevo cada 15 minutos) o
largo (y un token robado sirve para siempre).

Con dos, el access vive poco y el refresh se puede revocar. Es el mismo diseño
de las sesiones con cookies, donde la cookie larga es la que se puede borrar.

EL TIMING DE UN LOGIN FALLIDO
-----------------------------
Un login que responde en 3 ms para un email inexistente y 300 ms para uno
existente permite enumerar cuentas: se mide, se distingue el código de error
por latencia, y se obtiene una lista de todos los usuarios del sistema.

Por eso, cuando el email no existe, se verifica igual contra un hash DUMMY.
Cuesta lo mismo y devuelve lo mismo. Ver `_verificar_contra_dummy`.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import get_settings
from src.core.security import (
    TokenInvalido,
    crear_access_token,
    crear_refresh_token,
    decodificar_token,
    hashear_token,
    verificar_password,
)
from src.modules.users.models import (
    SesionInvalida,
    buscar_usuario_por_email,
    get_db,
)

# Hash bcrypt de una cadena que NADIE va a acertar. Cuesta lo mismo verificarlo
# que verificar una contraseña real.
#
# Es un bcrypt de "contraseña-que-nadie-va-a-adivinar-123" con costo 12. Se
# calcula una vez al importar el módulo: hashearlo en cada login fallido
# gastaria CPU de más sin agregar nada.
_HASH_DUMMY = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.5t5nJdMEGlphvQ9GUQQrMyYqvGVxe"


class CredencialesInvalidas(Exception):
    """Email o contraseña incorrectos. Mismo mensaje para los dos casos."""


def _verificar_contra_dummy(password: str) -> None:
    """
    Verifica contra un hash falso para igualar los tiempos.

    Sin esto: email inexistente -> 3 ms (no hay nada que verificar), email
    existente con contraseña mala -> 300 ms (bcrypt). La diferencia DELATA que
    emails existen.

    Con esto: los dos caminos cuestan un bcrypt, y el atacante no puede
    enumerar cuentas por latencia.
    """
    verificar_password(password or "", _HASH_DUMMY)


def login(
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """
    Autentica y abre sesión.

    Devuelve el access y el refresh EN EL CUERPO. NO en cookie: una API para
    clientes que probablemente son SPA o scripts. Para un navegador, la
    alternativa correcta es cookie `HttpOnly` + `Secure` + `SameSite`, que es
    un diseño distinto y no se improvisa con un token en el JSON.
    """
    config = get_settings()

    usuario = buscar_usuario_por_email(email, incluir_password=True)

    if usuario is None:
        # Se verifica igual para no filtrar que emails existen por latencia.
        _verificar_contra_dummy(password)
        raise CredencialesInvalidas("Email o contraseña incorrectos.")

    if not verificar_password(password, usuario["password_hash"]):
        raise CredencialesInvalidas("Email o contraseña incorrectos.")

    if usuario["deleted_at"] is not None:
        raise CredencialesInvalidas("Email o contraseña incorrectos.")

    if not usuario["is_active"]:
        # Acá SÍ se dice algo distinto, y es correcto: el usuario existe, se
        # equivoco de password o está desactivado. Decir "desactivado" no filtra
        # nada nuevo — para saberlo ya hubo que acertar la contraseña.
        raise CredencialesInvalidas(
            "Tu cuenta esta desactivada. Contacta al administrador."
        )

    tokens = _abrir_sesion(usuario, ip=ip, user_agent=user_agent)

    from src.modules.users.service import registrar_auditoria

    registrar_auditoria(
        accion="LOGIN",
        tabla="users",
        record_id=usuario["id"],
        user_id=usuario["id"],
        valor_nuevo=f"login desde {ip or 'ip desconocida'}",
        ip=ip,
        user_agent=user_agent,
    )

    return {
        **tokens,
        "usuario": {
            "id": usuario["id"],
            "email": usuario["email"],
            "full_name": usuario["full_name"],
            "role_name": usuario["role_name"],
        },
        "expira_en_segundos": config.access_token_expire_minutes * 60,
    }


def _abrir_sesion(usuario: dict[str, Any], ip=None, user_agent=None) -> dict[str, Any]:
    """Crea la fila en `sessions` y devuelve los dos tokens."""
    config = get_settings()

    refresh_token, expira_refresh = crear_refresh_token(usuario["id"])
    token_hash = hashear_token(refresh_token)

    with get_db().connect() as conexion:
        transaccion = conexion.begin()

        # Se inserta primero para tener el id de sesión, que va DENTRO del
        # access token: sin `sid` no se puede saber que sesión validar en cada
        # request.
        fila = (
            conexion.execute(
                text("""
                INSERT INTO sessions (
                    user_id, token_hash, expires_at, user_agent, ip_address
                )
                VALUES (:user_id, :token_hash, :expires_at, :user_agent, :ip)
                RETURNING id
            """),
                {
                    "user_id": usuario["id"],
                    "token_hash": token_hash,
                    "expires_at": expira_refresh,
                    "user_agent": (user_agent or "")[:500] or None,
                    "ip": (ip or "")[:45] or None,
                },
            )
            .mappings()
            .first()
        )

        transaccion.commit()

    session_id = fila["id"]

    access_token, _ = crear_access_token(
        user_id=usuario["id"],
        role_name=usuario["role_name"],
        session_id=session_id,
    )

    _limitar_sesiones(usuario["id"], conservar=session_id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "access_expires_in": config.access_token_expire_minutes * 60,
        "refresh_expires_in": config.refresh_token_expire_days * 86400,
        "session_id": session_id,
    }


def _limitar_sesiones(user_id: str, conservar: str | None = None) -> None:
    """
    Deja solo `max_sessions_per_user` sesiones vivas; corta la más vieja.

    Sin este tope, un login en bucle (un script, o alguien probando)
    acumula cientos de filas y cada refresh las recorre.

    `conservar` ES LA Sesion QUE SE Esta ABRIENDO, y no es una formalidad.
    --------------------------------------------------------------
    Sin esto pasaba esto: un usuario con 5 sesiones abiertas entra con una sexta
    desde el celular, `_limitar_sesiones` revoca la más vieja —que era la del
    navegador de escritorio, con la que estaba trabajando— y a los 15 minutos
    ese navegador recibe un 401 sin motivo apparent. El sintoma es "la app me
    cerro la sesion sola", que es la clase de fallo que hace que un usuario
    deje de confiar en el sistema.

    Pasa más de lo que parece porque `/auth/refresh` rota la sesion: cada
    renovacion crea una fila nueva y revoca la anterior, asi que un mismo
    dispositivo va dejando filas a su paso. Con dos pestañas abiertas y cinco
    sesiones, el corte cae donde caiga.

    EL DESEMPATE TIENE QUE SER DETERMINISTA
    ----------------------------------------
    `ORDER BY created_at DESC` solo no alcanza: si dos sesiones se crean en el
    mismo microsegundo —y pasando por `/auth/refresh` es lo normal— Postgres no
    garantiza un orden entre ellas, y la sesion recien creada puede caer fuera
    del corte. `id DESC` desempata de forma estable y siempre deja lo más
    reciente arriba.
    """
    config = get_settings()

    # El SELECT va FUERA de la transaccion: con el `begin()` de abajo, el orden
    # importa. Este bug estaba LATENTE — `begin()` solo se alcanzaba cuando
    # habia más de `max_sessions_per_user` sesiones, o sea en el sexto login.
    with get_db().connect() as conexion:
        filas = (
            conexion.execute(
                text("""
                SELECT id FROM sessions
                WHERE user_id = :user_id AND revoked_at IS NULL AND expires_at > NOW()
                ORDER BY created_at DESC, id DESC
            """),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )

    # La sesión nueva se aparta antes de contar. El `None` está para el caso de
    # que `conservar` no venga informado (limpieza manual, tests).
    candidatas = [f["id"] for f in filas if conservar is None or f["id"] != conservar]

    excedente = candidatas[max(0, config.max_sessions_per_user - 1) :]

    if excedente:
        with get_db().connect() as conexion:
            transaccion = conexion.begin()
            # IN con placeholders generados: los ids vienen de la base, pero
            # igual se parametrizan. El nombre del marcador lo genera el
            # índice, no el dato.
            marcadores = ",".join(f":s{i}" for i in range(len(excedente)))
            conexion.execute(
                text(f"""
                    UPDATE sessions
                    SET revoked_at = NOW()
                    WHERE id IN ({marcadores})
                """),
                {f"s{i}": s for i, s in enumerate(excedente)},
            )

            transaccion.commit()


def refresh(refresh_token: str, ip: str | None = None) -> dict[str, Any]:
    """
    Canjea un refresh por un access nuevo.

    ROTACION: el refresh viejo se revoca y se emite uno nuevo. Si alguien
    clono el token, los dos lo van a usar y el segundo va a fallar, que es la
    forma de detectar el robo.

    Sin rotar, un refresh robado sirve para siempre: se puede revocar la sesión,
    pero el atacante tiene una copia del token y abre otra sesión con el mismo
    valor.
    """
    config = get_settings()

    try:
        payload = decodificar_token(refresh_token, "refresh")
    except TokenInvalido as exc:
        raise SesionInvalida(str(exc)) from exc

    user_id = payload.get("sub")
    token_hash = hashear_token(refresh_token)

    with get_db().connect() as conexion:
        transaccion = conexion.begin()

        fila = (
            conexion.execute(
                text("""
                SELECT s.id, s.revoked_at, s.expires_at, s.revoked_before,
                       u.is_active, u.deleted_at, u.email, u.full_name,
                       r.name AS role_name
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                JOIN roles r ON r.id = u.role_id
                WHERE s.token_hash = :token_hash
                FOR UPDATE
            """),
                {"token_hash": token_hash},
            )
            .mappings()
            .first()
        )

        if fila is None:
            transaccion.rollback()
            raise SesionInvalida("Sesion desconocida. Volve a iniciar sesion.")

        if fila["revoked_at"] is not None:
            transaccion.rollback()
            # Un refresh ya usado que vuelve es la señal clasica de que el
            # token fue copiado. Se cierran TODAS las sesiones del usuario: si
            # hay dos usos, no se sabe cual es el legitimo.
            from src.modules.users.service import revocar_todas_las_sesiones

            revocar_todas_las_sesiones(user_id, motivo="refresh reutilizado")
            raise SesionInvalida(
                "Esta sesion ya se habia usado. Por seguridad se cerraron todas "
                "las sesiones: inicia sesion de nuevo."
            )

        if fila["expires_at"] <= datetime.now(timezone.utc):
            transaccion.rollback()
            raise SesionInvalida("La sesion expiro. Volve a iniciar sesion.")

        if not fila["is_active"] or fila["deleted_at"] is not None:
            transaccion.rollback()
            raise SesionInvalida("La cuenta esta desactivada.")

        # Se revoca la fila vieja y `_abrir_sesión` crea una nueva. El `sid`
        # cambia, y eso es correcto: el access anterior sigue sirviendo hasta
        # que expire (es su `exp`), pero ya no hay forma de renovarlo con ese
        # refresh, porque quedó revocado.
        #
        # `_limitar_sesiones` limpia las viejas para que `mis_sesiones` no
        # crezca sin límite.
        conexion.execute(
            text(
                "UPDATE sessions SET revoked_at = NOW(), last_used_at = NOW() WHERE id = :id"
            ),
            {"id": fila["id"]},
        )
        transaccion.commit()

    usuario = {
        "id": user_id,
        "email": fila["email"],
        "full_name": fila["full_name"],
        "role_name": fila["role_name"],
    }

    tokens = _abrir_sesion(usuario, ip=ip)
    tokens["access_expires_in"] = config.access_token_expire_minutes * 60
    tokens["refresh_expires_in"] = config.refresh_token_expire_days * 86400

    return tokens


def logout(session_id: str | None, usuario_id: str | None) -> bool:
    """
    Cierra Está sesión.

    Marca `revoked_at`, no `revoked_before`: el logout es "sali de acá", no
    "expulsa a todos los dispositivos". Un usuario que cierra sesión en la
    computadora del trabajo no quiere que se cierre la del celular.

    Devuelve False si no habia sesión que cerrar, para que un logout repetido
    no dé error: es idempotente a propósito.
    """
    if not session_id:
        return False

    try:
        with get_db().connect() as conexion:
            transaccion = conexion.begin()
            resultado = conexion.execute(
                text("""
                    UPDATE sessions
                    SET revoked_at = COALESCE(revoked_at, NOW()), last_used_at = NOW()
                    WHERE id = :session_id AND revoked_at IS NULL
                """),
                {"session_id": session_id},
            )
            transaccion.commit()
            cerrado = resultado.rowcount > 0
    except SQLAlchemyError:
        return False

    if cerrado and usuario_id:
        from src.modules.users.service import registrar_auditoria

        registrar_auditoria(
            accion="LOGOUT",
            tabla="users",
            record_id=usuario_id,
            user_id=usuario_id,
            valor_nuevo="logout",
        )

    return cerrado


def mis_sesiones(usuario_id: str) -> list[dict[str, Any]]:
    """
    Sesiones activas del usuario, para "cerrar otros dispositivos".

    No se devuelve `token_hash`: no sirve para nada del lado del cliente y que
    un endpoint devuelva el hash de un refresh es una invitacion a filtrarlo.
    """
    with get_db().connect() as conexion:
        filas = (
            conexion.execute(
                text("""
                SELECT id, user_agent, ip_address, created_at, last_used_at, expires_at
                FROM sessions
                WHERE user_id = :user_id
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                ORDER BY created_at DESC
            """),
                {"user_id": usuario_id},
            )
            .mappings()
            .all()
        )

    from src.modules.users.models import _fila_a_dict

    return [_fila_a_dict(f) for f in filas]


def cambiar_password_por_api(
    usuario_id: str,
    password_actual: str,
    password_nueva: str,
    ip: str | None = None,
) -> None:
    """
    Cambió de contraseña por "mi cuenta".

    Además de cambiar la contraseña, revoca TODAS las otras sesiones: si el
    cambió se hizo porque sospechas que alguien entró, dejar las sesiones
    viejas vivas es justamente lo que no hay que hacer.
    """
    from src.modules.users.service import cambiar_password

    cambiar_password(
        user_id=usuario_id,
        password_nueva=password_nueva,
        actor_id=usuario_id,
        password_actual=password_actual,
    )

    revocar_todas_las_sesiones(usuario_id, motivo="cambio de contraseña")

    from src.modules.users.service import registrar_auditoria

    registrar_auditoria(
        accion="UPDATE",
        tabla="users",
        record_id=usuario_id,
        user_id=usuario_id,
        valor_nuevo="password cambiado",
        ip=ip,
    )


def purgar_sesiones_vencidas() -> int:
    """
    Borra las sesiones expiradas o revocadas hace mucho.

    Corre por cron, no por request: borrar filas con un DELETE en cada login es
    una escritura inutil en el camino caliente. Se deja exposed como funcion
    para llamarla desde un job, no desde la API.
    """
    with get_db().connect() as conexion:
        transaccion = conexion.begin()
        resultado = conexion.execute(
            text("""
                DELETE FROM sessions
                WHERE expires_at < NOW() - INTERVAL '7 days'
                   OR (revoked_at IS NOT NULL AND revoked_at < NOW() - INTERVAL '30 days')
            """)
        )
        transaccion.commit()
        return resultado.rowcount
