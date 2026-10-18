"""
Guardia del SQL generado por el modelo.

Este archivo es la razon por la que el agente de preguntas es aceptable en un
sistema con datos de clientes. Un LLM escribe SQL; este modulo se encarga de
que ese SQL no pueda escribir, no pueda leer de mas, ni pueda tumbar el servicio.

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

# Palabras que pueden escribir, cambiar la sesion, o leer fuera de la BD.
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
    # cambiar el rol o la sesion es el camino clasico de
    # "SELECT ...; SET ROLE postgres; ..."
    "set",
    "reset",
    "security",
    "definer",
    # funciones de solo lectura carisima: un `pg_sleep(600)` colgado en una
    # subconsulta es un DoS de una linea
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
# Literales de texto: su contenido NO es codigo. Sin esto, un cliente llamado
# "Pedro DROP TABLE" haria fallar la validacion de una consulta legitima.
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

    Es un parser minimo y a proposito: recorre el texto contando parentesis y
    se queda con lo que va entre `AS (` y su `)` de cierre, saltandose los
    parentesis anidados y los que estan dentro de cadenas o comentarios.

    Que sea "minimo" no significa que sea flojo: la alternativa —filtrar
    palabras prohibidas del texto y buscar en el resto— ya se probo y dejo
    pasar un INSERT. Este parser no busca palabras: mira la estructura, que es
    lo unico que no se puede falsear escribiendo el SQL de otra manera.
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
        # Se termino la lista de CTEs: lo que sigue es la consulta principal.
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
        # parentesis no importa para el analisis.
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
    Valida y devuelve el SQL listo para ejecutar.

    Lanza `SQLRechazado` con un motivo legible. El motivo importa: "consulta no
    permitida" sin decir cual deja al operador adivinando si el modelo esta
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
    # filtro por palabra clave, asi que el WITH no abre la puerta: el unico
    # prefijo valido es SELECT o WITH.
    if not re.match(r"^(SELECT|WITH)\b", limpio, re.IGNORECASE):
        raise SQLRechazado(
            "Solo se permiten consultas de lectura (SELECT). "
            f"La consulta empieza con: {limpio[:60]!r}"
        )

    # WITH es la evasion clasica de un filtro por palabra clave: `WITH x AS
    # (INSERT INTO ... RETURNING *) SELECT * FROM x` empieza con WITH y
    # termina con SELECT, asi que un filtro por prefijo lo deja pasar.
    #
    # LA PRIMERA VERSION DE ESTE CHECK FiltrABA LAS PALABRAS PROHIBIDAS DEL
    # CUERPO DEL CTE Y DESPUES BUSCABA EN EL RESTO. Es un error que se hace
    # pasar por correcto: al borrar "insert" del texto, el resto ya no la
    # tiene, y la consulta entra. Se probo y pas�o.
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

    # Columnas que no salen del servidor. Va DESPUES del chequeo de palabras
    # para que el mensaje sea el mas especifico posible: "no podes leer
    # password" es mas util que "palabra no permitida: password".
    columna_prohibida = _referencia_columna_prohibida(limpio)
    if columna_prohibida:
        raise SQLRechazado(
            f"La columna `{columna_prohibida}` no se puede consultar: no sale "
            "del servidor por preguntas en lenguaje natural."
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
      3. La primera linea que empieza con SELECT/WITH, cuando metio una
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
    Valida un identificador usado como nombre de tabla.

    Un nombre que viene del exterior NO se puede interpolar en SQL: aunque lo
    haya puesto el operador, el camino de vuelta desde la base o desde un
    parametro HTTP lo convierte en dato no confiable otra vez.
    """
    texto = str(valor)
    if not _IDENTIFICADORES_PERMITIDOS.match(texto):
        raise SQLRechazado(f"Identificador invalido: {texto!r}")
    return texto


def tablas_permitidas() -> set[str]:
    """
    Las 11 tablas del inventario.

    No se descubre de information_schema: si el modelo puede leer los
    catalogs, tambien aprende de tablas que se agreguen despues sin querer.
    Una lista explicita, usada tanto en el prompt como en la validacion, es mas
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


# Columnas que el LLM no puede leer NI SI EL MISMO LO PIDE.
#
# `_PALABRAS_PROHIBIDAS` filtra ESCRITURAS; esto filtra LECTURAS que no
# deberian salir del servidor. Es distinto y no estaba cubierto: `users.password`
# es un hash bcrypt, no un dato de negocio, y no hay ningun caso de uso legitimo
# para que un LLM lo consulte. Decirselo en el prompt no alcanza, lo mismo que
# con los DROP TABLE: una instruccion no es un control de acceso.
_COLUMNAS_PROHIBIDAS: set[str] = {
    "password",
    # Contenido crudo de la bitacora: puede llevar valores de filas de
    # cualquier tabla, incluidas las de negocio y las de clientes.
    "old_value",
    "new_value",
}


def _referencia_columna_prohibida(sql: str) -> str | None:
    """
    Devuelve el nombre de la columna prohibida que el SQL menciona, o None.

    Se busca como palabra completa (`\\bpassword\\b`) para no dar falsos
    positivos con nombres legitimos. Tambien cubre el acceso por JSONB
    (`->>'password'`), donde el nombre aparece igual.
    """
    minusculas = _CADENAS.sub(" '' ", sql).lower()
    for columna in _COLUMNAS_PROHIBIDAS:
        if re.search(r"\b" + columna + r"\b", minusculas):
            return columna
    return None
