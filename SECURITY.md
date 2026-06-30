# Security Policy

## Disclaimer

This project is provided as sample/educational code and is NOT intended for production use without
additional security hardening. See the "Production Hardening Recommendations" section below.

## Reporting Vulnerabilities

If you discover a potential security issue in this project, please report it by emailing
aws-security@amazon.com. Do not report security vulnerabilities through public GitHub issues.

## AWS Services Used

- AWS Glue (Python Shell) — config-driven ETL engine for API ingestion
- AWS Step Functions — orchestration of parallel entity processing
- AWS Lambda — pre-processing (endpoint resolution) and post-processing (audit summary)
- Amazon DynamoDB — page-level audit trail and execution summary tracking
- Amazon S3 — raw JSON storage, normalized CSV output, error records, configuration
- AWS Secrets Manager — OAuth2 credentials and certificate passwords
- Amazon EventBridge — scheduled triggers for incremental loads

## Prerequisites and Permissions

To deploy this solution, you need:

- An AWS account with permissions to create Glue jobs, Lambda functions, Step Functions, DynamoDB tables, S3 buckets, and Secrets Manager secrets
- A Secrets Manager secret containing: `client_id`, `client_secrets`, `cert_password`
- A PFX certificate uploaded to S3 for mTLS authentication
- An OAuth2 token endpoint URL

## Production Hardening Recommendations

Before using this code in a production environment:

- **Dependencies**: Pin exact `==` versions in both `requirements.txt` files (`requests==2.32.4`, `cryptography==48.0.1`, `pandas==2.0.3`), generate hashes with `pip-compile --generate-hashes`, and align the `--additional-python-modules` argument in `infra/glue.tf` to the same pinned set (it currently uses unbounded `>=` ranges)
- **Error Handling**: Replace `except Exception` with specific exception types (`ClientError`, `RequestException`, `JSONDecodeError`)
- **Error Responses**: Return generic error messages from Lambda; log details to CloudWatch only
- **Region Handling**: Always pass the region explicitly from environment variables
- **IAM Roles**: Create least-privilege IAM roles for each component (Glue, Lambda, Step Functions)
- **Encryption**: Enable SSE-KMS on S3 buckets and DynamoDB tables with Customer Managed Keys
- **DynamoDB Deletion Protection**: Set `deletion_protection_enabled = true` on the detail and summary audit tables to guard against accidental table deletion (PITR and SSE are already enabled)
- **KMS Key Policy**: The sample uses the default anti-lockout policy granting `kms:*` to the account root. For shared/production accounts, split the policy — grant admin actions (`kms:Create*`, `kms:Put*`, `kms:ScheduleKeyDeletion`, `kms:Disable*`) only to a specific deploy-role ARN, with a separate scoped usage statement
- **VPC**: Consider placing Glue jobs and Lambda functions in a VPC for network isolation
- **Logging**: Enable CloudTrail, S3 access logging, and Lambda insights
- **Monitoring**: Add CloudWatch alarms for circuit breaker activation and consecutive failures
- **Certificate Rotation**: Implement automated PFX certificate rotation via Secrets Manager

## Resource Cleanup

To remove all resources deployed by this project:

1. Delete the Step Functions state machine
2. Delete Lambda functions (pre-processing and post-processing)
3. Delete the Glue job
4. Empty and delete S3 buckets (raw, transformed, error, artifacts)
5. Delete DynamoDB tables (detail and summary)
6. Delete the Secrets Manager secret
7. Delete the EventBridge scheduler rule

Or if deployed via Terraform:
```bash
cd infra
terraform destroy -var-file=environments/<env>.tfvars
```

## Dependencies

| Dependency | Version (current) | Recommended production pin | Notes |
|------------|-------------------|----------------------------|-------|
| requests | ~=2.32.4 | ==2.32.4 | HTTP client for API calls — uses HTTPS only |
| cryptography | ~=48.0.1 | ==48.0.1 | PFX certificate extraction |
| pandas | ~=2.0.0 (Glue only) | ==2.0.3 | DataFrame operations for data normalization |
| boto3 | (AWS SDK) | n/a | Provided by Lambda/Glue runtime |

> For production, replace the bounded `~=` ranges with exact `==` pins (see Production Hardening Recommendations above) and generate hashes with `pip-compile --generate-hashes` for reproducible, auditable builds.
