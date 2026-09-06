#!/usr/bin/env bash
# Generate a self-signed certificate for local HTTPS (localhost only).
# Both servers look for certs/localhost.crt and certs/localhost.key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT/certs}"
mkdir -p "$CERT_DIR"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/localhost.key" \
  -out "$CERT_DIR/localhost.crt" \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "Wrote $CERT_DIR/localhost.crt and $CERT_DIR/localhost.key"
echo "Your browser will warn about the self-signed cert the first time - accept it."
