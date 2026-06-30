# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Pre-processing Lambda Function

Validates input parameters, maps entity types to API endpoints,
and initializes configuration for each entity based on load type.
"""

import os
import json
from typing import Dict, Any


def validate_environment_variables(api_config: Dict[str, str]) -> None:
    """
    Validates that all required environment variables are set.

    :param api_config: Dictionary containing API configuration
    :raises ValueError: If any required environment variables are missing
    """
    missing_vars = [key for key, value in api_config.items() if not value]
    if missing_vars:
        raise ValueError(f"Missing environment variables: {missing_vars}")


def initialize_params(load_type: str, entity_config: Dict[str, Dict]) -> Dict[str, Dict]:
    """
    Initializes parameters for each entity based on load type and entity configuration.

    :param load_type: Type of the load ('Incremental' or 'Historical')
    :param entity_config: A dictionary containing the entity configuration

    :return: Dictionary of initialized parameters for each entity
    """
    processed_config: Dict[str, Dict] = {}

    if load_type not in ['Historical', 'Incremental']:
        raise ValueError(f"Invalid load_type: {load_type}. Must be either 'Historical' or 'Incremental'")

    for entity, config in entity_config.items():
        if 'params' not in config:
            raise ValueError(f"Missing 'params' configuration for entity '{entity}'")

        entity_params = config['params'].copy()

        if load_type == 'Incremental':
            if 'deltaHours' not in entity_params:
                print(f"Warning: deltaHours not specified for {entity}, defaulting to 1")
                entity_params['deltaHours'] = 1

        processed_config[entity] = {
            'params': entity_params,
        }

    return processed_config


def get_endpoint_mapping(entity_type: str, api_config: Dict[str, str]) -> str:
    """
    Maps entity type to corresponding endpoint from API_CONFIG.

    :param entity_type: Type of the entity
    :param api_config: Dictionary containing API endpoints
    :return: Corresponding endpoint URL
    """
    # Build endpoint mapping from environment variables
    # Customize this mapping for your API structure
    endpoint_mapping = {}
    for key, value in api_config.items():
        # Convert env var names to entity types (e.g., FACILITY_ENDPOINT -> facility)
        entity_name = key.replace('_ENDPOINT', '').lower()
        endpoint_mapping[entity_name] = value

    endpoint = endpoint_mapping.get(entity_type)
    if not endpoint:
        raise ValueError(f"No endpoint configured for entity type: {entity_type}")

    return endpoint


def lambda_handler(event, context):
    """
    Lambda handler for pre-processing step.

    Expected event format:
    {
        "loadType": "Incremental" | "Historical",
        "entity_config": {
            "entity_name": {
                "params": { ... }
            }
        }
    }
    """
    try:
        # Read the load_type and entity_config from the event input
        load_type = event.get('loadType')
        entity_configs = event.get('entity_config', [])

        # Ensure that load_type and entity_configs are provided in the event
        if not load_type or not entity_configs:
            raise ValueError("Both 'loadType' and 'entity_config' are required in the event input.")

        # Build API config from environment variables
        # Add/modify these based on your API entities
        API_CONFIG = {}
        for key, value in os.environ.items():
            if key.endswith('_ENDPOINT'):
                API_CONFIG[key] = value

        # Validate that all required environment variables are set
        validate_environment_variables(API_CONFIG)

        parameters = initialize_params(load_type, entity_configs)

        # Prepare response with endpoints
        entities_with_endpoints = [
            {
                'entity_type': entity,
                'endpoint': get_endpoint_mapping(entity, API_CONFIG),
                'params': config['params'],
                'load_type': load_type
            } for entity, config in parameters.items()
        ]

        return {
            'statusCode': 200,
            'body': json.dumps({
                'entities': entities_with_endpoints
            })
        }

    except ValueError as ve:
        print(f"Validation error: {type(ve).__name__}: {str(ve)}")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Invalid input parameters. Check CloudWatch logs for details.'
            })
        }
    except KeyError as e:
        print(f"Configuration error: missing key {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal configuration error. Check CloudWatch logs for details.'
            })
        }
    except TypeError as e:
        print(f"Type error in processing: {type(e).__name__}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal processing error. Check CloudWatch logs for details.'
            })
        }
