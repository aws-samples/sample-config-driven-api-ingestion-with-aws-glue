# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB Tables
# ─────────────────────────────────────────────────────────────────────────────

# Detail audit table: page-level tracking for resumability and debugging
resource "aws_dynamodb_table" "detail_audit" {
  name         = "${var.project_name}-${var.environment}-detail-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "entity-type"
  range_key    = "last-execution-time"

  attribute {
    name = "entity-type"
    type = "S"
  }

  attribute {
    name = "last-execution-time"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.pipeline.arn
  }
}

# Summary audit table: one record per entity per execution
resource "aws_dynamodb_table" "summary_audit" {
  name         = "${var.project_name}-${var.environment}-summary-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "entity-type"
  range_key    = "last-execution-time"

  attribute {
    name = "entity-type"
    type = "S"
  }

  attribute {
    name = "last-execution-time"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.pipeline.arn
  }
}
