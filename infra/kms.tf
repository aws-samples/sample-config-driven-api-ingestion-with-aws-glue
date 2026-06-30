# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# KMS Customer Managed Keys
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_kms_key" "pipeline" {
  description             = "CMK for ${var.project_name}-${var.environment} pipeline encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccountAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowServiceUsage"
        Effect = "Allow"
        Principal = {
          Service = [
            "logs.${var.aws_region}.amazonaws.com",
            "s3.amazonaws.com",
            "dynamodb.amazonaws.com",
            "secretsmanager.amazonaws.com",
            "scheduler.amazonaws.com"
          ]
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "pipeline" {
  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.pipeline.key_id
}
