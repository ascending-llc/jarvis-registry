output "bucket_name" {
  description = "Name of the created Terraform state S3 bucket."
  value       = aws_s3_bucket.state.bucket
}

output "bucket_arn" {
  description = "ARN of the created Terraform state S3 bucket."
  value       = aws_s3_bucket.state.arn
}

output "region" {
  description = "AWS region of the Terraform state S3 bucket."
  value       = var.region
}
