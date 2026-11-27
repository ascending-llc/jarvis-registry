output "registry_url" {
  description = "Public Registry URL when domain is configured, else null."
  value       = var.domain != "" ? "https://${var.domain}/gateway" : null
}

output "registry_role_arn" {
  description = "ARN of the Registry IRSA IAM role."
  value       = aws_iam_role.registry.arn
}

output "secret_arn" {
  description = "ARN of the AWS Secrets Manager secret holding registry-env."
  value       = aws_secretsmanager_secret.registry.arn
}

output "secret_name" {
  description = "Name of the AWS Secrets Manager secret holding registry-env."
  value       = aws_secretsmanager_secret.registry.name
}

output "namespace" {
  description = "Kubernetes namespace where Jarvis Registry is installed."
  value       = var.namespace
}

output "cluster_name" {
  description = "EKS cluster name where Jarvis Registry is installed."
  value       = var.cluster_name
}
