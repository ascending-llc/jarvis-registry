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

  mock_data "aws_eks_cluster" {
    defaults = {
      name = "jarvis-quickstart-test"
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
      ]
    }
  }
}

mock_provider "helm" {}

run "quickstart_with_local_chart_path" {
  command = plan

  variables {
    region          = "us-east-1"
    name            = "jarvis-quickstart-test"
    domain          = "registry.example.com"
    certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    chart_path      = "../../helm/jarvis-registry"
    image_tag       = "asc0.5.21"
    app_secrets = {
      AUTH_PROVIDER       = "entra"
      ENTRA_CLIENT_SECRET = "SENTINEL_QUICKSTART_SECRET_CHECK"
    }
  }

  assert {
    condition     = output.registry_url == "https://registry.example.com/gateway"
    error_message = "Expected quickstart output.registry_url to equal https://registry.example.com/gateway."
  }

  assert {
    condition     = output.namespace == "jarvis-registry"
    error_message = "Expected quickstart output.namespace to equal jarvis-registry."
  }

  assert {
    condition     = output.secret_name == "jarvis-quickstart-test/registry-env"
    error_message = "Expected quickstart output.secret_name to default to <name>/registry-env."
  }
}
