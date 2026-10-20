# ===========================================================================
# Smart Inventory B2B — Makefile
# ===========================================================================
#
# Interfaz unica para Docker. Los comandos reales viven en docker-compose; aqui
# solo se encadenan con las banderas que se olvidan una y otra vez.
#
# `docker compose` (v2, con espacio) y no `docker-compose` (v1, con guion): el
# v1 esta deprecado, no viene con Docker Desktop desde hace tiempo y ya no
# soporta `--wait`, que es la mitad del valor de este archivo.
#
# Ejecuta `make` para la lista de targets.

.PHONY: build up up-force down stop start restart logs logs-backend logs-frontend \
        ps estado stats disk shell db-shell clean prune reload \
        migrar migrar-list migrar-estado \
        insomnia insomnia-check \
        ai-health ai-pull ai-list ai-test up-ollama \
        up-dev up-qa up-prod up_dev up_qa

.DEFAULT_GOAL := ayuda

# ---------------------------------------------------------------------------
# Ambiente: prod (default), dev o qa.  `make up ENV=dev`
# ---------------------------------------------------------------------------
ENV ?= prod
COMPOSE := docker compose
COMPOSE_FILES := -f docker-compose.yml

ifeq ($(ENV),dev)
COMPOSE_FILES += -f docker-compose.dev.yml
else ifeq ($(ENV),qa)
COMPOSE_FILES += -f docker-compose.qa.yml
else ifeq ($(ENV),prod)
# prod es el base: no hay overlay.
else
$(error ENV invalido: '$(ENV)'. Usa prod, dev o qa. Ej: make up ENV=dev)
endif

# Puertos publicados en el host. El compose define 5002:5000 y 5175:5173;
# aqui solo se usan para imprimir URLs.
API_PORT ?= 5002
WEB_PORT ?= 5175

# ===========================================================================
# Ciclo de vida
# ===========================================================================

# build reconstruye las imagenes (reutilizando cache de capas).
#
# NO es un alias de up: existe para cuando se quiere compilar sin levantar,
# tipicamente en CI o antes de un `make down`.
build:
	$(COMPOSE) $(COMPOSE_FILES) build

# up reconstruye las imagenes antes de levantar, para no usar imagenes viejas
# cuando cambian requirements.txt, package.json o el codigo del front.
#
# `--wait` Y POR QUE HACE FALTA
# ------------------------------
# Sin `--wait`, `docker compose up -d` devuelve el control en cuanto los
# contenedores EXISTEN, que no es lo mismo que estejam sirviendo. Quien lo corre
# abre localhost:5173 al segundo 2 y encuentra el front sin API detras.
#
# No es una carrera teorica. `backend` espera a que `db` este healthy, y cada
# servicio tiene su healthcheck: `pg_isready` en la base, un SELECT 1 real en
# la API (no el socket), y la raiz del dev server en el front. Sin `--wait` no
# hay forma de saber si ya se puede abrir el navegador.
#
# `--wait-timeout 120` y no el default (infinito): el arranque incluye
# `init.sql` en una base nueva y la subida de uvicorn. Si se pasa, `make up`
# sale con codigo != 0 y dice QUE contenedor no llego — la unica forma de que
# un fallo de arranque sea visible en vez de silencioso.
up: build
	$(COMPOSE) $(COMPOSE_FILES) up -d --remove-orphans --wait --wait-timeout 120
	@$(MAKE) --no-print-directory estado

# up-force ignora la cache de Docker y rehace todas las capas.
#
# El `--wait` es el mismo y por el mismo motivo: `--no-cache` tarda mas, no
# significa que la app quede mas lista.
up-force:
	$(COMPOSE) $(COMPOSE_FILES) build --no-cache
	$(COMPOSE) $(COMPOSE_FILES) up -d --remove-orphans --wait --wait-timeout 120
	@$(MAKE) --no-print-directory estado

up-prod:
	$(MAKE) up ENV=prod

up-dev:
	$(MAKE) up ENV=dev

up-qa:
	$(MAKE) up ENV=qa

# Alias con guion bajo, para quien los tipea por costumbre.
up_dev: up-dev
up_qa: up-qa

# up-ollama levanta el stack CON Ollama dentro de Docker. Para desarrollo se
# usa el Ollama del host (docker-compose.dev.yml), asi que esto es para
# produccion, CI, o una maquina donde no hay Ollama instalado.
up-ollama:
	docker compose -f docker-compose.yml -f docker-compose.ollama.yml \
		up -d --remove-orphans --wait --wait-timeout 180
	@echo ""
	@echo "  Ollamalevantado. Falta descargar el modelo:"
	@echo "    make ai-pull

# --- Estado y logs -------------------------------------------------------

ps:
	$(COMPOSE) $(COMPOSE_FILES) ps

# estado responde a "¿ya puedo abrir el?". Imprime la salud real de cada
# servicio, no solo que el contenedor existe.
#
# Y ADEMAS dice QUE FALTA cuando algo no esta en pie, porque un `ps` con tres
# lineas y una en "unhealthy" no le dice al operador si tiene que levantar la
# base, la API, o todo.
estado:
	@echo ""
	@$(COMPOSE) $(COMPOSE_FILES) ps
	@echo ""
	@$(COMPOSE) $(COMPOSE_FILES) ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | \
		while read servicio estado salud; do \
			case "$$servicio" in \
				db) etiqueta="Base de datos" ;; \
				backend) etiqueta="API" ;; \
				frontend) etiqueta="Front" ;; \
				ollama) etiqueta="IA (Ollama)" ;; \
				*) etiqueta="$$servicio" ;; \
			esac; \
			if [ "$$estado" = "running" ] && { [ "$$salud" = "healthy" ] || [ "$$salud" = "" ]; }; then \
				echo "  OK      $$etiqueta"; \
			elif [ "$$estado" = "running" ]; then \
				echo "  ESPERANDO $$etiqueta ($$salud)"; \
			else \
				echo "  CAIDO   $$etiqueta ($$estado)"; \
			fi; \
		done
	@echo ""
	@echo "  Ambiente .. $(ENV)"
	@echo "  Front .... http://localhost:$(WEB_PORT)"
	@echo "  API ...... http://localhost:$(API_PORT)"
	@echo "  Docs ..... http://localhost:$(API_PORT)/docs"
	@echo ""
	@echo "  Si algo esta CAIDO o ESPERANDO: 'make up' lo levanta."
	@echo "  Si la base no arranca, mirá: make logs-db"
	@echo ""

logs:
	$(COMPOSE) $(COMPOSE_FILES) logs -f

logs-db:
	$(COMPOSE) $(COMPOSE_FILES) logs -f db

logs-backend:
	$(COMPOSE) $(COMPOSE_FILES) logs -f backend

logs-frontend:
	$(COMPOSE) $(COMPOSE_FILES) logs -f frontend

# --- Parada --------------------------------------------------------------

# down para los contenedores y quita la red. CONSERVA el volumen: los datos de
# Postgres siguen ahi y la proxima vez se levanta la misma base.
down:
	$(COMPOSE) $(COMPOSE_FILES) down

stop:
	$(COMPOSE) $(COMPOSE_FILES) stop

start:
	$(COMPOSE) $(COMPOSE_FILES) start

restart:
	$(COMPOSE) $(COMPOSE_FILES) restart

# reload reinicia SOLO backend y frontend, sin rebuild. Es lo que se quiere al
# editar codigo en dev: la capa de dependencias no se toca, asi que tarda
# segundos en vez de minutos.
reload:
	$(COMPOSE) $(COMPOSE_FILES) restart backend frontend

# --- Diagnostico ---------------------------------------------------------

# Consumo de RAM/CPU en vivo contra la cuota de cada contenedor.
stats:
	docker stats --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'

# Espacio que ocupa Docker en disco.
disk:
	docker system df

shell:
	$(COMPOSE) $(COMPOSE_FILES) exec backend bash

db-shell:
	$(COMPOSE) $(COMPOSE_FILES) exec db psql -U postgres -d smart_inventory

# ===========================================================================
# Migraciones — SOLO para una base que YA EXISTE
# ===========================================================================
#
#   Instalacion nueva  ->  `make up`.   No hacen falta: el compose monta
#                                         `database/init.sql` y Postgres lo
#                                         corre en el primer arranque del
#                                         volumen.
#   Base ya existente   ->  `make migrar`.
#
# POR QUE LA LOGICA ESTA EN scripts/migrar.sh Y NO AQUI
# -----------------------------------------------------
# La primera version eran dos bloques de recipe y fallaba de una forma facil de
# no ver: imprimia el estado, no aplicaba nada y salia con codigo 0. Un `make`
# que dice "ok" y no hace nada es peor que uno que falla, porque el operador
# cree que migro. Las dos causas son de Make, no bugs:
#
#   1. Cada linea de un recipe es un shell DISTINTO. Un `if ... fi` y un
#      `for ... done` en lineas separadas son dos procesos que no comparten ni
#      las variables, y el `if` se comia el bucle entero.
#   2. El shell por defecto es `/bin/sh`, no bash: `read -p`, `case` con `;;`
#      y los acentos en los patrones se portan mal.
#
# En un script hay un shell, el quoting es normal y el exit code es el del
# script. Ademas se corre sin `make`, que es lo que necesita alguien que no
# conoce el Makefile.
#
# POR QUE HACE FALTA LA TABLA `schema_migrations`
# ----------------------------------------------
# Sin ella no se puede saber si una base necesita migraciones. Mirar el esquema
# NO sirve: `init.sql` y una base migrada terminan identicas, porque una base
# creada desde cero ya esta al dia. Se intento usar `devices` como
# discriminante (la primera tabla) y dio "esta base es vieja" sobre una recien
# creada, porque `init.sql` tambien la crea.
#
# Un esquema completo no dice de que parte del camino viene la base. El
# registro si: es lo mismo que hace Alembic con su tabla de version.
#
# POR QUE NO HAY ALEMBIC TODAVIA
# ------------------------------
# Alembic tiene sentido cuando las migraciones se GENERAN de los modelos. Aqui
# el DDL esta escrito a mano y `init.sql` es la unica fuente de verdad; lo que
# se busca es que `backend/alembic/` deje de estar vacio, no todo lo demas.
#
# LA RUTA DENTRO DEL CONTENEDOR es `/docker-entrypoint-initdb.d/migrations/`,
# que es donde el compose monta `./database`. NO es `/database/`: el unico
# montaje es el del init, y usar la ruta equivocada da "No such file or
# directory" y parece que las migraciones no existen.

# Que migraciones faltan en esta base. NO aplica nada.
#
# Es el comando de la duda "¿esto lo necesito?": responde mirando el registro,
# no suponiendo, y separa tres casos que antes se confundian — base al dia,
# base con pendientes, y base SIN registro (creada antes de que la tabla
# existiera), donde no se puede saber y hay que decirlo en vez de adivinar.
migrar-estado:
	@bash scripts/migrar.sh --estado

# Aplica las que faltan, en orden, dentro del contenedor.
#
#   make migrar              -> pregunta
#   make migrar MIGRAR_SI=1  -> no pregunta (script y CI)
#
# `IF NOT EXISTS` y `DROP CONSTRAINT IF EXISTS` hacen que reaplicar una
# migracion no rompa nada. No hay transaccion global: cada archivo va entero o
# no va, y por eso el registro se escribe SOLO despues de que `psql` salio con
# 0. Registrar antes seria marcar como al dia una base a la que no le corrio
# el DDL.
#
# La guarda existe porque este repo todavia NO tiene scripts/migrar.sh: sin
# ella, un `make migrar` tecleado por costumbre daria
# "bash: scripts/migrar.sh: No such file or directory", que el operador leeria
# como "las migraciones fallaron" cuando en realidad nunca hubo nada que
# correr. Con la guarda, el error dice QUE falta y POR QUE no hace falta aun.
migrar:
	@if [ -f scripts/migrar.sh ]; then \
		bash scripts/migrar.sh $(if $(MIGRAR_SI),--si,); \
	else \
		echo "migrar: falta scripts/migrar.sh."; \
		echo "         Para una base NUEVA no hace falta: 'make up' corre database/init.sql."; \
		echo "         Para una base existente hay que escribir ese script todavia."; \
		exit 1; \
	fi

# Lista los archivos de migracion presentes en el repo, sin tocar la base.
migrar-list:
	@if [ -d database/migrations ]; then \
		ls -1 database/migrations; \
	else \
		echo "migrar-list: no existe database/migrations/."; \
		echo "              Las migraciones se crean al lado de database/init.sql."; \
		exit 1; \
	fi

# ===========================================================================
# Coleccion de Insomnia
# ===========================================================================
#
# Se regenera desde el OpenAPI de la API viva, no a mano. El archivo se
# versiona, asi que sin esto se queda viejo solo.

insomnia:
	@if [ -f scripts/generar_insomnia.py ]; then \
		python3 scripts/generar_insomnia.py; \
	else \
		echo "insomnia: falta scripts/generar_insomnia.py."; \
		echo "           Se puede exportar a mano desde http://localhost:$(API_PORT)/openapi.json"; \
		exit 1; \
	fi

# Falla si el archivo no corresponde a la API actual. Para CI, o para cuando se
# anade una ruta y hay que acordarse de regenerarlo.
insomnia-check:
	@if [ -f scripts/generar_insomnia.py ]; then \
		python3 scripts/generar_insomnia.py --check; \
	else \
		echo "insomnia-check: falta scripts/generar_insomnia.py."; \
		exit 1; \
	fi

# ===========================================================================
# IA
# ===========================================================================
#
# Los targets apuntan al Ollama del HOST, no a un contenedor: en desarrollo el
# modelo ya esta instalado en la maquina y duplicarlo en Docker seria descargar
# 5 GB otra vez para no usarlos.
#
# Si estas usando el overlay de Ollama (`make up-ollama`), estos targets talken
# con el puerto publicado del host, asi que funcionan igual.

# Estado del proveedor. Es lo primero que hay que mirar cuando "la IA no
# responde": dice si el problema es la config, el modelo que no esta descargado,
# o la RAM.
ai-health:
	@curl -s --max-time 10 http://localhost:$(API_PORT)/api/ai/health | \
		python3 -m json.tool 2>/dev/null || \
		echo "No se pudo consultar /api/ai/health. Esta la API arriba? (make ps)"

# Descarga el modelo configurado. Es un paso de UNA VEZ: la primera vez tarda
# minutos y los siguientes es instantaneo.
#
# `OLLAMA_MODEL` sale del .env; si no esta, usa el default del compose.
ai-pull:
	@echo "Descargando $$(OLLAMA_MODEL:-llama3.1:8b) y el fallback..."
	ollama pull $${OLLAMA_MODEL:-llama3.1:8b}
	@echo "Ahora el fallback:"
	@ollama pull $${OLLAMA_FALLBACK_MODEL:-llama3.2:3b}
	@echo "Listo. Verificá con: make ai-health"

# Que modelos hay. El primero que se use es el cargado en memoria.
ai-list:
	@ollama list

# Prueba de humo de los cuatro endpoints. Pensado para correr a mano
# mientras se developea, no en CI (en CI el modelo no siempre esta).
#
# Sin argumentos: pregunta de conteo, que es la mas barata.
ai-test:
	@echo "1) Estado del proveedor"
	@curl -s --max-time 10 http://localhost:$(API_PORT)/api/ai/health | \
		python3 -c "import json,sys; d=json.load(sys.stdin); print('   respondio:', d['respondio'], '| modelo:', d['modelo_configurado'], '|', d['latencia_ms'], 'ms')" \
		|| echo "   API no responde. make ps"
	@echo ""
	@echo "2) Pregunta en lenguaje natural"
	@curl -s --max-time 180 -X POST http://localhost:$(API_PORT)/api/ai/preguntar \
		-H 'Content-Type: application/json' \
		-d '{"pregunta": "$(or $(PREGUNTA),¿Cuántos equipos hay en cada estado? )"}' | \
		python3 -c "import json,sys; d=json.load(sys.stdin); print('   SQL:', d.get('sql')); print('   ->', d.get('respuesta','')[:200]); print('   filas:', d.get('cantidad_filas'), '| modelo:', d.get('modelo'), '| error:', d.get('error'))"
	@echo ""
	@echo "3) Reporte"
	@curl -s --max-time 180 -X POST http://localhost:$(API_PORT)/api/ai/reporte \
		-H 'Content-Type: application/json' \
		-d '{"periodo": "anio_actual"}' | \
		python3 -c "import json,sys; d=json.load(sys.stdin); print('   ->', d.get('reporte','')[:250]); print('   error:', d.get('error'))"
	@echo ""
	@echo "4) Seguridad: el modelo no debe poder escribir ni leer password"
	@$(MAKE) --no-print-directory ai-test-guard

# La guardia de SQL. Se prueba por separado porque un fallo ahi es lo mas
# grave que puede pasar en este sistema, y merece su propio target visible.
ai-test-guard:
	@docker compose exec -T backend python -m pytest tests/ -q 2>/dev/null || \
		python3 -m pytest backend/tests/ -q


# ===========================================================================
# Limpieza
# ===========================================================================

# Borra contenedores, red y volumenes. INCLUYE la base de datos: la proxima
# vez que se levante se crea desde cero con `init.sql`.
clean:
	$(COMPOSE) $(COMPOSE_FILES) down -v

# Borra solo imagenes huerfanas y cache de build. Conserva volumenes y datos.
# No toca imagenes de infraestructura (kindest/node, envoy): `prune -a` se
# lleva tambien imagenes que no son nuestras y que se descargan solas.
prune:
	docker image prune -f
	docker builder prune -f

# ===========================================================================
# Ayuda
# ===========================================================================

ayuda:
	@echo ""
	@echo "Smart Inventory B2B — make ayuda"
	@echo ""
	@echo "  Ambiente: $(ENV)   (make up ENV=dev | ENV=qa | ENV=prod)"
	@echo ""
	@echo "  Levantar"
	@echo "    up              reconstruye y levanta; espera a que todo este healthy"
	@echo "    up-force        lo mismo, ignorando la cache de Docker"
	@echo "    up-dev / up-qa / up-prod"
	@echo "    reload          reinicia backend y frontend sin rebuild (dev)"
	@echo "    estado          salud y URLs de cada servicio"
	@echo ""
	@echo "  Ver"
	@echo "    ps / logs / logs-backend / logs-frontend / stats / disk"
	@echo ""
	@echo "  Parar"
	@echo "    stop / start / restart / down      down conserva la base"
	@echo "    clean                              down -v: BORRA la base"
	@echo "    prune                              imagenes huerfanas y cache"
	@echo ""
	@echo "  Entrar"
	@echo "    shell        bash en backend"
	@echo "    db-shell     psql contra la base"
	@echo ""
	@echo "  Migraciones (solo base existente)"
	@echo "    migrar-estado / migrar / migrar-list"
	@echo ""
	@echo "  Insomnia"
	@echo "    insomnia / insomnia-check"
	@echo ""
	@echo "  IA"
	@echo "    ai-health        estado del proveedor (lo primero que hay que mirar)"
	@echo "    ai-pull          descarga el modelo: paso de una vez, tarda minutos"
	@echo "    ai-list          modelos disponibles"
	@echo "    ai-test          prueba de humo de los 4 endpoints"
	@echo "    up-ollama        stack con Ollama DENTRO de Docker (prod / CI)"
	@echo ""