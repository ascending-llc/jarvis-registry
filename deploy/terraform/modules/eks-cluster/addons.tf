resource "helm_release" "cluster_defaults" {
  name      = "cluster-defaults"
  chart     = "${path.module}/charts/cluster-defaults"
  namespace = "kube-system"
  wait      = true

  values = [
    yamlencode({
      tags = var.tags
    }),
  ]

  depends_on = [module.eks]
}

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = var.external_secrets_chart_version
  namespace        = "external-secrets"
  create_namespace = true
  wait             = true

  set = [
    {
      name  = "installCRDs"
      value = "true"
    },
  ]

  depends_on = [module.eks]
}
