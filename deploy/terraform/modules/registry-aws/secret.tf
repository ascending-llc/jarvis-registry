resource "aws_secretsmanager_secret" "registry" {
  name                    = local.secret_name
  recovery_window_in_days = var.secret_recovery_window_in_days
  tags                    = var.tags
}

ephemeral "random_bytes" "secret_key" {
  count  = var.seed_secret ? 1 : 0
  length = 64
}

ephemeral "random_bytes" "creds_key" {
  count  = var.seed_secret ? 1 : 0
  length = 32
}

ephemeral "random_bytes" "registry_client_secret" {
  count  = var.seed_secret ? 1 : 0
  length = 32
}

ephemeral "tls_private_key" "jwt" {
  count     = var.seed_secret ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}

ephemeral "random_password" "mongodb_admin_password" {
  count   = var.seed_secret ? 1 : 0
  length  = 32
  special = false
}

# 567 random bytes encode to exactly 756 base64 characters (567 * 4 / 3, no '=' padding),
# matching the MongoDB replica-set keyfile specification.
ephemeral "random_bytes" "mongodb_keyfile" {
  count  = var.seed_secret ? 1 : 0
  length = 567
}

ephemeral "random_password" "weaviate_api_key" {
  count   = var.seed_secret ? 1 : 0
  length  = 32
  special = false
}

locals {
  generated_secrets = var.seed_secret ? {
    SECRET_KEY                              = replace(replace(trimsuffix(ephemeral.random_bytes.secret_key[0].base64, "="), "+", "-"), "/", "_")
    CREDS_KEY                               = ephemeral.random_bytes.creds_key[0].hex
    REGISTRY_CLIENT_SECRET                  = ephemeral.random_bytes.registry_client_secret[0].hex
    JWT_PRIVATE_KEY                         = ephemeral.tls_private_key.jwt[0].private_key_pem
    JWT_PUBLIC_KEY                          = ephemeral.tls_private_key.jwt[0].public_key_pem
    MONGODB_ADMIN_PASSWORD                  = ephemeral.random_password.mongodb_admin_password[0].result
    MONGODB_KEYFILE                         = ephemeral.random_bytes.mongodb_keyfile[0].base64
    WEAVIATE_API_KEY                        = ephemeral.random_password.weaviate_api_key[0].result
    AUTHENTICATION_APIKEY_ENABLED           = "true"
    AUTHENTICATION_APIKEY_ALLOWED_KEYS      = ephemeral.random_password.weaviate_api_key[0].result
    AUTHENTICATION_APIKEY_USERS             = "registry"
    AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED = "false"
    AUTHORIZATION_ADMINLIST_ENABLED         = "true"
    AUTHORIZATION_ADMINLIST_USERS           = "registry"
    PERSISTENCE_DATA_PATH                   = "/var/lib/weaviate"
    CLUSTER_HOSTNAME                        = "node1"
    ENABLE_AUTO_SCHEMA                      = "true"
  } : {}
}

# Seed the secret once via write-only arguments so neither generated keys nor app_secrets
# ever land in Terraform plan or state. Rotate or edit values directly in AWS Secrets Manager
# after install: incrementing secret_string_wo_version would overwrite the entire secret with
# freshly generated values.
resource "aws_secretsmanager_secret_version" "seed" {
  count = var.seed_secret ? 1 : 0

  secret_id                = aws_secretsmanager_secret.registry.id
  secret_string_wo         = jsonencode(merge(local.generated_secrets, var.app_secrets))
  secret_string_wo_version = 1
}
