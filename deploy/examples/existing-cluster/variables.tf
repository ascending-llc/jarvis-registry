variable "region" {
  description = "AWS region of the existing EKS cluster."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Short prefix for IAM roles and the Secrets Manager secret."
  type        = string
  default     = "jarvis-registry"
}

variable "cluster_name" {
  description = "Name of the existing Amazon EKS cluster."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace for Jarvis Registry."
  type        = string
  default     = "jarvis-registry"
}

variable "create_namespace" {
  description = "Whether Helm should create the namespace if it does not exist."
  type        = bool
  default     = true
}

variable "create_databases" {
  description = "Set true to deploy bundled MongoDB, Redis, and Weaviate; false to use existing/shared databases."
  type        = bool
  default     = false
}

variable "seed_secret" {
  description = "Set true on fresh installs to seed Secrets Manager; false when adopting an existing populated secret."
  type        = bool
  default     = false
}

variable "secret_name" {
  description = "Optional override for the AWS Secrets Manager secret name. Defaults to '<name>/registry-env'."
  type        = string
  default     = null
}

variable "secret_recovery_window_in_days" {
  description = "Days AWS Secrets Manager retains a deleted secret."
  type        = number
  default     = 30
}

variable "app_secrets" {
  description = "Ephemeral map of IdP settings when seed_secret = true. Unused when seed_secret = false."
  type        = map(string)
  default     = {}
  sensitive   = true
  ephemeral   = true
}

variable "domain" {
  description = "Public DNS hostname for the Registry Ingress."
  type        = string
  default     = ""
}

variable "ingress_class_name" {
  description = "Kubernetes IngressClass name on the existing cluster."
  type        = string
  default     = "alb"
}

variable "certificate_arn" {
  description = "ACM certificate ARN (not required when alb_join_existing = true)."
  type        = string
  default     = ""
}

variable "alb_group_name" {
  description = "Self-managed AWS Load Balancer Controller IngressGroup name to join an existing ALB."
  type        = string
  default     = ""
}

variable "alb_group_order" {
  description = "IngressGroup rule priority order."
  type        = number
  default     = 0
}

variable "alb_join_existing" {
  description = "When true (with alb_group_name set), omit load-balancer/listener annotations so the primary Ingress remains authoritative."
  type        = bool
  default     = false
}

variable "monitoring_enabled" {
  description = "When true, wire monitoring External Secrets SA to reuse the Registry IRSA role."
  type        = bool
  default     = false
}

variable "chart_version" {
  description = "Published jarvis-registry Helm chart version."
  type        = string
  default     = ""
}

variable "chart_path" {
  description = "Optional local filesystem path to an unreleased jarvis-registry chart."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Optional image tag override."
  type        = string
  default     = ""
}

variable "values" {
  description = "Optional list of extra raw YAML values applied before module-generated values."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to all AWS resources."
  type        = map(string)
  default     = {}
}
