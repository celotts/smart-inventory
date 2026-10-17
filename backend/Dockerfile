FROM python:3.9-slim

WORKDIR /app

# Instalar dependencias del sistema esenciales para compilar paquetes
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    python3-protobuf \
    python3-setuptools \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente
COPY src/ ./src/

EXPOSE 5000

CMD ["python", "src/main.py"]