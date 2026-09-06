#!/usr/bin/env bash
# Generate a self-signed certificate for local HTTPS (localhost only).
# Both servers in this repo look for localhost.crt / localhost.key next to them.
set -euo pipefail

cd "$(dirname "$0")"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout localhost.key \
  -out localhost.crt \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "Wrote localhost.crt and localhost.key"
echo "Your browser will warn about the self-signed cert the first time - accept it."
