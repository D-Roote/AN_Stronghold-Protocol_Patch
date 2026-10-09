#!/bin/sh
# acme.sh calls this only after --install-cert has copied both pending files.
set -eu
umask 077
pending=/certs/pending
openssl x509 -in "$pending/fullchain.pem" -checkend 300 -noout
openssl x509 -in "$pending/fullchain.pem" -checkhost "$TLS_DOMAIN" -noout
certificate_key=$(openssl x509 -in "$pending/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
private_key=$(openssl pkey -in "$pending/privkey.pem" -pubout -outform DER | sha256sum)
if [ "$certificate_key" != "$private_key" ]; then
    echo 'Certificate and private key do not match; active certificate preserved' >&2
    exit 1
fi
cp "$pending/fullchain.pem" /certs/fullchain.pem.next
cp "$pending/privkey.pem" /certs/privkey.pem.next
chmod 644 /certs/fullchain.pem.next
chmod 600 /certs/privkey.pem.next
mv /certs/fullchain.pem.next /certs/fullchain.pem
mv /certs/privkey.pem.next /certs/privkey.pem
sha256sum /certs/fullchain.pem /certs/privkey.pem > /certs/.reload.next
mv /certs/.reload.next /certs/.reload
