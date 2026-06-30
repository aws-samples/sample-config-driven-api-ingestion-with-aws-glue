# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# EventBridge Scheduler - Incremental Load
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_scheduler_schedule" "incremental_load" {
  name        = "${var.project_name}-${var.environment}-incremental-load"
  description = "Triggers the API ingestion pipeline for incremental loads"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  schedule_expression = var.schedule_expression
  kms_key_arn         = aws_kms_key.pipeline.arn

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.eventbridge_scheduler.arn

    input = jsonencode({
      loadType = "Incremental"
      entity_config = {
        for entity, _ in var.api_endpoints :
        entity => {
          params = {
            deltaHours = "8"
          }
        }
      }
    })
  }
}
