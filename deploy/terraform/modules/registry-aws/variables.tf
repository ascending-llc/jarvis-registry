variable "name" {
  description = "Short prefix for AWS resources created by this module (e.g. 'jarvis-registry' or an environment name)."
  type        = string
}

variable "cluster_name" {
  description = "Name of the target Amazon EKS cluster."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace for the Jarvis Registry Helm release."
  type        = string
  default     = "jarvis-registry"
}

variable "create_namespace" {
  description = "Whether Helm should create the Kubernetes namespace if it does not exist."
  type        = bool
  default     = true
}

variable "service_account_name" {
  description = "Kubernetes ServiceAccount name trusted by the Registry IRSA role and passed to the Helm chart."
  type        = string
  default     = "registry-service-account"
}

variable "registry_role_name" {
  description = "Explicit IAM role name for aws_iam_role.registry. When null, uses name_prefix '\"<name>-role-\"'."
  type        = string
  default     = null
}

variable "registry_policy_name" {
  description = "Explicit IAM policy name for aws_iam_policy.registry. When null, uses name_prefix '\"<name>-policy-\"'."
  type        = string
  default     = null
}

variable "agentcore_federation_enabled" {
  description = "Create the AgentCore IAM role, policy, and assume-role grant for Bedrock AgentCore federation."
  type        = bool
  default     = false
}

variable "agentcore_role_name" {
  description = "Explicit IAM role name for aws_iam_role.agentcore[0]. When null, uses name_prefix '\"<name>-agentcore-role-\"'."
  type        = string
  default     = null
}

variable "agentcore_policy_name" {
  description = "Explicit IAM policy name for aws_iam_policy.agentcore[0]. When null, uses name_prefix '\"<name>-agentcore-policy-\"'."
  type        = string
  default     = null
}

variable "registry_assume_agentcore_policy_name" {
  description = "Explicit inline IAM role policy name for aws_iam_role_policy.registry_assume_agentcore[0]. When null, uses name_prefix '\"assume-agentcore-role-\"'."
  type        = string
  default     = null
}

variable "agentcore_assumable_role_arns" {
  description = "Additional AWS IAM principal ARNs allowed to assume the AgentCore role alongside the Registry IRSA role."
  type        = list(string)
  default     = []
}

variable "secret_name" {
  description = "AWS Secrets Manager secret name. Defaults to '<name>/registry-env' when null."
  type        = string
  default     = null
}

variable "secret_recovery_window_in_days" {
  description = "Days AWS Secrets Manager retains a deleted secret before permanent removal. Set 0 for disposable trials."
  type        = number
  default     = 30

  validation {
    condition     = var.secret_recovery_window_in_days == 0 || (var.secret_recovery_window_in_days >= 7 && var.secret_recovery_window_in_days <= 30)
    error_message = "secret_recovery_window_in_days must be 0 or between 7 and 30."
  }
}

variable "seed_secret" {
  description = "Seed the Secrets Manager secret on first install using write-only arguments and ephemeral generators. Set false for adopted environments."
  type        = bool
  default     = true
}

variable "app_secrets" {
  description = <<-EOT
    Ephemeral map of application secrets merged on top of generated keys when seed_secret is true.
    Never written to Terraform plan or state.

    Required when seed_secret = true:
      - AUTH_PROVIDER: "entra" or "google"
      - For AUTH_PROVIDER="entra": ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, and the four distinct UUIDs
        JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID, JARVIS_REGISTRY_POWER_USER_GROUP_OBJECT_ID,
        JARVIS_REGISTRY_USER_GROUP_OBJECT_ID, JARVIS_REGISTRY_READ_ONLY_GROUP_OBJECT_ID.
      - For AUTH_PROVIDER="google": GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_ENABLED="true", ENTRA_ENABLED="false"
        (plus GOOGLE_ALLOWED_HD, GOOGLE_CUSTOMER_ID, GOOGLE_SERVICE_ACCOUNT_KEY_JSON for group mapping).
  EOT
  type        = map(string)
  default     = {}
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = !var.seed_secret || contains(["entra", "google"], lower(trimspace(try(var.app_secrets["AUTH_PROVIDER"], ""))))
    error_message = "When seed_secret is true, app_secrets must set AUTH_PROVIDER to \"entra\" or \"google\"."
  }
}

variable "create_databases" {
  description = "Enable the chart's bundled MongoDB, Redis, and Weaviate instances. Set false when bringing your own or sharing with Jarvis Chat."
  type        = bool
  default     = true
}

variable "chart_repository" {
  description = "Helm chart repository URL (OCI or HTTP) used when chart_path is empty."
  type        = string
  default     = "oci://ghcr.io/ascending-llc/jarvis-chart"
}

variable "chart_version" {
  description = "Helm chart version to install from chart_repository. Ignored when chart_path is set."
  type        = string
  default     = ""
}

variable "chart_path" {
  description = "Optional local filesystem path to an unreleased jarvis-registry chart. Overrides chart_repository and chart_version when non-empty."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Optional image tag override for Registry workloads. Empty uses the chart's appVersion."
  type        = string
  default     = ""
}

variable "helm_timeout_seconds" {
  description = "Timeout in seconds for helm_release.registry."
  type        = number
  default     = 1200
}

variable "values" {
  description = "Optional list of raw YAML strings applied to helm_release.registry before the module-generated values."
  type        = list(string)
  default     = []
}

variable "domain" {
  description = "Public DNS hostname for the Registry Ingress (e.g. 'registry.example.com'). Leave empty to disable Ingress management."
  type        = string
  default     = ""
}

variable "ingress_class_name" {
  description = "Kubernetes IngressClass name used when domain is set."
  type        = string
  default     = "alb"
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS on the ALB Ingress. Required when domain is set unless alb_join_existing is true."
  type        = string
  default     = ""

  validation {
    condition     = var.domain == "" || (var.alb_group_name != "" && var.alb_join_existing) || trimspace(var.certificate_arn) != ""
    error_message = "certificate_arn must be set when domain is configured and alb_join_existing is false."
  }
}

variable "alb_group_name" {
  description = "AWS Load Balancer Controller IngressGroup name (for self-managed AWS Load Balancer Controller only; EKS Auto Mode configures groups via IngressClassParams, not annotations)."
  type        = string
  default     = ""
}

variable "alb_group_order" {
  description = "IngressGroup rule priority order used when alb_group_name is set."
  type        = number
  default     = 0
}

variable "alb_join_existing" {
  description = "When true (with alb_group_name set), emit only per-Ingress ALB annotations (group.name, group.order, target-type, healthcheck-path) so load-balancer and listener settings remain owned by the primary Ingress."
  type        = bool
  default     = false
}

variable "monitoring_enabled" {
  description = "When true, wire the monitoring stack's External Secrets ServiceAccount to reuse the Registry IRSA role."
  type        = bool
  default     = false
}

variable "add_ons_ready" {
  description = "Optional readiness token from the eks-cluster module so Helm release and OIDC lookups wait for cluster add-ons and CRDs."
  type        = any
  default     = null
}

variable "tags" {
  description = "Tags applied to AWS resources created by this module."
  type        = map(string)
  default     = {}
}
