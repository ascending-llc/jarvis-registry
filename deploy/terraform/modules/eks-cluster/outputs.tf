output "cluster_name" {
  description = "Name of the created Amazon EKS cluster."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API server endpoint URL."
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded certificate authority data for the EKS cluster."
  value       = module.eks.cluster_certificate_authority_data
}

output "vpc_id" {
  description = "ID of the VPC created for the EKS cluster."
  value       = module.vpc.vpc_id
}

output "ingress_class_name" {
  description = "IngressClass name provisioned by the cluster-defaults chart."
  value       = "alb"
}

output "add_ons_ready" {
  description = "Readiness token resolved only after OIDC provider, cluster-defaults, and external-secrets CRDs are installed. Pass to registry-aws."
  value = join(",", [
    helm_release.cluster_defaults.id,
    helm_release.external_secrets.id,
    module.eks.oidc_provider_arn,
  ])
}
