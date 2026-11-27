# Existing-cluster root: installs Jarvis Registry onto an EKS cluster you already manage.
#
# Cluster prerequisites (must already exist in the target EKS cluster):
#   1. An IAM OIDC provider registered for the cluster (IRSA).
#   2. An ALB Ingress controller (EKS Auto Mode or self-managed AWS Load Balancer Controller)
#      and an IngressClass matching var.ingress_class_name.
#   3. A default StorageClass (only required when create_databases = true).
#   4. The External Secrets Operator (CRDs + controller).

module "registry" {
  source = "../../terraform/modules/registry-aws"

  name                           = var.name
  cluster_name                   = var.cluster_name
  namespace                      = var.namespace
  create_namespace               = var.create_namespace
  create_databases               = var.create_databases
  seed_secret                    = var.seed_secret
  secret_name                    = var.secret_name
  secret_recovery_window_in_days = var.secret_recovery_window_in_days
  app_secrets                    = var.app_secrets
  domain                         = var.domain
  ingress_class_name             = var.ingress_class_name
  certificate_arn                = var.certificate_arn
  alb_group_name                 = var.alb_group_name
  alb_group_order                = var.alb_group_order
  alb_join_existing              = var.alb_join_existing
  monitoring_enabled             = var.monitoring_enabled
  chart_version                  = var.chart_version
  chart_path                     = var.chart_path
  image_tag                      = var.image_tag
  values                         = var.values
  tags                           = var.tags
}
