from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    priv = private_pem.replace("\\n", "\\\\n")

    pub = public_pem.replace("\\n", "\\\\n")

    print("# Paste into .env (local dev) or store as secrets in AWS Secrets Manager/Azure Key Vault (prod).")

    print(f'JWT_PRIVATE_KEY="{priv}"')

    print(f'JWT_PUBLIC_KEY="{pub}"')


if __name__ == "__main__":
    main()
