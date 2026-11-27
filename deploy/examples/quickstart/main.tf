# Quickstart root: provisions an EKS Auto Mode cluster (with VPC, default gp3 StorageClass,
# alb IngressClass, and External Secrets Operator) and installs Jarvis Registry on top.

module "eks_cluster" {
  source = "../../terraform/modules/eks-cluster"

  name                 = var.name
  kubernetes_version   = var.kubernetes_version
  single_nat_gateway   = var.single_nat_gateway
  admin_principal_arns = var.admin_principal_arns
  tags                 = var.tags
}

module "registry" {
  source = "../../terraform/modules/registry-aws"

  name                           = var.name
  cluster_name                   = module.eks_cluster.cluster_name
  namespace                      = var.namespace
  domain                         = var.domain
  certificate_arn                = var.certificate_arn
  ingress_class_name             = module.eks_cluster.ingress_class_name
  chart_version                  = var.chart_version
  chart_path                     = var.chart_path
  image_tag                      = var.image_tag
  secret_recovery_window_in_days = var.secret_recovery_window_in_days
  app_secrets                    = var.app_secrets
  add_ons_ready                  = module.eks_cluster.add_ons_ready
  tags                           = var.tags
}
