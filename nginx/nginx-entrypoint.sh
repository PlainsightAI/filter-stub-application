#!/bin/sh
set -eu

CERT_DIR="/etc/nginx/certs"
CERT_FILE="$CERT_DIR/fullchain.pem"
KEY_FILE="$CERT_DIR/privkey.pem"

HOSTNAME="${CERT_HOSTNAME:-localhost}"
DAYS="${CERT_DAYS:-365}"

mkdir -p "$CERT_DIR"

if [ ! -s "$CERT_FILE" ] || [ ! -s "$KEY_FILE" ]; then
    echo "[INFO] Generating self-signed SSL certificate for $HOSTNAME"

    openssl req -x509 \
        -nodes \
        -days "$DAYS" \
        -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -subj "/CN=$HOSTNAME" \
        -addext "subjectAltName=DNS:$HOSTNAME"

    chmod 600 "$KEY_FILE"
    chmod 644 "$CERT_FILE"

    echo "[INFO] Certificate generated successfully"
else
    echo "[INFO] Using existing SSL certificate"
fi

echo "[INFO] Starting NGINX"

exec nginx -g "daemon off;"
