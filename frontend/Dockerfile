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

# `EXPOSE` documenta el puerto; no lo publica. Quien publica es el compose.
EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:5173/ >/dev/null 2>&1 || exit 1

# `worker_processes auto` y `worker_connections` suben en el default de nginx.
# Un unico worker con el default bloquea: cada peticionSlow (un reporte de IA)
# ocupa el unico worker y las demas paginas esperan.
