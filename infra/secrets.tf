# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# Secrets Manager
# ─────────────────────────────────────────────────────────────────────────────

# Creates the secret with placeholder values.
# Terraform creates this secret automatically during deployment.
# After deployment, update it with real credentials:
#
#   aws secretsmanager put-secret-value \
#     --secret-id <secret-name> \
#     --secret-string '{"client_id":"YOUR_ID","client_secrets":"YOUR_SECRET","cert_password":"YOUR_CERT_PASS"}'
#

resource "aws_secretsmanager_secret" "api_credentials" {
  name        = var.secret_name
  description = "API credentials for the ingestion pipeline. Update with real values after deployment."
  kms_key_id  = aws_kms_key.pipeline.arn
}

resource "aws_secretsmanager_secret_version" "api_credentials" {
  secret_id = aws_secretsmanager_secret.api_credentials.id
  secret_string = jsonencode({
    client_id      = "CHANGE_ME"
    client_secrets = "CHANGE_ME"  # pragma: allowlist secret
    cert_password  = "CHANGE_ME"  # pragma: allowlist secret
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
