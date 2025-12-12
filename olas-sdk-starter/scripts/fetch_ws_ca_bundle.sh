#!/bin/bash
# Script to fetch the CA certificate chain from ws.pett.ai and prepare it for WS_PETT_AI_CA_BUNDLE_B64
#
# Usage:
#   ./scripts/fetch_ws_ca_bundle.sh [output_file]
#
# If output_file is provided, saves the PEM to that file.
# Otherwise, outputs the base64-encoded bundle to stdout (ready for GitHub secret).

set -euo pipefail

HOST="ws.pett.ai"
PORT="443"
OUTPUT_FILE="${1:-}"

# Function to extract certificate chain
fetch_cert_chain() {
    local host="$1"
    local port="$2"
    
    echo "🔍 Fetching certificate chain from ${host}:${port}..." >&2
    
    # Use openssl to get the full certificate chain
    # This includes the server cert and all intermediate CAs
    openssl s_client -showcerts -connect "${host}:${port}" \
        -servername "${host}" </dev/null 2>/dev/null | \
        sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p'
}

# Function to get only the CA/intermediate certificates (excluding the server cert)
fetch_ca_chain() {
    local host="$1"
    local port="$2"
    
    echo "🔍 Fetching CA certificate chain from ${host}:${port}..." >&2
    
    # Get all certificates and skip the first one (server cert)
    # Keep only intermediate/root CA certificates
    openssl s_client -showcerts -connect "${host}:${port}" \
        -servername "${host}" </dev/null 2>/dev/null | \
        awk '/-----BEGIN CERTIFICATE-----/ { cert=1; count++ } \
             cert { print } \
             /-----END CERTIFICATE-----/ { if (count > 1) print; cert=0 }'
}

# Try to fetch the CA chain (intermediate certificates)
CERT_CHAIN=$(fetch_ca_chain "$HOST" "$PORT" || fetch_cert_chain "$HOST" "$PORT")

if [ -z "$CERT_CHAIN" ]; then
    echo "❌ Failed to fetch certificate chain from ${HOST}:${PORT}" >&2
    exit 1
fi

# Count certificates in the chain
CERT_COUNT=$(echo "$CERT_CHAIN" | grep -c "BEGIN CERTIFICATE" || echo "0")
echo "✅ Fetched certificate chain with ${CERT_COUNT} certificate(s)" >&2

if [ -n "$OUTPUT_FILE" ]; then
    # Save PEM file
    echo "$CERT_CHAIN" > "$OUTPUT_FILE"
    chmod 600 "$OUTPUT_FILE"
    echo "💾 Saved certificate chain to: $OUTPUT_FILE" >&2
    echo "" >&2
    echo "To generate base64 for GitHub secret, run:" >&2
    echo "  base64 -i $OUTPUT_FILE | tr -d '\n'" >&2
else
    # Output base64-encoded bundle (ready for GitHub secret)
    echo "$CERT_CHAIN" | base64 | tr -d '\n'
    echo "" >&2
fi


