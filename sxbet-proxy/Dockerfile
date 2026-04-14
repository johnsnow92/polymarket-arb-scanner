FROM nginx:alpine

# Reverse proxy configuration for SX Bet API
# Routes all requests through Singapore to bypass Netherlands geo-block
RUN cat > /etc/nginx/conf.d/default.conf << 'EOF'
server {
    listen 8080;

    # Health check
    location /healthz {
        return 200 '{"status":"ok"}';
        add_header Content-Type application/json;
    }

    # Reverse proxy all other requests to SX Bet API
    location / {
        proxy_pass https://api.sx.bet;
        proxy_ssl_server_name on;
        proxy_set_header Host api.sx.bet;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Accept $http_accept;
        proxy_set_header Content-Type $http_content_type;
        proxy_set_header Authorization $http_authorization;
        proxy_connect_timeout 15s;
        proxy_read_timeout 30s;
    }
}
EOF

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
