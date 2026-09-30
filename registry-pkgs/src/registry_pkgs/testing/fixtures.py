import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from registry_pkgs.core.config import DISABLE_DOTENV_ENV_VAR


def disable_dotenv_loading() -> None:
    """Stop every test from reading a developer's `.env`. Call at the top of ``conftest.py``, before any app import.

    Two loaders are closed:
    - ``JarvisBaseSettings`` has ``env_file=".env"``, resolved against the current directory, so a run from
      the repo root would read the root `.env`. ``JARVIS_DISABLE_DOTENV=1`` drops that source.
    - In its default "DEV" mode, ``import litellm`` calls ``dotenv.load_dotenv()``, which walks up from the
      ``.venv`` to the repo-root `.env` and copies it into ``os.environ``. ``LITELLM_MODE=PRODUCTION``
      skips that; it must be set before anything imports litellm.
    """
    os.environ[DISABLE_DOTENV_ENV_VAR] = "1"
    os.environ["LITELLM_MODE"] = "PRODUCTION"


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
    - ``SECRET_KEY`` (HMAC / signing key)
    - ``TOOL_DISCOVERY_MODE`` (required validator value)

    Also calls ``disable_dotenv_loading`` so no `.env` leaks into the run.

    Returns the RSA private key for reuse in test fixtures.
    """
    disable_dotenv_loading()
    os.environ["TOOL_DISCOVERY_MODE"] = "external"
    os.environ["CREDS_KEY"] = os.urandom(32).hex()
    os.environ["SECRET_KEY"] = os.urandom(32).hex()
    return setup_test_rsa_keys()
