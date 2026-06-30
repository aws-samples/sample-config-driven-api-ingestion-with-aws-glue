# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

output "staging_bucket_name" {
  description = "S3 bucket for raw JSON, transformed CSV, and error records"
  value       = aws_s3_bucket.staging.id
}

output "artifacts_bucket_name" {
  description = "S3 bucket for Glue scripts, config, and certificates"
  value       = aws_s3_bucket.artifacts.id
}

output "glue_job_name" {
  description = "Name of the Glue Python Shell job"
  value       = aws_glue_job.api_consumer.name
}

output "state_machine_arn" {
  description = "ARN of the Step Functions state machine"
  value       = aws_sfn_state_machine.pipeline.arn
}

output "pre_processing_lambda_arn" {
  description = "ARN of the pre-processing Lambda function"
  value       = aws_lambda_function.pre_processing.arn
}

output "post_processing_lambda_arn" {
  description = "ARN of the post-processing Lambda function"
  value       = aws_lambda_function.post_processing.arn
}

output "detail_audit_table_name" {
  description = "DynamoDB table for page-level audit details"
  value       = aws_dynamodb_table.detail_audit.name
}

output "summary_audit_table_name" {
  description = "DynamoDB table for execution summaries"
  value       = aws_dynamodb_table.summary_audit.name
}

output "eventbridge_schedule_name" {
  description = "EventBridge schedule name for incremental loads"
  value       = aws_scheduler_schedule.incremental_load.name
}
