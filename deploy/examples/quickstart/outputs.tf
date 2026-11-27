output "cluster_name" {
  description = "EKS cluster name (consumed by CI rollout checks via 'terraform output -raw cluster_name')."
  value       = module.registry.cluster_name
}

output "registry_url" {
  description = "Public URL of the Jarvis Registry gateway."
  value       = module.registry.registry_url
}

output "registry_role_arn" {
  description = "ARN of the Registry IRSA IAM role."
  value       = module.registry.registry_role_arn
}

output "secret_arn" {
  description = "ARN of the AWS Secrets Manager secret."
  value       = module.registry.secret_arn
}

output "secret_name" {
  description = "Name of the AWS Secrets Manager secret."
  value       = module.registry.secret_name
}

output "namespace" {
  description = "Kubernetes namespace where Jarvis Registry is installed."
  value       = module.registry.namespace
}
