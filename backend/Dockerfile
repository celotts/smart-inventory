FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# NO hay capa de apt-get (build-essential, gcc). Se estaba ahi por costumbre,
# de cuando psycopg2 se compilaba desde fuente, y hoy no hace falta para nada:
#
#   - psycopg2-BINARY trae su propio wheel, no compila.
#   - FastAPI, uvicorn y SQLAlchemy son puros o tienen wheels.
#
# Ademas esa capa rompia el build: la imagen arrastra Debian trixie, cuyo apt
# rechaza `--no-recommends` combinado con `-y` ("Command line option
# --no-recommends is not understood in combination with the other options").
# Quitar la capa elimina el problema y deja el build sin compilar nada.
#
# Si alguna vez hace falta compilar de verdad, el camino no es apt: es
# multi-stage con una imagen de build, y se copia el resultado a la final.

# requirements.txt va antes del codigo a proposito: mientras no cambien las
# dependencias, Docker reutiliza esta capa y `make up` no reinstala nada.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Los tests van en la imagen.
#
# POR QUE
# -------
# `requirements.txt` dice, textual: "los tests corren DENTRO del contenedor y
# necesitan las MISMAS versiones de bcrypt, PyJWT y psycopg2 que la app:
# probarlos con otra combinacion de versiones prueba otro sistema". Y pytest
# esta en `requirements.txt` justamente por eso.
#
# Pero la carpeta no estaba en la imagen: `docker compose exec backend pytest
# tests/` fallaba con "file or directory not found" y el Makefile caia al
# Python del host, que tiene OTRAS versiones de las dependencias. O sea: los
# tests nunca se corrieron donde el proyecto dice que se corren.
COPY tests/ ./tests/

EXPOSE 5000

# uvicorn y no `python src/main.py`: el segundo corre el archivo como script
# suelto, sin reload y sin poder reportar cuando el socket ya escucha, que es
# justo lo que necesita el healthcheck para no mentir. Ver src/main.py.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "5000"]