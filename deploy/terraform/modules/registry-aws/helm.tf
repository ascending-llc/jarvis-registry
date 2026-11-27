locals {
  alb_join_mode = var.alb_group_name != "" && var.alb_join_existing

  ingress_annotations = var.domain == "" ? {} : (
    local.alb_join_mode ? {
      "alb.ingress.kubernetes.io/group.name"       = var.alb_group_name
      "alb.ingress.kubernetes.io/group.order"      = tostring(var.alb_group_order)
      "alb.ingress.kubernetes.io/target-type"      = "ip"
      "alb.ingress.kubernetes.io/healthcheck-path" = "/health"
      } : merge(
      {
        "alb.ingress.kubernetes.io/scheme"           = "internet-facing"
        "alb.ingress.kubernetes.io/target-type"      = "ip"
        "alb.ingress.kubernetes.io/listen-ports"     = jsonencode([{ HTTP = 80 }, { HTTPS = 443 }])
        "alb.ingress.kubernetes.io/ssl-redirect"     = "443"
        "alb.ingress.kubernetes.io/healthcheck-path" = "/health"
      },
      var.certificate_arn != "" ? {
        "alb.ingress.kubernetes.io/certificate-arn" = var.certificate_arn
      } : {},
      var.alb_group_name != "" ? {
        "alb.ingress.kubernetes.io/group.name"  = var.alb_group_name
        "alb.ingress.kubernetes.io/group.order" = tostring(var.alb_group_order)
      } : {},
    )
  )

  secret_store_provider = {
    aws = {
      service = "SecretsManager"
      region  = local.region
      auth = {
        jwt = {
          serviceAccountRef = {
            name = var.service_account_name
          }
        }
      }
    }
  }

  generated_values = {
    registry = merge(
      {
        serviceAccount = {
          name = var.service_account_name
          annotations = {
            "eks.amazonaws.com/role-arn" = aws_iam_role.registry.arn
          }
        }
        env = {
          remoteRef = local.secret_name
        }
        secretStore = {
          provider = local.secret_store_provider
        }
        authServer = {
          forwardedAllowIps = local.vpc_cidrs
        }
        mongodb = {
          enabled = var.create_databases
        }
        redis = {
          enabled = var.create_databases
        }
        weaviate = {
          enabled = var.create_databases
        }
      },
      var.image_tag != "" ? {
        imageTag = var.image_tag
      } : {},
      var.domain != "" ? {
        ingress = {
          enabled     = true
          host        = var.domain
          className   = var.ingress_class_name
          annotations = local.ingress_annotations
        }
      } : {},
      var.monitoring_enabled ? {
        monitoring = {
          externalSecrets = {
            serviceAccount = {
              # Same name as the Registry SA so the role's IRSA trust
              # (system:serviceaccount:*:<service_account_name>) covers it.
              name = var.service_account_name
              annotations = {
                "eks.amazonaws.com/role-arn" = aws_iam_role.registry.arn
              }
            }
          }
        }
      } : {},
      var.seed_secret && var.domain != "" ? {
        extraEnv = {
          AUTH_SERVER_URL          = "http://auth-server:8888"
          AUTH_SERVER_EXTERNAL_URL = "https://${var.domain}"
          AUTH_SERVER_API_PREFIX   = "/auth"
          REGISTRY_URL             = "https://${var.domain}/gateway"
          REGISTRY_CLIENT_URL      = "https://${var.domain}/gateway"
          NGINX_BASE_PATH          = "/gateway"
          MCPGW_ALLOWED_HOSTS      = "${var.domain},${var.domain}:*"
          MCPGW_ALLOWED_ORIGINS    = "https://${var.domain},https://${var.domain}:*"
          AWS_REGION               = local.region
        }
      } : {},
    )
  }
}

resource "helm_release" "registry" {
  name             = "jarvis-registry"
  repository       = var.chart_path == "" ? var.chart_repository : null
  chart            = var.chart_path != "" ? var.chart_path : "jarvis-registry"
  version          = var.chart_path == "" && var.chart_version != "" ? var.chart_version : null
  namespace        = var.namespace
  create_namespace = var.create_namespace
  wait             = true
  atomic           = false
  timeout          = var.helm_timeout_seconds

  values = concat(
    var.values,
    [yamlencode(local.generated_values)],
  )

  depends_on = [
    aws_iam_role_policy_attachment.registry,
    aws_secretsmanager_secret.registry,
    aws_secretsmanager_secret_version.seed,
  ]

  lifecycle {
    precondition {
      condition     = local.add_ons_ready_valid
      error_message = "add_ons_ready must be null or a non-empty readiness token."
    }
  }
}
