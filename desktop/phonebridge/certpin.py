"""
PhoneBridge — Certificate Pinning (TOFU)

Trust-On-First-Use certificate verification. On the first connection to
a phone, the server's TLS certificate fingerprint is saved. On subsequent
connections, the fingerprint is checked — if it changes, the user is warned.

This prevents MITM attacks without requiring a full CA chain, which is
ideal for self-signed certificate setups like PhoneBridge.
"""

import hashlib
import logging
import ssl
import socket
from typing import Optional

logger = logging.getLogger("phonebridge.certpin")


def get_server_fingerprint(host: str, port: int, timeout: float = 10.0) -> Optional[str]:
    """
    Connect to a TLS server and return the SHA-256 fingerprint of its certificate.
    
    Returns the fingerprint as a colon-separated hex string (e.g., "AB:CD:EF:...")
    or None if the connection fails.
    
    Args:
        host: Server hostname or IP address
        port: Server port
        timeout: Connection timeout in seconds
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)
                if not der_cert:
                    logger.warning(f"No certificate received from {host}:{port}")
                    return None

                digest = hashlib.sha256(der_cert).hexdigest().upper()
                # Format as colon-separated pairs: AB:CD:EF:...
                fingerprint = ":".join(
                    digest[i:i+2] for i in range(0, len(digest), 2)
                )
                logger.debug(f"Server fingerprint for {host}:{port}: {fingerprint}")
                return fingerprint

    except (socket.timeout, socket.error) as e:
        logger.debug(f"Could not connect to {host}:{port} for fingerprint: {e}")
        return None
    except ssl.SSLError as e:
        logger.debug(f"SSL error getting fingerprint from {host}:{port}: {e}")
        return None
    except Exception as e:
        logger.debug(f"Unexpected error getting fingerprint from {host}:{port}: {e}")
        return None


def verify_fingerprint(
    host: str,
    port: int,
    expected_fingerprint: str,
    timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """
    Verify a server's certificate fingerprint against an expected value.
    
    Returns a tuple of (is_valid, actual_fingerprint).
    - (True, fingerprint) if fingerprints match
    - (False, fingerprint) if fingerprints don't match (possible MITM)
    - (True, None)  if can't connect (skip verification, don't block)
    
    Args:
        host: Server hostname or IP
        port: Server port
        expected_fingerprint: The previously saved fingerprint to check against
        timeout: Connection timeout in seconds
    """
    actual = get_server_fingerprint(host, port, timeout)
    
    if actual is None:
        # Can't connect — don't block the flow, just log
        logger.debug(f"Could not verify fingerprint for {host}:{port} — server unreachable")
        return True, None
    
    # Normalize for comparison (case-insensitive, strip whitespace)
    expected_clean = expected_fingerprint.strip().upper()
    actual_clean = actual.strip().upper()
    
    if expected_clean == actual_clean:
        logger.debug(f"Certificate fingerprint verified for {host}:{port}")
        return True, actual
    else:
        logger.warning(
            f"⚠️ Certificate fingerprint MISMATCH for {host}:{port}!\n"
            f"  Expected: {expected_clean}\n"
            f"  Got:      {actual_clean}\n"
            f"  This may indicate a server change or MITM attack."
        )
        return False, actual


def fingerprint_changed(saved: str, current: str) -> bool:
    """
    Check if a certificate fingerprint has changed.
    
    Args:
        saved: Previously saved fingerprint (from config)
        current: Current fingerprint from the server
        
    Returns True if they differ (fingerprint has changed).
    """
    if not saved or not current:
        return False  # No comparison possible
    return saved.strip().upper() != current.strip().upper()
