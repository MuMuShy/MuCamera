FROM nginx:alpine

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy web files (done via volume mount in docker-compose)
# The web files are mounted from ../web

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
