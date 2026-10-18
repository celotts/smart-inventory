"""Modelos de request y response de auth y usuarios."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_in: int = Field(
        ..., description="Segundos de validez del access token."
    )
    refresh_expires_in: int = Field(
        ..., description="Segundos de validez del refresh token."
    )


class LoginResponse(TokenResponse):
    usuario: "UsuarioResumen"


class RefreshRequest(BaseModel):
    # El refresh se acepta en el BODY, no solo en el header, porque un refresh
    # es de uso único: mandarlo en el header lo deja en los logs de cualquier
    # proxy que registre Authorization.
    refresh_token: str = Field(..., min_length=10, max_length=2000)


class CambiarPasswordRequest(BaseModel):
    password_actual: str = Field(..., min_length=1, max_length=128)
    password_nueva: str = Field(..., min_length=8, max_length=72)

    @field_validator("password_nueva")
    @classmethod
    def _distinta(cls, v, info):
        """
        Que la nueva sea distinta de la actual.

        Sin este chequeo, "cambiá tu contraseña" con la misma dos veces dice
        "listo" y no cambió nada: el operador cree que lo hizo y la cuenta
        sigue con la contraseña que él ya忌 sab exposureó.
        """
        actual = info.data.get("password_actual")
        if actual and v == actual:
            raise ValueError("La contraseña nueva debe ser distinta de la actual.")
        return v


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------


class UsuarioResumen(BaseModel):
    id: str
    email: str
    full_name: str
    role_name: str
    is_active: bool = True
    is_specialist: bool = False
    address: str | None = None
    phone: str | None = None
    phone2: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def _email_en_minusculas(cls, v):
        # Normalizar acá evita dos cuentas con el mismo mail en mayúsculas y
        # minusculas: el login es case-insensitive, pero el UNIQUE de la base
        # es case-SENSITIVE, así que "Ana@" y "ana@" coexistirían y la
        # validación del schema no lo impediría.
        return (v or "").strip().lower()


class CrearUsuarioRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(
        ...,
        min_length=8,
        max_length=72,
        description="Entre 8 y 72 bytes. Debe tener mayúscula, minúscula y número.",
    )
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=6, max_length=255)
    phone2: str = Field("", max_length=255)
    address: str = Field("N/A", max_length=255)
    role_name: str = Field("OPERATOR", max_length=50)
    is_active: bool = True
    is_specialist: bool = False

    @field_validator("email")
    @classmethod
    def _email_basico(cls, v):
        valor = (v or "").strip().lower()
        if "@" not in valor or "." not in valor.split("@")[-1]:
            raise ValueError(f"'{valor}' no parece un email válido.")
        return valor

    @field_validator("role_name")
    @classmethod
    def _rol_en_mayusculas(cls, v):
        # Si el rol se guarda como 'admin' y el mapa de permisos usa 'ADMIN', la
        # comparación falla por una mayúscula y el usuario queda sin permisos.
        # Normalizar acá hace que la comparación no falle por una mayúscula.
        return (v or "").strip().upper()


class ActualizarUsuarioRequest(BaseModel):
    """PATCH parcial: solo se actualiza lo que viene."""

    full_name: str | None = Field(None, min_length=2, max_length=255)
    phone: str | None = Field(None, min_length=6, max_length=255)
    phone2: str | None = Field(None, max_length=255)
    address: str | None = Field(None, max_length=255)
    role_name: str | None = Field(None, max_length=50)
    is_active: bool | None = None
    is_specialist: bool | None = None

    @field_validator("role_name")
    @classmethod
    def _rol(cls, v):
        return v.strip().upper() if v else v


class ResetPasswordRequest(BaseModel):
    """Un ADMIN cambia la contraseña de otro. No pide la actual."""

    password_nueva: str = Field(..., min_length=8, max_length=72)


class ListaUsuariosResponse(BaseModel):
    usuarios: list[UsuarioResumen]
    total: int
    limite: int
    offset: int


class SesionActiva(BaseModel):
    """No incluye `token_hash`: no le sirve a nadie del lado del cliente."""

    id: str
    user_agent: str | None = None
    ip_address: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime


class MiPerfilResponse(BaseModel):
    usuario: UsuarioResumen
    permisos: list[str] = Field(
        ..., description="Los permisos del rol, resueltos server-side."
    )
    sesiones_activas: int = 0


LoginResponse.model_rebuild()
