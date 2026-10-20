# Frontend: nginx sirviendo archivos estaticos.
#
# POR QUE NO HAY `npm install` NI BUNDLE
# ---------------------------------------
# La app es JavaScript con modulos ES nativos y CSS propio. El navegador los
# carga directamente, sin paso de build.
#
# Eso no es una decision estetica, es de seguridad: sin un `package.json` con
# dependencias, no hay cadena de suministro que auditar. La version anterior
# usaba dos CDN (Tailwind y html5-qrcode) desde la pagina; ahora hay cero
# dependencias externas.
#
# QUE HACE ESTE DOCKERFILE Y POR QUE NO USA EL DE POR DEFECTO
# -----------------------------------------------------------
# `nginx:alpine` sirve archivos y no trae CSP, ni cacheo, ni limites de cuerpo.
# Con la app anterior eso no importaba, porque hacia falta `unsafe-inline` para
# que Tailwind generara CSS con JavaScript. Ahora que la app es CSS propio y
# modulos ES, la CSP puede ser estricta sin ceder nada.
#
# Ademas `cdn.tailwindcss.com` es un motor que lee el DOM y genera CSS con
# JavaScript, lo que obliga a `unsafe-inline` y `unsafe-eval`: la unica
# proteccion contra XSS queda desactivada para poder usar un estilo.

FROM nginx:alpine

# Nginx corre como root en el master y como `nginx` en los workers. Quitar los
# permisos de escritura de los archivos que se sirven evita que un proceso
# comprometido pueda modificar su propio codigo y persistir.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=root:root index.html /usr/share/nginx/html/index.html
COPY --chown=root:root src /usr/share/nginx/html/src
COPY --chown=root:root public /usr/share/nginx/html/

# ============================================================================
# VERSIONADO DE LOS ASSETS
# ============================================================================
#
# EL PROBLEMA QUE RESUELVE
# ------------------------
# Los modulos ES se sirven con nombres FIJOS: `/src/main.js` siempre se llama
# asi, aunque cambie su contenido. El navegador guarda ese archivo en su
# cache y —por su cuenta, sin revalidar, si la respuesta no lo obliga— puede
# seguir sirviendo el archivo viejo indefinidamente. El service worker hace
# lo mismo por su lado.
#
# El sintoma es el peor posible: el servidor ya tiene el arreglo, y el
# operador sigue viendo el bug. "Arregle el bug y el navegador sigue viendo
# el bug". La unica salida era una recarga forzada a mano, y si el service
# worker estaba instalado, ni eso alcanzaba.
#
# POR QUE UN DIRECTORIO Y NO UN QUERY STRING
# -----------------------------------------
# Porque los modulos se importan UNOS A OTROS con rutas RELATIVAS:
# `main.js` importa `./api.js`, y `vistas/login.js` importa `../dom.js`. Un
# `?v=hash` en el HTML no se propaga a esos imports: el grafo entero seguiria
# apuntando a URLs fijas. Moviendo la app a un directorio con el hash, TODAS
# las URLs quedan versionadas de una: la del entry point, la de cada modulo
# que el importa y la de cada CSS.
#
# QUE GANA
# --------
#  1. La URL de un archivo cambia con cada build, asi que ninguna entrada de
#     cache vieja puede coincidir. El problema deja de existir, en vez de
#     depender de que el navegador se porte bien.
#  2. Se puede usar `immutable` con un ano: como la URL es unica, cachearla
#     para siempre es seguro. La app carga mas rapido y hace menos peticiones.
#  3. `build.txt` queda con el hash, y la app lo compara con el suyo para
#     recargarse sola si aun le quedó una version vieja en el cache.
RUN set -eux; \
    cd /usr/share/nginx/html; \
    HASH="$( { find src -type f -exec cat {} \; ; cat index.html ; } \
             | sha256sum | cut -c1-16 )"; \
    mkdir -p "v/$HASH"; \
    cp -r src "v/$HASH/src"; \
    sed "s|href=\"/src/|href=\"/v/$HASH/src/|g; s|src=\"/src/|src=\"/v/$HASH/src/|g" \
        index.html > index.html.tmp; \
    mv index.html.tmp index.html; \
    printf '%s' "$HASH" > build.txt; \
    # El service worker precachea el esqueleto: sus rutas tienen que llevar la
    # misma version, o precacharia archivos que el servidor ya no tiene.
    sed -i "s|'/src/|'/v/$HASH/src/|g" sw.js; \
    # `src/` NO se borra: queda como red de seguridad para un service worker
    # viejo que todavia sirva un `index.html` anterior, con las rutas sin
    # version. Si se borrara, ese HTML apuntaria a un 404 y la app no
    # arrancaria. No lo referencia nadie: el `index.html` actual usa `/v/`.
    true

# `EXPOSE` documenta el puerto; no lo publica. Quien publica es el compose.
EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:5173/ >/dev/null 2>&1 || exit 1

# `worker_processes auto` y `worker_connections` suben en el default de nginx.
# Un unico worker con el default bloquea: cada peticionSlow (un reporte de IA)
# ocupa el unico worker y las demas paginas esperan.
