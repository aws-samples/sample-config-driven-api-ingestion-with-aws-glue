# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

variable "project_name" {
  description = "Name of the project, used as prefix for all resources"
  type        = string
  default     = "api-ingestion"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication"
  type        = string
  default     = null
}

# ─────────────────────────────────────────────────────────────────────────────
# API Configuration
# ─────────────────────────────────────────────────────────────────────────────

variable "api_endpoints" {
  description = "Map of entity names to their API endpoint URLs"
  type        = map(string)
  default = {
    product = "https://api.example.com/v2/products"
    order   = "https://api.example.com/v1/orders"
    vendor  = "https://api.example.com/v2/vendors"
  }
}

variable "token_url" {
  description = "OAuth2 token endpoint URL"
  type        = string
  default     = "https://auth.example.com/oauth2/token"
}

# ─────────────────────────────────────────────────────────────────────────────
# Secrets Manager
# ─────────────────────────────────────────────────────────────────────────────

variable "secret_name" {
  description = "Name of the Secrets Manager secret containing API credentials"
  type        = string
  default     = "api-ingestion/credentials"
}

# ─────────────────────────────────────────────────────────────────────────────
# EventBridge Schedule
# ─────────────────────────────────────────────────────────────────────────────

variable "schedule_expression" {
  description = "EventBridge schedule expression for incremental loads"
  type        = string
  default     = "rate(8 hours)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Glue Job Configuration
# ─────────────────────────────────────────────────────────────────────────────

variable "glue_python_version" {
  description = "Python version for the Glue Python Shell job"
  type        = string
  default     = "3.9"
}

variable "glue_max_capacity" {
  description = "Max DPU capacity for the Glue job"
  type        = number
  default     = 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda Configuration
# ─────────────────────────────────────────────────────────────────────────────

variable "lambda_runtime" {
  description = "Lambda runtime version"
  type        = string
  default     = "python3.13"
}

variable "lambda_memory_size" {
  description = "Lambda memory size in MB"
  type        = number
  default     = 256
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 900
}

# ─────────────────────────────────────────────────────────────────────────────
# Tags
# ─────────────────────────────────────────────────────────────────────────────

variable "tags" {
  description = "Common tags applied to all resources"
  type        = map(string)
  default     = {}
}
