FROM node:18-alpine

WORKDIR /app

# Copiar los archivos de configuración de dependencias
COPY package*.json ./

# `npm install` sin flags: no hay package-lock.json todavia, asi que `npm ci`
# fallaria. Cuando exista, cambiar a `npm ci` — falla si el lock no cuadra con
# package.json, en vez de resolver versiones distintas en cada build.
RUN npm install

# Copiar el resto del código del frontend
COPY . .

# 5173 y no 3000: `vite --host` publica en 5173 por defecto, y EXPOSE es lo
# unico que hace el puerto visible en el `docker compose ps`. Poner 3000 aqui
# mientras el compose publica 5173 es una inconsistencia que no rompe nada y
# por eso confunde a todo el mundo.
EXPOSE 5173

CMD ["npm", "run", "dev"]