variable "name" {
  description = "Cluster and VPC name prefix."
  type        = string
}

variable "kubernetes_version" {
  description = "Amazon EKS Kubernetes version."
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  description = "IPv4 CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to span for public and private subnets."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2
    error_message = "az_count must be at least 2 for Amazon EKS."
  }
}

variable "single_nat_gateway" {
  description = "Use a single shared NAT Gateway to reduce trial cost. Set to false for production high availability."
  type        = bool
  default     = true
}

variable "admin_principal_arns" {
  description = "List of IAM user or role ARNs granted EKS cluster-admin access via EKS Access Entries."
  type        = list(string)
  default     = []
}

variable "external_secrets_chart_version" {
  description = "Pinned Helm chart version for External Secrets Operator."
  type        = string
  default     = "0.17.0"
}

variable "tags" {
  description = "Tags applied to VPC, EKS, and Kubernetes-provisioned AWS resources (ALB via IngressClassParams, EBS volumes via StorageClass)."
  type        = map(string)
  default     = {}
}
