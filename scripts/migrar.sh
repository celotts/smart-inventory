#!/usr/bin/env bash
# ===========================================================================
# Aplicador de migraciones.
#
#   ./scripts/migrar.sh --estado    ¿que falta? No cambia nada.
#   ./scripts/migrar.sh             aplica, preguntando antes.
#   ./scripts/migrar.sh --si        aplica sin preguntar (CI).
#
# POR QUE UN SCRIPT Y NO UNAS Líneas EN EL MAKEFILE
# -------------------------------------------------
# La primera versión eran dos bloques de recipe y fallaba de una forma fácil de
# no ver: imprimia el estado, no aplicaba nada y salia con código 0. Un `make`
# que dice "ok" y no hace nada es peor que uno que falla, porque el operador
# cree que migro. Las dos causas son de Make, no bugs:
#
#   1. Cada línea de un recipe es un shell DISTINTO. Un `if ... fi` y un
#      `for ... done` en líneas separadas son dos procesos que no comparten ni
#      las variables, y el `if` se comia el bucle entero.
#   2. El shell por defecto es /bin/sh, no bash: `read -p`, `case` con `;;` y
#      los acentos en los patrones se portan mal.
#
# En un script hay un shell, el quoting es normal y el exit code es el del
# script. Además se corre sin `make`, que es lo que necesita alguien que no
# conoce el Makefile.
#
# POR QUE LA TABLA schema_migrations
# ----------------------------------
# Sin ella no se puede saber si una base necesita migraciones. Mirar el esquema
# NO sirve: `init.sql` replica las migraciones, así que una base creada desde
# cero y una migrada son Idénticas. Se intento usar una tabla del inventario
# como discriminante y dio "está base es vieja" sobre una recien creada,
# porque `init.sql` también la crea.
#
# Un esquema completo no dice de que parte del camino viene la base. El registro
# si. Es lo mismo que hace Alembic con su tabla de versión, que es justo la
# razón por la que este proyecto no necesita Alembic todavía.
#
# POR QUE NO HAY ALEMBIC Todavía
# ------------------------------
# Alembic tiene sentido cuando las migraciones se GENERAN de los modelos. Aquí
# el DDL está escrito a mano y `init.sql` es la única fuente de verdad;
# autogenerarlas seria justo lo que este repo no quiere.
#
# LA RUTA DENTRO DEL CONTENEDOR
# -----------------------------
# `/docker-entrypoint-initdb.d/migrations/` es donde el compose monta
# `./database`. NO es `/database/`: el único montaje es el del init, y usar la
# ruta equivocada da "No such file or directory" y parece que las migraciones no
# existen.
# ===========================================================================

set -uo pipefail

DB_SERVICE="${DB_SERVICE:-db}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-smart_inventory}"

# Ruta dentro del CONTENEDOR. Ver la nota de arriba antes de tocarla.
MIGRACIONES_DENTRO="/docker-entrypoint-initdb.d/migrations"
MIGRACIONES_FUERA="$(dirname "$0")/../database/migrations"

SOLO_ESTADO=0
ASUMIR_SI=0

ROJO=$'\033[31m'; VERDE=$'\033[32m'; AMARILLO=$'\033[33m'; GRIS=$'\033[90m'; FIN=$'\033[0m'

# ---------------------------------------------------------------------------
# Conexión: al contenedor si está arriba, a postgres local si no.
# ---------------------------------------------------------------------------
# La base de desarrollo está en Docker, pero el caso "tengo postgres en la
# maquina y el contenedor caido" pasa, y es justo cuando uno más necesita que
# las migraciones corran.
psql_principal() {
    if docker compose ps --status running --services 2>/dev/null | grep -qx "$DB_SERVICE"; then
        docker compose exec -T "$DB_SERVICE" \
            psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
    elif command -v psql >/dev/null 2>&1; then
        psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
    else
        echo "${ROJO}No hay contenedor '$DB_SERVICE' corriendo ni psql en el PATH.${FIN}" >&2
        echo "Levantalo con 'make up' o revisá las credenciales." >&2
        return 127
    fi
}

consulta() {
    psql_principal -t -A -c "$1" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --estado)  SOLO_ESTADO=1 ;;
        --si|-y)   ASUMIR_SI=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -30
            exit 0 ;;
        *)
            echo "${ROJO}Argumento desconocido: $1${FIN}" >&2
            exit 2 ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# El registro
# ---------------------------------------------------------------------------
crear_registro() {
    psql_principal -q -c "
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     VARCHAR(100) PRIMARY KEY,
            description TEXT,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            checksum    TEXT
        );" >/dev/null
}

aplicadas() {
    consulta "SELECT version FROM schema_migrations ORDER BY version;" || true
}

# nombres de los archivos .sql del repo, en orden
archivos_disponibles() {
    if [ -d "$MIGRACIONES_FUERA" ]; then
        ls -1 "$MIGRACIONES_FUERA"/*.sql 2>/dev/null | sort
    fi
}

nombre_version() {
    basename "$1" .sql
}

descripcion_de() {
    # "0001_add_users.sql" -> "add users"
    nombre_version "$1" | sed -E 's/^[0-9]+_//; s/_/ /g'
}

checksum_de() {
    # shasum en macOS, sha1sum en Linux.
    if command -v sha1sum >/dev/null 2>&1; then
        sha1sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 1 "$1" | cut -d' ' -f1
    else
        echo "sin-checksum"
    fi
}

# ---------------------------------------------------------------------------
# Modo estado
# ---------------------------------------------------------------------------
mostrar_estado() {
    echo ""
    echo "  Migraciones — $DB_NAME"
    echo ""

    # El tercer caso: base SIN registro. No es lo mismo que "al día".
    if ! consulta "SELECT to_regclass('public.schema_migrations') IS NOT NULL;" | grep -qx t; then
        echo "  ${AMARILLO}Esta base NO tiene la tabla schema_migrations.${FIN}"
        echo "  ${GRIS}No hay forma de saber si necesita migraciones: el esquema"
        echo "  se parece al de una base recien creada, pero eso no lo confirma.${FIN}"
        echo ""
        echo "  Si la base se creo con 'make up' recien, no necesita nada."
        echo "  Si es una base vieja, corré 'make migrar' y el primer script"
        echo "  se encargara."
        echo ""
        return 0
    fi

    local ya_aplicadas
    ya_aplicadas="$(aplicadas)"

    local hay_pendientes=0
    local archivo
    for archivo in $(archivos_disponibles); do
        local version
        version="$(nombre_version "$archivo")"
        if echo "$ya_aplicadas" | grep -qx "$version"; then
            echo "  ${VERDE}[aplicada]${FIN} $version — $(descripcion_de "$archivo")"
        else
            echo "  ${AMARILLO}[pendiente]${FIN} $version — $(descripcion_de "$archivo")"
            hay_pendientes=1
        fi
    done

    if [ "$hay_pendientes" -eq 0 ]; then
        echo ""
        echo "  ${VERDE}La base esta al dia.${FIN}"
    else
        echo ""
        echo "  ${AMARILLO}Hay migraciones pendientes: 'make migrar'.${FIN}"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Modo aplicar
# ---------------------------------------------------------------------------
aplicar() {
    crear_registro

    local ya_aplicadas
    ya_aplicadas="$(aplicadas)"

    local pendientes=()
    local archivo
    for archivo in $(archivos_disponibles); do
        local version
        version="$(nombre_version "$archivo")"
        if ! echo "$ya_aplicadas" | grep -qx "$version"; then
            pendientes+=("$archivo")
        fi
    done

    if [ ${#pendientes[@]} -eq 0 ]; then
        echo "${VERDE}No hay migraciones pendientes.${FIN}"
        return 0
    fi

    echo ""
    echo "  Se van a aplicar ${#pendientes[@]} migracion(es):"
    local p
    for p in "${pendientes[@]}"; do
        echo "    ${AMARILLO}$(nombre_version "$p")${FIN} — $(descripcion_de "$p")"
    done
    echo ""

    if [ "$ASUMIR_SI" -eq 0 ]; then
        if [ ! -t 0 ]; then
            # Sin TTY (CI) y sin --si: no se puede preguntar, y contestar "si"
            # por omision seria la peor forma de migrar una base sin que nadie
            # lo haya decidido.
            echo "${ROJO}No hay terminal para preguntar y no se paso --si.${FIN}" >&2
            echo "  make migrar MIGRAR_SI=1   (script y CI)" >&2
            exit 1
        fi
        local respuesta=""
        read -r -p "  Continuar? [s/N] " respuesta
        case "$respuesta" in
            s|S|y|Y|si|SI|yes|YES) ;;
            *) echo "Cancelado."; return 0 ;;
        esac
    fi

    local archivo version
    for archivo in "${pendientes[@]}"; do
        version="$(nombre_version "$archivo")"
        echo "  ${GRIS}->${FIN} $version — $(descripcion_de "$archivo")"

        # POR QUE EL ARCHIVO SE ENVUELVE EN UNA TRANSACCION Acá Y NO DENTRO
        # DEL .sql
        # -------------------------------------------------
        # Sin esto, cada sentencia del archivo hace commit por su cuenta. Un
        # fallo en la línea 40 deja las 39 anteriores aplicadas y las 41 en
        # adelante sin correr: la base queda A MEDIAS, y el `schema_migrations`
        # no la marca, así que el próximo `make migrar` la vuelve a correr
        # encima y falla en el primer `already exists`.
        #
        # Eso se probó y paso de verdad con la 0002. El comentario de más abajo
        # decia "el archivo va entero o no va" y era falso.
        #
        # Envolverlo aquí mantiene esa garantía sin obligar a que cada .sql
        # recuerde abrir y cerrar su transaccion. Y `ON_ERROR_STOP=1` hace que
        # psql ABORTE al primer error: sin eso sigue, termina el archivo y sale
        # con 0, que es el peor de los dos casos.
        #
        # El archivo se pasa por STDIN y no con `-f`: el único montaje dentro
        # del contenedor es /docker-entrypoint-initdb.d/, y leer por stdin
        # evita depender de que la ruta exista ahí.
        #
        # Lo que NO se puede hacer en una transaccion y por eso no se usa
        # acá: `CREATE INDEX CONCURRENTLY` y `VACUUM`. Si alguna migración los
        # necesita, tiene que ir en su propio archivo, fuera de este wrapper.
        if ! {
                echo "BEGIN;"
                cat "$archivo"
                echo "COMMIT;"
            } | docker compose exec -T "$DB_SERVICE" \
                psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1; then

            echo ""
            echo "  ${ROJO}FALLO la migracion $version.${FIN}"
            echo "  ${GRIS}El archivo va entero o no va: no hay transaccion global."
            echo "  Corregi el problema y volve a correr 'make migrar'.${FIN}"
            echo ""
            return 1
        fi

        # El registro se escribe Solo después de que psql salió con 0. Registrar
        # antes seria marcar como al día una base a la que no le corrio el DDL.
        if ! psql_principal -q -c \
            "INSERT INTO schema_migrations (version, description, checksum)
             VALUES ('$version', '$(descripcion_de "$archivo")', '$(checksum_de "$archivo")')
             ON CONFLICT (version) DO NOTHING;"; then
            echo "  ${ROJO}El DDL aplico pero no se pudo registrar $version.${FIN}"
            echo "  ${GRIS}Es el caso peor: el cambio esta pero el registro no.${FIN}"
            return 1
        fi

        echo "     ${VERDE}ok${FIN}"
    done

    echo ""
    echo "${VERDE}Migraciones aplicadas.${FIN}"
    echo ""
    return 0
}

# ---------------------------------------------------------------------------
main() {
    if [ "$SOLO_ESTADO" -eq 1 ]; then
        mostrar_estado
        return $?
    fi
    aplicar
}

main "$@"