resource "aws_iam_role" "registry" {
  name        = var.registry_role_name
  name_prefix = var.registry_role_name == null ? "${var.name}-role-" : null

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRoleWithWebIdentity"
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.this.arn
        }
        Condition = {
          StringLike = {
            "${local.oidc_provider}:sub" = "system:serviceaccount:*:${var.service_account_name}"
            "${local.oidc_provider}:aud" = "sts.amazonaws.com"
          }
        }
      },
    ]
  })

  tags = var.tags
}

resource "aws_iam_policy" "registry" {
  name        = var.registry_policy_name
  name_prefix = var.registry_policy_name == null ? "${var.name}-policy-" : null
  description = "Policy for Jarvis Registry to access AWS resources"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "s3"
        Effect   = "Allow"
        Action   = ["s3:ListAllMyBuckets"]
        Resource = ["*"]
      },
      {
        Sid      = "secretsmanager"
        Effect   = "Allow"
        Action   = ["secretsmanager:*"]
        Resource = [aws_secretsmanager_secret.registry.id]
      },
      {
        Sid      = "bedrock"
        Effect   = "Allow"
        Action   = ["bedrock:*"]
        Resource = ["*"]
      },
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "registry" {
  role       = aws_iam_role.registry.name
  policy_arn = aws_iam_policy.registry.arn
}

resource "aws_iam_role" "agentcore" {
  count = var.agentcore_federation_enabled ? 1 : 0

  name        = var.agentcore_role_name
  name_prefix = var.agentcore_role_name == null ? "${var.name}-agentcore-role-" : null

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect = "Allow"
          Action = "sts:AssumeRole"
          Principal = {
            AWS = aws_iam_role.registry.arn
          }
        },
      ],
      [
        for arn in var.agentcore_assumable_role_arns : {
          Effect = "Allow"
          Action = "sts:AssumeRole"
          Principal = {
            AWS = arn
          }
        }
      ],
    )
  })

  tags = var.tags
}

resource "aws_iam_policy" "agentcore" {
  count = var.agentcore_federation_enabled ? 1 : 0

  name        = var.agentcore_policy_name
  name_prefix = var.agentcore_policy_name == null ? "${var.name}-agentcore-policy-" : null
  description = "Policy for Bedrock AgentCore runtime discovery and invocation"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "agentcore"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore:GetAgentCard",
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:ListTagsForResource",
        ]
        Resource = ["*"]
      },
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "agentcore" {
  count = var.agentcore_federation_enabled ? 1 : 0

  role       = aws_iam_role.agentcore[0].name
  policy_arn = aws_iam_policy.agentcore[0].arn
}

resource "aws_iam_role_policy" "registry_assume_agentcore" {
  count = var.agentcore_federation_enabled ? 1 : 0

  name        = var.registry_assume_agentcore_policy_name
  name_prefix = var.registry_assume_agentcore_policy_name == null ? "assume-agentcore-role-" : null
  role        = aws_iam_role.registry.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = aws_iam_role.agentcore[0].arn
      },
    ]
  })
}
