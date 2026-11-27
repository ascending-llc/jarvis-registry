mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition  = "aws"
      dns_suffix = "amazonaws.com"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:root"
      user_id    = "123456789012"
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "us-east-1"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_data "aws_iam_session_context" {
    defaults = {
      issuer_arn = "arn:aws:iam::123456789012:role/test-creator"
    }
  }
}

mock_provider "helm" {}

variables {
  name = "jarvis-test"
  tags = {
    Environment = "e2e"
    ManagedBy   = "terraform"
  }
}

run "plans_cluster_defaults_external_secrets_and_tags" {
  command = plan

  assert {
    condition     = helm_release.cluster_defaults.name == "cluster-defaults"
    error_message = "Expected helm_release.cluster_defaults to be planned."
  }

  assert {
    condition     = yamldecode(helm_release.cluster_defaults.values[0]).tags.Environment == "e2e" && yamldecode(helm_release.cluster_defaults.values[0]).tags.ManagedBy == "terraform"
    error_message = "Expected var.tags to be passed into cluster-defaults Helm values for IngressClassParams and StorageClass."
  }

  assert {
    condition     = helm_release.external_secrets.name == "external-secrets" && helm_release.external_secrets.namespace == "external-secrets"
    error_message = "Expected helm_release.external_secrets to be planned in namespace external-secrets."
  }

  assert {
    condition     = anytrue([for s in helm_release.external_secrets.set : s.name == "installCRDs" && s.value == "true"])
    error_message = "Expected external-secrets release to set installCRDs=true."
  }

  assert {
    condition     = length(module.vpc.nat_ids) == 1
    error_message = "Expected single_nat_gateway = true (default) to plan 1 NAT Gateway."
  }
}

run "single_nat_gateway_false_passes_through_to_vpc" {
  command = plan

  variables {
    az_count           = 2
    single_nat_gateway = false
  }

  assert {
    condition     = length(module.vpc.nat_ids) == 2
    error_message = "Expected single_nat_gateway = false with az_count = 2 to plan 2 NAT Gateways."
  }
}
