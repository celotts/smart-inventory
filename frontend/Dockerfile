FROM nginx:alpine
WORKDIR /usr/share/nginx/html
COPY . .
EXPOSE 5173
# nginx default listens on 80; remap to 5173
RUN sed -i 's/listen 80;/listen 5173;/' /etc/nginx/conf.d/default.conf
CMD ["nginx", "-g", "daemon off;"]