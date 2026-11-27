# Mock cloud providers only; random and tls run locally with no AWS credentials,
# which also exercises ephemeral secret generation (Terraform mock_provider does not
# support ephemeral resources).
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }

  mock_data "aws_eks_cluster" {
    defaults = {
      name = "test-cluster"
      identity = [
        {
          oidc = [
            {
              issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE000000000000000000000000"
            },
          ]
        },
      ]
      vpc_config = [
        {
          vpc_id = "vpc-0123456789abcdef0"
        },
      ]
    }
  }

  mock_data "aws_iam_openid_connect_provider" {
    defaults = {
      arn = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE000000000000000000000000"
      url = "https://oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE000000000000000000000000"
    }
  }

  mock_data "aws_vpc" {
    defaults = {
      id         = "vpc-0123456789abcdef0"
      cidr_block = "10.0.0.0/16"
      cidr_block_associations = [
        {
          association_id = "vpc-cidr-assoc-1"
          cidr_block     = "10.0.0.0/16"
          state          = "associated"
        },
        {
          association_id = "vpc-cidr-assoc-2"
          cidr_block     = "100.64.0.0/16"
          state          = "associated"
        },
      ]
    }
  }
}

mock_provider "helm" {}

# Make the computed role ARN known during plan so yamlencode(local.generated_values)
# in helm_release.registry.values[0] can be decoded and asserted under command = plan.
override_resource {
  target          = aws_iam_role.registry
  override_during = plan
  values = {
    arn  = "arn:aws:iam::123456789012:role/jarvis-test-role"
    name = "jarvis-test-role"
  }
}

variables {
  name         = "jarvis-test"
  cluster_name = "test-cluster"
}

run "seed_secret_disabled_creates_no_seed_version" {
  command = plan

  variables {
    seed_secret = false
  }

  assert {
    condition     = length(aws_secretsmanager_secret_version.seed) == 0
    error_message = "Expected zero aws_secretsmanager_secret_version.seed instances when seed_secret is false."
  }

  assert {
    condition     = !contains(keys(yamldecode(helm_release.registry.values[0]).registry), "extraEnv")
    error_message = "Adopted environments (seed_secret = false) must not render registry.extraEnv."
  }
}

run "seed_secret_requires_auth_provider" {
  command = plan

  variables {
    seed_secret = true
    app_secrets = {}
  }

  expect_failures = [
    var.app_secrets,
  ]
}

run "create_databases_false_disables_all_three_databases" {
  command = plan

  variables {
    seed_secret      = false
    create_databases = false
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.mongodb.enabled == false
    error_message = "Expected registry.mongodb.enabled to be false."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.redis.enabled == false
    error_message = "Expected registry.redis.enabled to be false."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.weaviate.enabled == false
    error_message = "Expected registry.weaviate.enabled to be false."
  }
}

run "agentcore_disabled_creates_no_agentcore_resources" {
  command = plan

  variables {
    seed_secret                  = false
    agentcore_federation_enabled = false
  }

  assert {
    condition     = length(aws_iam_role.agentcore) == 0
    error_message = "Expected zero aws_iam_role.agentcore instances."
  }

  assert {
    condition     = length(aws_iam_policy.agentcore) == 0
    error_message = "Expected zero aws_iam_policy.agentcore instances."
  }

  assert {
    condition     = length(aws_iam_role_policy_attachment.agentcore) == 0
    error_message = "Expected zero aws_iam_role_policy_attachment.agentcore instances."
  }

  assert {
    condition     = length(aws_iam_role_policy.registry_assume_agentcore) == 0
    error_message = "Expected zero aws_iam_role_policy.registry_assume_agentcore instances."
  }
}

run "domain_without_certificate_arn_fails_validation" {
  command = plan

  variables {
    seed_secret     = false
    domain          = "registry.example.com"
    certificate_arn = ""
  }

  expect_failures = [
    var.certificate_arn,
  ]
}

run "domain_without_alb_group_sets_certificate_and_no_group_name" {
  command = plan

  variables {
    seed_secret     = true
    domain          = "registry.example.com"
    certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    app_secrets = {
      AUTH_PROVIDER       = "entra"
      ENTRA_CLIENT_SECRET = "SENTINEL_EPHEMERAL_SECRET_CHECK"
    }
  }

  assert {
    condition     = length(aws_secretsmanager_secret_version.seed) == 1 && aws_secretsmanager_secret_version.seed[0].secret_string == null && aws_secretsmanager_secret_version.seed[0].secret_string_wo_version == 1
    error_message = "Expected aws_secretsmanager_secret_version.seed[0] using write-only secret_string_wo (secret_string must remain null)."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/certificate-arn"] == "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    error_message = "Expected certificate-arn annotation on standalone Ingress."
  }

  assert {
    condition     = !contains(keys(yamldecode(helm_release.registry.values[0]).registry.ingress.annotations), "alb.ingress.kubernetes.io/group.name")
    error_message = "Expected no group.name annotation when alb_group_name is empty."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.extraEnv.AUTH_SERVER_API_PREFIX == "/auth"
    error_message = "Expected AUTH_SERVER_API_PREFIX=/auth in extraEnv on new installs."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.extraEnv.REGISTRY_URL == "https://registry.example.com/gateway"
    error_message = "Expected REGISTRY_URL in extraEnv on new installs."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.extraEnv.REGISTRY_CLIENT_URL == "https://registry.example.com/gateway"
    error_message = "Expected REGISTRY_CLIENT_URL to match REGISTRY_URL path in extraEnv."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.extraEnv.NGINX_BASE_PATH == "/gateway"
    error_message = "Expected NGINX_BASE_PATH=/gateway in extraEnv."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.authServer.forwardedAllowIps == "10.0.0.0/16,100.64.0.0/16"
    error_message = "Expected plain comma-joined VPC CIDRs in forwardedAllowIps."
  }
}

run "domain_with_alb_group_sets_group_name_and_certificate" {
  command = plan

  variables {
    seed_secret     = false
    domain          = "registry.example.com"
    certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    alb_group_name  = "shared-group"
    alb_group_order = 20
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/group.name"] == "shared-group"
    error_message = "Expected group.name annotation when alb_group_name is set."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/group.order"] == "20"
    error_message = "Expected group.order annotation matching alb_group_order."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/certificate-arn"] == "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    error_message = "Expected certificate-arn annotation when alb_join_existing is false."
  }
}

run "alb_join_existing_emits_only_per_ingress_annotations" {
  command = plan

  variables {
    seed_secret       = false
    domain            = "jarvis.example.com"
    alb_group_name    = "group"
    alb_group_order   = 10
    alb_join_existing = true
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/group.name"] == "group"
    error_message = "Expected group.name annotation in alb_join_existing mode."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/group.order"] == "10"
    error_message = "Expected group.order annotation in alb_join_existing mode."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/target-type"] == "ip"
    error_message = "Expected target-type annotation in alb_join_existing mode."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.ingress.annotations["alb.ingress.kubernetes.io/healthcheck-path"] == "/health"
    error_message = "Expected healthcheck-path annotation in alb_join_existing mode."
  }

  assert {
    condition = alltrue([
      for forbidden in [
        "alb.ingress.kubernetes.io/scheme",
        "alb.ingress.kubernetes.io/subnets",
        "alb.ingress.kubernetes.io/listen-ports",
        "alb.ingress.kubernetes.io/ssl-redirect",
        "alb.ingress.kubernetes.io/certificate-arn",
      ] : !contains(keys(yamldecode(helm_release.registry.values[0]).registry.ingress.annotations), forbidden)
    ])
    error_message = "alb_join_existing = true must not emit scheme, subnets, listen-ports, ssl-redirect, or certificate-arn."
  }
}

run "irsa_trust_condition_and_helm_defaults" {
  command = plan

  variables {
    seed_secret = false
    chart_path  = "../../../helm/jarvis-registry"
  }

  assert {
    condition     = jsondecode(aws_iam_role.registry.assume_role_policy).Statement[0].Condition.StringLike["oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE000000000000000000000000:sub"] == "system:serviceaccount:*:registry-service-account"
    error_message = "IRSA trust sub condition must equal system:serviceaccount:*:registry-service-account."
  }

  assert {
    condition     = jsondecode(aws_iam_role.registry.assume_role_policy).Statement[0].Condition.StringLike["oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE000000000000000000000000:aud"] == "sts.amazonaws.com"
    error_message = "IRSA trust aud condition must equal sts.amazonaws.com."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.serviceAccount.name == "registry-service-account"
    error_message = "Helm serviceAccount.name must align with IRSA trust condition."
  }

  assert {
    condition     = helm_release.registry.name == "jarvis-registry" && helm_release.registry.chart == "../../../helm/jarvis-registry" && helm_release.registry.wait == true && helm_release.registry.atomic == false && helm_release.registry.timeout == 1200
    error_message = "Expected fixed release name jarvis-registry with local chart_path, wait=true, atomic=false, timeout=1200."
  }
}

run "agentcore_and_monitoring_enabled_parity" {
  command = plan

  variables {
    seed_secret                  = false
    agentcore_federation_enabled = true
    monitoring_enabled           = true
  }

  assert {
    condition     = length(aws_iam_role.agentcore) == 1 && length(aws_iam_policy.agentcore) == 1 && length(aws_iam_role_policy_attachment.agentcore) == 1 && length(aws_iam_role_policy.registry_assume_agentcore) == 1
    error_message = "Expected all four AgentCore IAM resources when agentcore_federation_enabled is true."
  }

  assert {
    condition     = length(jsondecode(aws_iam_policy.agentcore[0].policy).Statement[0].Action) == 5 && contains(jsondecode(aws_iam_policy.agentcore[0].policy).Statement[0].Action, "bedrock-agentcore:InvokeAgentRuntime")
    error_message = "AgentCore policy must match the five least-privilege bedrock-agentcore actions."
  }

  assert {
    condition     = yamldecode(helm_release.registry.values[0]).registry.monitoring.externalSecrets.serviceAccount.annotations["eks.amazonaws.com/role-arn"] == "arn:aws:iam::123456789012:role/jarvis-test-role"
    error_message = "monitoring_enabled must wire monitoring externalSecrets SA role-arn annotation."
  }
}
