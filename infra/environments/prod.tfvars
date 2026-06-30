project_name = "api-ingestion"
environment  = "prod"
aws_region   = "us-east-1"

# API endpoints for each entity type
api_endpoints = {
  product = "https://api.example.com/v2/products"
  order   = "https://api.example.com/v1/orders"
  vendor  = "https://api.example.com/v2/vendors"
}

# OAuth2 token endpoint
token_url = "https://auth.example.com/oauth2/token"

# Secrets Manager secret name (Terraform creates this — update values after deployment)
secret_name = "api-ingestion/prod/credentials"  # pragma: allowlist secret

# EventBridge schedule for incremental loads
schedule_expression = "rate(8 hours)"

# Tags
tags = {
  Team        = "data-engineering"
  Environment = "prod"
}
