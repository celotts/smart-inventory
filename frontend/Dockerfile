FROM node:18-alpine

WORKDIR /app

# Copiar los archivos de configuración de dependencias
COPY package*.json ./

# Instalar todas las dependencias (incluyendo Vite u otras herramientas)
RUN npm install

# Copiar el resto del código del frontend
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]