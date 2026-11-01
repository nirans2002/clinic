proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Forwarded-For $remote_addr;   # overwrite, never append: clients cannot spoof their IP
proxy_set_header X-Real-IP $remote_addr;
proxy_read_timeout 60s;
