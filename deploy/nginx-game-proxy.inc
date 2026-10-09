location / {
    proxy_pass http://stronghold:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header CF-Connecting-IP "";
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
    proxy_buffering off;
}
