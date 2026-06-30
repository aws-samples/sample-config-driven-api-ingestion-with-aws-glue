# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# AWS Glue Python Shell Job
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_job" "api_consumer" {
  name              = "${var.project_name}-${var.environment}-api-consumer"
  role_arn          = aws_iam_role.glue_job.arn
  security_configuration = aws_glue_security_configuration.pipeline.name

  command {
    name            = "pythonshell"
    script_location = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.glue_script.key}"
    python_version  = var.glue_python_version
  }

  max_capacity = var.glue_max_capacity
  max_retries  = 0
  timeout      = 120 # minutes

  default_arguments = {
    "--job-language"                     = "python"
    "--REGION"                           = var.aws_region
    "--STAGING_BUCKET_NAME"              = aws_s3_bucket.staging.id
    "--SECRET_NAME"                      = var.secret_name
    "--TOKEN_URL"                        = var.token_url
    "--CERT_PATH"                        = "s3://${aws_s3_bucket.artifacts.id}/certs/<your-certificate>.pfx"
    "--DYNAMODB_DETAIL_TABLE"            = aws_dynamodb_table.detail_audit.name
    "--ORIGINAL_INTERFACE_DATA_PREFIX"   = "raw_data"
    "--FLATTENED_INTERFACE_DATA_PREFIX"  = "transformed_data"
    "--CONFIG_PATH"                      = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.config_file.key}"
    "--additional-python-modules"        = "requests>=2.32.2,cryptography>=43.0.3,pandas>=2.0.0"
  }

  execution_property {
    max_concurrent_runs = 10
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# Glue Security Configuration
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_glue_security_configuration" "pipeline" {
  name = "${var.project_name}-${var.environment}-security-config"

  encryption_configuration {
    cloudwatch_encryption {
      cloudwatch_encryption_mode = "SSE-KMS"
      kms_key_arn                = aws_kms_key.pipeline.arn
    }

    job_bookmarks_encryption {
      job_bookmarks_encryption_mode = "CSE-KMS"
      kms_key_arn                   = aws_kms_key.pipeline.arn
    }

    s3_encryption {
      s3_encryption_mode = "SSE-KMS"
      kms_key_arn        = aws_kms_key.pipeline.arn
    }
  }
}
