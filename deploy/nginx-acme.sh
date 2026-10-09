#!/bin/sh
# This wrapper renders only our public variables; nginx's $uri/$host remain intact.
set -eu

case "${TLS_DOMAIN:-}" in
    ''|*[!a-z0-9.-]*) echo 'TLS_DOMAIN must be a DNS hostname' >&2; exit 1 ;;
esac
case "${ASSET_PUBLIC_PATH:-}" in
    *[!a-zA-Z0-9/_-]*) echo 'Invalid ASSET_PUBLIC_PATH' >&2; exit 1 ;;
esac

render() {
    template=/opt/tls/http.conf.template
    if [ -s /etc/nginx/tls/.reload ] && [ -s /etc/nginx/tls/fullchain.pem ] &&
       [ -s /etc/nginx/tls/privkey.pem ]; then
        template=/opt/tls/https.conf.template
    fi
    envsubst '${TLS_DOMAIN} ${ASSET_PUBLIC_PATH}' < "$template" > /etc/nginx/conf.d/default.conf.next.$$
    mv /etc/nginx/conf.d/default.conf.next.$$ /etc/nginx/conf.d/default.conf
    if [ -f /etc/nginx/templates/asset-routes.inc.template ]; then
        envsubst '${ASSET_PUBLIC_PATH}' < /etc/nginx/templates/asset-routes.inc.template > /etc/nginx/conf.d/asset-routes.inc
    fi
    nginx -t
}

generation() {
    if [ -s /etc/nginx/tls/.reload ]; then cksum /etc/nginx/tls/.reload; fi
}

if [ "${1:-}" = reload ]; then
    render
    nginx -s reload
    exit 0
fi

# Read generation before rendering, so a deployment during startup isn't missed.
seen=$(generation)
render
(
    while sleep 5; do
        latest=$(generation)
        if [ "$latest" != "$seen" ] && [ -n "$latest" ]; then
            if render && nginx -s reload; then
                seen=$latest
                echo 'TLS certificate loaded by nginx'
            else
                echo 'TLS reload failed; retrying while nginx keeps its existing workers' >&2
            fi
        fi
    done
) &
exec nginx -g 'daemon off;'
