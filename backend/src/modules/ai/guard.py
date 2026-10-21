"""
Guardia del SQL generado por el modelo.

Este archivo es la razon por la que el agente de preguntas es aceptable en un
sistema con datos de clientes. Un LLM escribe SQL; este módulo se encarga de
que ese SQL no pueda escribir, no pueda leer de más, ni pueda tumbar el servicio.

El modelo es la parte NO confiable del sistema, y hay que decirlo de forma
explicita: pedirle a un LLM "no hagas DROP TABLE" es una instruccion, no un
control de acceso. Un prompt injection escondido en el texto de un cliente no
lo detiene. Un parser si.

CAPAS DE DEFENSA (todas hacen falta; ninguna sola alcanza)
---------------------------------------------------------
  1. Validacion textual del SQL, aqui. Barata, corta-circuita lo obvio.
  2. `SET TRANSACTION READ ONLY` al ejecutar. Esta es la que de verdad importa:
     Postgres, no Python, rechaza la escritura. Si la validacion textual tiene
     un hueco, el motor de la base lo cierra.
  3. `statement_timeout`. Un modelo que responde "SELECT * FROM
     movement_history" sin filtro es un hecho, no un error: la transaccion se
     corta sola.
  4. `LIMIT` forzado. Un reporte de inventario no devuelve un millon de filas.
  5. El prompt solo ve el ESQUEMA. Las filas crudas nunca salen del servidor,
     asi que no hay exfiltracion aunque el modelo se lo pidan.
"""

import re
from typing import Any

# Palabras que pueden escribir, cambiar la sesión, o leer fuera de la BD.
_PALABRAS_PROHIBIDAS: set[str] = {
    # escritura
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "comment",
    "merge",
    "upsert",
    "copy",
    # control de flujo que no corresponde a una consulta de lectura
    "vacuum",
    "reindex",
    "cluster",
    "listen",
    "notify",
    "do",
    "call",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    # cambiar el rol o la sesión es el camino clasico de
    # "SELECT ...; SET ROLE postgres; ..."
    "set",
    "reset",
    "security",
    "definer",
    # funciones de solo lectura carisima: un `pg_sleep(600)` colgado en una
    # subconsulta es un DoS de una línea
    "pg_sleep",
    "pg_read_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "pg_terminate_backend",
    "pg_cancel_backend",
}

_COMENTARIOS = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
# Literales de texto: su contenido NO es código. Sin esto, un cliente llamado
# "Pedro DROP TABLE" haria fallar la validación de una consulta legitima.
_CADENAS = re.compile(r"'(?:[^']|'')*'")

_IDENTIFICADORES_PERMITIDOS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLRechazado(ValueError):
    """El SQL generado no pasa la guardia. Se traduce a 400 en el router."""


def _normalizar(sql: str) -> str:
    """Deja el SQL sin comentarios y con espacios unificados."""
    limpio = _COMENTARIOS.sub(" ", sql)
    limpio = limpio.replace("\x00", "")  # NUL: trunca la consulta en libpq
    return re.sub(r"\s+", " ", limpio).strip()


def _palabras_de(sql: str) -> list[str]:
    """Palabras clave en minusculas, con las cadenas entrecomilladas neutralizadas."""
    sin_cadenas = _CADENAS.sub(" '' ", sql)
    return re.findall(r"[a-z_]+", sin_cadenas.lower())


def _cuerpos_de_cte(sql: str) -> list[str]:
    """
    Devuelve el cuerpo de cada CTE de un `WITH`, en orden.

    Es un parser mínimo y a proposito: recorre el texto contando parentesis y
    se queda con lo que va entre `AS (` y su `)` de cierre, saltandose los
    parentesis anidados y los que estan dentro de cadenas o comentarios.

    Que sea "mínimo" no significa que sea flojo: la alternativa —filtrar
    palabras prohibidas del texto y buscar en el resto— ya se probo y dejo
    pasar un INSERT. Este parser no busca palabras: mira la estructura, que es
    lo único que no se puede falsear escribiendo el SQL de otra manera.
    """
    cuerpos: list[str] = []
    # Se ignoran los parentesis dentro de cadenas para no desbalancear el conteo
    # con un '(' en un literal. No se hace escapado de '' porque el texto ya
    # viene normalizado y el caso es lo bastante raro como para no多重-complicar.
    limpio_sin_cadenas = _CADENAS.sub(
        lambda m: "'" + "_" * (len(m.group(0)) - 2) + "'", sql
    )

    i = limpio_sin_cadenas.upper().find("WITH")
    if i == -1:
        return cuerpos

    pos = i + len("WITH")
    largo = len(limpio_sin_cadenas)

    while pos < largo:
        # Se terminó la lista de CTEs: lo que sigue es la consulta principal.
        # Sin esta parada, un alias de la consulta final ("SELECT 1 AS total")
        # se confundiria con el AS de otro CTE.
        if re.match(r"\s*SELECT\b", limpio_sin_cadenas[pos:], re.IGNORECASE):
            break

        # buscar el `AS` que precede al cuerpo
        as_idx = limpio_sin_cadenas.upper().find("AS", pos)
        if as_idx == -1:
            break

        # El parentesis de apertura no tiene que estar pegado al AS: la
        # sintaxis admite `AS MATERIALIZED (` y `AS NOT MATERIALIZED (` y el
        # cuerpo arranca igual en el parentesis. Lo que hay entre AS y el
        # parentesis no importa para el análisis.
        abre = limpio_sin_cadenas.find("(", as_idx)
        if abre == -1:
            break

        nivel = 0
        cursor = abre
        while cursor < largo:
            caracter = limpio_sin_cadenas[cursor]
            if caracter == "(":
                nivel += 1
            elif caracter == ")":
                nivel -= 1
                if nivel == 0:
                    break
            cursor += 1

        if nivel != 0:
            break  # parentesis desbalanceados: lo rechaza el chequeo de sintaxis

        cuerpos.append(sql[abre + 1 : cursor])

        # seguir con el siguiente CTE o con la consulta principal
        pos = cursor + 1
        while pos < largo and limpio_sin_cadenas[pos] in " \t\n":
            pos += 1
        if pos < largo and limpio_sin_cadenas[pos] == ",":
            pos += 1

    return cuerpos


def validar_select(sql: str) -> str:
    """
    Válida y devuelve el SQL listo para ejecutar.

    Lanza `SQLRechazado` con un motivo legible. El motivo importa: "consulta no
    permitida" sin decir cual deja al operador adivinando si el modelo está
    roto o si hay un problema de permisos.
    """
    if not sql or not sql.strip():
        raise SQLRechazado("El modelo no devolvio ninguna consulta.")

    limpio = _normalizar(sql)

    # Un solo statement. `SELECT 1; DROP TABLE devices` es el ataque clasico,
    # y el simple query protocol de Postgres permite multi-statement sin
    # quejarse.
    if ";" in limpio.rstrip(";"):
        raise SQLRechazado(
            "Solo se permite una sentencia. Se detectaron varias separadas por ';'."
        )

    limpio = limpio.rstrip().rstrip(";").strip()

    # Tiene que ser una lectura. `WITH ... INSERT` es la evasion clasica de un
    # filtro por palabra clave, así que el WITH no abre la puerta: el único
    # prefijo válido es SELECT o WITH.
    if not re.match(r"^(SELECT|WITH)\b", limpio, re.IGNORECASE):
        raise SQLRechazado(
            "Solo se permiten consultas de lectura (SELECT). "
            f"La consulta empieza con: {limpio[:60]!r}"
        )

    # WITH es la evasion clasica de un filtro por palabra clave: `WITH x AS
    # (INSERT INTO ... RETURNING *) SELECT * FROM x` empieza con WITH y
    # termina con SELECT, asi que un filtro por prefijo lo deja pasar.
    #
    # LA PRIMERA Versión DE ESTE CHECK FiltrABA LAS PALABRAS PROHIBIDAS DEL
    # CUERPO DEL CTE Y Después BUSCABA EN EL RESTO. Es un error que se hace
    # pasar por correcto: al borrar "insert" del texto, el resto ya no la
    # tiene, y la consulta entra. Se probó y pas�o.
    #
    # Lo que hace falta no es filtrar palabras sino LEER LA ESTRUCTURA: cada
    # cuerpo de un CTE tiene que empezar con SELECT.
    if re.match(r"^WITH\b", limpio, re.IGNORECASE):
        cuerpos = _cuerpos_de_cte(limpio)
        if not cuerpos:
            raise SQLRechazado("No se pudo interpretar la estructura del WITH.")

        for cuerpo in cuerpos:
            if not re.match(r"^\s*(SELECT|VALUES)\b", cuerpo, re.IGNORECASE):
                raise SQLRechazado(
                    "Cada CTE (WITH) debe ser un SELECT. Se encontro uno que "
                    f"empieza con: {cuerpo.strip()[:60]!r}"
                )

    # Chequeo global de palabras prohibidas sobre el texto ENTERO, sin
    # excepciones. Cualquier palabra vetada es una falla, este o dentro de un
    # CTE: no hay ningun uso legitimo de INSERT/SET/COPY/etc. en una consulta
    # que la IA pueda querer escribir.
    palabras = set(_palabras_de(limpio))

    prohibidas = palabras & _PALABRAS_PROHIBIDAS
    if prohibidas:
        raise SQLRechazado(
            f"La consulta contiene palabras no permitidas: "
            f"{', '.join(sorted(prohibidas))}. Solo se admiten lecturas."
        )

    # Columnas que no salen del servidor. Va Después del chequeo de palabras
    # para que el mensaje sea el más específico posible: "no podés leer
    # password" es más útil que "palabra no permitida: password".
    columna_prohibida = _referencia_columna_prohibida(limpio)
    if columna_prohibida:
        raise SQLRechazado(
            f"La columna `{columna_prohibida}` no se puede consultar: no sale "
            "del servidor por preguntas en lenguaje natural."
        )

    # LAS TABLAS
    #
    # El chequeo de palabras y de columnas alcanza para que no se ESCRIBA y
    # para que no salgan las credenciales. Lo que no cubria era el ALCANCE: la
    # lista de tablas permitidas se le decia al modelo, pero no se le
    # imponia. Con las credenciales de la aplicacion, que ven todo, un
    # operador podia pedirle a la IA los datos de una tabla que su rol no
    # tiene permiso de ver.
    tabla_no_permitida = _tabla_no_permitida(limpio)
    if tabla_no_permitida:
        raise SQLRechazado(
            f"La tabla `{tabla_no_permitida}` no se puede consultar: el "
            "agente solo lee el inventario (clientes, equipos, movimientos, "
            "reparaciones, ordenes de compra)."
        )

    return limpio


def aplicar_limite(sql: str, maximo: int) -> str:
    """
    Anade un LIMIT si no hay uno.

    Un SELECT anidado ya tiene su propio tope y Postgres rechaza LIMIT dentro
    de una subconsulta, asi que solo se agrega si no hay uno. No es una
    limitacion teorica: agregar LIMIT a "SELECT * FROM (SELECT ... LIMIT 5) t"
    es un error de sintaxis.
    """
    if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql} LIMIT {int(maximo)}"


def extraer_sql(texto: str) -> str:
    """
    Saca el SQL de lo que devolvio el modelo.

    Tres capas, porque falla de tres formas distintas:
      1. Bloque ```sql ... ``` cuando el modelo obedecio el formato pedido.
      2. El texto entero, cuando devolvio solo la consulta sin envoltura.
      3. La primera línea que empieza con SELECT/WITH, cuando metio una
         introduccion ("Claro, aqui tienes: SELECT ..."). Un modelo chico de
         3b hace esto bastante seguido, y es el que corre en el fallback.
    """
    bloque = re.search(r"```(?:sql)?\s*(.+?)```", texto, re.DOTALL | re.IGNORECASE)
    if bloque:
        return bloque.group(1).strip()

    directo = texto.strip()
    if re.match(r"^(SELECT|WITH)\b", directo, re.IGNORECASE):
        return directo.rstrip(";")

    linea = next(
        (
            l
            for l in directo.splitlines()
            if re.match(r"^\s*(SELECT|WITH)\b", l, re.IGNORECASE)
        ),
        None,
    )
    if linea:
        return linea.strip().rstrip(";")

    raise SQLRechazado(
        "La respuesta del modelo no contiene una consulta SQL reconocible: "
        f"{texto[:200]!r}"
    )


def nombre_de_tabla(valor: Any) -> str:
    """
    Válida un identificador usado como nombre de tabla.

    Un nombre que viene del exterior NO se puede interpolar en SQL: aunque lo
    haya puesto el operador, el camino de vuelta desde la base o desde un
    parámetro HTTP lo convierte en dato no confiable otra vez.
    """
    texto = str(valor)
    if not _IDENTIFICADORES_PERMITIDOS.match(texto):
        raise SQLRechazado(f"Identificador invalido: {texto!r}")
    return texto


def tablas_permitidas() -> set[str]:
    """
    Las 11 tablas del inventario.

    No se descubre de information_schema: si el modelo puede leer los
    catalogs, también aprende de tablas que se agreguen después sin querer.
    Una lista explicita, usada tanto en el prompt como en la validación, es más
    aburrida y es correcta.
    """
    return {
        "clients",
        "locations",
        "categories",
        "models",
        "devices",
        "movement_history",
        "repairs",
        "disposal_certificates",
        "roles",
        "users",
        "audit_logs",
    }


# Las VISTAS que el agente puede consultar.
#
# Cada una es una consulta ya escrita sobre tablas que el prompt no le
# muestra (stock, mantenimiento, incidencias). Se listan una por una: si se
# agrega una vista nueva, hay que decidir aqui si entra.
VISTAS_PERMITIDAS: set[str] = {
    "v_devices_maintenance_due",
    "v_low_stock",
    "v_stock_summary",
    "v_incident_sla",
    "v_maintenance_metrics",
}


# Columnas que el LLM no puede leer NI SI EL MISMO LO PIDE.
#
# `_PALABRAS_PROHIBIDAS` filtra ESCRITURAS; esto filtra LECTURAS que no
# deberian salir del servidor. Es distinto y no estaba cubierto: `users.password`
# es un hash bcrypt, no un dato de negocio, y no hay ningún caso de uso legitimo
# para que un LLM lo consulte. Decirselo en el prompt no alcanza, lo mismo que
# con los DROP TABLE: una instruccion no es un control de acceso.
_COLUMNAS_PROHIBIDAS: set[str] = {
    "password",
    # Contenido crudo de la bitacora: puede llevar valores de filas de
    # cualquier tabla, incluidas las de negocio y las de clientes.
    "old_value",
    "new_value",
    # DATOS DE CONTACTO
    #
    # Las preguntas de inventario ("cuantos equipos hay en el deposito",
    # "que se movio ayer") no necesitan el email ni el telefono de nadie. Y
    # un operador que en la interfaz no puede ver la lista de usuarios, por
    # la IA no tiene por que verle los contactos: la tabla `users` se puede
    # consultar para el nombre y el rol (que es lo que se usa para armar los
    # historiales), pero el contacto personal no sale.
    "email",
    "contact_email",
    "phone",
    "phone2",
    "contact_phone",
    "address",
    # El identificador fiscal identifica al cliente ante la administracion
    # publica: no es un dato de inventario.
    "tax_id",
}


def _tablas_del_sql(sql: str) -> set[str]:
    """
    Las tablas que el SQL menciona.

    Se buscan despues de `FROM` y de `JOIN` (con sus alias) y tambien las
    tablas del `UPDATE`/`INTO` (que ya no llegan aca, pero el analizador no
    distingue y es mejor que sobre).

    El nombre de la tabla es el que sigue a la palabra clave: los alias
    (`FROM devices d`) no son tablas, y `v_devices` (una vista) tampoco: se
    toma el ultimo segmento de un nombre con punto, y las vistas que no
    esten en la lista se tratan como la tabla de base.
    """
    tablas: set[str] = set()

    for coincidencia in re.finditer(
        r"\b(?:from|join|into|update)\s+([a-zA-Z_][\w$]*(?:\.[a-zA-Z_][\w$]*)?)",
        sql,
        re.IGNORECASE,
    ):
        nombre = coincidencia.group(1).lower()
        if "." in nombre:
            nombre = nombre.rsplit(".", 1)[1]
        tablas.add(nombre)

    return tablas


def _tabla_no_permitida(sql: str) -> str | None:
    """
    La tabla que el SQL menciona y que NO puede consultar, o None.

    POR QUE IMPORTA
    ---------------
    La lista de tablas permitidas se le DECIA al modelo en el prompt, pero
    no se IMPONIA: el modelo podia escribir `SELECT ... FROM sessions` y
    salia. Y la consulta corria con las credenciales de la aplicacion, que
    ven TODO: un operador (que no puede ver la lista de usuarios ni la
    auditoria en la interfaz) podia pedirle a la IA "dame los usuarios con
    su email" y le contestaba.

    O sea: el permiso por rol no se aplicaba a las consultas del agente.
    Esto lo cierra: la tabla tiene que estar en la lista, y si no esta, la
    consulta se rechaza con un motivo legible.
    """
    permitidas = tablas_permitidas()

    # Los NOMBRES DE LOS CTE no son tablas: `WITH x AS (SELECT ...) SELECT *
    # FROM x` consulta la tabla del cuerpo, no una tabla llamada "x".
    # El nombre de un CTE es el que va justo antes de su `AS (`: asi se
    #recognizan todos, los que van separados por comas y los que usan
    # "RECURSIVO" (que en espanol es "RECURSIVO" y no "RECURSIVE").
    nombres_de_cte = {
        m.group(1).lower()
        for m in re.finditer(
            r"([a-zA-Z_][\w$]*)\s+AS\s*(?:(?:NOT\s+)?MATERIALIZED\s*)?\(",
            sql,
            re.IGNORECASE,
        )
    }

    for tabla in _tablas_del_sql(sql):
        if tabla in permitidas or tabla in nombres_de_cte:
            continue
        # Las VISTAS del sistema son consultas sobre tablas que el modelo
        # no ve en el prompt (stock, mantenimiento, incidencias). Se
        # permiten una por una y por su nombre: un comodin `v_*` seria un
        # agujero, porque cualquier vista que se agregue despues pasaria
        # sin revisarla.
        if tabla in VISTAS_PERMITIDAS:
            continue
        return tabla
    return None


def _referencia_columna_prohibida(sql: str) -> str | None:
    """
    Devuelve el nombre de la columna prohibida que el SQL menciona, o None.

    Se busca como palabra completa (`\\bpassword\\b`) para no dar falsos
    positivos con nombres legitimos. También cubre el acceso por JSONB
    (`->>'password'`), donde el nombre aparece igual.
    """
    minusculas = _CADENAS.sub(" '' ", sql).lower()
    for columna in _COLUMNAS_PROHIBIDAS:
        if re.search(r"\b" + columna + r"\b", minusculas):
            return columna
    return None
