module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnets
  control_plane_subnet_ids = module.vpc.private_subnets

  endpoint_public_access                   = true
  enable_irsa                              = true
  enable_cluster_creator_admin_permissions = true

  # EKS Auto Mode supplies managed compute autoscaling, EBS CSI (ebs.csi.eks.amazonaws.com),
  # and native ALB load balancing (eks.amazonaws.com/alb) without custom driver IAM roles.
  compute_config = {
    enabled    = true
    node_pools = ["general-purpose", "system"]
  }

  # Grants the cluster IAM role permission to propagate custom tags onto EKS Auto Mode
  # AWS resources (ALBs, EBS volumes, EC2 instances).
  enable_auto_mode_custom_tags = true

  access_entries = local.access_entries

  tags = var.tags
}
