# Quick Guide: Getting WS_PETT_AI_CA_BUNDLE_B64 Value

## What to Add to GitHub Secret

The `WS_PETT_AI_CA_BUNDLE_B64` secret should contain the **base64-encoded CA certificate chain** for `ws.pett.ai`. This includes the intermediate CA certificates needed to verify the SSL/TLS connection.

## Quick Method (Recommended)

Run this command to get the base64 value directly:

```bash
cd olas-sdk-starter
python scripts/fetch_ws_ca_bundle.py
```

This will output the base64-encoded certificate chain to stdout (ready to paste into GitHub Secrets).

## Alternative Methods

### Method 1: Using the Script to Save First

```bash
# Save the certificate to a file
python scripts/fetch_ws_ca_bundle.py agent/certs/ws_pett_ai_ca.pem

# Then encode it
base64 -i agent/certs/ws_pett_ai_ca.pem | tr -d '\n'
```

### Method 2: Using OpenSSL Directly

```bash
# Fetch and encode in one command
openssl s_client -showcerts -connect ws.pett.ai:443 -servername ws.pett.ai </dev/null 2>/dev/null | \
  awk '/-----BEGIN CERTIFICATE-----/ { cert=1; count++ } \
       cert { print } \
       /-----END CERTIFICATE-----/ { if (count > 1) print; cert=0 }' | \
  base64 | tr -d '\n'
```

### Method 3: Using Bash Script

```bash
./scripts/fetch_ws_ca_bundle.sh | head -1
```

## What You Should See

- **Certificate Count**: The chain should contain **2 certificates** (intermediate CAs from Google Trust Services)
- **Format**: The base64 string should be a single line with no newlines
- **Length**: Approximately 4,980 characters

## Setting the GitHub Secret

1. Go to your repository → **Settings** → **Secrets and variables** → **Actions**
2. Click **"New repository secret"**
3. **Name**: `WS_PETT_AI_CA_BUNDLE_B64`
4. **Value**: Paste the entire base64 string (no newlines, no spaces)
5. Click **"Add secret"**

## Verification

After setting the secret, the CI/CD pipeline will:

1. Decode the secret during the build
2. Save it to `agent/certs/ws_pett_ai_ca.pem`
3. Include it in the PyInstaller bundle
4. The websocket client will automatically use it for SSL verification

## Certificate Details

The fetched certificate chain includes:

- **Issuer**: Google Trust Services LLC (GTS Root R1)
- **Intermediate CA**: WR3 (Google Trust Services)
- **Valid Until**: February 20, 2029

## Troubleshooting

- **"OpenSSL not found"**: Install OpenSSL or use the Python method
- **"No certificates found"**: Check network connectivity to `ws.pett.ai`
- **SSL errors after setting secret**: Verify the base64 string has no newlines or extra spaces

