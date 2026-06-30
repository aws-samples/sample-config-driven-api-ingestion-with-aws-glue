# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
API Consumer Glue Job

A config-driven ETL job that:
1. Authenticates to an external REST API via mTLS + OAuth2
2. Paginates through API responses using cursor-based pagination
3. Transforms data according to JSON configuration
4. Validates data types using schema definitions
5. Deduplicates records by primary key
6. Uploads validated CSV output to S3
7. Tracks page-level audit details in DynamoDB
"""

import os
import sys
import tempfile
import requests
import json
import time
import boto3
import pandas as pd
import io
import csv
import re
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from typing import List, Dict, Any, Optional, Union
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from cryptography.hazmat.primitives.serialization import pkcs12
from awsglue.utils import getResolvedOptions


# ─────────────────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────────────────

def get_secret(secret_name: str, region_name: str) -> dict:
    """Retrieve a secret from AWS Secrets Manager."""
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region_name)

    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except ClientError as e:
        print(f"Error fetching secret: {str(e)}")
        raise


def get_access_token(client_id: str, client_secret: str, access_token_url: str) -> str:
    """Obtain an OAuth2 access token using client credentials flow."""
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }

    try:
        response = requests.post(access_token_url, data=payload, timeout=30)
        response.raise_for_status()
        return response.json().get("access_token", "")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching access token: {str(e)}")
        return ""


def download_and_verify_certificate(s3_path: str, cert_password: str) -> Optional[str]:
    """Download a PFX certificate from S3 and verify it."""
    try:
        bucket_name = s3_path.split('/')[2]
        key = '/'.join(s3_path.split('/')[3:])

        temp_cert_file = tempfile.NamedTemporaryFile(suffix='.pfx', delete=False)
        local_cert_path = temp_cert_file.name
        temp_cert_file.close()

        s3_client = boto3.client('s3')
        s3_client.download_file(bucket_name, key, local_cert_path)

        # Verify certificate can be loaded
        with open(local_cert_path, 'rb') as cert_file:
            pkcs12.load_key_and_certificates(
                cert_file.read(),
                cert_password.encode('utf-8')
            )
        return local_cert_path
    except (ClientError, OSError, ValueError) as e:
        print(f"Certificate processing error: {type(e).__name__}: {str(e)}")
        return None


def flatten_json(nested_json: dict) -> dict:
    """Flatten a nested JSON object into a single-level dictionary."""
    flattened = {}

    def _flatten(obj, name=''):
        if isinstance(obj, dict):
            for key, value in obj.items():
                _flatten(value, f"{name}{key}_")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _flatten(item, f"{name}{i}_")
        else:
            key = name[:-1] if name.endswith('_') else name
            flattened[key] = obj

    _flatten(nested_json)
    return flattened


# ─────────────────────────────────────────────────────────────────────────────
# API Client with mTLS + Retry
# ─────────────────────────────────────────────────────────────────────────────

class APIClient:
    """HTTP client with mTLS certificate authentication and retry logic."""

    def __init__(self, cert_path: str, cert_password: str,
                 max_retries: int = 5, backoff_factor: float = 2.0, timeout: int = 60):
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.timeout = timeout

        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

        # Configure mTLS
        self._setup_mtls()

    def _setup_mtls(self):
        """Extract cert and key from PFX file for mTLS."""
        try:
            with open(self.cert_path, 'rb') as f:
                pfx_data = f.read()

            private_key, certificate, _ = pkcs12.load_key_and_certificates(
                pfx_data, self.cert_password.encode('utf-8')
            )

            # Write cert and key to temp files for requests library
            from cryptography.hazmat.primitives import serialization

            self.temp_cert = tempfile.NamedTemporaryFile(suffix='.pem', delete=False)
            self.temp_key = tempfile.NamedTemporaryFile(suffix='.pem', delete=False)

            self.temp_cert.write(certificate.public_bytes(serialization.Encoding.PEM))
            self.temp_cert.close()

            self.temp_key.write(private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()
            ))
            self.temp_key.close()

            self.session.cert = (self.temp_cert.name, self.temp_key.name)
        except (ValueError, OSError, TypeError) as e:
            print(f"Error setting up mTLS: {type(e).__name__}: {str(e)}")
            raise

    def make_request(self, url: str, params: dict = None, headers: dict = None) -> dict:
        """Make an authenticated HTTP GET request."""
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()


# ─────────────────────────────────────────────────────────────────────────────
# S3 Handler
# ─────────────────────────────────────────────────────────────────────────────

class S3Handler:
    """Handles all S3 read/write operations."""

    def __init__(self, staging_bucket_name: str, region: str,
                 original_interface_data_prefix: str = 'raw_data',
                 flattened_interface_data_prefix: str = 'transformed_data',
                 error_data_prefix: str = 'error_data',
                 output_bucket_name: str = None):
        self.staging_bucket = staging_bucket_name
        self.output_bucket = output_bucket_name or staging_bucket_name
        self.region = region
        self.original_prefix = original_interface_data_prefix
        self.flattened_prefix = flattened_interface_data_prefix
        self.error_prefix = error_data_prefix
        self.s3_client = boto3.client('s3', region_name=region)

    def upload_json(self, data: Any, entity_type: str, load_type: str, prefix: str = None) -> str:
        """Upload JSON data to S3."""
        prefix = prefix or self.original_prefix
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"{prefix}/{entity_type}/{load_type}/{timestamp}.json"

        self.s3_client.put_object(
            Bucket=self.staging_bucket,
            Key=s3_key,
            Body=json.dumps(data, default=str),
            ContentType='application/json'
        )
        print(f"Uploaded JSON to s3://{self.staging_bucket}/{s3_key}")
        return s3_key

    def upload_csv(self, df: pd.DataFrame, entity_type: str, table_name: str,
                   load_type: str) -> str:
        """Upload a DataFrame as CSV to the output S3 bucket."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"{self.flattened_prefix}/{entity_type}/{table_name}/{load_type}/{timestamp}.csv"

        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False, quoting=csv.QUOTE_ALL)

        self.s3_client.put_object(
            Bucket=self.output_bucket,
            Key=s3_key,
            Body=csv_buffer.getvalue(),
            ContentType='text/csv'
        )
        print(f"Uploaded CSV ({len(df)} rows) to s3://{self.output_bucket}/{s3_key}")
        return s3_key

    def upload_error_records(self, records: list, entity_type: str, table_name: str) -> str:
        """Upload error records to S3 for review."""
        if not records:
            return ""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"{self.error_prefix}/{entity_type}/{table_name}/{timestamp}.json"

        self.s3_client.put_object(
            Bucket=self.staging_bucket,
            Key=s3_key,
            Body=json.dumps(records, default=str),
            ContentType='application/json'
        )
        print(f"Uploaded {len(records)} error records to s3://{self.staging_bucket}/{s3_key}")
        return s3_key

    @staticmethod
    def get_schema_from_path(schema_path: str) -> dict:
        """Load a schema JSON from S3 or local path."""
        if schema_path.startswith('s3://'):
            parts = schema_path.replace('s3://', '').split('/', 1)
            bucket, key = parts[0], parts[1]
            s3 = boto3.client('s3')
            response = s3.get_object(Bucket=bucket, Key=key)
            return json.loads(response['Body'].read().decode('utf-8'))
        else:
            with open(schema_path, 'r') as f:
                return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB Audit Handler
# ─────────────────────────────────────────────────────────────────────────────

class DynamoDBHandler:
    """Records page-level audit details to DynamoDB."""

    def __init__(self, table_name: str, region: str, load_type: str, ingestion_id: str):
        self.dynamodb = boto3.resource('dynamodb', region_name=region)
        self.table = self.dynamodb.Table(table_name)
        self.load_type = load_type
        self.ingestion_id = ingestion_id

    def put_page_details(self, entity_type: str, page_number: int,
                         next_cursor: str, status: str, timestamp: str,
                         max_records_per_page: int = 0,
                         total_records_consumed: int = 0):
        """Record page-level processing details."""
        entity_type_key = f"{entity_type}_{self.load_type}"
        try:
            self.table.put_item(Item={
                'entity-type': entity_type_key,
                'last-execution-time': timestamp,
                'page_number': page_number,
                'next_cursor': next_cursor,
                'status': status,
                'ingestion_id': self.ingestion_id,
                'max_records_per_page': max_records_per_page,
                'total_records_consumed': total_records_consumed,
                'timestamp_page': timestamp
            })
        except ClientError as e:
            print(f"DynamoDB error writing page details: {e.response['Error']['Code']}: {e.response['Error']['Message']}")

    def get_last_cursor(self, entity_type: str) -> Optional[str]:
        """Retrieve the last successful cursor for resumability.

        Queries the most recent page record for the entity and returns
        its next_cursor value, allowing the job to resume from where it
        left off on a previous run.
        """
        entity_type_key = f"{entity_type}_{self.load_type}"
        try:
            response = self.table.query(
                KeyConditionExpression=Key('entity-type').eq(entity_type_key),
                ScanIndexForward=False,
                Limit=1
            )
            items = response.get('Items', [])
            if items:
                cursor = items[0].get('next_cursor')
                if cursor and cursor not in ('END', 'ERROR'):
                    print(f"Resuming from cursor: {cursor}")
                    return cursor
            return None
        except ClientError as e:
            print(f"DynamoDB error reading cursor: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Data Type Validator
# ─────────────────────────────────────────────────────────────────────────────

class DataTypeValidator:
    """Validates and casts data types based on schema configuration."""

    SUPPORTED_TYPES = {'INT', 'LONG', 'DOUBLE', 'STRING', 'TIMESTAMP'}

    def __init__(self, schema_config: Dict[str, Any]):
        self.schema_fields = {
            field['name']: field['datatype']
            for field in schema_config.get('fields', [])
        }
        self.validation_errors = []

    def validate_and_cast_record(self, record: Dict[str, Any],
                                  strict_mode: bool = False) -> Dict[str, Any]:
        """Validate and cast all fields in a record according to schema."""
        validated_record = {}
        self.validation_errors = []

        for field_name, expected_type in self.schema_fields.items():
            if field_name in record:
                try:
                    validated_record[field_name] = self._cast_value(
                        record[field_name], expected_type, field_name)
                except ValueError as e:
                    self.validation_errors.append(f"Field '{field_name}': {str(e)}")
                    if strict_mode:
                        raise
                    validated_record[field_name] = None
            else:
                validated_record[field_name] = None

        return validated_record

    def _cast_value(self, value: Any, target_type: str, field_name: str) -> Any:
        """Cast a value to the target data type."""
        if value is None or value == '' or value == {} or value == []:
            return None
        if isinstance(value, float) and pd.isna(value):
            return None

        str_value = str(value).strip()
        if str_value.lower() in ['nan', 'null', 'none', 'n/a', '']:
            return None

        if target_type == 'STRING':
            return str_value
        elif target_type == 'INT':
            return int(float(str_value))
        elif target_type == 'LONG':
            return int(float(str_value))
        elif target_type == 'DOUBLE':
            return float(str_value)
        elif target_type == 'TIMESTAMP':
            return str_value  # Return as-is after validation
        else:
            raise ValueError(f"Unsupported type: {target_type}")

    def validate_batch(self, records: List[Dict], strict_mode: bool = False) -> tuple:
        """Validate a batch of records. Returns (validated, errors)."""
        validated, errors = [], []
        for record in records:
            result = self.validate_and_cast_record(record, strict_mode)
            validated.append(result)
            if self.validation_errors:
                error_record = record.copy()
                error_record['_validation_errors'] = self.validation_errors.copy()
                errors.append(error_record)
        return validated, errors


# ─────────────────────────────────────────────────────────────────────────────
# Config-Driven Data Processor
# ─────────────────────────────────────────────────────────────────────────────

class ConfigDrivenDataProcessor:
    """
    Processes API response data according to JSON configuration.

    The config defines:
    - main_table: field mappings, source paths, primary keys, not-null constraints
    - child_tables: nested array extraction with foreign key relationships
    """

    def __init__(self, entity_type: str, entity_config: Dict):
        self.entity_type = entity_type
        self.config = entity_config
        self.etl_source = "api_consumer"
        self.etl_timestamp = datetime.now().isoformat()
        self.etl_ingestion_id = str(int(datetime.now().timestamp() * 1000))
        self.error_records = []
        self.validator = None

    def setup_validator(self, schema_path: str):
        """Initialize the DataTypeValidator with a schema from S3 or local path."""
        schema_config = S3Handler.get_schema_from_path(schema_path)
        self.validator = DataTypeValidator(schema_config)
        print(f"Validator initialized with {len(self.validator.schema_fields)} field definitions")

    def validate_dataframe(self, df: pd.DataFrame, strict_mode: bool = False) -> tuple:
        """Validate all records in a DataFrame using the configured schema.

        Returns:
            tuple: (validated_df, validation_summary, error_records)
        """
        if not self.validator:
            return df, {'has_errors': False, 'total_errors': 0, 'errors': []}, []

        records = df.to_dict('records')
        validated_records, error_records = self.validator.validate_batch(records, strict_mode)

        validated_df = pd.DataFrame(validated_records) if validated_records else pd.DataFrame()

        validation_summary = {
            'has_errors': len(error_records) > 0,
            'total_errors': len(error_records),
            'errors': [r.get('_validation_errors', []) for r in error_records[:5]]
        }

        return validated_df, validation_summary, error_records

    def process_entity(self, data: list) -> Dict[str, pd.DataFrame]:
        """Process entity data into DataFrames based on config."""
        if not data or not self.config:
            return {}

        result = {}
        main_config = self.config.get('main_table', {})
        main_table_name = main_config.get('name', self.entity_type)
        child_configs = self.config.get('child_tables', [])

        data_list = data if isinstance(data, list) else [data]
        all_records = []
        all_child_records = {c.get('name'): [] for c in child_configs if c.get('name')}

        for item in data_list:
            # Process main table
            main_records = self._process_main_table(item, main_config)
            if main_records:
                all_records.extend(main_records)

                # Process child tables
                for main_record in main_records:
                    for child_config in child_configs:
                        child_name = child_config.get('name')
                        if not child_name:
                            continue
                        child_data = self._process_child_table(item, child_config, main_record)
                        if child_data:
                            all_child_records[child_name].extend(child_data)

        # Create and deduplicate DataFrames
        if all_records:
            main_df = pd.DataFrame(all_records)
            pk_columns = main_config.get('primary_key', {}).get('columns', [])
            existing_pk = [c for c in pk_columns if c in main_df.columns]
            if existing_pk:
                main_df = main_df.drop_duplicates(subset=existing_pk, keep='first')
            result[main_table_name] = main_df

        for child_name, child_records in all_child_records.items():
            if child_records:
                child_df = pd.DataFrame(child_records)
                child_df = child_df.drop_duplicates(keep='first')
                result[child_name] = child_df

        return result

    def _process_main_table(self, item: dict, config: dict) -> list:
        """Extract main table records with multi-source merging support.

        When is_multi_source is True, iterates all source_paths and merges
        their field values into a single record per primary key. Fields from
        later source paths fill in values that earlier paths left empty.
        """
        records = []
        fields = config.get('fields', [])
        source_paths = config.get('source_paths', [{'path': ''}])
        not_null = config.get('not_null', [])
        primary_key = config.get('primary_key', {}).get('columns', [])
        is_multi_source = config.get('is_multi_source', False)

        if is_multi_source and len(source_paths) > 1:
            # Multi-source: merge data from all source paths into one record
            # Use a dict keyed by primary key to accumulate merged data
            merged_records = {}  # key -> merged field dict

            for source_path_config in source_paths:
                path = source_path_config.get('path', '')
                source_data = self._get_nested_value(item, path) if path else item

                if source_data is None:
                    continue

                # Normalize to list for uniform handling
                source_items = source_data if isinstance(source_data, list) else [source_data]

                for sub_item in source_items:
                    # Extract fields from this source
                    extracted = self._extract_fields(sub_item, fields, item)

                    # Determine the primary key value for this record
                    pk_values = tuple(
                        str(extracted.get(pk) or self._get_nested_value(item, '') and item.get(
                            next((f.get('source', f['name']) for f in fields if f['name'] == pk), pk)
                        ) or '')
                        for pk in primary_key
                    ) if primary_key else ('_single_',)

                    if pk_values not in merged_records:
                        merged_records[pk_values] = {}

                    # Merge: fill in non-null values from this source
                    self._merge_source_data(merged_records[pk_values], extracted)

            # Now create final records from the merged data
            for pk_values, merged_data in merged_records.items():
                # Fill any remaining None primary key fields from root
                for pk_field in primary_key:
                    if pk_field not in merged_data or merged_data[pk_field] is None:
                        source_name = next(
                            (f.get('source', f['name']) for f in fields if f['name'] == pk_field),
                            pk_field
                        )
                        root_value = item.get(source_name)
                        if root_value is not None:
                            merged_data[pk_field] = root_value

                if self._passes_not_null(merged_data, not_null):
                    record = self._add_metadata(merged_data)
                    records.append(record)

        else:
            # Single source processing (original logic)
            for source_path_config in source_paths:
                path = source_path_config.get('path', '')
                source_data = self._get_nested_value(item, path) if path else item

                if source_data is None:
                    continue

                if isinstance(source_data, list):
                    for sub_item in source_data:
                        record = self._extract_fields(sub_item, fields, item)
                        if self._passes_not_null(record, not_null):
                            record = self._add_metadata(record)
                            records.append(record)
                else:
                    record = self._extract_fields(source_data, fields, item)
                    if self._passes_not_null(record, not_null):
                        record = self._add_metadata(record)
                        records.append(record)

        return records

    def _merge_source_data(self, target: dict, source: dict):
        """Merge source field values into target, preserving non-null values.

        Only overwrites a field in target if the target's current value is None
        and the source has a non-null value. This ensures earlier sources take
        precedence, and later sources fill gaps.
        """
        for key, value in source.items():
            if value is not None and (key not in target or target[key] is None):
                target[key] = value

    def _process_child_table(self, item: dict, config: dict, main_record: dict) -> list:
        """Extract child table records from an item."""
        records = []
        source_path = config.get('source_path', '')
        fields = config.get('fields', [])

        source_data = self._get_nested_value(item, source_path) if source_path else item
        if not source_data or not isinstance(source_data, list):
            return records

        for child_item in source_data:
            record = self._extract_fields(child_item, fields, item)
            # Copy parent field values from main record
            for field in fields:
                if field.get('parent_field') and field['name'] in main_record:
                    record[field['name']] = main_record[field['name']]
            record = self._add_metadata(record)
            records.append(record)

        return records

    def _extract_fields(self, data: dict, fields: list, root_data: dict = None) -> dict:
        """Extract field values from data based on field config."""
        record = {}
        for field in fields:
            name = field.get('name')
            source = field.get('source', name)
            default = field.get('default')

            value = data.get(source) if data else None
            if value is None and root_data:
                value = root_data.get(source)
            if value is None and default is not None:
                value = default

            record[name] = value
        return record

    def _get_nested_value(self, obj: dict, path: str):
        """Get a value from a nested dict using dot notation."""
        if not path:
            return obj
        parts = path.split('.')
        current = obj
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _passes_not_null(self, record: dict, not_null_cols: list) -> bool:
        """Check if all required fields have values."""
        for col in not_null_cols:
            if col not in record or record[col] is None:
                return False
        return True

    def _add_metadata(self, record: dict) -> dict:
        """Add ETL metadata fields."""
        record['etl_source'] = self.etl_source
        record['etl_ingestion_id'] = self.etl_ingestion_id
        record['etl_inserted_timestamp'] = self.etl_timestamp
        return record


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading (Paginated API Fetch)
# ─────────────────────────────────────────────────────────────────────────────

def load_data(api_client: APIClient, endpoint: str, parameters: dict,
              access_token: str, entity_type: str,
              dynamodb_handler: DynamoDBHandler = None,
              start_cursor: str = None) -> dict:
    """
    Fetch all pages of data from a paginated API endpoint.

    Supports:
    - Cursor-based pagination (response_metadata.next_cursor)
    - Automatic token refresh on 401
    - Circuit breaker with 5-minute cooldown on consecutive failures
    - Resume from a previous cursor (bookmarking)
    """
    all_data = []

    def refresh_access_token():
        secret_name = os.environ.get('SECRET_NAME')
        region = os.environ.get('REGION')
        token_url = os.environ.get('TOKEN_URL')
        secrets = get_secret(secret_name, region)
        return get_access_token(secrets['client_id'], secrets['client_secret'], token_url)

    def process_page(current_endpoint, current_params, current_headers):
        """Process a single page with retry logic and exponential backoff."""
        nonlocal access_token
        max_retries = 5

        for retry_count in range(max_retries):
            try:
                response = api_client.make_request(
                    current_endpoint, params=current_params, headers=current_headers)

                if not response:
                    return None, None, "FAILED"

                next_cursor = None
                if 'response_metadata' in response and 'next_cursor' in response['response_metadata']:
                    next_cursor = response['response_metadata']['next_cursor']

                return response, next_cursor, "SUCCESS"

            except requests.exceptions.HTTPError as e:
                if hasattr(e, 'response') and e.response.status_code == 401:
                    print("Token expired, refreshing...")
                    access_token = refresh_access_token()
                    current_headers['Authorization'] = f'Bearer {access_token}'
                    continue
                elif hasattr(e, 'response') and e.response.status_code >= 500:
                    wait_time = (2 ** (retry_count + 1)) * 5  # 10s, 20s, 40s, 80s, 160s
                    print(f"Server error {e.response.status_code}, waiting {wait_time}s (retry {retry_count + 1}/{max_retries})...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"HTTP Error: {str(e)}")
                    return None, None, "FAILED"
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, OSError) as e:
                wait_time = (2 ** (retry_count + 1)) * 5
                print(f"Connection error: {type(e).__name__}: {str(e)}, waiting {wait_time}s (retry {retry_count + 1}/{max_retries})...")
                time.sleep(wait_time)
                continue

        return None, None, "FAILED"

    # Main pagination loop
    page_count = 1
    next_cursor = start_cursor  # Resume from provided cursor if available
    headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
    consecutive_failures = 0
    max_consecutive_failures = 5
    circuit_breaker_cooldown = 300  # 5 minutes

    if next_cursor:
        print(f"Resuming pagination from cursor: {next_cursor}")

    while True:
        # Circuit breaker: on N consecutive failures, cool down then retry
        if consecutive_failures >= max_consecutive_failures:
            print(f"Circuit breaker activated: {consecutive_failures} consecutive failures. "
                  f"Cooling down for {circuit_breaker_cooldown}s...")
            time.sleep(circuit_breaker_cooldown)
            consecutive_failures = 0

        current_params = parameters.copy()
        if next_cursor:
            current_params['cursor'] = next_cursor

        # Preserve current cursor before the call so a failure doesn't wipe it
        previous_cursor = next_cursor

        response_data, new_cursor, status = process_page(endpoint, current_params, headers)

        if not response_data or status == "FAILED":
            consecutive_failures += 1
            # Restore cursor so retry fetches the same page, not page 1
            next_cursor = previous_cursor
            print(f"Failed to fetch page {page_count} (consecutive failures: {consecutive_failures})")
            wait_time = min(60, consecutive_failures * 10)
            print(f"Waiting {wait_time}s before retrying page {page_count}...")
            time.sleep(wait_time)
            continue
        else:
            consecutive_failures = 0
            next_cursor = new_cursor

        page_data = response_data.get('data', [])
        if page_data:
            all_data.extend(page_data)
            print(f"Page {page_count}: {len(page_data)} records fetched (total: {len(all_data)})")

        if dynamodb_handler:
            sort_key = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{page_count}"
            dynamodb_handler.put_page_details(
                entity_type=entity_type,
                page_number=page_count,
                next_cursor=next_cursor if next_cursor else "END",
                status=status,
                timestamp=sort_key,
                max_records_per_page=len(page_data),
                total_records_consumed=len(all_data)
            )

        if not next_cursor:
            print(f"Pagination complete. {page_count} pages, {len(all_data)} total records.")
            break

        page_count += 1
        time.sleep(2)

    return {
        "metadata": {"total_pages": page_count, "total_records": len(all_data)},
        "data": all_data
    }


# ─────────────────────────────────────────────────────────────────────────────
# Processing & Storage
# ─────────────────────────────────────────────────────────────────────────────

def process_and_store_to_s3(data: dict, s3_handler: S3Handler,
                            entity_type: str, load_type: str, config_path: str):
    """Transform, validate, and upload data to S3.

    1. Stores original JSON to S3
    2. Processes data using config-driven processor
    3. Validates each table against schema (if schema exists)
    4. Uploads validated CSVs to S3
    5. Uploads error records to S3 error path
    """
    # Load entity configuration
    config_data = S3Handler.get_schema_from_path(config_path)
    entity_config = config_data.get('entities', {}).get(entity_type)

    if not entity_config:
        print(f"No configuration found for entity: {entity_type}")
        return

    # Store original JSON
    s3_handler.upload_json(data['data'], entity_type, load_type)

    # Process data using config
    processor = ConfigDrivenDataProcessor(entity_type, entity_config)

    # Setup validator if schema file exists
    schema_file_map = {
        'product': 'product_schema.example.json',
        'order': 'order_schema.json',
        'vendor': 'vendor_schema.json'
    }
    schema_filename = schema_file_map.get(entity_type.lower())
    if schema_filename:
        try:
            if config_path.startswith('s3://'):
                config_s3_path = config_path.replace('s3://', '')
                bucket_name = config_s3_path.split('/')[0]
                base_path = '/'.join(config_s3_path.split('/')[1:-1])
                schema_path = f"s3://{bucket_name}/{base_path}/schemas/{schema_filename}"
            else:
                config_dir = os.path.dirname(config_path)
                schema_path = os.path.join(config_dir, 'schemas', schema_filename)
            processor.setup_validator(schema_path)
        except Exception as e:
            print(f"Schema not found or invalid for {entity_type}, skipping validation: {str(e)}")

    tables = processor.process_entity(data['data'])

    # Validate and upload each table
    for table_name, df in tables.items():
        if df.empty:
            continue

        # Run validation if validator is configured
        if processor.validator:
            validated_df, validation_summary, error_records = processor.validate_dataframe(df)

            if validation_summary['has_errors']:
                print(f"Table {table_name}: {validation_summary['total_errors']} validation errors")
                # Upload error records to S3 error path
                if error_records:
                    s3_handler.upload_error_records(error_records, entity_type, f"{table_name}_errors")

            # Upload the validated (cleaned) DataFrame
            s3_handler.upload_csv(validated_df, entity_type, table_name, load_type)
        else:
            # No validator — upload as-is
            s3_handler.upload_csv(df, entity_type, table_name, load_type)

    # Upload processing-level error records (missing required fields, etc.)
    if processor.error_records:
        s3_handler.upload_error_records(processor.error_records, entity_type, "processing_errors")


# ─────────────────────────────────────────────────────────────────────────────
# Glue Job Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """AWS Glue Python Shell job entry point."""
    try:
        args = getResolvedOptions(sys.argv, [
            'entity_type',
            'endpoint',
            'params',
            'load_type',
            'REGION',
            'STAGING_BUCKET_NAME',
            'SECRET_NAME',
            'TOKEN_URL',
            'CERT_PATH',
            'DYNAMODB_DETAIL_TABLE',
            'ingestion_id',
            'ORIGINAL_INTERFACE_DATA_PREFIX',
            'FLATTENED_INTERFACE_DATA_PREFIX',
            'CONFIG_PATH'
        ])

        # Optional: resume cursor (passed when restarting a failed job)
        try:
            optional_args = getResolvedOptions(sys.argv, ['start_cursor'])
            start_cursor = optional_args.get('start_cursor')
        except Exception:
            start_cursor = None

        entity_type = args['entity_type']
        entity_endpoint = args['endpoint']

        # Parse parameters
        try:
            entity_params = json.loads(args['params'].replace('\\"', '"'))
        except json.JSONDecodeError:
            entity_params = {}

        print(f"Processing entity: {entity_type}")
        print(f"Parameters: {entity_params}")

        # Store env vars for token refresh
        os.environ['SECRET_NAME'] = args['SECRET_NAME']
        os.environ['REGION'] = args['REGION']
        os.environ['TOKEN_URL'] = args['TOKEN_URL']

        # Get API credentials
        secrets = get_secret(args['SECRET_NAME'], args['REGION'])
        client_id = secrets['client_id']
        client_secret = secrets['client_secrets']
        cert_passphrase = secrets['cert_password']

        # Verify certificate
        local_cert_path = download_and_verify_certificate(args['CERT_PATH'], cert_passphrase)
        if not local_cert_path:
            raise Exception("Certificate verification failed")

        # Initialize API client
        api_client = APIClient(
            cert_path=local_cert_path,
            cert_password=cert_passphrase,
            max_retries=5,
            backoff_factor=2.0,
            timeout=60
        )

        # Get access token
        access_token = get_access_token(client_id, client_secret, args['TOKEN_URL'])
        if not access_token:
            raise Exception("Failed to obtain access token")

        # Initialize handlers
        s3_handler = S3Handler(
            staging_bucket_name=args['STAGING_BUCKET_NAME'],
            region=args['REGION'],
            original_interface_data_prefix=args['ORIGINAL_INTERFACE_DATA_PREFIX'],
            flattened_interface_data_prefix=args['FLATTENED_INTERFACE_DATA_PREFIX'],
            error_data_prefix='error_data'
        )

        dynamodb_handler = DynamoDBHandler(
            table_name=args['DYNAMODB_DETAIL_TABLE'],
            region=args['REGION'],
            load_type=args['load_type'],
            ingestion_id=args['ingestion_id']
        )

        # If no explicit start_cursor provided, try to resume from DynamoDB
        resume_cursor = start_cursor
        if not resume_cursor:
            resume_cursor = dynamodb_handler.get_last_cursor(entity_type)

        # Fetch data from API
        entity_data = load_data(
            api_client=api_client,
            endpoint=entity_endpoint,
            parameters=entity_params,
            access_token=access_token,
            entity_type=entity_type,
            dynamodb_handler=dynamodb_handler,
            start_cursor=resume_cursor
        )

        # Process and store
        if entity_data.get('data'):
            process_and_store_to_s3(
                data=entity_data,
                s3_handler=s3_handler,
                entity_type=entity_type,
                load_type=args['load_type'],
                config_path=args['CONFIG_PATH']
            )
            print(f"Job completed: {entity_data['metadata']['total_records']} records processed")
        else:
            print("No data found for processing")

    except (ClientError, requests.exceptions.RequestException, ValueError, json.JSONDecodeError) as e:
        print(f"Error in main execution: {type(e).__name__}: {str(e)}")
        raise


if __name__ == '__main__':
    main()
