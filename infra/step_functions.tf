# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ─────────────────────────────────────────────────────────────────────────────
# Step Functions State Machine
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.project_name}-${var.environment}-pipeline"
  role_arn = aws_iam_role.step_functions.arn

  tracing_configuration {
    enabled = true
  }

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  definition = jsonencode({
    Comment = "Config-Driven API Ingestion Pipeline"
    StartAt = "Pipeline_Input"
    States = {
      Pipeline_Input = {
        Type = "Pass"
        Next = "Pre-processing Lambda"
      }

      "Pre-processing Lambda" = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.pre_processing.arn
          "Payload.$"  = "$"
        }
        ResultPath = "$.lambdaResult"
        ResultSelector = {
          "body.$"       = "$.Payload.body"
          "statusCode.$" = "$.Payload.statusCode"
        }
        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 1
            MaxAttempts     = 3
            BackoffRate     = 2
            JitterStrategy  = "FULL"
          }
        ]
        Next = "EntityJSONFormatter"
      }

      EntityJSONFormatter = {
        Type = "Pass"
        Parameters = {
          "parsedBody.$" = "States.StringToJson($.lambdaResult.body)"
        }
        Next = "Map State for Entities"
      }

      "Map State for Entities" = {
        Type           = "Map"
        ItemsPath      = "$.parsedBody.entities"
        ResultPath     = "$.mapResults"
        MaxConcurrency = 0
        ItemProcessor = {
          ProcessorConfig = {
            Mode = "INLINE"
          }
          StartAt = "API Consumer Glue Job"
          States = {
            "API Consumer Glue Job" = {
              Type     = "Task"
              Resource = "arn:aws:states:::glue:startJobRun.sync"
              Parameters = {
                JobName = aws_glue_job.api_consumer.name
                Arguments = {
                  "--entity_type.$" = "$.entity_type"
                  "--endpoint.$"    = "$.endpoint"
                  "--params.$"      = "States.JsonToString($.params)"
                  "--load_type.$"   = "$.load_type"
                  "--ingestion_id.$" = "States.UUID()"
                }
              }
              ResultPath = "$.glueJobResult"
              Retry = [
                {
                  ErrorEquals     = ["States.TaskFailed"]
                  IntervalSeconds = 60
                  MaxAttempts     = 3
                  BackoffRate     = 2
                }
              ]
              Catch = [
                {
                  ErrorEquals = ["States.ALL"]
                  ResultPath  = "$.error"
                  Next        = "Handle Error"
                }
              ]
              Next = "ProcessMapResults"
            }

            ProcessMapResults = {
              Type = "Pass"
              End  = true
            }

            "Handle Error" = {
              Type = "Pass"
              Parameters = {
                status             = "FAILED"
                "entity_type.$"    = "$.entity_type"
                "errorDetails.$"   = "$.error"
              }
              End = true
            }
          }
        }
        Next = "Check All Jobs Completed"
      }

      "Check All Jobs Completed" = {
        Type = "Choice"
        Choices = [
          {
            Variable  = "$.mapResults"
            IsPresent = true
            Next      = "Post-processing Lambda"
          }
        ]
        Default = "Job Status Check Failed"
      }

      "Job Status Check Failed" = {
        Type  = "Fail"
        Error = "JobStatusCheckFailed"
        Cause = "Unable to determine status of one or more Glue jobs"
      }

      "Post-processing Lambda" = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.post_processing.arn
          Payload = {
            input = {
              "mapResults.$" = "$.mapResults"
              "parsedBody.$" = "$.parsedBody"
            }
          }
        }
        ResultSelector = {
          "body.$"       = "$.Payload.body"
          "statusCode.$" = "$.Payload.statusCode"
        }
        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 1
            MaxAttempts     = 3
            BackoffRate     = 2
            JitterStrategy  = "FULL"
          }
        ]
        End = true
      }
    }
  })
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/states/${var.project_name}-${var.environment}-pipeline"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.pipeline.arn
}
