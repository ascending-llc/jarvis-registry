data "aws_region" "current" {}

data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

data "aws_iam_openid_connect_provider" "this" {
  url = data.aws_eks_cluster.this.identity[0].oidc[0].issuer

  lifecycle {
    precondition {
      condition     = local.add_ons_ready_valid
      error_message = "add_ons_ready must be null or a non-empty readiness token."
    }
  }
}

data "aws_vpc" "this" {
  id = data.aws_eks_cluster.this.vpc_config[0].vpc_id
}
