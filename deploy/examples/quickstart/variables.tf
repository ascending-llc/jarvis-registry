variable "region" {
  description = "AWS region for the EKS cluster and Jarvis Registry."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Short prefix for the VPC, EKS cluster, IAM roles, and secret."
  type        = string
  default     = "jarvis-registry"
}

variable "namespace" {
  description = "Kubernetes namespace for Jarvis Registry."
  type        = string
  default     = "jarvis-registry"
}

variable "kubernetes_version" {
  description = "Amazon EKS Kubernetes version."
  type        = string
  default     = "1.33"
}

variable "single_nat_gateway" {
  description = "Use one NAT Gateway to lower trial cost. Set to false for production HA."
  type        = bool
  default     = true
}

variable "admin_principal_arns" {
  description = "IAM user or role ARNs granted cluster-admin access to the EKS cluster."
  type        = list(string)
  default     = []
}

variable "domain" {
  description = "Public DNS hostname for the Registry Ingress (e.g. 'registry.example.com')."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS on the ALB."
  type        = string
}

variable "chart_version" {
  description = "Published jarvis-registry Helm chart version (ignored when chart_path is set)."
  type        = string
  default     = ""
}

variable "chart_path" {
  description = "Optional local filesystem path to an unreleased jarvis-registry chart (e.g. '../../helm/jarvis-registry')."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Optional image tag override (e.g. 'asc0.5.21'). Empty uses the chart's appVersion."
  type        = string
  default     = ""
}

variable "secret_recovery_window_in_days" {
  description = "Secrets Manager recovery window in days. Default 0 in quickstart so torn-down trials can reuse the secret name immediately."
  type        = number
  default     = 0
}

variable "app_secrets" {
  description = "Ephemeral map of identity-provider settings (AUTH_PROVIDER and IdP credentials) seeded into Secrets Manager on first install."
  type        = map(string)
  sensitive   = true
  ephemeral   = true
}

variable "tags" {
  description = "Tags applied to all AWS resources via provider default_tags and forwarded to EKS Auto Mode resources."
  type        = map(string)
  default     = {}
}
