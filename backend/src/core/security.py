"""
Primitivas de autenticacion: hashing de passwords y emision/verificacion de JWT.

DONDE VIVE EL SECRETO
---------------------
`SECRET_KEY` sale de la variable de entorno, NUNCA de un default utilizable.
Y si corre en produccion con el valor de ejemplo, la API no arranca: se
despierta en el deploy, no el primer dia que alguien filtre un token.

POR QUE HAY QUE PONERLE UN DEFAULT A `SECRET_KEY`
-------------------------------------------------
Porque sin default, `make up` falla en el primer arranque y alguien copia un
valor de ejemplo al `.env` sin mirarlo — y ese valor viaja al repositorio y a
produccion. El default va, pero `verificar_configuracion_de_secreto()` lo
rechaza en produccion. El error es el mismo en los dos casos ("poné una key"),
pero uno pasa a los 5 segundos de arrancar y el otro a los 6 meses.

POR QUE BCRYPT Y NO PBKDF2/SHA
-------------------------------
Las passwords de la base ya son bcrypt: las genero `pgcrypto` con
`crypt(..., gen_salt('bf', 10))`. Bcrypt verifica `$2a$` igual que `$2b$`, asi
que el admin que siembla `init.sql` sigue funcionando. Cambiar de algoritmo
implicaria rehashear todo, y mientras tanto conviven dos esquemas.

LO DE LOS 72 BYTES
------------------
Bcrypt trunca silenciosamente a 72 bytes. Una password de 100 caracteres se
verifica contra los primeros 72: el resto es decoracion que el atacante puede
cambiar sin que nadie lo note. Por eso hay un limite explicito y un error
claro, en vez de dejar que bcrypt haga su truncamiento en silencio.
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from src.core.config import get_settings

# Costo 12. El default de la lib es 12 tambien, pero dejarlo explicito evita
# que un upgrade del paquete cambie el costo de las passwords nuevas y deje
# las viejas con un costo distinto sin que nadie lo note.
#
# 12 son ~250 ms por hash en una maquina moderna. Es el balance habitual: mas
# alto frena mas al atacante y molesta mas a los usuarios legitimos.
BCRYPT_COST = 12

# Bcrypt no mira mas alla de 72 bytes. Si se pasa, trunca.
BCRYPT_MAX_BYTES = 72


class _EncoderTolerante(json.JSONEncoder):
    """
    Serializa a string lo que no sepa serializar.

    Existe para que un `uuid.UUID` o un `Decimal` que se colen en el payload no
    revienten el login con un TypeError en el ULTIMO paso, después de haber
    verificado la contraseña. Que el token se pueda firmar siempre importa más
    que no perder precisión en un campo que ya viene casteado.
    """

    def default(self, o):
        return str(o)


# Longitud minima. La NIST recomienda 8; 12 es mas prudente para un sistema
# donde las passwords dan acceso a inventario y datos de clientes.
PASSWORD_MIN_LEN = 8


class ErrorDePassword(ValueError):
    """La password no cumple las reglas. El mensaje va al usuario final."""


class TokenInvalido(Exception):
    """El token no sirve: vencido, mal firmado, de otro tipo. 401."""


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hashear_password(password: str) -> str:
    """
    Devuelve el hash bcrypt como texto.

    `bcrypt` trabaja con BYTES y no con str: pasarlo en str es el error
    clasico (`TypeError: argument must be a bytes-like object`). Se codifica
    explicitamente en UTF-8 para que una password con tilde o ñ sea la misma
    string que el usuario escribio, y no una distinta.
    """
    validar_password(password)

    # bcrypt trunca a 72 bytes y en versiones recientes LANZA ValueError si se
    # pasa de largo. La validacion de arriba lo evita antes de llegar.
    crudo = password.encode("utf-8")
    if len(crudo) > BCRYPT_MAX_BYTES:
        raise ErrorDePassword(
            f"La contraseña es demasiado larga (máximo {BCRYPT_MAX_BYTES} bytes)."
        )

    return bcrypt.hashpw(crudo, bcrypt.gensalt(rounds=BCRYPT_COST)).decode("utf-8")


def verificar_password(password: str, hash_guardado: str) -> bool:
    """
    Compara. Nunca lanza: una excepcion aca seria un 500 en un login con
    password incorrecto.

    Si el hash guardado no es un bcrypt valido (usuario creado a mano con un
    string cualquiera), se devuelve False en vez de propagar el error.
    """
    if not hash_guardado:
        return False

    try:
        crudo = password.encode("utf-8")
    except UnicodeEncodeError:
        return False

    try:
        return bcrypt.checkpw(crudo[:BCRYPT_MAX_BYTES], hash_guardado.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash malformado en la base. Es un dato corrupto, no un intento de
        # ataque: se trata como password incorrecta.
        return False


def validar_password(password: str) -> None:
    """Reglas de password. Lanza `ErrorDePassword` con el motivo."""
    if password is None:
        raise ErrorDePassword("La contraseña es obligatoria.")

    if len(password) < PASSWORD_MIN_LEN:
        raise ErrorDePassword(
            f"La contraseña debe tener al menos {PASSWORD_MIN_LEN} caracteres."
        )

    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ErrorDePassword(
            f"La contraseña es demasiado larga: máximo {BCRYPT_MAX_BYTES} bytes."
        )

    if password.strip() != password:
        # Espacios al principio o al final casi siempre son un error de tipeo, y
        # hacen que "password " y "password" sean cuentas distintas.
        raise ErrorDePassword(
            "La contraseña no puede empezar ni terminar con espacios."
        )

    if not any(c.isupper() for c in password):
        raise ErrorDePassword("La contraseña debe tener al menos una mayúscula.")
    if not any(c.islower() for c in password):
        raise ErrorDePassword("La contraseña debe tener al menos una minúscula.")
    if not any(c.isdigit() for c in password):
        raise ErrorDePassword("La contraseña debe tener al menos un número.")


# ---------------------------------------------------------------------------
# Hash de refresh tokens
# ---------------------------------------------------------------------------


def hashear_token(token: str) -> str:
    """
    SHA-256 del refresh token, en hex.

    NO es bcrypt a proposito, y la diferencia es importante: un refresh token
    son ~200 caracteres de entropia criptografica (`secrets.token_urlsafe`).
    No se puede adivinar, no se puede probar por diccionario, y no hay tabla de rainbow que
    valga porque el espacio es de 2^256. SHA-256 alcanza y es instantaneo, que
    es lo que se necesita: esto corre en CADA refresh, en el camino caliente.

    bcrypt aca seria slower sin ganar nada, porque protege contra fuerza bruta
    de passwords humans, que no es el caso de un token aleatorio de 200
    caracteres.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def comparar_tokens(token_recibido: str, hash_guardado: str) -> bool:
    """
    Comparacion en tiempo CONSTANTE.

    `==` sobre strings corta en el primer caracter distinto: con eso, un
    atacante que mide tiempos puede reconstruir el hash byte a byte. Es un
    ataque real y lento, pero es gratis para quien lo intenta, asi que
    `hmac.compare_digest` no es opcional.
    """
    return hmac.compare_digest(hashear_token(token_recibido), hash_guardado)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def crear_access_token(
    user_id: str,
    role_name: str,
    session_id: str | None = None,
) -> tuple:
    """
    Devuelve (token, expira_en).

    El payload es MINIMO a proposito. Un JWT no está cifrado: cualquiera que lo
    intercepte puede leerlo con un `echo | cut -d. -f2 | base64 -d`. Meter el
    email o el nombre ahí es publicarlos. Va el id, el rol (para no ir a la
    base en cada permiso) y el id de sesión (para poder revocar).
    """
    config = get_settings()
    ahora = datetime.now(timezone.utc)
    expira = ahora + timedelta(minutes=config.access_token_expire_minutes)

    # `str(user_id)` NO es cosmetico: psycopg2 devuelve las columnas UUID como
    # objetos `uuid.UUID`, y `json.dumps` — que es lo que usa PyJWT para armar
    # el token — no los serializa. Sin el cast, el login revienta con
    # "Object of type UUID is not JSON serializable" DESPUES de verificar la
    # password: el error aparece en el ultimo paso, que es el mas confuso de
    # depurar.
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role_name,
        "type": "access",
        "iat": int(ahora.timestamp()),
        "exp": int(expira.timestamp()),
        "jti": secrets.token_urlsafe(16),  # id unico del token
    }
    if session_id:
        payload["sid"] = str(session_id)

    # `json_encoder`, NO `default`. PyJWT 2.10 expone `json_encoder`; pasar
    # `default=` da "encode() got an unexpected keyword argument", que es un
    # error de la libreria escondido adentro de un flujo de login.
    #
    # El encoder con `default=str` es la red de seguridad: cualquier valor no
    # JSON-serializable que se cuele en el payload se convierte a string en vez
    # de reventar el login. Los casts de arriba siguen siendo los que importan;
    # esto evita que el PROXIMO campo uuid que se agregue reviente el ultimo
    # paso de la autenticacion.
    token = jwt.encode(
        payload,
        config.secret_key,
        algorithm="HS256",
        json_encoder=_EncoderTolerante,
    )
    # PyJWT 2.x devuelve str; en 1.x devolvia bytes. El cast deja el codigo
    # funcionando en las dos sin cambiarlo en cada llamada.
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return token, expira


def crear_refresh_token(user_id: str) -> "tuple":
    """Devuelve (token, expira_en). El token va hasheado a la tabla."""
    config = get_settings()
    ahora = datetime.now(timezone.utc)
    expira = ahora + timedelta(days=config.refresh_token_expire_days)

    # Payload con datos generados en el JWT: el refresh token se verifica
    # contra la tabla, no por firma. Se mantiene igual para poder tener un token
    # legible en un log de debugging sin exponer nada.
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(ahora.timestamp()),
        "exp": int(expira.timestamp()),
        "jti": secrets.token_urlsafe(32),
    }

    token = jwt.encode(payload, config.secret_key, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return token, expira


def decodificar_token(token: str, tipo_esperado: str) -> dict[str, Any]:
    """
    Verifica firma y expiracion. Lanza `TokenInvalido`.

    `algorithms=["HS256"]` explicito: sin ese argumento, PyJWT toma el
    algoritmo DEL TOKEN, y un atacante puede mandar `alg: none` y un token sin
    firma que pasa la verificación. Es el bypass mas famoso de JWT y se cierra
    passandolo como lista blanca.
    """
    config = get_settings()

    if not token:
        raise TokenInvalido("Falta el token.")

    # Bearer <token> o el token pelado. Aceptar ambos hace que copiar el token
    # del header de Postman funcione pegandolo entero.
    limpio = token[7:].strip() if token.lower().startswith("bearer ") else token.strip()

    try:
        payload = jwt.decode(
            limpio,
            config.secret_key,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalido("El token expiró.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalido("Token inválido.") from exc

    if payload.get("type") != tipo_esperado:
        # Un access token no se puede usar como refresh ni al reves. Sin este
        # chequeo, el refresh endpoint aceptaria el access token — que es de
        # vida corta — y "renovaría" la sesión con algo que expira en minutos.
        raise TokenInvalido(f"Se esperaba un token de tipo {tipo_esperado}.")

    return payload


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------


def verificar_configuracion_de_secreto() -> None:
    """
    Se llama en el arranque de la API. Falla si el secreto es inseguro.

    Que falle al arrancar y no al primer login: un deploy sin SECRET_KEY tiene
    que caerse en el pipeline, no en produccion un martes a la tarde.
    """
    config = get_settings()

    if config.app_env.lower() not in ("production", "prod"):
        return

    if not config.secret_key:
        raise RuntimeError(
            "SECRET_KEY no está definida. Sin ella nadie puede autenticarse."
        )

    if config.secret_key == config.SECRET_KEY_DE_EJEMPLO:
        raise RuntimeError(
            "SECRET_KEY es el valor de ejemplo. En producción todos los que "
            "conozcan el repositorio pueden firmar tokens de ADMIN.\n"
            "Generá una con:\n"
            '    python3 -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "y ponela en el .env o en el secret manager."
        )

    if len(config.secret_key) < 32:
        raise RuntimeError(
            f"SECRET_KEY es demasiado corta ({len(config.secret_key)} caracteres). "
            "Mínimo 32."
        )
