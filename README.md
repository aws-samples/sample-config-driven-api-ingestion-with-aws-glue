# Config-Driven Paginated API Ingestion Pipeline

A single, reusable AWS Glue job that ingests data from any paginated REST API and normalizes deeply nested JSON responses into a relational model — eliminating the need to write and maintain a separate job for every entity type.

> **Important:** This is sample code for demonstration and educational purposes. Review and adapt for your use case before production use.

## The Problem

Enterprise teams consume multiple entity types from paginated REST APIs — each with its own nested JSON structure and target relational schema. The traditional approach (one Glue job per entity) creates:

- Maintenance burden that grows linearly with each new entity
- Inconsistent error handling across jobs
- Code changes required every time an API response structure changes

## The Solution

Separate the **relational model definition** (config) from the **processing engine** (code):

- A JSON config file defines main tables, child tables, foreign keys, field mappings, primary keys, and not-null constraints
- A single Glue job reads the config and processes any entity type
- **Adding a new entity requires zero code changes — just a new config block**

## Architecture

![System Architecture](<docs/diagrams/architecutre-diagram.png>)

## Process Flow

![Process Flow](<docs/diagrams/process-flow.png>)

## AWS Services Used

| Service | Role |
|---------|------|
| AWS Glue (Python Shell) | Config-driven ETL engine — pagination, normalization, validation |
| AWS Step Functions | Orchestration — parallel entity processing with error handling |
| AWS Lambda | Pre-processing (endpoint resolution) and post-processing (audit) |
| Amazon DynamoDB | Page-level audit trail and execution summary tracking |
| Amazon S3 | Raw JSON storage, normalized CSV output, error records |
| AWS Secrets Manager | API credentials (OAuth2 client ID/secret, certificate password) |
| Amazon EventBridge | Scheduled triggers for incremental and historical loads |

## Project Structure

```
.
├── src/
│   ├── glue/
│   │   └── api_consumer_glue.py           # Core: config-driven ingestion engine
│   ├── lambda/
│   │   ├── pre_processing/
│   │   │   └── function_code.py           # Endpoint resolution + param init
│   │   └── post_processing/
│   │       └── function_code.py           # Audit summary + execution tracking
│   └── step-functions/
│       └── state_machine_definition.asl.json  # Reference ASL definition
├── infra/
│   ├── providers.tf                       # AWS provider + Terraform config
│   ├── variables.tf                       # All configurable inputs
│   ├── s3.tf                              # Staging + artifacts buckets
│   ├── kms.tf                             # Encryption keys
│   ├── dynamodb.tf                        # Detail + summary audit tables
│   ├── iam.tf                             # Least-privilege roles
│   ├── glue.tf                            # Python Shell job
│   ├── lambda.tf                          # Pre/post-processing functions
│   ├── step_functions.tf                  # State machine (inline ASL)
│   ├── eventbridge.tf                     # Scheduled triggers
│   ├── secrets.tf                         # Secrets Manager secret
│   ├── outputs.tf                         # Resource names/ARNs
│   └── environments/
│       ├── dev.tfvars                     # Dev environment config
│       ├── staging.tfvars                 # Staging environment config
│       └── prod.tfvars                    # Prod environment config
├── config/
│   ├── config.example.json                # ER model definition (THE config)
│   └── schemas/
│       └── product_schema.example.json    # Data type validation schema
├── dependencies/
│   ├── glue/requirements.txt
│   └── lambda/requirements.txt
└── docs/
    ├── configuration-guide.md             # Full config reference
    └── diagrams/
        ├── architecture.drawio            # Architecture diagram
        └── process-flow.drawio            # Process flow diagram
```

## How the Config Works

The config file **is** the ER model. Each entity defines tables, relationships, and field mappings:

```json
{
  "entities": {
    "product": {
      "main_table": {
        "name": "product",
        "source_paths": [
          {"path": "BasicInformation"},
          {"path": "ActivationInformation"}
        ],
        "primary_key": {"columns": ["product_id"]},
        "not_null": ["product_id"],
        "fields": [
          {"name": "product_id", "source": "ProductID"},
          {"name": "product_name", "source": "ProductName"},
          {"name": "status", "source": "StatusCode", "default": "UNKNOWN"}
        ]
      },
      "child_tables": [
        {
          "name": "product_location",
          "source_path": "Addresses",
          "foreign_key": {"columns": ["product_id"], "references": "product"},
          "fields": [
            {"name": "product_id", "source": "ProductID", "parent_field": true},
            {"name": "city", "source": "City"},
            {"name": "country", "source": "Country"}
          ]
        }
      ]
    }
  }
}
```

**Adding a new entity = adding a JSON block. Zero code changes.**

## Adding a New Entity

1. Add a new block to `config/config.json` with your field mappings
2. (Optional) Add a schema file to `config/schemas/` for type validation
3. Add the entity name + endpoint to your Lambda environment variables
4. Include the entity in your Step Function input payload

No Glue job code changes required.

## Load Types

| Type | Trigger | Behavior |
|------|---------|----------|
| Incremental | Scheduled (e.g., every 8 hours) | Fetches records modified in last N hours via `deltaHours` param |
| Historical | On-demand or scheduled | Full data load, page-level tracking for resumability |

## What to Customize

Before deploying, review and update these values for your environment:

| File | Setting | Action |
|------|---------|--------|
| `infra/environments/dev.tfvars` | `aws_region` | Set to your target AWS region |
| `infra/environments/dev.tfvars` | `aws_profile` | Uncomment and set your AWS CLI profile, or leave commented to use default credentials |
| `infra/environments/dev.tfvars` | `api_endpoints` | Replace example URLs with your real API endpoints |
| `infra/environments/dev.tfvars` | `token_url` | Set to your OAuth2 token endpoint |
| `infra/glue.tf` | `--CERT_PATH` | Replace `<your-certificate>.pfx` with your actual certificate filename after uploading to S3 |
| `config/config.example.json` | Entity definitions | Map your API response fields to your target schema |
| `config/schemas/` | Schema files | Define data types for validation (one per entity) |

**Note:** The Secrets Manager secret is created by Terraform with placeholder values. You do **not** need to create it manually — just update the values after deployment (see Post-Deployment below).

## Deployment

Infrastructure is managed with Terraform under `infra/`.

### Prerequisites

1. [Terraform >= 1.5.0](https://developer.hashicorp.com/terraform/install)
2. AWS CLI configured with appropriate credentials
3. A PFX certificate for mTLS authentication (upload after deploy)
4. OAuth2 API credentials (client ID, client secret, cert password)

### Deploy

```bash
cd infra
terraform init
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

### Post-Deployment

1. Update Secrets Manager with real API credentials:

   **Option A — AWS CLI:**
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id <secret-name> \
     --secret-string '{"client_id":"...","client_secrets":"...","cert_password":"..."}'
   ```

   **Option B — AWS Console:**
   - Navigate to **Secrets Manager** → find the secret (`api-ingestion/<env>/credentials`)
   - Click **Retrieve secret value** → **Edit**
   - Replace the placeholder values with your real credentials
   - Click **Save**

2. Upload the mTLS certificate:
```bash
aws s3 cp certificate.pfx s3://<artifacts-bucket>/certs/certificate.pfx
```

3. Update `config/config.example.json` with your entity field mappings and re-upload to S3.

See [docs/configuration-guide.md](docs/configuration-guide.md) for the full configuration reference.

## Teardown

To remove all deployed resources:

```bash
cd infra
terraform destroy -var-file=environments/dev.tfvars
```

**Important:** The S3 access-logs bucket has a lifecycle policy (objects expire after 1 year) but may not be empty at destroy time. If `terraform destroy` fails on the logging bucket, empty it first:

```bash
aws s3 rm s3://<access-logs-bucket> --recursive
terraform destroy -var-file=environments/dev.tfvars
```

Similarly, the staging and artifacts buckets must be emptied before they can be deleted.

## Testing

Trigger a test execution after deployment:

```bash
aws stepfunctions start-execution \
  --state-machine-arn <state-machine-arn> \
  --input '{
    "loadType": "Incremental",
    "entity_config": {
      "product": {"params": {"deltaHours": "8"}}
    }
  }'
```

On success, verify:
- Raw JSON in `s3://<staging-bucket>/raw_data/product/Incremental/`
- Normalized CSVs in `s3://<staging-bucket>/transformed_data/product/product/Incremental/`
- Audit records in the DynamoDB detail and summary tables

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for reporting security issues.

## License

This library is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file.
