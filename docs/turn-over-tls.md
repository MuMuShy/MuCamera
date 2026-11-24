# TURN over TLS（443）— 給只開 80/443 的網路

公務網路等環境通常只放行 TCP 80/443，瀏覽器連不到 coturn 的 3478，
網頁可以開但影像出不來。解法是讓瀏覽器多一條 `turns:turn.ihousing.tw:443?transport=tcp`。

```
瀏覽器 ──TLS 443──► 主機 nginx (stream, SNI 分流)
                     ├─ SNI = turn.ihousing.tw ─► 127.0.0.1:5350 ─► coturn TLS :5349
                     └─ 其他網域 ───────────────► 127.0.0.1:8443（原本的 https 網站）
```

## 現行部署（linehero-wiki 35.229.151.7）

1. **DNS**：Cloudflare `turn.ihousing.tw` A → 35.229.151.7，**必須灰雲（DNS only）**，
   Cloudflare 代理只轉 HTTP，不轉 TURN。
2. **憑證**：`certbot certonly --webroot -w /var/www/html -d turn.ihousing.tw`
   - coturn 容器以 uid 65534 執行，讀不到 `/etc/letsencrypt` 的私鑰，
     由 `/etc/letsencrypt/renewal-hooks/deploy/mumu-coturn.sh` 複製到 `coturn/certs/`（chown 65534、chmod 600）並重啟 coturn。
   - `coturn/certs/` 不進 Git。
3. **coturn**（`coturn/turnserver.conf`）：
   ```
   tls-listening-port=5349
   cert=/etc/coturn/certs/fullchain.pem
   pkey=/etc/coturn/certs/privkey.pem
   no-tlsv1
   ```
   `docker-compose.yml` 已掛 `./coturn/certs:/etc/coturn/certs:ro`。
4. **主機 nginx**（需套件 `libnginx-mod-stream`）：
   - `/etc/nginx/stream-sni.conf`（由 `nginx.conf` 最後一行 `include` 載入）：
     ```nginx
     stream {
         map $ssl_preread_server_name $sni_upstream {
             turn.ihousing.tw  127.0.0.1:5350;
             default           127.0.0.1:8443;
         }
         server {
             listen 443;
             ssl_preread on;
             proxy_protocol on;          # 讓網站保留真實來源 IP
             proxy_connect_timeout 10s;
             proxy_timeout 1h;
             proxy_pass $sni_upstream;
         }
         server {                        # coturn 不支援 PROXY protocol，先拆掉
             listen 127.0.0.1:5350 proxy_protocol;
             proxy_timeout 1h;
             proxy_pass 127.0.0.1:5349;
         }
     }
     ```
   - 原本各 https 網站（camera / wordpress / crypto）的 `listen 443 ssl` 改為：
     ```nginx
     listen 127.0.0.1:8443 ssl proxy_protocol;   # crypto 另有 http2
     set_real_ip_from 127.0.0.1;
     real_ip_header proxy_protocol;
     ```
   - **之後新增 https 網站也要用這個 listen 寫法**，寫 `listen 443` 會跟 stream 衝突。
     certbot `--nginx` 自動加的 `listen 443 ssl` 也要手動改掉。
5. **後端** `.env`：`TURN_TLS_HOST=turn.ihousing.tw`，重啟 backend。

## 還原

改動前的 nginx 設定備份在 `~/backups/nginx-<時間>.tgz`：

```bash
sudo rm -rf /etc/nginx && sudo tar xzf ~/backups/nginx-<時間>.tgz -C /etc
sudo nginx -t && sudo systemctl restart nginx
```

## 驗證

```bash
echo | openssl s_client -connect turn.ihousing.tw:443 -servername turn.ihousing.tw | grep 'Verify return'
```

瀏覽器端：`chrome://webrtc-internals` 中選到的 candidate pair，
本地端為 `relay`、protocol `tcp`、url 為 `turns:turn.ihousing.tw:443`。
