#!/bin/sh
set -eu
: "${TLS_DOMAIN:?Configure TLS_DOMAIN first}"
: "${TLS_EMAIL:?Configure TLS_EMAIL first}"
: "${TLS_CA:?Configure TLS_CA first}"
mkdir -p /certs/pending
chmod 700 /certs/pending
acme.sh --register-account --server "$TLS_CA" --accountemail "$TLS_EMAIL" --log /acme.sh/acme.log
if acme.sh --issue --server "$TLS_CA" --webroot /var/www/acme \
    --domain "$TLS_DOMAIN" --keylength ec-256 --log /acme.sh/acme.log; then
    :
else
    code=$?
    # acme.sh returns 2 when an existing certificate isn't due for renewal.
    [ "$code" -eq 2 ] || exit "$code"
fi
acme.sh --install-cert --domain "$TLS_DOMAIN" --ecc \
    --key-file /certs/pending/privkey.pem --fullchain-file /certs/pending/fullchain.pem \
    --reloadcmd '/bin/sh /opt/tls/acme-deploy.sh'
