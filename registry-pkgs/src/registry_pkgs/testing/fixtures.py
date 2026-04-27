import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def setup_test_rsa_keys() -> rsa.RSAPrivateKey:
    """Generate a test RSA key pair and set ``JWT_PRIVATE_KEY`` / ``JWT_PUBLIC_KEY``.

    Call this at the top of ``conftest.py`` (before any app import) so that
    ``Settings()`` instantiation does not fail on missing JWT secrets.

    Returns the private key so tests can reuse it for fixtures (e.g. signing
    custom tokens).
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    os.environ["JWT_PRIVATE_KEY"] = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    os.environ["JWT_PUBLIC_KEY"] = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return key


def setup_registry_test_env() -> rsa.RSAPrivateKey:
    """Bootstrap all environment variables required by ``registry.Settings()``.

    Covers:
    - ``JWT_PRIVATE_KEY`` / ``JWT_PUBLIC_KEY`` (RSA key pair)
    - ``CREDS_KEY`` (hex-encoded encryption key)
    - ``TOOL_DISCOVERY_MODE`` (required validator value)

    Returns the RSA private key for reuse in test fixtures.
    """
    os.environ["TOOL_DISCOVERY_MODE"] = "external"
    os.environ["CREDS_KEY"] = os.urandom(32).hex()
    return setup_test_rsa_keys()
