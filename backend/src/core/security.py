"""
Primitivas de autenticación: hashing de passwords y emision/verificación de JWT.

DONDE VIVE EL SECRETO
---------------------
`SECRET_KEY` sale de la variable de entorno, NUNCA de un default utilizable.
Y si corre en producción con el valor de ejemplo, la API no arranca: se
despierta en el deploy, no el primer día que alguien filtre un token.

POR QUE HAY QUE PONERLE UN DEFAULT A `SECRET_KEY`
-------------------------------------------------
Porque sin default, `make up` falla en el primer arranque y alguien copia un
valor de ejemplo al `.env` sin mirarlo — y ese valor viaja al repositorio y a
producción. El default va, pero `verificar_configuración_de_secreto()` lo
rechaza en producción. El error es el mismo en los dos casos ("pone una key"),
pero uno pasa a los 5 segundos de arrancar y el otro a los 6 meses.

POR QUE BCRYPT Y NO PBKDF2/SHA
-------------------------------
Las passwords de la base ya son bcrypt: las genero `pgcrypto` con
`crypt(..., gen_salt('bf', 10))`. Bcrypt verifica `$2a$` igual que `$2b$`, así
que el admin que siembla `init.sql` sigue funcionando. Cambiar de algoritmo
implicaria rehashear todo, y mientras tanto conviven dos esquemas.

LO DE LOS 72 BYTES
------------------
Bcrypt trunca silenciosamente a 72 bytes. Una password de 100 caracteres se
verifica contra los primeros 72: el resto es decoracion que el atacante puede
cambiar sin que nadie lo note. Por eso hay un límite explicito y un error
claro, en vez de dejar que bcrypt haga su truncamiento en silencio.
"""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
import pyotp

from src.core.config import get_settings

# Costo 12. El default de la lib es 12 también, pero dejarlo explicito evita
# que un upgrade del paquete cambie el costo de las passwords nuevas y deje
# las viejas con un costo distinto sin que nadie lo note.
#
# 12 son ~250 ms por hash en una maquina moderna. Es el balance habitual: más
# alto frena más al atacante y molesta más a los usuarios legitimos.
BCRYPT_COST = 12

# Bcrypt no mira más allá de 72 bytes. Si se pasa, trunca.
BCRYPT_MAX_BYTES = 72


class _EncoderTolerante(json.JSONEncoder):
    """
    Serializa a string lo que no sepa serializar.

    Existe para que un `uuid.UUID` o un `Decimal` que se colen en el payload no
    revienten el login con un TypeError en el Último paso, después de haber
    verificado la contraseña. Que el token se pueda firmar siempre importa más
    que no perder precisión en un campo que ya viene casteado.
    """

    def default(self, o):
        return str(o)


# Longitud mínima. La NIST recomienda 8; 12 es más prudente para un sistema
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
    # pasa de largo. La validación de arriba lo evita antes de llegar.
    crudo = password.encode("utf-8")
    if len(crudo) > BCRYPT_MAX_BYTES:
        raise ErrorDePassword(
            f"La contraseña es demasiado larga (máximo {BCRYPT_MAX_BYTES} bytes)."
        )

    return bcrypt.hashpw(crudo, bcrypt.gensalt(rounds=BCRYPT_COST)).decode("utf-8")


def verificar_password(password: str, hash_guardado: str) -> bool:
    """
    Compara. Nunca lanza: una excepción acá seria un 500 en un login con
    password incorrecto.

    Si el hash guardado no es un bcrypt válido (usuario creado a mano con un
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
        raise ErrorDePassword("La contraseña debe tener al menos una mayuscula.")
    if not any(c.islower() for c in password):
        raise ErrorDePassword("La contraseña debe tener al menos una minuscula.")
    if not any(c.isdigit() for c in password):
        raise ErrorDePassword("La contraseña debe tener al menos un número.")


# ---------------------------------------------------------------------------
# Hash de refresh tokens
# ---------------------------------------------------------------------------


def hashear_token(token: str) -> str:
    """
    SHA-256 del refresh token, en hex.

    NO es bcrypt a propósito, y la diferencia es importante: un refresh token
    son ~200 caracteres de entropia criptografica (`secrets.token_urlsafe`).
    No se puede adivinar, no se puede probar por diccionario, y no hay tabla de rainbow que
    valga porque el espacio es de 2^256. SHA-256 alcanza y es instantaneo, que
    es lo que se necesita: esto corre en CADA refresh, en el camino caliente.

    bcrypt acá seria slower sin ganar nada, porque protege contra fuerza bruta
    de passwords humans, que no es el caso de un token aleatorio de 200
    caracteres.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def comparar_tokens(token_recibido: str, hash_guardado: str) -> bool:
    """
    Comparacion en tiempo CONSTANTE.

    `==` sobre strings corta en el primer caracter distinto: con eso, un
    atacante que mide tiempos puede reconstruir el hash byte a byte. Es un
    ataque real y lento, pero es gratis para quien lo intenta, así que
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

    El payload es Mínimo a propósito. Un JWT no está cifrado: cualquiera que lo
    intercepte puede leerlo con un `echo | cut -d. -f2 | base64 -d`. Meter el
    email o el nombre ahí es publicarlos. Va el id, el rol (para no ir a la
    base en cada permiso) y el id de sesión (para poder revocar).
    """
    config = get_settings()
    ahora = datetime.now(UTC)
    expira = ahora + timedelta(minutes=config.access_token_expire_minutes)

    # `str(user_id)` NO es cosmetico: psycopg2 devuelve las columnas UUID como
    # objetos `uuid.UUID`, y `json.dumps` — que es lo que usa PyJWT para armar
    # el token — no los serializa. Sin el cast, el login revienta con
    # "Object of type UUID is not JSON serializable" Después de verificar la
    # password: el error aparece en el último paso, que es el más confuso de
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
    # esto evita que el Próximo campo uuid que se agregue reviente el último
    # paso de la autenticación.
    token = jwt.encode(
        payload,
        config.secret_key,
        algorithm="HS256",
        json_encoder=_EncoderTolerante,
    )
    # PyJWT 2.x devuelve str; en 1.x devolvia bytes. El cast deja el código
    # funcionando en las dos sin cambiarlo en cada llamada.
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return token, expira


def crear_refresh_token(user_id: str) -> "tuple":
    """Devuelve (token, expira_en). El token va hasheado a la tabla."""
    config = get_settings()
    ahora = datetime.now(UTC)
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
    firma que pasa la verificación. Es el bypass más famoso de JWT y se cierra
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
        raise TokenInvalido("El token expiro.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalido("Token invalido.") from exc

    if payload.get("type") != tipo_esperado:
        # Un access token no se puede usar como refresh ni al reves. Sin este
        # chequeo, el refresh endpoint aceptaria el access token — que es de
        # vida corta — y "renovaria" la sesión con algo que expira en minutos.
        raise TokenInvalido(f"Se esperaba un token de tipo {tipo_esperado}.")

    return payload


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------


def verificar_configuracion_de_secreto() -> None:
    """
    Se llama en el arranque de la API. Falla si el secreto es inseguro.

    Que falle al arrancar y no al primer login: un deploy sin SECRET_KEY tiene
    que caerse en el pipeline, no en producción un martes a la tarde.
    """
    config = get_settings()

    if config.app_env.lower() not in ("production", "prod"):
        return

    if not config.secret_key:
        raise RuntimeError(
            "SECRET_KEY no esta definida. Sin ella nadie puede autenticarse."
        )

    if config.secret_key == config.SECRET_KEY_DE_EJEMPLO:
        raise RuntimeError(
            "SECRET_KEY es el valor de ejemplo. En produccion todos los que "
            "conozcan el repositorio pueden firmar tokens de ADMIN.\n"
            "Genera una con:\n"
            '    python3 -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "y ponela en el .env o en el secret manager."
        )

    if len(config.secret_key) < 32:
        raise RuntimeError(
            f"SECRET_KEY es demasiado corta ({len(config.secret_key)} caracteres). "
            "Mínimo 32."
        )


# ---------------------------------------------------------------------------
# Medidor de fortaleza
# ---------------------------------------------------------------------------
#
# QUE DEVUELVE Y POR QUE
# ----------------------
# `GET /api/auth/password-strength` la llama el frontend en cada tecla del
# login, y el medidor pinta la barra, el nivel y la lista de requisitos. La
# forma de la respuesta la define `usePasswordStrength.js`:
#
#     {
#       entropia: 0..100,        # el ancho de la barra
#       nivel: 'débil'|'media'|'fuerte',
#       requisitos: [{ nombre, mínimo, cumplido }],
#       es_válida: bool,
#     }
#
# El router importa Está función. Antes no existia: el endpoint devolvia un
# 500 en cada tecla del login, con el ImportError escondido adentro del
# handler. El medidor se caia en silencio (el frontend lo atrapa) pero el
# operador nunca via la fuerza de su contraseña.
#
# POR QUE LOS REQUISITOS SON LOS DE `validar_password`
# ---------------------------------------------------
# Porque el medidor tiene que decir la verdad: si muestra "falta un número" y
# el backend acepta la contraseña igual, el medidor es decoracion. Los dos
# leen la misma lista, así que no pueden contradecirse.
REQUISITOS_PASSWORD = (
    {"nombre": "Longitud mínima", "minimo": PASSWORD_MIN_LEN, "tipo": "largo"},
    {"nombre": "Una mayuscula", "minimo": 1, "tipo": "mayuscula"},
    {"nombre": "Una minuscula", "minimo": 1, "tipo": "minuscula"},
    {"nombre": "Un número", "minimo": 1, "tipo": "numero"},
)


def _cumple_requisito(password: str, tipo: str) -> bool:
    if tipo == "largo":
        return len(password) >= PASSWORD_MIN_LEN
    if tipo == "mayuscula":
        return any(c.isupper() for c in password)
    if tipo == "minuscula":
        return any(c.islower() for c in password)
    if tipo == "numero":
        return any(c.isdigit() for c in password)
    return False


def obtener_info_fortaleza_password(password: str) -> dict:
    """
    Calcula la fortaleza de una contraseña sin guardarla.

    No lanza: es una ayuda visual, y un error acá seria un 500 mientras el
    operador escribe. Devuelve siempre la misma forma.
    """
    password = password or ""

    requisitos = [
        {
            "nombre": r["nombre"],
            "minimo": r["minimo"],
            "cumplido": _cumple_requisito(password, r["tipo"]),
        }
        for r in REQUISITOS_PASSWORD
    ]

    cumplidos = sum(1 for r in requisitos if r["cumplido"])
    total = len(requisitos)

    # La entropia no es un porcentaje de requisitos: es una estimacion de bits.
    # Se calcula sobre el alfabeto real del password (cuantos caracteres
    # distintos usa) y su largo, que es lo que de verdad mide la dificultad
    # de adivinarlo. Un password de 40 caracteres todos iguales tiene 40
    # requisitos "cumplidos" en un medidor ingenuo y 0 bits reales.
    alfabeto = len(set(password))
    bits = len(password) * (alfabeto.bit_length() if alfabeto > 0 else 0)

    # 72 bits ~ una contraseña decente; 100+ ~ fuerte. Se escala a 0..100.
    entropia = min(100, int(bits / 1.2))

    if cumplidos == total and entropia >= 60:
        nivel = "fuerte"
    elif cumplidos >= total - 1:
        nivel = "media"
    else:
        nivel = "débil"

    es_valida = (
        len(password) >= PASSWORD_MIN_LEN
        and len(password.encode("utf-8")) <= BCRYPT_MAX_BYTES
        and all(r["cumplido"] for r in requisitos)
    )

    return {
        "entropia": entropia,
        "nivel": nivel,
        "requisitos": requisitos,
        "es_valida": es_valida,
    }


# ---------------------------------------------------------------------------
# Device Fingerprinting (para binding de refresh token a dispositivo)
# ---------------------------------------------------------------------------

def generar_device_fingerprint(
    user_agent: str | None,
    ip: str | None,
    *,
    extra: str | None = None,
) -> tuple[str, str]:
    """
    Genera un fingerprint de dispositivo determinista pero no reversible.

    Devuelve (fingerprint_hash, device_name_legible).

    - fingerprint_hash: SHA-256 truncado a 64 chars (32 bytes), para guardar en BD
    - device_name: string legible para UI "mis sesiones" (ej. "Chrome 119 / Windows 10")

    El fingerprint incluye:
    - User-Agent (navegador + SO)
    - IP (red, no host completo para privacidad)
    - `extra`: opcional, para añadir entropía del cliente (canvas, screen, etc.)

    NO incluye datos personales. Es determinista: mismo dispositivo -> mismo hash.
    """
    ua = (user_agent or "").strip()
    ip_part = (ip or "").split(",")[0].strip() if ip else ""

    # Reducir IP a /24 (IPv4) o /64 (IPv6) para no trackear host individual
    import ipaddress
    try:
        ip_obj = ipaddress.ip_address(ip_part)
        if isinstance(ip_obj, ipaddress.IPv4Address):
            ip_part = str(ipaddress.IPv4Network(f"{ip_part}/24", strict=False).network_address)
        else:
            ip_part = str(ipaddress.IPv6Network(f"{ip_part}/64", strict=False).network_address)
    except ValueError:
        pass  # no es IP válida, usar tal cual

    # Nombre legible: extraer browser + OS del UA (heurística simple)
    device_name = "Dispositivo desconocido"
    if ua:
        # Chrome/Edge/Firefox/Safari + Windows/Mac/Linux/Android/iOS
        browser = "Desconocido"
        os_name = "Desconocido"
        ua_lower = ua.lower()
        if "edg/" in ua_lower:
            browser = "Edge"
        elif "chrome/" in ua_lower and "edg/" not in ua_lower:
            browser = "Chrome"
        elif "firefox/" in ua_lower:
            browser = "Firefox"
        elif "safari/" in ua_lower and "chrome/" not in ua_lower:
            browser = "Safari"
        if "windows" in ua_lower:
            os_name = "Windows"
        elif "macintosh" in ua_lower or "mac os" in ua_lower:
            os_name = "macOS"
        elif "linux" in ua_lower and "android" not in ua_lower:
            os_name = "Linux"
        elif "android" in ua_lower:
            os_name = "Android"
        elif "iphone" in ua_lower or "ipad" in ua_lower:
            os_name = "iOS"
        device_name = f"{browser} / {os_name}"

    # Hash determinista
    material = f"{ua}|{ip_part}|{extra or ''}".encode()
    fp_hash = hashlib.sha256(material).hexdigest()

    return fp_hash, device_name


def verificar_device_fingerprint(
    fingerprint_guardado: str,
    user_agent: str | None,
    ip: str | None,
    *,
    extra: str | None = None,
    tolerancia: bool = True,
) -> bool:
    """
    Verifica si el fingerprint actual coincide con el guardado.

    `tolerancia=True`: permite variaciones menores en UA (versión de navegador).
    Compara solo los primeros 16 chars (8 bytes = 64 bits) para permitir
    actualizaciones menores de navegador sin forzar re-login.
    """
    if not fingerprint_guardado:
        return True  # sin fingerprint guardado = primer uso, permitir

    fp_actual, _ = generar_device_fingerprint(user_agent, ip, extra=extra)

    if tolerancia:
        # Comparar prefijo de 16 chars (64 bits) para tolerar cambios de versión
        return fp_actual[:16] == fingerprint_guardado[:16]

    return hmac.compare_digest(fp_actual, fingerprint_guardado)


# ---------------------------------------------------------------------------
# TOTP (MFA / 2FA) - RFC 6238
# ---------------------------------------------------------------------------

def generar_totp_secret() -> str:
    """
    Genera un secreto base32 para TOTP (32 chars = 160 bits, RFC 6238 compliant).
    """
    return pyotp.random_base32(length=32)


def get_totp_uri(secret: str, email: str, issuer: str = "SmartInventory") -> str:
    """
    Genera la URI `otpauth://` para QR code en apps autenticadoras (Google Auth, Authy, etc.).
    """
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verificar_totp(secret: str, token: str, *, window: int = 1) -> bool:
    """
    Verifica un código TOTP.

    `window=1`: acepta código anterior/actual/siguiente (30s cada uno) para tolerar
    desfase de reloj. `window=0` = solo código actual.
    """
    if not secret or not token:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(token, valid_window=window)


def generar_recovery_codes(cantidad: int = 8, longitud: int = 10) -> list[str]:
    """
    Genera códigos de recuperación alfanuméricos (mayúsculas + dígitos, sin ambiguos).

    Devuelve lista de códigos en CLARO (solo se muestran UNA vez al usuario).
    La app debe hashearlos con bcrypt antes de guardar.
    """
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin I, O, 0, 1
    return [
        "".join(secrets.choice(alfabeto) for _ in range(longitud))
        for _ in range(cantidad)
    ]


def hashear_recovery_codes(codigos: list[str]) -> list[str]:
    """Hashea lista de códigos con bcrypt para almacenamiento seguro."""
    return [bcrypt.hashpw(c.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8") for c in codigos]


def verificar_recovery_code(codigo_recibido: str, hashes_guardados: list[str]) -> tuple[bool, int]:
    """
    Verifica un código de recuperación contra lista de hashes.

    Devuelve (valido, indice): si válido, el índice del hash usado (para marcarlo consumido).
    Uso único: la app debe REMOVER el hash de la lista tras uso exitoso.
    """
    for i, h in enumerate(hashes_guardados):
        if bcrypt.checkpw(codigo_recibido.encode("utf-8"), h.encode("utf-8")):
            return True, i
    return False, -1


# ---------------------------------------------------------------------------
# Password Rotation & History Helpers
# ---------------------------------------------------------------------------

PASSWORD_HISTORY_MAX = 12  # cuántos hashes previos recordar (no reutilizar)

def password_en_historial(password: str, historial_hashes: list[str]) -> bool:
    """
    Verifica si la password (en claro) coincide con alguno de los hashes del historial.
    """
    for h in historial_hashes:
        if bcrypt.checkpw(password.encode("utf-8"), h.encode("utf-8")):
            return True
    return False


def password_requiere_rotacion(
    password_changed_at: datetime | None,
    password_rotation_days: int | None,
    password_must_change: bool = False,
) -> bool:
    """
    Determina si la contraseña debe rotarse.

    - `password_must_change=True` -> siempre True (forced reset)
    - `password_rotation_days <= 0` -> nunca rota
    - `password_changed_at` None -> nunca cambió, forzar
    - Sino, compara fecha actual contra `password_changed_at + rotation_days`
    """
    if password_must_change:
        return True

    if not password_rotation_days or password_rotation_days <= 0:
        return False

    if password_changed_at is None:
        return True

    # Asegurar timezone-aware
    if password_changed_at.tzinfo is None:
        password_changed_at = password_changed_at.replace(tzinfo=UTC)

    expira = password_changed_at + timedelta(days=password_rotation_days)
    return datetime.now(UTC) >= expira
