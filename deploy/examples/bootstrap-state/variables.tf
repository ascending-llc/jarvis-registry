variable "region" {
  description = "AWS region where the Terraform state S3 bucket is created."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for Terraform state (e.g. 'my-org-jarvis-registry-tfstate')."
  type        = string
}

variable "tags" {
  description = "Tags applied to all resources via the AWS provider default_tags."
  type        = map(string)
  default     = {}
}
