# MuMu Camera Nginx Configuration

Nginx reverse proxy for the MuMu Camera system.

## Purpose

Nginx serves multiple roles:

1. **Static File Server**: Serves web client files (HTML, JS, CSS)
2. **Reverse Proxy**: Proxies API requests to backend
3. **WebSocket Proxy**: Handles WebSocket connections to backend
4. **Load Balancer**: Can distribute load across multiple backend instances

## Configuration

The main configuration file is `nginx.conf`, which:

- Serves static files from `/usr/share/nginx/html` (mounted from `../web`)
- Proxies `/api/*` requests to backend
- Proxies `/ws/*` WebSocket connections to backend
- Adds security headers
- Enables gzip compression

## Key Features

### WebSocket Support

WebSocket connections are properly upgraded:

```nginx
location /ws/ {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
}
```

### Long-lived Connections

WebSocket timeouts set to 7 days:

```nginx
proxy_connect_timeout 7d;
proxy_send_timeout 7d;
proxy_read_timeout 7d;
```

### Security Headers

Standard security headers included:

- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: no-referrer-when-downgrade`

### Compression

Gzip compression for text-based resources to reduce bandwidth.

## Usage

### Development (Docker Compose)

```bash
docker-compose up nginx
```

Access:
- Web UI: http://localhost:8080
- API: http://localhost:8080/api/...
- WebSocket: ws://localhost:8080/ws/...

### Standalone Docker

```bash
docker build -t mumu-nginx .
docker run -d \
  -p 8080:80 \
  -v $(pwd)/../web:/usr/share/nginx/html \
  --link mumu-backend:backend \
  mumu-nginx
```

### Local nginx

If running nginx locally (not in Docker):

```bash
# Test configuration
nginx -t -c /path/to/nginx.conf

# Reload configuration
nginx -s reload
```

Update upstream backend address in `nginx.conf`:

```nginx
upstream backend {
    server localhost:8000;  # Instead of backend:8000
}
```

## HTTPS Configuration

For production, enable HTTPS. Example with Let's Encrypt:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # ... rest of configuration
}
```

Update web client to use `wss://` for WebSocket:

```javascript
const WS_BASE = 'wss://yourdomain.com';
```

## Load Balancing

To scale backend across multiple instances:

```nginx
upstream backend {
    least_conn;  # Use least connections algorithm
    server backend1:8000;
    server backend2:8000;
    server backend3:8000;
}
```

Note: For WebSocket load balancing, ensure sticky sessions or use a shared state store (like Redis, which this system already uses).

## Rate Limiting

Add rate limiting to protect against abuse:

```nginx
http {
    # Define rate limit zone: 10MB zone, 10 requests per second per IP
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

    server {
        location /api/ {
            limit_req zone=api_limit burst=20 nodelay;
            # ... rest of proxy config
        }
    }
}
```

## Monitoring

### Access Logs

View access logs:

```bash
docker-compose logs nginx
# or
tail -f /var/log/nginx/access.log
```

### Error Logs

View error logs:

```bash
docker-compose logs nginx | grep error
# or
tail -f /var/log/nginx/error.log
```

### Status Endpoint

Add nginx status module (requires recompile):

```nginx
location /nginx_status {
    stub_status on;
    access_log off;
    allow 127.0.0.1;
    deny all;
}
```

## Troubleshooting

### 502 Bad Gateway

Backend is not reachable:

1. Check backend is running: `docker-compose ps backend`
2. Check backend health: `curl http://localhost:8000/health`
3. Verify DNS resolution: `docker-compose exec nginx ping backend`

### WebSocket Connection Fails

1. Check WebSocket headers are properly set
2. Verify timeouts are long enough
3. Check firewall rules
4. Test with `wscat`: `wscat -c ws://localhost:8080/ws/viewer`

### CORS Errors

1. Ensure backend CORS settings are correct
2. Check nginx isn't blocking headers
3. Verify preflight OPTIONS requests work

### Static Files Not Loading

1. Check file permissions
2. Verify volume mount: `docker-compose exec nginx ls /usr/share/nginx/html`
3. Check nginx error logs

## Performance Tuning

### Worker Processes

Adjust based on CPU cores:

```nginx
worker_processes auto;  # Auto-detect CPU cores
```

### Worker Connections

Increase for high traffic:

```nginx
events {
    worker_connections 4096;  # Default is 1024
}
```

### Caching

Add caching for static assets:

```nginx
location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### Buffer Sizes

Tune for your workload:

```nginx
client_body_buffer_size 10K;
client_header_buffer_size 1k;
client_max_body_size 8m;
large_client_header_buffers 2 1k;
```

## License

MIT
