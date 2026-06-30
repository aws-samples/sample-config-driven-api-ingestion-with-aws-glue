# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# Lambda: Pre-processing
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "pre_processing" {
  type        = "zip"
  source_dir  = "${path.module}/../src/lambda/pre_processing"
  output_path = "${path.module}/.build/pre_processing.zip"
}

resource "aws_lambda_function" "pre_processing" {
  function_name                  = "${var.project_name}-${var.environment}-pre-processing"
  role                           = aws_iam_role.lambda_pre_processing.arn
  handler                        = "function_code.lambda_handler"
  runtime                        = var.lambda_runtime
  memory_size                    = var.lambda_memory_size
  timeout                        = var.lambda_timeout
  filename                       = data.archive_file.pre_processing.output_path
  source_code_hash               = data.archive_file.pre_processing.output_base64sha256
  reserved_concurrent_executions = 10
  kms_key_arn                    = aws_kms_key.pipeline.arn

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  environment {
    variables = merge(
      {
        for entity, endpoint in var.api_endpoints :
        "${upper(entity)}_ENDPOINT" => endpoint
      }
    )
  }
}

resource "aws_cloudwatch_log_group" "pre_processing" {
  name              = "/aws/lambda/${aws_lambda_function.pre_processing.function_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.pipeline.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda: Post-processing
# ─────────────────────────────────────────────────────────────────────────────

data "archive_file" "post_processing" {
  type        = "zip"
  source_dir  = "${path.module}/../src/lambda/post_processing"
  output_path = "${path.module}/.build/post_processing.zip"
}

resource "aws_lambda_function" "post_processing" {
  function_name                  = "${var.project_name}-${var.environment}-post-processing"
  role                           = aws_iam_role.lambda_post_processing.arn
  handler                        = "function_code.lambda_handler"
  runtime                        = var.lambda_runtime
  memory_size                    = var.lambda_memory_size
  timeout                        = var.lambda_timeout
  filename                       = data.archive_file.post_processing.output_path
  source_code_hash               = data.archive_file.post_processing.output_base64sha256
  reserved_concurrent_executions = 10
  kms_key_arn                    = aws_kms_key.pipeline.arn

  tracing_config {
    mode = "Active"
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  environment {
    variables = {
      DETAIL_AUDIT_TABLE  = aws_dynamodb_table.detail_audit.name
      SUMMARY_AUDIT_TABLE = aws_dynamodb_table.summary_audit.name
    }
  }
}

resource "aws_cloudwatch_log_group" "post_processing" {
  name              = "/aws/lambda/${aws_lambda_function.post_processing.function_name}"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.pipeline.arn
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda DLQ (SQS)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_sqs_queue" "lambda_dlq" {
  name                       = "${var.project_name}-${var.environment}-lambda-dlq"
  kms_master_key_id          = aws_kms_key.pipeline.id
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 300
}

# Enforce TLS in transit — deny any access over a non-TLS connection.
# Mirrors the DenyInsecureTransport policy applied to the S3 buckets.
resource "aws_sqs_queue_policy" "lambda_dlq_deny_insecure" {
  queue_url = aws_sqs_queue.lambda_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sqs:*"
        Resource  = aws_sqs_queue.lambda_dlq.arn
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}
