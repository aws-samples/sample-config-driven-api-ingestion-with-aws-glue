# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Post-processing Lambda Function

Aggregates Glue job results and records execution summaries in DynamoDB.
"""

import boto3
from typing import Dict, List, Any
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from decimal import Decimal
import json
import os


def decimal_to_int(obj):
    """Convert Decimal objects to int for JSON serialization."""
    if isinstance(obj, list):
        return [decimal_to_int(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: decimal_to_int(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return int(obj)
    return obj


class DynamoDBHandler:
    """Handles DynamoDB operations for audit tracking."""

    def __init__(self, detail_table_name: str, summary_table_name: str, region: str):
        self.dynamodb = boto3.resource('dynamodb', region_name=region)
        self.detail_table = self.dynamodb.Table(detail_table_name)
        self.summary_table = self.dynamodb.Table(summary_table_name)

    def get_latest_entries(self, entity_type: str, load_type: str) -> List[Dict[str, Any]]:
        """Fetch latest entries for given entity_type_load_type from detail table."""
        try:
            entity_type_key = f"{entity_type}_{load_type}"
            response = self.detail_table.query(
                KeyConditionExpression=Key('entity-type').eq(entity_type_key),
                ScanIndexForward=False,
                Limit=1
            )
            return response.get('Items', [])
        except ClientError as e:
            print(f"DynamoDB error fetching latest entries: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
            raise

    def put_summary(self, entries: List[Dict[str, Any]]) -> bool:
        """Store summarized entries in summary table."""
        try:
            with self.summary_table.batch_writer() as batch:
                for entry in entries:
                    entity_type_key = f"{entry['entity_type']}_{entry['load_type']}"
                    summary_item = {
                        'entity-type': entity_type_key,
                        'last-execution-time': entry.get('last_processed_timestamp', 'NONE'),
                        'ingestion_id': entry.get('ingestion_id', 'NONE'),
                        'last_page_scanned': entry.get('last_page_scanned', 0),
                        'max_records_per_page': entry.get('max_records_per_page', 0),
                        'total_records_consumed': entry.get('total_records_consumed', 0),
                        'job_run_id': entry['job_run_id'],
                        'job_run_state': entry['job_run_state'],
                        'timestamp_page': entry.get('timestamp_page', 'NONE'),
                        'error_message': entry.get('error_message', '')
                    }
                    batch.put_item(Item=summary_item)
            return True
        except ClientError as e:
            print(f"DynamoDB error storing summary: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
            return False


def lambda_handler(event, context):
    """Process Glue job results and record summaries in DynamoDB."""
    try:
        print(f"Received event: {json.dumps(event)}")

        # Get environment variables
        detail_table = os.environ['DETAIL_AUDIT_TABLE']
        summary_table = os.environ['SUMMARY_AUDIT_TABLE']
        region = os.environ['AWS_REGION']

        # Initialize DynamoDB handler
        dynamo_handler = DynamoDBHandler(
            detail_table_name=detail_table,
            summary_table_name=summary_table,
            region=region
        )

        # Handle either single entity or multiple entities in mapResults
        entities_to_process = event.get("input", {}).get("mapResults", [])
        parsed_entities = event.get("input", {}).get("parsedBody", {}).get("entities", [])

        # Create a mapping of entity types from parsedBody
        entity_type_map = {}
        for parsed_entity in parsed_entities:
            if parsed_entity.get('entity_type') and parsed_entity.get('endpoint'):
                entity_type_map[parsed_entity.get('endpoint')] = parsed_entity.get('entity_type')

        summaries = []

        for entity in entities_to_process:
            try:
                entity_type = entity.get('entity_type')
                if not entity_type:
                    endpoint = entity.get('endpoint')
                    if endpoint and endpoint in entity_type_map:
                        entity_type = entity_type_map[endpoint]
                    else:
                        glue_result = entity.get('glueJobResult', {})
                        arguments = glue_result.get('Arguments', {})
                        entity_type = arguments.get('--entity_type')

                if not entity_type:
                    print("Missing entity_type - skipping")
                    continue

                # Handle job status and details
                glue_result = entity.get('glueJobResult', {})
                job_run_id = glue_result.get('Id', 'UNKNOWN')

                if entity.get('status') == 'FAILED':
                    job_run_state = 'FAILED'
                    error_details = entity.get('errorDetails', {})
                    error_message = error_details.get('Cause', error_details.get('Error', 'Unknown error'))
                else:
                    job_run_state = glue_result.get('JobRunState', 'SUCCEEDED')
                    error_message = glue_result.get('ErrorMessage', '')

                ingestion_id = 'UNKNOWN'
                if glue_result and 'Arguments' in glue_result:
                    ingestion_id = glue_result['Arguments'].get('--ingestion_id', 'UNKNOWN')

                load_type = entity.get('load_type')
                if not load_type and glue_result and 'Arguments' in glue_result:
                    load_type = glue_result['Arguments'].get('--load_type', 'UNKNOWN')
                if not load_type:
                    # Fall back: try to find load_type from the parsed entities config
                    for pe in parsed_entities:
                        if pe.get('entity_type') == entity_type:
                            load_type = pe.get('load_type', 'UNKNOWN')
                            break
                    else:
                        load_type = 'UNKNOWN'

                # Get latest entries for the entity type
                latest_entries = dynamo_handler.get_latest_entries(entity_type, load_type)

                if latest_entries:
                    latest_entry = latest_entries[0]
                else:
                    # No detail entries (job failed before writing any pages)
                    # Still write a summary so the failure is visible
                    print(f"No detail entries for {entity_type} — recording failure summary")
                    latest_entry = {}

                # Create summary record
                summary = {
                    'entity_type': entity_type,
                    'load_type': load_type,
                    'last_processed_timestamp': latest_entry.get('last-execution-time', 'NONE'),
                    'ingestion_id': ingestion_id,
                    'last_page_scanned': latest_entry.get('page_number', 0),
                    'max_records_per_page': latest_entry.get('max_records_per_page', 0),
                    'total_records_consumed': latest_entry.get('total_records_consumed', 0),
                    'job_run_id': job_run_id,
                    'job_run_state': job_run_state,
                    'timestamp_page': latest_entry.get('timestamp_page', 'NONE'),
                    'error_message': error_message
                }

                if dynamo_handler.put_summary([summary]):
                    summaries.append(summary)
                    print(f"Created summary for {entity_type}: state={job_run_state}, run_id={job_run_id}")
                else:
                    print(f"Failed to store summary for {entity_type}")

            except (ClientError, KeyError, TypeError) as e:
                print(f"Error processing entity {entity.get('entity_type', 'unknown')}: {type(e).__name__}: {str(e)}")
                continue

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Successfully processed and stored summaries',
                'entries_processed': len(summaries),
                'summaries': decimal_to_int(summaries)
            })
        }

    except (KeyError, ValueError) as e:
        print(f"Configuration error in lambda execution: {type(e).__name__}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal processing error. Check CloudWatch logs for details.'
            })
        }
    except ClientError as e:
        print(f"AWS service error in lambda execution: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal processing error. Check CloudWatch logs for details.'
            })
        }
