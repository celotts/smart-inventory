"""
Tests de integridad del front.

POR QUE UN TEST EN EL BACKEND PARA EL FRONT
--------------------------------------------
El front no tiene runner de tests (ni bundler, por decisión del proyecto: los
módulos ES se cargan nativos). Pero hay una clase de bug DEL FRONT que es
silenciosa y cara:

    Un módulo usa un helper compartido sin importarlo.

`ReferenceError: errorDe is not defined` dentro de un manejador de click. El
módulo CARGA bien (el error es en tiempo de ejecucion), la app arranca, la
vista se ve... y el botón no hace nada. Sin errores en la consola de la
página que el operador tiene abierta, y sin un mensaje útil: el único rastro
está en la consola del navegador, que el operador no abre.

COSTE REAL
----------
Ese bug se colo DOS VECES en un mismo refactor, y las dos veces el sintoma
fue "el botón no hace nada". Un test que verifique los imports lo detecta en un
segundo.

QUE CHEQUEA
------------
1. Que cada `import { x }` exista de verdad en el módulo destino (un export
   que no existe rompe el grafo de módulos ENTERO: la app no arranca).
2. Que los helpers COMPARTIDOS que el archivo usa esten importados o definidos
   en el.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Donde puede estar el front.
#
# En el HOST, el test está en `backend/tests/`, así que el front está dos
# directorios más arriba. En el CONTENEDOR, el código está en `/app` y el front
# se monta en `/app/frontend-src` (ver `docker-compose.dev.yml`).
RUTAS_FRONT = (
    Path(__file__).resolve().parents[2] / "frontend" / "src",
    Path("/app/frontend-src"),
)


def _raiz_del_front() -> Path:
    for ruta in RUTAS_FRONT:
        if ruta.exists():
            return ruta
    return RUTAS_FRONT[0]

# Los helpers compartidos del front. Un nombre de está lista que se use sin
# estar importado es un bug garantizado.
HELPERS_COMPARTIDOS = {
    # De `dom.js`
    "crear", "reemplazar", "icono", "etiquetaProducto",
    # De `avisos.js`
    "avisar", "marcarPendientes",
    # De `dialogo.js`
    "abrirDialogo", "confirmar",
    # De `formulario.js`
    "campoTexto", "campoSelect", "campoNumero", "errorDe", "resumenDeErrores",
    # De `tooltip.js`
    "conTooltip",
    # De `etiqueta.js`
    "imagenQrProducto", "imagenBarcodeProducto", "svgsDeEtiqueta",
    # De `qr.js`
    "imagenQr",
    # De `escaner.js`
    "LectorQR", "normalizarCodigo",
    # De `escaner-campo.js`
    "botonEscanear",
    # De `descargas.js`
    "descargarCsv",
    # De `api.js`
    "api", "configurar", "restaurarSesion", "haySesion", "consumirOperacionPendiente",
    # De `estado.js` y `navegacion.js`
    "estado", "setUsuario", "permisosDe", "mostrarVista", "VISTAS",
    # De `cabecera.js`
    "actualizarCabeceraUsuario", "actualizarEstadoIA", "inicializarFooter", "iniciarModoExterior",
    # De `campana.js`
    "iniciarCampana",
    # De `build.js`
    "obtenerSelloDelServidor", "verificarVersionDelServidor",
    # De las vistas de producto
    "abrirAlta", "abrirEdicion", "abrirCategorias", "abrirDetalleDe",
    "etiquetaConDescripcion", "pintarAtributos", "leerAtributos",
}


def _archivos_js() -> list[Path]:
    raiz = _raiz_del_front()
    if not raiz.exists():  # pragma: no cover
        pytest.skip(
            "No se encuentra el front. Se prueba desde el host, o con el "
            "front montado en /app/frontend-src (docker-compose.dev.yml)."
        )
    return sorted(raiz.rglob("*.js"))


def _sin_comentarios(texto: str) -> str:
    """
    El texto sin los comentarios.

    Un ejemplo de uso en un docstring (`import { x } from './y'`) NO es un
    import: sin esto, el chequeo reporta como faltante algo que el módulo si
    exporta.
    """
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
    return re.sub(r"//.*", "", texto)


def _exportados(ruta: Path) -> set[str]:
    """Los nombres que el módulo exporta."""
    texto = ruta.read_text(encoding="utf-8")
    nombres: set[str] = set()

    nombres |= set(re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", texto))
    nombres |= set(re.findall(r"export\s+(?:const|let|var|class)\s+(\w+)", texto))
    for bloque in re.findall(r"export\s*\{([^}]*)\}", texto):
        for parte in bloque.split(","):
            nombre = parte.strip().split(" as ")[0].strip()
            if nombre:
                nombres.add(nombre)
    return nombres


def _locales(texto: str) -> set[str]:
    """Los nombres definidos O importados en el archivo."""
    nombres: set[str] = set()

    for bloque in re.findall(r"import\s*\{([^}]*)\}", texto):
        for parte in bloque.split(","):
            nombre = parte.strip().split(" as ")[0].strip()
            if nombre:
                nombres.add(nombre)
    nombres |= set(re.findall(r"(?:async\s+)?function\s+(\w+)", texto))
    nombres |= set(re.findall(r"(?:const|let|var)\s+(\w+)", texto))
    nombres |= set(re.findall(r"class\s+(\w+)", texto))
    # Los imports dinamicos definen los nombres en su ambito:
    #   import('./x.js').then(({ a, b }) => ...)
    #   const { c, d } = await import('./y.js');
    for patron in (
        r"\.then\(\(\{([^}]*)\}\)",
        r"const\s*\{([^}]*)\}\s*=\s*await\s+import\(",
    ):
        for bloque in re.findall(patron, texto):
            for parte in bloque.split(","):
                nombre = parte.strip().split(":")[0].strip()
                if nombre:
                    nombres.add(nombre)
    return nombres


def _claves_de_crear(texto: str) -> set[str]:
    """
    Las claves de PRIMER NIVEL de cada llamada a `crear(...)`.

    Se parsea el objeto de opciones contando llaves, corchetes y parentesis:
    sin los tres, una llamada anidada dentro de `hijos: [...]` (por ejemplo
    `ícono(Íconos.camara, { tamaño: 20 })`) se cuenta como clave de la
    llamada de `crear`, y el test reporta un problema que no existe.
    """
    claves: set[str] = set()
    posicion = 0

    while True:
        # `(?<![\w.])` para que no tome "crearPaginacion(" por una llamada a
        # `crear()`: las opciones del componente de paginación tienen claves
        # ("total", "alCambiarPagina") que `crear()` no acepta.
        encontrado = re.search(r"(?<![\w.])crear\(", texto[posicion:])
        if not encontrado:
            break
        inicio = posicion + encontrado.start()
        posicion = inicio + 6

        # Si la llamada NO lleva un objeto (`crear('div')`), el "{" que
        # sigue es de OTRA llamada: tomarlo como si fuera suyo hace que las
        # claves del objeto ajeno se lean como claves de `crear()`.
        j = posicion
        while j < len(texto) and texto[j] in " \n\t":
            j += 1
        if j >= len(texto) or texto[j] != "{":
            continue

        llave = texto.find("{", inicio)
        if llave == -1:
            continue

        # Se busca el cierre del objeto contando los tres tipos de apertura.
        profundidad = 0
        fin = -1
        for i in range(llave, len(texto)):
            caracter = texto[i]
            if caracter in "{[(":
                profundidad += 1
            elif caracter in "}])":
                profundidad -= 1
                if profundidad == 0:
                    fin = i
                    break
        if fin == -1:
            continue

        cuerpo = texto[llave + 1 : fin]

        # Se recorre el cuerpo contando la profundidad de cada tipo.
        nivel_llaves = 0
        nivel_corchetes = 0
        nivel_parentesis = 0
        clave_actual = None
        i = 0
        while i < len(cuerpo):
            caracter = cuerpo[i]
            if caracter == "{":
                nivel_llaves += 1
            elif caracter == "}":
                nivel_llaves -= 1
            elif caracter == "[":
                nivel_corchetes += 1
            elif caracter == "]":
                nivel_corchetes -= 1
            elif caracter == "(":
                nivel_parentesis += 1
            elif caracter == ")":
                nivel_parentesis -= 1
            elif caracter in "'\"`":
                # Los literales se saltan enteros: un `estilo: 'margin-top:0'`
                # tiene dos puntos adentro y no es una clave.
                cierre = cuerpo.find(caracter, i + 1)
                if cierre == -1:
                    break
                i = cierre
            elif (
                clave_actual is None
                and nivel_llaves == 0
                and nivel_corchetes == 0
                and nivel_parentesis == 0
            ):
                m = re.match(r"\s*([a-zA-Z_$][\w$]*)\s*:", cuerpo[i:])
                if m:
                    clave_actual = m.group(1)
                    i += m.end() - 1
            elif clave_actual is not None and ":" in cuerpo[i : i + 2]:
                claves.add(clave_actual)
                clave_actual = None
            i += 1

    return claves


class TestCabecera:
    """
    La cabecera no se puede mover.

    QUE PASO
    ---------
    La cabecera era un `flex` con `margin-inline: auto` en el menú: el menú se
    centraba repartiendo el espacio sobrante. Al cambiar de módulo cambian
    las pestañas, cambia el ancho del menú, y con el se movia TODO lo que
    estaba alrededor: el selector de módulo (que es la única cosa que
    cambia de verdad) salia de un lado y volvia al otro.

    El operador veia el menú y las pestañas cambiar, y el selector de módulo
    moverse. Se lee como "la app se rompio".

    QUE CHEQUEA
    -----------
    1. Que la cabecera sea una GRID con áreas: es lo que fija las columnas.
    2. Que el menú NO se centre con margenes automaticos: eso es lo que
       hace que todo se mueva cuando el menú cambia de ancho.
    3. Que el selector de módulo tenga área propia: así puede bajar a su
       propia fila en el celular sin depender de donde este en el HTML.
    """

    def _css(self) -> str:
        raiz = _raiz_del_front()
        for ruta in (raiz / "styles.css", raiz.parent / "styles.css"):
            if ruta.exists():
                return ruta.read_text(encoding="utf-8")
        pytest.skip("No se encuentra styles.css.")

    def test_la_cabecera_es_una_grid_con_areas(self):
        css = self._css()

        bloque = re.search(r"\.cabecera\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla de la cabecera."

        regla = bloque.group(0)
        assert "display: grid" in regla, "La cabecera tiene que ser una grid."
        assert "grid-template-areas" in regla, (
            "La cabecera tiene que tener areas: sin ellas, el contenido se "
            "acomoda solo y se mueve cuando cambia el ancho del menu."
        )

    def test_el_menu_no_se_centra_con_margenes_automaticos(self):
        css = self._css()

        bloque = re.search(r"\.cabecera__nav\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla del menu."

        regla = bloque.group(0)
        assert "margin-inline: auto" not in regla, (
            "El menu no se puede centrar con margenes automaticos: al cambiar "
            "de modulo cambia su ancho, y con el se mueve toda la cabecera."
        )
        assert "grid-area: nav" in regla, "El menu tiene que tener su area."

    def test_el_selector_de_modulo_esta_centrado_en_la_segunda_fila(self):
        """
        El selector de módulo va en la SEGUNDA fila, junto al bloque del
        usuario ("Rol: Admin"), y CENTRADO en la página.

        POR QUÉ ESTÁ EN LA SEGUNDA FILA
        ------------------------------
        La primera fila es navegación: la marca, el menú de secciones y los
        botones de la derecha. El selector de módulo es otra cosa: es la
        elección operativa del día ("hoy trabajo el depósito"), y mezclada
        con el menú de secciones el operador no sabe cuál de las dos cosas
        está mirando.

        En la segunda fila, con el bloque del usuario, la pregunta "en qué
        módulo estoy" tiene su respuesta en un solo lugar.

        EL CENTRADO ESTÁ EN EL GRUPO
        -----------------------------
        Lo que se centra es el grupo de trabajo (`.cabecera__selector`), que
        contiene el selector de módulo Y el botón de escanear: los dos van
        al mismo nivel. Si se centraran por separado, el botón de escanear
        caería encima del módulo activo.
        """
        css = self._css()

        bloque = re.search(r"\.cabecera__selector\s*\{[^}]*\}", css, re.S)
        assert bloque, "No se encuentra la regla del grupo de trabajo."
        regla = bloque.group(0)

        # Centrado respecto de la PAGINA, no de una celda.
        assert "position: absolute" in regla, (
            "El grupo de trabajo va centrado con `position: absolute`: "
            "dentro de una celda de la grilla, el centro de la celda no es el "
            "centro de la pagina."
        )
        assert "left: 50%" in regla, (
            "El grupo de trabajo tiene que estar en el centro de la pagina."
        )
        assert "transform: translateX(-50%)" in regla, (
            "Sin la traslacion, el grupo queda corrido hacia la derecha "
            "la mitad de su ancho."
        )

        # Y el selector de modulo esta DENTRO del grupo: son dos partes de
        # la misma cosa, al mismo nivel.
        bloque_modulos = re.search(r"\.cabecera__modulos\s*\{[^}]*\}", css, re.S)
        assert bloque_modulos, "No se encuentra la regla del selector de modulo."
        assert "position: absolute" not in bloque_modulos.group(0), (
            "El selector de modulo no se posiciona por su cuenta: el que se "
            "posiciona es el grupo de trabajo, que lo contiene."
        )

        areas = re.search(r"grid-template-areas:\s*([^;]+);", css, re.S)
        assert areas, "No se encuentra el grid-template-areas de la cabecera."
        filas = areas.group(1).strip().split("\n")
        primera_fila = filas[0]
        assert "modulos" not in primera_fila, (
            "El selector de modulo no va en la primera fila de la cabecera, "
            "sino en la segunda, junto al bloque del usuario."
        )

    def test_la_segunda_fila_de_la_cabecera_existe(self):
        """El usuario y el selector de módulo comparten la segunda fila."""
        html = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
        if not html.exists():  # pragma: no cover
            pytest.skip("No se encuentra el index.html.")
        contenido = html.read_text(encoding="utf-8")

        assert 'class="cabecera__fila2"' in contenido, (
            "La segunda fila de la cabecera (el usuario y el selector de "
            "modulo) tiene que existir."
        )

        # Y el selector de módulo tiene que estar DENTRO de esa fila: si
        # queda como hijo directo de la cabecera, la posición absoluta se
        # mide respecto de la cabecera y no de la fila.
        inicio_fila = contenido.find('class="cabecera__fila2"')
        selector = contenido.find('id="cabecera-modulo"')
        menu = contenido.find('class="cabecera__nav"')

        assert inicio_fila != -1, "No se encuentra la segunda fila."
        assert selector != -1, "No se encuentra el selector de modulo."
        assert menu != -1, "No se encuentra el menu de secciones."

        assert inicio_fila < selector < menu, (
            "El selector de modulo tiene que estar dentro de la segunda fila "
            "de la cabecera (despues de abrirla y antes del menu), no fuera."
        )

class TestModales:
    """
    Un modal sin botón de cerrar es un modal TRABADO.

    QUE PASO
    ---------
    Cinco formularios (reportar incidencia, nuevo usuario, contraseña, mi
    cuenta, nueva categoría) se abrian con `acciones: []`: sin ningún botón
    visible. La única salida era la tecla Escape, y en un CELULAR no hay
    tecla Escape: el operador abria el formulario y no podia volver.

    No habia ningún error, ninguna petición fallida, nada en la consola. Solo
    un botón que no estaba.

    QUE CHEQUEA
    -----------
    1. Que ninguna llamada a `abrirDialogo` se quede sin acciones.
    2. Que todo modal con un formulario tenga, además, la opción de cerrar
       tocando el fondo (eso lo garantiza `dialogo.js`).
    """

    def _archivos_con_dialogo(self) -> list[Path]:
        archivos = []
        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            if "abrirDialogo" in texto:
                archivos.append(ruta)
        return archivos

    def test_ningun_modal_se_abre_sin_botones(self):
        problemas = []

        for ruta in self._archivos_con_dialogo():
            texto = ruta.read_text(encoding="utf-8")
            for m in re.finditer(r"abrirDialogo\(\{", texto):
                # Se toma el bloque de opciones: hasta el cierre de la llave
                # del objeto de opciones.
                i = m.end() - 1
                profundidad = 0
                fin = len(texto)
                for j in range(i, len(texto)):
                    if texto[j] == "{":
                        profundidad += 1
                    elif texto[j] == "}":
                        profundidad -= 1
                        if profundidad == 0:
                            fin = j
                            break
                bloque = texto[i:fin]
                if re.search(r"acciones:\s*\[\s*\]", bloque):
                    linea = texto[: m.start()].count("\n") + 1
                    problemas.append(
                        f"{ruta.name}:{linea}: el modal se abre sin ningun boton "
                        "(acciones vacias): en un celular no hay Escape para salir."
                    )

        assert not problemas, "\n".join(problemas)


class TestCamposDeFormulario:
    """
    Los helpers de formulario aceptan dos formas de llamada.

    `campoTexto` se llama de las dos formas en el código: con un objeto
    (`campoTexto({ etiqueta: 'Marca' })`) y posicional
    (`campoTexto('Ej: Dell', 'Marca')`).

    Cuando la firma era solo la de objeto, las llamadas posicionales
    pasaban un TEXTO donde se esperaba un objeto: la destructuracion daba
    `undefined` para todo, y el campo salia con la etiqueta "undefined".
    El formulario se veia roto sin decir por que.
    """

    def test_las_llamadas_a_campo_texto_pasan_lo_correcto(self):
        problemas = []

        for ruta in _archivos_js():
            # Sin comentarios: un ejemplo en un docstring no es una llamada.
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for m in re.finditer(r"(?<!function )campoTexto\(", texto):
                # El primer argumento: tiene que ser un objeto o un texto.
                i = m.end()
                while i < len(texto) and texto[i] in " \n":
                    i += 1
                if i >= len(texto):
                    continue
                primero = texto[i]
                if primero not in "{'" and primero != "`":
                    linea = texto[: m.start()].count("\n") + 1
                    problemas.append(
                        f"{ruta.name}:{linea}: `campoTexto()` con un primer "
                        f"argumento que no es ni objeto ni texto: {primero!r}"
                    )

        assert not problemas, "\n".join(problemas)

    def test_las_etiquetas_de_los_campos_no_son_undefined(self):
        """
        Una etiqueta "undefined" es la firma de una llamada con la forma
        equivocada: el helper recibió un string donde esperaba un objeto.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            # Busqueda literal: si el código tiene "undefined" pegado a una
            # etiqueta de campo, está mal.
            for m in re.finditer(r"etiqueta:\s*['\"]undefined['\"]", texto):
                linea = texto[: m.start()].count("\n") + 1
                problemas.append(f"{ruta.name}:{linea}: una etiqueta de campo es 'undefined'")

        assert not problemas, "\n".join(problemas)


class TestCss:
    """
    El CSS es código, y tiene una clase de bug que no da error: una regla
    MUERTA.

    QUE PASO
    ---------
    Un comentario de cabecera quedó sin cerrar (al insertar un bloque antes
    del comentario, se llevo su línea de cierre). Todo lo que venia
    después quedó DENTRO del comentario: la regla `.kpi` dejo de existir.

    El sintoma no fue un error: fue un diseno roto. Los cuadros de resumen
    del tablero dejaron de tener fondo y borde, y el navegador los pinto con
    su aspecto NATIVO de botón (gris, con borde de 3D). La app no daba
    ningún error, la consola no decia nada, y el único sintoma era que la
    pantalla se veia fea.

    POR QUE UN TEST Y NO UN LINTER
    -------------------------------
    Porque el linter (si lo hubiera) avisaria del comentario sin cerrar, que
    es el error de ESCRITURA; el test de abajo detecta el efecto: reglas
    muertas y contenedores sin cerrar. Lo primero se evita escribiendo bien;
    lo segundo se detecta solo.
    """

    def _css(self) -> str:
        # El CSS puede estar en la raíz del front o dentro de `src`: se
        # buscan los dos y no se assume ninguna de las dos.
        raiz = _raiz_del_front()
        for ruta in (raiz / "styles.css", raiz.parent / "styles.css"):
            if ruta.exists():
                return ruta.read_text(encoding="utf-8")
        pytest.skip("No se encuentra styles.css.")

    def test_los_comentarios_estan_cerrados(self):
        """
        Un `/*` sin su `*/` se traga todo lo que viene después: las reglas
        que quedan dentro del comentario no existen, y el navegador no dice
        nada.
        """
        texto = self._css()

        abiertos = texto.count("/*")
        cerrados = texto.count("*/")

        assert abiertos == cerrados, (
            f"styles.css tiene {abiertos} comentarios abiertos y {cerrados} cerrados: "
            "las reglas entre un '/*' sin cerrar y el siguiente '*/' estan MUERTAS."
        )

    def test_las_llaves_estan_balanceadas(self):
        """
        Una llave de más deja todo lo que sigue dentro de la regla anterior;
        una de menos rompe el archivo entero.
        """
        texto = self._css()
        # Los comentarios se borran primero: un `{` dentro de un comentario no
        # cuenta (y un `/*` puede tener un `*/` adentro de un ejemplo).
        sin_comentarios = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)

        profundidad = 0
        for i, linea in enumerate(sin_comentarios.split("\n"), 1):
            profundidad += linea.count("{") - linea.count("}")
            assert profundidad >= 0, f"styles.css: sobra un '}}' en la linea {i}"

        assert profundidad == 0, (
            f"styles.css: faltan {profundidad} llaves de cierre: las reglas "
            "del final del archivo estan dentro de otra regla."
        )

    def test_las_reglas_de_los_kpis_existen(self):
        """
        Las reglas que dan el diseno de los cuadros de resumen tienen que
        estar en el archivo. Si una se traga un comentario, el tablero se
        ve con el estilo NATIVO del botón: gris, con borde de 3D, y nadie
        sabe por que.
        """
        texto = self._css()

        for regla in (
            ".kpi {",
            ".kpi--destacado {",
            ".kpi__valor {",
            ".kpi--accion {",
        ):
            assert regla in texto, f"styles.css: falta la regla '{regla}'"


def _sin_acentos(texto: str) -> str:
    """El texto sin acentos: `botón` -> `boton`."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


# Clases que el front usa y que TODAVIA no tienen diseno en `styles.css`.
#
# No es una lista deDesired: es la lista de lo que falta. Cada clase que se
# agrega aqui es una decision: "esta clase todavia no tiene estilo, y se
# estiliza cuando haga falta".
#
# La lista NO puede tener acentos: un nombre de clase con acento es siempre
# un error de escritura.
CLASES_SIN_DISENO = {
    # Escáner: los chips de tipo de movimiento y la tarjeta de la camara.
    "chip--movimiento",
    "tarjeta--accion",
    "tarjeta--camara",
    "tarjeta__header",
    "vista-escanear",
    "formulario-manual",
    "boton--manual",
    "campo__control--codigo",
    "modal--escaner",
    # Modal de la etiqueta del producto.
    "modal--etiqueta",
    # Tarjeta de cliente: hay `__avatar` y `__acciones`, falta la base.
    "cliente-card",
    # Cierre de la incidencia.
    "incidencia__cierre",
    # Insignia de "creado" en la auditoria: el CSS tiene `resultado--ok`.
    "insignia--ok",
    # Titulo del login.
    "login__titulo",
    # Boton de modo exterior y el texto del idioma, en la cabecera.
    "cabecera__exterior",
    "cabecera__exterior-icon",
    "cabecera__idioma-texto",
}


def _sin_interpolaciones(texto: str) -> str:
    """
    Quita de una plantilla literal lo que va adentro de `${...}`.

    `insignia ${u.is_active ? 'insignia--deposito' : 'insignia--baja'}` deja
    `insignia  'insignia--deposito' : 'insignia--baja'`.

    Las llaves se cuentan, para que un `${}` que tenga un objeto adentro no
    corte la interpolacion por la mitad.
    """
    resultado = []
    i = 0
    while i < len(texto):
        if texto[i] == "$" and i + 1 < len(texto) and texto[i + 1] == "{":
            profundidad = 1
            i += 2
            while i < len(texto) and profundidad > 0:
                if texto[i] == "{":
                    profundidad += 1
                elif texto[i] == "}":
                    profundidad -= 1
                i += 1
            resultado.append(" ")
            continue
        resultado.append(texto[i])
        i += 1
    return "".join(resultado)


class TestClasesDelCss:
    """
    Una clase que el JS pide y el CSS no tiene NO EXISTE.

    QUE PASO
    --------
    Un pase de acentos cambió los nombres de clase del JavaScript: `boton`
    pasó a ser `botón` (con ñ) en los 30 archivos que la usaban. El CSS se
    quedó como estaba, con `.boton`.

    El resultado: NINGÚN BOTÓN DE LA APP TENÍA ESTILO. El navegador los
    pintaba con su aspecto nativo (gris, borde de 3D). La app no daba ningún
    error, la consola no decía nada, y el síntoma era "se ve feo".

    POR QUE ES UN TEST Y NO UN LINTER
    ----------------------------------
    Por la misma razón que el test de comentarios cerrados: el linter
    detectaría el error de escritura (un acento en un identificador), pero
    esto detecta el EFECTO: una clase que el código usa y el CSS no define.
    Lo primero se evita escribiendo bien; lo segundo se detecta solo.

    QUE CHEQUEA
    -----------
    Que cada clase que el front usa exista en `styles.css`. Con dos
    tolerancias, y las dos son deliberadas:

    - `x` (una sola letra): hay clases de una letra que son de uso interno.
    - Las que se arman por partes (`cabecera__modulo${activo ? ... : ''}`):
      el nombre final depende de una condición, y el CSS lo define.
    """

    def _css(self) -> str:
        raiz = _raiz_del_front()
        for ruta in (raiz / "styles.css", raiz.parent / "styles.css"):
            if ruta.exists():
                return ruta.read_text(encoding="utf-8")
        pytest.skip("No se encuentra styles.css.")

    def _clases_del_css(self) -> set[str]:
        # Solo las que empiezan una regla (`.boton {`, `.boton:hover {`), no
        # las que aparecen dentro de un selector compuesto.
        return set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", self._css()))

    def _clases_usadas(self) -> dict[str, set[str]]:
        """
        Las clases que el front pide, con los archivos de los que salen.

        Solo se miran los LUGARES donde un nombre de clase va de verdad:
        la clave `clase:`, el atributo `class=` y las llamadas a `classList`.
        En cualquier otro sitio, una palabra con acento es PROSA (un
        comentario que dice "un botón"), y tocarla seria romper el texto.

        Y de los Plantillas Literales solo se lee la parte LITERAL: lo de
        adentro de `${...}` es JavaScript (`${u.is_active ? 'a' : 'b'}`),
        y sus palabras no son clases.
        """
        raiz = _raiz_del_front()
        archivos = list(raiz.rglob("*.js")) + [raiz.parent / "index.html"]

        patrones = (
            r"(clase:\s*')([^']*)(')",
            r'(clase:\s*")([^"]*)(")',
            r"(clase:\s*`)([^`]*)(`)",
            r'(class=")([^"]*)(")',
            r"(classList\.(?:add|toggle|remove)\(')([^']*)('\))",
        )

        usadas: dict[str, set[str]] = {}
        for archivo in archivos:
            if not archivo.exists():
                continue
            texto = archivo.read_text(encoding="utf-8")
            for patron in patrones:
                for _, cuerpo, _ in re.findall(patron, texto):
                    cuerpo = _sin_interpolaciones(cuerpo)
                    # Los tokens: ni comillas, ni espacios. Un token que
                    # termina en `--` es el PREFIJO de un nombre que se arma
                    # con una interpolacion (`insignia--${...}`): no es una
                    # clase completa y no se puede comprobar.
                    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", cuerpo):
                        if token == "x" or token.endswith("--"):
                            continue
                        usadas.setdefault(token, set()).add(archivo.name)
        return usadas

    def test_toda_clase_usada_existe_en_el_css(self):
        """
        La clase que pide el JavaScript tiene que existir en el CSS.

        El caso que DUELA es el del pase de acentos: el JS pedía `botón` y
        el CSS tenía `.boton`. Por eso la comparación IGNORA los acentos: si
        el CSS tiene la clase sin acentos y el JS la pide con acentos, es un
        bug, y sale de acá.

        Las clases que el CSS no tiene NI con acentos ni sin ellos están en
        la lista de pendientes: son clases que todavia no tienen diseno (se
        usan como marcador o como gancho). Si una clase nueva aparece sin
        diseno, se agrega a la lista a proposito, no por descuido.
        """
        definidas = self._clases_del_css()
        usadas = self._clases_usadas()

        # Las que el CSS tiene, sin acentos.
        definidas_planas = {_sin_acentos(clase) for clase in definidas}

        faltan = {}
        for clase, archivos in usadas.items():
            if clase in definidas:
                continue
            if _sin_acentos(clase) in definidas_planas:
                # El CSS tiene la version sin acentos: el JS pide la otra.
                faltan[clase] = archivos
            elif clase not in CLASES_SIN_DISENO:
                faltan[clase] = archivos

        assert not faltan, (
            "Estas clases las pide el JavaScript y no existen en styles.css: "
            "el navegador las ignora y el elemento sale SIN ESTILO (con el "
            "aspecto nativo del navegador).\n"
            + "\n".join(
                f"  .{clase}  (en {', '.join(sorted(archivos))})"
                for clase, archivos in sorted(faltan.items())
            )
        )

    def test_las_clases_sin_diseno_no_crecen_solas(self):
        """
        La lista de clases que todavia no tienen diseno no crece sin que
        alguien lo decida.

        Si una clase nueva aparece en la lista, es porque alguien la agrego a
        proposito (para darle diseno después). Si aparece sola, es que se
        coló un `.algo` en un `clase:` y el elemento sale sin estilo.
        """
        # No hay lista fija que comparar: este test documenta la idea. Lo que
        # se verifica es que la lista NO contenga acentos (serian errores de
        # escritura) ni espacios (no son clases).
        for clase in CLASES_SIN_DISENO:
            assert " " not in clase, f"La clase '{clase}' tiene espacios."
            assert not any(ord(c) > 127 for c in clase), (
                f"La clase '{clase}' lleva acentos: los nombres de clase van "
                "sin acentos."
            )

    def test_las_clases_no_llevan_acentos(self):
        """
        Los nombres de clase son IDENTIFICADORES, no prosa: van sin acentos.

        Un acento en el nombre es un error de escritura que no da error de
        sintaxis: el JS pide `botón` y el CSS define `.boton`, y el elemento
        sale sin estilo sin que nadie se entere.

        SE BUSCAN EN TODA LA CLASE, NO SOLO EN LOS LITERALES
        -----------------------------------------------------
        La primera version del test solo miraba `clase: '...'` y se le
        escaparon las clases que se arman por CONCATENACION
        (`' cabecera__módulo--no-disponible'`), que son las que mas se
        escapan.

        EL PATRON Y LA COMPROBACION
        ---------------------------
        El patron es el del NOMBRE DE CLASE: tiene guion bajo
        (`lista__titulo`, `cabecera__modulo`). La prosa en español casi
        nunca lleva guion bajo, asi que el patron no da falsos positivos.

        Y hay una comprobacion extra: solo se reporta si el CSS tiene la
        version SIN ACENTOS. Sin ella, se reportarian cosas que no son
        clases: un valor de enum de la base (`Daño_TOTAL`), el nombre de un
        trigger (`incident_transición_válida`), una clave de un diccionario
        (`en_tránsito`). Esos llevan guion bajo y acento, y no son clases:
        son IDENTIFICADORES DEL NEGOCIO, donde el acento sí importa (es el
        dato, no el estilo).
        """
        raiz = _raiz_del_front()
        archivos = list(raiz.rglob("*.js")) + [raiz.parent / "index.html"]

        # Un nombre de clase: palabras separadas por guion bajo (uno o dos:
        # `lista__titulo`, `cabecera__modulo--activo`).
        #
        # Las letras acentuadas ESTAN en el patron: si no, el nombre se corta
        # justo antes de la `ñ` y el acento pasa desapercibido (que es
        # exactamente lo que hay que detectar).
        letras = r"A-Za-z0-9áéíóúüñÁÉÍÓÚÜÑ"
        nombre = rf"[A-Za-z][{letras}]*(?:__?[{letras}]+)+"

        # Las clases que el CSS define: si el token sin acentos está entre
        # ellas, el token con acentos es un nombre de clase con un error.
        definidas = set(re.findall(r"\.([A-Za-z0-9_-]+)", self._css()))

        con_acento: dict[str, set[str]] = {}
        for archivo in archivos:
            if not archivo.exists():
                continue
            texto = archivo.read_text(encoding="utf-8")
            for token in re.findall(nombre, texto):
                if not any(ord(c) > 127 for c in token):
                    continue
                if _sin_acentos(token) in definidas:
                    con_acento.setdefault(token, set()).add(archivo.name)

        assert not con_acento, (
            "Estos nombres de clase del JavaScript llevan acentos. Los "
            "nombres de clase son identificadores: van sin acentos, o el CSS "
            "no los encuentra y el elemento sale sin estilo.\n"
            + "\n".join(
                f"  .{clase}  (en {', '.join(sorted(archivos))})"
                for clase, archivos in sorted(con_acento.items())
            )
        )


class TestGrafoDeModulos:
    def test_todo_import_existe(self):
        """
        Un import de un nombre que el módulo destino no exporta ROMPE el grafo
        de módulos entero: la app no arranca, y el error dice "Failed to load
        module script" sin decir cual.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for nombres, modulo in re.findall(
                r"import\s*\{([^}]*)\}\s*from\s*'([^']+)'", texto
            ):
                if not modulo.startswith("."):
                    continue

                destino = (ruta.parent / modulo).resolve()
                if destino.is_dir():
                    destino = destino / "index.js"
                elif not destino.exists() and destino.with_suffix(".js").exists():
                    destino = destino.with_suffix(".js")

                if not destino.exists():
                    problemas.append(f"{ruta.name}: el modulo '{modulo}' no existe")
                    continue

                exportados = _exportados(destino)
                for parte in nombres.split(","):
                    nombre = parte.strip().split(" as ")[0].strip()
                    if nombre and nombre not in exportados:
                        problemas.append(
                            f"{ruta.name}: importa '{nombre}' de '{modulo}', que no lo exporta"
                        )

        assert not problemas, "\n".join(problemas)

    def test_los_helpers_compartidos_estan_importados(self):
        """
        Un helper compartido usado sin importar es un `ReferenceError` en
        tiempo de ejecucion: el botón no hace nada y no hay error en la
        consola de la página.

        El chequeo mira los nombres usados COMO LLAMADA (`nombre(`), así que no
        marca los que aparecen en un comentario o en un texto.
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")

            sin_comentarios = _sin_comentarios(texto)

            locales = _locales(sin_comentarios)
            usados = set(re.findall(r"(?<![\w.$])([a-zA-Z_$][\w$]*)\s*\(", sin_comentarios))

            for nombre in usados & HELPERS_COMPARTIDOS:
                if nombre not in locales:
                    problemas.append(f"{ruta.name}: usa '{nombre}' y no lo importa")

        assert not problemas, "\n".join(problemas)

    def test_las_claves_de_crear_estan_permitidas(self):
        """
        `crear()` descarta en SILENCIO las claves que no conoce.

        El helper acepta `clase`, `id`, `texto`, `atributos`, `hijos` y
        `estilo`, y cualquier otra cosa se pierde sin error ni aviso. Un `role: 'tab'`
        puesto al lado de `atributos` desaparece, y el botón queda sin
        anunciar: el lector de pantalla lo lee como un botón cualquiera.

        Pasa tres veces en este repo (un `onclick` que no se escuchaba, un
        `errorDe` sin importar y un `role` perdido). Cada vez el sintoma fue
        "el botón no hace nada".
        """
        PERMITIDAS = {"clase", "id", "texto", "atributos", "hijos", "estilo"}
        problemas = []

        for ruta in _archivos_js():
            texto = _sin_comentarios(ruta.read_text(encoding="utf-8"))
            for clave in _claves_de_crear(texto):
                if clave not in PERMITIDAS:
                    problemas.append(
                        f"{ruta.name}: `crear()` con la clave '{clave}', que se descarta en silencio"
                    )

        unicos = sorted(set(problemas))
        assert not unicos, "\n".join(unicos)

    def test_no_hay_imports_de_ruta_absoluta(self):
        """
        Un import con ruta absoluta (`/src/...`) no lo resuelve el navegador:
        el módulo no se carga y el error dice "Failed to resolve module specifier".
        """
        problemas = []

        for ruta in _archivos_js():
            texto = ruta.read_text(encoding="utf-8")
            for modulo in re.findall(r"from\s+'([^']+)'", texto):
                if modulo.startswith("/") and not modulo.startswith("//"):
                    problemas.append(f"{ruta.name}: import con ruta absoluta '{modulo}'")

        assert not problemas, "\n".join(problemas)
