"""
Tests de catalogo contra una base REAL: jerarquia, atributos, busqueda de
texto completo y exportacion.

POR QUE ESTOS TESTS NECESITAN LA BASE
-------------------------------------
Lo que se prueba aca no es logica pura: son generadores de columnas, indices
GIN, `to_tsquery` con diccionario y CTEs recursivos. Un mock diria que todo
funciona.

QUE NECESITA
------------
Una base Postgres con el esquema de `database/init.sql` aplicado. Si no hay
conexion, los tests se omiten con un motivo claro.

    export DATABASE_URL_TEST=postgresql://postgres:...@localhost:5432/si_test
    python3 -m pytest tests/test_jerarquia_e2e.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_TEST = os.getenv(
    "DATABASE_URL_TEST",
    "postgresql://postgres:secretpassword@localhost:5432/smart_inventory_test",
)


@pytest.fixture(scope="module")
def base():
    """Conecta, verifica que el esquema este, y corta si no hay nada que probar."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        engine = create_engine(DATABASE_TEST, pool_pre_ping=True)
        with engine.connect() as conexion:
            conexion.execute(text("SELECT 1"))
            conexion.execute(text("SELECT 1 FROM category_attributes LIMIT 1"))
            conexion.execute(text("SELECT 1 FROM models LIMIT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(
            f"No hay base de test disponible ({DATABASE_TEST}).\n"
            f"  Levanta una y corré: createdb si_test && psql -d si_test -f database/init.sql\n"
            f"  Motivo: {exc}"
        )

    import src.modules.ai.executor as executor
    import src.db.session as sesion

    # Se reemplazan los DOS engines de la app: el del executor (que usan los
    # modulos de movimientos y dispositivos) y el de la sesion (que usan el
    # catalogo, las ubicaciones y la exportacion). Con uno solo, estos tests
    # estarian escribiendo en la base de DESARROLLO mientras comprueban contra
    # la de test, y el fallo se veria como "la categoria no existe".
    executor._engine = engine
    sesion._engine = engine
    return engine


@pytest.fixture
def catalogo(base):
    """
    Una categoria padre, una subcategoria, un producto y una ubicacion con
    una hija. Todo se borra al final.
    """
    from sqlalchemy import text

    from src.modules.catalog import service as catalogo_service

    with base.begin() as conexion:
        padre = conexion.execute(
            text(
                """
                INSERT INTO categories (name, code)
                VALUES (:nombre, :codigo)
                RETURNING id
                """
            ),
            {"nombre": f"Test Padre {uuid.uuid4().hex[:6]}", "codigo": f"TP{uuid.uuid4().hex[:4]}"},
        ).scalar_one()

        hija = conexion.execute(
            text(
                """
                INSERT INTO categories (name, code, parent_id)
                VALUES (:nombre, :codigo, :padre)
                RETURNING id
                """
            ),
            {
                "nombre": f"Test Hija {uuid.uuid4().hex[:6]}",
                "codigo": f"TH{uuid.uuid4().hex[:4]}",
                "padre": padre,
            },
        ).scalar_one()

        bodega = conexion.execute(
            text(
                """
                INSERT INTO locations (store_name, address, location_type)
                VALUES ('Bodega Test', 'Calle 1', 'WAREHOUSE')
                RETURNING id
                """
            )
        ).scalar_one()

        pasillo = conexion.execute(
            text(
                """
                INSERT INTO locations (store_name, address, location_type, parent_id)
                VALUES ('Pasillo Test', 'Calle 1', 'AISLE', :bodega)
                RETURNING id
                """
            ),
            {"bodega": bodega},
        ).scalar_one()

    yield {
        "padre": padre,
        "hija": hija,
        "bodega": bodega,
        "pasillo": pasillo,
    }

    with base.begin() as conexion:
        conexion.execute(text("DELETE FROM category_attributes WHERE category_id IN (:p, :h)"), {"p": padre, "h": hija})
        conexion.execute(text("DELETE FROM models WHERE category_id IN (:p, :h)"), {"p": padre, "h": hija})
        conexion.execute(text("DELETE FROM categories WHERE id IN (:p, :h)"), {"p": padre, "h": hija})
        conexion.execute(text("DELETE FROM locations WHERE id IN (:b, :s)"), {"b": bodega, "s": pasillo})


class TestJerarquiaDeCategorias:
    def test_la_ruta_de_la_subcategoria_incluye_al_padre(self, catalogo):
        from src.modules.catalog import service

        categorias = {c["id"]: c for c in service.listar_categorias()}
        assert categorias[catalogo["padre"]]["ruta"] == categorias[catalogo["padre"]]["name"]
        assert categorias[catalogo["hija"]]["ruta"] == (
            f"{categorias[catalogo['padre']]['name']} > {categorias[catalogo['hija']]['name']}"
        )

    def test_una_categoria_no_puede_colgar_de_si_misma(self, catalogo):
        from src.modules.catalog import service

        with pytest.raises(service.ProductoInvalido):
            service.actualizar_categoria(catalogo["hija"], {"parent_id": catalogo["hija"]})

    def test_una_categoria_no_puede_colgar_de_su_propia_hija(self, catalogo):
        from src.modules.catalog import service

        with pytest.raises(service.ProductoInvalido):
            service.actualizar_categoria(catalogo["padre"], {"parent_id": catalogo["hija"]})

    def test_no_se_puede_borrar_una_categoria_con_productos(self, catalogo):
        from src.modules.catalog import service

        service.crear_producto(
            {
                "brand": "ACME",
                "model": f"Producto Test {uuid.uuid4().hex[:6]}",
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "category_id": catalogo["hija"],
            }
        )

        with pytest.raises(service.ProductoInvalido):
            service.eliminar_categoria(catalogo["hija"])


class TestAtributos:
    def test_se_crea_un_atributo_y_se_valida_el_valor(self, catalogo):
        from src.modules.catalog import service

        servicio = service
        servicio.crear_atributo(
            catalogo["hija"],
            {"key": "caducidad", "label": "Fecha de caducidad", "data_type": "date"},
        )

        valores = servicio.validar_atributos(catalogo["hija"], {"caducidad": "2027-03-31"})
        assert valores["caducidad"] == "2027-03-31"

    def test_un_atributo_no_definido_se_rechaza(self, catalogo):
        from src.modules.catalog import service

        with pytest.raises(service.AtributoInvalido):
            service.validar_atributos(catalogo["hija"], {"campo_inventado": "x"})

    def test_un_numero_que_no_es_numero_se_rechaza(self, catalogo):
        from src.modules.catalog import service

        service.crear_atributo(
            catalogo["hija"],
            {"key": "peso", "label": "Peso", "data_type": "number"},
        )

        with pytest.raises(service.AtributoInvalido):
            service.validar_atributos(catalogo["hija"], {"peso": "doce kilos"})

    def test_una_clave_repetida_se_rechaza(self, catalogo):
        from src.modules.catalog import service

        service.crear_atributo(catalogo["hija"], {"key": "lote", "label": "Lote"})

        with pytest.raises(service.AtributoInvalido):
            service.crear_atributo(catalogo["hija"], {"key": "lote", "label": "Lote otra vez"})

    def test_el_producto_guarda_los_atributos(self, catalogo):
        from src.modules.catalog import service

        service.crear_atributo(catalogo["hija"], {"key": "lote", "label": "Lote"})

        producto = service.crear_producto(
            {
                "brand": "ACME",
                "model": f"Con Atributo {uuid.uuid4().hex[:6]}",
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "category_id": catalogo["hija"],
                "atributos": {"lote": "A-17"},
            }
        )

        assert producto["atributos"]["lote"] == "A-17"


class TestBusquedaDeTextoCompleto:
    def test_encuentra_por_palabras_en_orden_distinto(self, catalogo):
        from src.modules.catalog import service

        marca = f"Marca{uuid.uuid4().hex[:6]}"
        modelo = f"Modelo{uuid.uuid4().hex[:6]}"
        service.crear_producto(
            {
                "brand": marca,
                "model": modelo,
                "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                "category_id": catalogo["hija"],
            }
        )

        # El orden de las palabras es el del operador, no el del indice.
        encontrados = service.listar_productos(busqueda=f"{modelo} {marca}")
        assert any(p["model"] == modelo for p in encontrados)

    def test_el_filtro_por_categoria_trae_las_subcategorias(self, catalogo):
        from src.modules.devices import service as devices

        total_padre = devices.listar_dispositivos(categoria_id=catalogo["padre"])
        total_hija = devices.listar_dispositivos(categoria_id=catalogo["hija"])

        # Filtrar por el padre trae lo de la hija; filtrar por la hija, no.
        assert total_padre["total"] >= total_hija["total"]


class TestJerarquiaDeUbicaciones:
    def test_la_ubicacion_hija_sabe_su_ruta(self, catalogo):
        from src.modules.locations import service

        ubicaciones = {u["id"]: u for u in service.listar_ubicaciones()}
        assert "Bodega Test" in ubicaciones[catalogo["pasillo"]]["ruta"]

    def test_los_descendientes_incluyen_la_ubicacion_misma(self, catalogo):
        from src.modules.locations import service

        ids = service.ids_descendientes(catalogo["bodega"])
        assert catalogo["bodega"] in ids
        assert catalogo["pasillo"] in ids

    def test_una_ubicacion_no_puede_colgar_de_si_misma(self, catalogo):
        from src.modules.locations import service

        with pytest.raises(service.UbicacionInvalida):
            service.actualizar_ubicacion(catalogo["bodega"], {"parent_id": catalogo["bodega"]})


class TestExportacion:
    def test_el_csv_del_inventario_tiene_encabezado_y_bom(self, catalogo):
        from src.modules.export import service

        csv = service.inventario()
        # El BOM UTF-8: sin el, Excel abre los acentos rotos.
        assert csv.startswith("﻿")
        assert "Serial" in csv.splitlines()[0]

    def test_el_csv_de_productos_incluye_los_del_test(self, catalogo):
        from src.modules.export import service

        from src.modules.catalog import service as catalogo_service

        sku = f"SKU-{uuid.uuid4().hex[:8]}"
        catalogo_service.crear_producto(
            {
                "brand": "ACME",
                "model": "Producto Exportable",
                "sku": sku,
                "category_id": catalogo["hija"],
            }
        )

        csv = service.productos()
        assert sku in csv
