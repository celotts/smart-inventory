#!/usr/bin/env python3
"""
La medicion: cuanto tarda cada endpoint, y cuanto pesa lo que devuelve.

QUE PARA
--------
    python3 scripts/medir-api.py                 # todos los endpoints
    python3 scripts/medir-api.py --veces 5       # con cinco repeticiones
    python3 scripts/medir-api.py --solo devices  # los que le interesen a uno

QUE MIDE
--------
1. **La latencia**: el tiempo que tarda el servidor en responder. Se
   reportan la mediana, el percentil 95 y el peor caso, porque la mediana
   sola esconde el problema: una consulta que tarda 4 s una vez de cada
   veinte es la que hace que la app "se cuelgue".

2. **El peso de la respuesta**: cuantos KILOBYTES viaja. Un endpoint que
   devuelve 3 MB en JSON es un problema aunque tarde 80 ms: el operador lo
   siente en la red, y el navegador lo tarda en parsear.

3. **Cuantas filas devuelve**: un endpoint que devuelve 5.000 filas es el
   que se rompe cuando la empresa crece.

QUE ES EL PUNTO VULNERABLE
---------------------------
El punto vulnerable no es el endpoint mas lento: es el que devuelve MAS
DATOS de los que la pantalla necesita. Un endpoint que responde en 40 ms y
manda 4 MB es peor que uno que tarda 300 ms y manda 20 KB, porque el
segundo se puede cachear y el primero no.

Por eso el informe marca los dos: la lentitud y el peso.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

API = os.getenv("API_URL", "http://localhost:5002")
EMAIL = os.getenv("E2E_ADMIN_EMAIL", "admin@smartinventory.local")
PASSWORD = os.getenv("E2E_ADMIN_PASSWORD", "admin123")

# El tiempo a partir del cual un endpoint se considera lento.
UMBRAL_LENTO_MS = 500
# El peso a partir del cual se considera pesado.
UMBRAL_PESADO_KB = 200
# Cuantas filas son demasiadas para una pantalla.
UMBRAL_FILAS = 500


def pedir(metodo: str, ruta: str, token: str | None = None, cuerpo=None):
    """Una peticion. Devuelve (codigo, texto, segundos)."""
    url = f"{API}{ruta}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(url, data=datos, method=metodo)
    peticion.add_header("Accept", "application/json")
    if cuerpo is not None:
        peticion.add_header("Content-Type", "application/json")
    if token:
        peticion.add_header("Authorization", f"Bearer {token}")

    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(peticion, timeout=120) as respuesta:
            texto = respuesta.read().decode("utf-8", "replace")
            codigo = respuesta.status
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode("utf-8", "replace")
        codigo = exc.code
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc), time.perf_counter() - inicio
    return codigo, texto, time.perf_counter() - inicio


def token_de_sesion() -> str:
    codigo, texto, _ = pedir("POST", "/api/auth/login", cuerpo={"email": EMAIL, "password": PASSWORD})
    if codigo != 200:
        print(f"No se pudo iniciar sesión: {codigo} {texto[:200]}", file=sys.stderr)
        raise SystemExit(1)
    return json.loads(texto)["access_token"]


def endpoints(token: str) -> list[tuple[str, str]]:
    """Los endpoints GET del OpenAPI, con sus parametros por defecto."""
    codigo, texto, _ = pedir("GET", "/openapi.json")
    if codigo != 200:
        print("No se pudo leer el OpenAPI.", file=sys.stderr)
        raise SystemExit(1)

    documento = json.loads(texto)
    lista = []
    for ruta, operaciones in documento.get("paths", {}).items():
        for metodo, definicion in operaciones.items():
            if metodo != "get":
                continue
            # Los que necesitan un id se prueban con un id de verdad.
            if "{device_id}" in ruta or "{id}" in ruta or "{categoria_id}" in ruta:
                continue
            if "{ubicacion_id}" in ruta or "{producto_id}" in ruta or "{po_id}" in ruta:
                continue
            if "{incidencia_id}" in ruta:
                continue
            # Los que piden un cuerpo no se pueden con GET.
            if definicion.get("requestBody"):
                continue
            lista.append(("GET", ruta))
    return sorted(lista, key=lambda par: par[1])


def filas_de(texto: str) -> int | None:
    """Cuantas filas trae la respuesta (si se puede saber)."""
    try:
        datos = json.loads(texto)
    except ValueError:
        return None
    if isinstance(datos, list):
        return len(datos)
    if isinstance(datos, dict):
        for clave in ("items", "datos", "filas", "results"):
            if isinstance(datos.get(clave), list):
                return len(datos[clave])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Medidor de la API")
    parser.add_argument("--veces", type=int, default=3, help="Repeticiones por endpoint.")
    parser.add_argument("--solo", default=None, help="Mide solo los endpoints que contengan esto.")
    parser.add_argument("--json", action="store_true", help="Salida en JSON.")
    args = parser.parse_args()

    token = token_de_sesion()
    catalogo = endpoints(token)
    if args.solo:
        catalogo = [e for e in catalogo if args.solo in e[1]]

    resultados = []
    for metodo, ruta in catalogo:
        tiempos = []
        peso = 0
        codigo = 0
        filas = None
        for _ in range(args.veces):
            codigo, texto, segundos = pedir(metodo, ruta, token)
            tiempos.append(segundos * 1000)
            peso = max(peso, len(texto.encode("utf-8")))
            filas = filas_de(texto)
        tiempos.sort()
        resultados.append({
            "ruta": ruta,
            "codigo": codigo,
            "p50": round(tiempos[len(tiempos) // 2], 1),
            "p95": round(tiempos[int(len(tiempos) * 0.95) - 1 if len(tiempos) > 1 else 0], 1),
            "max": round(max(tiempos), 1),
            "kb": round(peso / 1024, 1),
            "filas": filas,
        })

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
        return 0

    # El informe.
    print()
    print("=" * 100)
    print("LA API CON CARGA")
    print("=" * 100)
    print()
    print(f"{'endpoint':<46} {'cod':>4} {'p50':>8} {'p95':>8} {'max':>8} {'KB':>8} {'filas':>7}")
    print("-" * 100)

    lentos = []
    pesados = []
    muchas = []
    for r in sorted(resultados, key=lambda x: -x["p95"]):
        marca = ""
        if r["p95"] > UMBRAL_LENTO_MS:
            lentos.append(r)
            marca += " LENTO"
        if r["kb"] > UMBRAL_PESADO_KB:
            pesados.append(r)
            marca += " PESADO"
        if r["filas"] and r["filas"] > UMBRAL_FILAS:
            muchas.append(r)
            marca += " MUCHAS"
        filas = str(r["filas"]) if r["filas"] is not None else "-"
        print(f"{r['ruta']:<46} {r['codigo']:>4} {r['p50']:>8.0f} {r['p95']:>8.0f} {r['max']:>8.0f} {r['kb']:>8.1f} {filas:>7}{marca}")

    print()
    print("-" * 100)
    print(f"LENTOS   (p95 > {UMBRAL_LENTO_MS} ms): {len(lentos)}")
    for r in lentos:
        print(f"  {r['ruta']:<46} p95={r['p95']:.0f} ms  max={r['max']:.0f} ms  {r['kb']:.1f} KB")
    print()
    print(f"PESADOS  (>{UMBRAL_PESADO_KB} KB): {len(pesados)}")
    for r in pesados:
        print(f"  {r['ruta']:<46} {r['kb']:.1f} KB  {r['filas'] if r['filas'] is not None else '-'} filas")
    print()
    print(f"MUCHAS FILAS (>{UMBRAL_FILAS}): {len(muchas)}")
    for r in muchas:
        print(f"  {r['ruta']:<46} {r['filas']} filas  {r['kb']:.1f} KB")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
