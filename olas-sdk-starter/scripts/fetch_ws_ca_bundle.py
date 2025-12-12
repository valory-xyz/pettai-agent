#!/usr/bin/env python3
"""
Script to fetch the CA certificate chain from ws.pett.ai and prepare it for WS_PETT_AI_CA_BUNDLE_B64.

Usage:
    python scripts/fetch_ws_ca_bundle.py [output_file]

If output_file is provided, saves the PEM to that file.
Otherwise, outputs the base64-encoded bundle to stdout (ready for GitHub secret).
"""

import ssl
import socket
import sys
import base64
import subprocess
from pathlib import Path


def fetch_cert_chain_openssl(hostname: str, port: int = 443) -> str:
    """
    Fetch the certificate chain using OpenSSL command-line tool.
    This is the most reliable method across Python versions.

    Returns the PEM-encoded certificate chain (intermediate CAs, excluding server cert).
    """
    try:
        # Use openssl to get the full certificate chain
        cmd = [
            "openssl",
            "s_client",
            "-showcerts",
            "-connect",
            f"{hostname}:{port}",
            "-servername",
            hostname,
        ]

        result = subprocess.run(
            cmd,
            input=b"",  # Send empty input to close connection
            capture_output=True,
            timeout=10,
            check=False,
        )

        if result.returncode != 0:
            raise Exception(f"OpenSSL command failed: {result.stderr.decode()}")

        output = result.stdout.decode()

        # Extract certificates from the output
        certs = []
        in_cert = False
        current_cert = []

        for line in output.splitlines():
            if "-----BEGIN CERTIFICATE-----" in line:
                in_cert = True
                current_cert = [line]
            elif "-----END CERTIFICATE-----" in line:
                current_cert.append(line)
                certs.append("\n".join(current_cert))
                in_cert = False
                current_cert = []
            elif in_cert:
                current_cert.append(line)

        if not certs:
            raise Exception("No certificates found in OpenSSL output")

        # Skip the first certificate (server cert), keep only intermediate/root CAs
        # If there's only one cert, we'll use it (might be self-signed or root)
        if len(certs) > 1:
            return "\n".join(certs[1:])
        else:
            # Only one cert - might be the full chain or we need it anyway
            return "\n".join(certs)

    except FileNotFoundError:
        raise Exception(
            "OpenSSL not found. Please install OpenSSL or use the bash script instead."
        )
    except Exception as e:
        raise Exception(f"Failed to fetch certificate via OpenSSL: {e}")


def fetch_cert_chain_python(hostname: str, port: int = 443) -> str:
    """
    Fetch certificate using Python's ssl module (fallback method).
    This only gets the peer certificate, not the full chain.
    """
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                # Get the peer certificate in binary form
                der_cert = ssock.getpeercert(binary_form=True)
                if not der_cert:
                    raise Exception("No certificate received from server")

                # Convert DER to PEM
                pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
                return pem_cert
    except Exception as e:
        raise Exception(f"Failed to fetch certificate via Python SSL: {e}")


def main():
    hostname = "ws.pett.ai"
    port = 443
    output_file = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"🔍 Fetching certificate chain from {hostname}:{port}...", file=sys.stderr)

    # Try OpenSSL first (most reliable, gets full chain)
    cert_chain = None
    try:
        cert_chain = fetch_cert_chain_openssl(hostname, port)
        print("✅ Fetched certificate chain using OpenSSL", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  OpenSSL method failed: {e}", file=sys.stderr)
        print(
            "⚠️  Trying Python SSL method (may only get server cert)...", file=sys.stderr
        )
        try:
            cert_chain = fetch_cert_chain_python(hostname, port)
            print(
                "⚠️  Warning: Only got server certificate, not full CA chain",
                file=sys.stderr,
            )
            print(
                "⚠️  Consider using OpenSSL for full chain: openssl s_client -showcerts ...",
                file=sys.stderr,
            )
        except Exception as e2:
            print(f"❌ Python SSL method also failed: {e2}", file=sys.stderr)
            print("❌ Failed to fetch certificate chain", file=sys.stderr)
            sys.exit(1)

    if not cert_chain or not cert_chain.strip():
        print("❌ Certificate chain is empty", file=sys.stderr)
        sys.exit(1)

    # Count certificates
    cert_count = cert_chain.count("-----BEGIN CERTIFICATE-----")
    print(
        f"✅ Fetched certificate chain with {cert_count} certificate(s)",
        file=sys.stderr,
    )

    if output_file:
        # Save PEM file
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(cert_chain)
        output_path.chmod(0o600)
        print(f"💾 Saved certificate chain to: {output_file}", file=sys.stderr)
        print("", file=sys.stderr)
        print("To generate base64 for GitHub secret, run:", file=sys.stderr)
        print(f"  base64 -i {output_file} | tr -d '\\n'", file=sys.stderr)
        print("", file=sys.stderr)
        print("Or use Python:", file=sys.stderr)
        cmd = (
            f'python -c "import base64; '
            f"print(base64.b64encode(open('{output_file}', 'rb').read()).decode())\""
        )
        print(f"  {cmd}", file=sys.stderr)
    else:
        # Output base64-encoded bundle (ready for GitHub secret)
        b64_encoded = base64.b64encode(cert_chain.encode()).decode()
        print(b64_encoded)
        print("", file=sys.stderr)
        print(
            "✅ Base64-encoded certificate chain (ready for WS_PETT_AI_CA_BUNDLE_B64)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
