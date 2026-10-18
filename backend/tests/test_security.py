"""
Tests de las primitivas de seguridad.

Que el JWT no se pueda falsear es lo UNICO de este modulo que no se negocia.
El resto —contraseñas, formato de token— son detalles, y se prueban igual.

Estos tests NO necesitan base de datos ni un LLM: son puros y corren en
milisegundos.
"""

import sys
from datetime import timedelta
from pathlib import Path

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.permissions import Permiso, es_admin, permisos_de, tiene
from src.core.security import (
    BCRYPT_MAX_BYTES,
    ErrorDePassword,
    TokenInvalido,
    comparar_tokens,
    crear_access_token,
    crear_refresh_token,
    decodificar_token,
    hashear_password,
    hashear_token,
    verificar_password,
)

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_hash_y_verificacion():
    hash_ = hashear_password("Tecnico2024")
    assert hash_ != "Tecnico2024"
    assert verificar_password("Tecnico2024", hash_)


def test_password_incorrecta():
    hash_ = hashear_password("Tecnico2024")
    assert not verificar_password("Tecnico2025", hash_)


def test_hash_salteado():
    """Dos usuarios con la misma contraseña tienen hashes distintos."""
    assert hashear_password("Tecnico2024") != hashear_password("Tecnico2024")


@pytest.mark.parametrize(
    "malas",
    [
        "corta",  # menos de PASSWORD_MIN_LEN
        "sinmayusculas2024",  # sin mayúscula
        "SINMINUSCULAS2024",  # sin minúscula
        "SinNumerosAqui",  # sin número
        "  ConEspacios  ",  # espacios al borde
    ],
)
def test_passwords_invalidas(malas):
    with pytest.raises(ErrorDePassword):
        hashear_password(malas)


def test_password_de_72_bytes_es_el_tope():
    """
    Bcrypt trunca a 72 bytes. El limite se aplica para que el usuario sepa que
    su contraseña es mas larga de lo que realmente se verifica, en vez de
    descubrirlo cuando alguien cambia los ultimos 10 caracteres.
    """
    exacta = "A1" + "x" * 70
    assert len(exacta.encode("utf-8")) == BCRYPT_MAX_BYTES
    assert verificar_password(exacta, hashear_password(exacta))

    larga = exacta + "y"
    with pytest.raises(ErrorDePassword):
        hashear_password(larga)


def test_password_con_acentes():
    """UTF-8: una contraseña con ñ tiene que seguir siendo la misma string."""
    hash_ = hashear_password("Contraseña2024")
    assert verificar_password("Contraseña2024", hash_)


def test_verificar_no_lanza_con_hash_malformado():
    """
    Un hash corrupto en la base tiene que ser un login fallido, no un 500. Si
    `verify` propaga, un usuario con un hash roto no puede loguearse NUNCA y el
    error dice "invalid salt" en vez de "contraseña incorrecta".
    """
    assert verificar_password("cualquiera", "no-es-un-hash") is False
    assert verificar_password("cualquiera", "") is False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_access_token_verifica():
    token, _ = crear_access_token("user-123", "ADMIN", "ses-1")
    payload = decodificar_token(token, "access")
    assert payload["sub"] == "user-123"
    assert payload["role"] == "ADMIN"
    assert payload["sid"] == "ses-1"


def test_refresh_no_sirve_como_access():
    """
    El bypass mas comun: mandar el access token (de vida corta) al endpoint de
    refresh y "renovar" con algo que expira en 15 minutos.
    """
    token, _ = crear_refresh_token("user-123")
    with pytest.raises(TokenInvalido):
        decodificar_token(token, "access")


def test_access_no_sirve_como_refresh():
    token, _ = crear_access_token("user-123", "ADMIN", "ses-1")
    with pytest.raises(TokenInvalido):
        decodificar_token(token, "refresh")


def test_token_con_firma_invalida():
    """Cambiar un byte del payload rompe la firma."""
    token, _ = crear_access_token("user-123", "ADMIN", "ses-1")
    cabecera, payload, firma = token.split(".")
    adulterado = f"{cabecera}.{payload}.{firma[:-4]}AAAA"
    with pytest.raises(TokenInvalido):
        decodificar_token(adulterado, "access")


def test_alg_none_es_rechazado():
    """
    El bypass mas famoso de JWT: un token con `"alg": "none"` y sin firma.

    Pasa si la libreria toma el algoritmo DEL TOKEN en vez de los que se le
    pasan. Por eso `decodificar_token` dice `algorithms=["HS256"]`.
    """
    forjado = jwt.encode(
        {
            "sub": "admin",
            "role": "ADMIN",
            "type": "access",
            "exp": int(
                (__import__("datetime").datetime.now() + timedelta(hours=1)).timestamp()
            ),
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(TokenInvalido):
        decodificar_token(forjado, "access")


def test_token_vencido():
    """Expirado es 401, no 500."""
    from src.core.config import get_settings

    config = get_settings()
    vencido = jwt.encode(
        {
            "sub": "u",
            "role": "ADMIN",
            "type": "access",
            "exp": 1000000000,  # 2001
            "iat": 999999999,
        },
        config.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalido):
        decodificar_token(vencido, "access")


def test_token_firmado_con_otra_clave():
    otro = jwt.encode(
        {"sub": "admin", "role": "ADMIN", "type": "access", "exp": 9999999999},
        "una-clave-que-no-es-la-nuestra",
        algorithm="HS256",
    )
    with pytest.raises(TokenInvalido):
        decodificar_token(otro, "access")


def test_bearer_opcional():
    """Aceptar `Bearer x` y el token pelado: pegar el token de Postman."""
    token, _ = crear_access_token("user-123", "ADMIN", "ses-1")
    assert decodificar_token(f"Bearer {token}", "access")["sub"] == "user-123"
    assert decodificar_token(token, "access")["sub"] == "user-123"


def test_token_vacio():
    with pytest.raises(TokenInvalido):
        decodificar_token("", "access")


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------


def test_hash_de_token_es_estable():
    """El hash tiene que ser determinista: es lo que se compara en la base."""
    token, _ = crear_refresh_token("u")
    assert hashear_token(token) == hashear_token(token)
    assert hashear_token(token) != hashear_token(token + "x")


def test_comparacion_de_tokens():
    token, _ = crear_refresh_token("u")
    assert comparar_tokens(token, hashear_token(token))
    assert not comparar_tokens(token + "x", hashear_token(token))


def test_el_refresh_token_no_se_guarda_en_claro():
    """
    El hash NO sirve para autenticarse. Si se guardara el token, un SELECT en la
    base bastaria para robar sesiones.
    """
    token, _ = crear_refresh_token("u")
    assert token not in hashear_token(token)


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------


def test_admin_tiene_todo():
    for permiso in Permiso:
        assert tiene("ADMIN", permiso), f"ADMIN no tiene {permiso}"


def test_tecnico_no_toca_usuarios():
    assert not tiene("TECHNICIAN", Permiso.USERS_CREAR)
    assert not tiene("TECHNICIAN", Permiso.USERS_BORRAR)
    assert tiene("TECHNICIAN", Permiso.REPARACIONES_EDITAR)


def test_operador_no_toca_taller():
    assert not tiene("OPERATOR", Permiso.REPARACIONES_EDITAR)
    assert tiene("OPERATOR", Permiso.MOVIMIENTOS_CREAR)


def test_rol_desconocido_no_tiene_nada():
    """
    FALLA CERRADO. Si un rol desconocido devolviera todos los permisos,
    bastaría con escribir mal un nombre en la base para tener acceso total.
    """
    assert permisos_de("SUPERADMIN") == set()
    assert not tiene("SUPERADMIN", Permiso.USERS_CREAR)
    assert not tiene("", Permiso.USERS_CREAR)
    assert not tiene(None, Permiso.USERS_CREAR)


def test_rol_es_case_insensitive():
    assert tiene("admin", Permiso.USERS_CREAR)
    assert es_admin("Admin")


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------


def test_secreto_de_ejemplo_se_rechaza_en_produccion(monkeypatch):
    """
    Un deploy con la clave de ejemplo tiene que CAERSE, no arrancar y dejar que
    cualquiera que lea el repo firme tokens de ADMIN.
    """
    from src.core import security
    from src.core.config import get_settings

    config = get_settings()
    monkeypatch.setattr(config, "app_env", "production", raising=False)
    monkeypatch.setattr(
        config, "secret_key", config.SECRET_KEY_DE_EJEMPLO, raising=False
    )

    with pytest.raises(RuntimeError) as excepcion:
        security.verificar_configuracion_de_secreto()

    assert "ejemplo" in str(excepcion.value)


def test_secreto_corto_se_rechaza(monkeypatch):
    from src.core import security
    from src.core.config import get_settings

    config = get_settings()
    monkeypatch.setattr(config, "app_env", "production", raising=False)
    monkeypatch.setattr(config, "secret_key", "corta", raising=False)

    with pytest.raises(RuntimeError):
        security.verificar_configuracion_de_secreto()


def test_en_desarrollo_no_se_rechaza_el_ejemplo(monkeypatch):
    """
    `make up` tiene que funcionar sin configurar nada. El rechazo es solo para
    produccion: si no, nadie levanta el proyecto la primera vez.
    """
    from src.core import security
    from src.core.config import get_settings

    config = get_settings()
    monkeypatch.setattr(config, "app_env", "development", raising=False)

    security.verificar_configuracion_de_secreto()  # no debe lanzar
