"""Comunas y regiones de Chile (datos embebidos, sin BD)."""
from __future__ import annotations

from src.core.localization.base import AdminDivision

# Datos oficiales INE (códigos 2024)
_REGIONES: list[AdminDivision] = [
    AdminDivision(code="15", name="Arica y Parinacota", type="region"),
    AdminDivision(code="01", name="Tarapacá", type="region"),
    AdminDivision(code="02", name="Antofagasta", type="region"),
    AdminDivision(code="03", name="Atacama", type="region"),
    AdminDivision(code="04", name="Coquimbo", type="region"),
    AdminDivision(code="05", name="Valparaíso", type="region"),
    AdminDivision(code="13", name="Metropolitana de Santiago", type="region"),
    AdminDivision(code="06", name="Libertador General Bernardo O'Higgins", type="region"),
    AdminDivision(code="07", name="Maule", type="region"),
    AdminDivision(code="16", name="Ñuble", type="region"),
    AdminDivision(code="08", name="Biobío", type="region"),
    AdminDivision(code="09", name="La Araucanía", type="region"),
    AdminDivision(code="14", name="Los Ríos", type="region"),
    AdminDivision(code="10", name="Los Lagos", type="region"),
    AdminDivision(code="11", name="Aysén del General Carlos Ibáñez del Campo", type="region"),
    AdminDivision(code="12", name="Magallanes y de la Antártica Chilena", type="region"),
]

# Comunas: código INE de 5 dígitos (RRCCC: Región + Comuna)
# Solo una muestra representativa; en producción cargar JSON completo desde archivo
_COMUNAS: list[AdminDivision] = [
    # Región Metropolitana (13)
    AdminDivision(code="13101", name="Santiago", parent_code="13", type="commune"),
    AdminDivision(code="13102", name="Cerrillos", parent_code="13", type="commune"),
    AdminDivision(code="13103", name="Cerro Navia", parent_code="13", type="commune"),
    AdminDivision(code="13104", name="Conchalí", parent_code="13", type="commune"),
    AdminDivision(code="13105", name="El Bosque", parent_code="13", type="commune"),
    AdminDivision(code="13106", name="Estación Central", parent_code="13", type="commune"),
    AdminDivision(code="13107", name="Huechuraba", parent_code="13", type="commune"),
    AdminDivision(code="13108", name="Independencia", parent_code="13", type="commune"),
    AdminDivision(code="13109", name="La Cisterna", parent_code="13", type="commune"),
    AdminDivision(code="13110", name="La Florida", parent_code="13", type="commune"),
    AdminDivision(code="13111", name="La Granja", parent_code="13", type="commune"),
    AdminDivision(code="13112", name="La Pintana", parent_code="13", type="commune"),
    AdminDivision(code="13113", name="La Reina", parent_code="13", type="commune"),
    AdminDivision(code="13114", name="Las Condes", parent_code="13", type="commune"),
    AdminDivision(code="13115", name="Lo Barnechea", parent_code="13", type="commune"),
    AdminDivision(code="13116", name="Lo Espejo", parent_code="13", type="commune"),
    AdminDivision(code="13117", name="Lo Prado", parent_code="13", type="commune"),
    AdminDivision(code="13118", name="Macul", parent_code="13", type="commune"),
    AdminDivision(code="13119", name="Maipú", parent_code="13", type="commune"),
    AdminDivision(code="13120", name="Ñuñoa", parent_code="13", type="commune"),
    AdminDivision(code="13121", name="Pedro Aguirre Cerda", parent_code="13", type="commune"),
    AdminDivision(code="13122", name="Peñalolén", parent_code="13", type="commune"),
    AdminDivision(code="13123", name="Providencia", parent_code="13", type="commune"),
    AdminDivision(code="13124", name="Pudahuel", parent_code="13", type="commune"),
    AdminDivision(code="13125", name="Quilicura", parent_code="13", type="commune"),
    AdminDivision(code="13126", name="Quinta Normal", parent_code="13", type="commune"),
    AdminDivision(code="13127", name="Recolecta", parent_code="13", type="commune"),
    AdminDivision(code="13128", name="Renca", parent_code="13", type="commune"),
    AdminDivision(code="13129", name="San Joaquín", parent_code="13", type="commune"),
    AdminDivision(code="13130", name="San Miguel", parent_code="13", type="commune"),
    AdminDivision(code="13131", name="San Ramón", parent_code="13", type="commune"),
    AdminDivision(code="13132", name="Vitacura", parent_code="13", type="commune"),
    AdminDivision(code="13201", name="Puente Alto", parent_code="13", type="commune"),
    AdminDivision(code="13202", name="Pirque", parent_code="13", type="commune"),
    AdminDivision(code="13203", name="San José de Maipo", parent_code="13", type="commune"),
    AdminDivision(code="13301", name="Colina", parent_code="13", type="commune"),
    AdminDivision(code="13302", name="Lampa", parent_code="13", type="commune"),
    AdminDivision(code="13303", name="Tiltil", parent_code="13", type="commune"),
    AdminDivision(code="13401", name="San Bernardo", parent_code="13", type="commune"),
    AdminDivision(code="13402", name="Buin", parent_code="13", type="commune"),
    AdminDivision(code="13403", name="Calera de Tango", parent_code="13", type="commune"),
    AdminDivision(code="13404", name="Paine", parent_code="13", type="commune"),
    AdminDivision(code="13501", name="Melipilla", parent_code="13", type="commune"),
    AdminDivision(code="13502", name="Alhué", parent_code="13", type="commune"),
    AdminDivision(code="13503", name="Curacaví", parent_code="13", type="commune"),
    AdminDivision(code="13504", name="María Pinto", parent_code="13", type="commune"),
    AdminDivision(code="13505", name="San Pedro", parent_code="13", type="commune"),
    AdminDivision(code="13601", name="Talagante", parent_code="13", type="commune"),
    AdminDivision(code="13602", name="El Monte", parent_code="13", type="commune"),
    AdminDivision(code="13603", name="Isla de Maipo", parent_code="13", type="commune"),
    AdminDivision(code="13604", name="Padre Hurtado", parent_code="13", type="commune"),
    AdminDivision(code="13605", name="Peñaflor", parent_code="13", type="commune"),
    # ... agregar las 346 comunas restantes (cargar desde JSON en producción)
]

# Índices para búsqueda rápida
_COMUNAS_BY_CODE: dict[str, AdminDivision] = {c.code: c for c in _COMUNAS}
_COMUNAS_BY_REGION: dict[str, list[AdminDivision]] = {}
for c in _COMUNAS:
    _COMUNAS_BY_REGION.setdefault(c.parent_code or "", []).append(c)


def get_regiones() -> list[AdminDivision]:
    return list(_REGIONES)


def get_comunas(region_code: str | None = None) -> list[AdminDivision]:
    if region_code:
        return _COMUNAS_BY_REGION.get(region_code, [])
    return list(_COMUNAS)


def find_comuna(code: str) -> AdminDivision | None:
    return _COMUNAS_BY_CODE.get(code)


def search_comunas(query: str, limit: int = 10) -> list[AdminDivision]:
    q = query.lower().strip()
    if not q:
        return []
    results = [c for c in _COMUNAS if q in c.name.lower()]
    return results[:limit]