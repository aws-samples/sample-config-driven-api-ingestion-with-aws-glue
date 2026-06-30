# Configuration Guide

## Overview

The pipeline is driven by two configuration files:

1. **`config/config.json`** — Entity ER model (field mappings, tables, relationships)
2. **`config/schemas/<entity>.json`** — Data type validation schemas (optional)

## Config Structure

```json
{
  "entities": {
    "<entity_name>": {
      "main_table": { ... },
      "child_tables": [ ... ]
    }
  }
}
```

## Main Table Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | string | Yes | Output table/file name |
| `is_multi_source` | boolean | No | Whether fields come from multiple response sections |
| `source_paths` | array | Yes | Where to find data in the API response |
| `primary_key.columns` | array | Yes | Columns for deduplication |
| `not_null` | array | No | Required fields — record skipped if missing |
| `fields` | array | Yes | Field mapping definitions |

## Field Properties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | string | Yes | Target column name in output |
| `source` | string | Yes | Field name in API response JSON |
| `default` | any | No | Default value if field missing/null |
| `parent_field` | boolean | No | Inherit value from parent table (child tables only) |

## Source Paths

### Multi-source merge (`is_multi_source: true`)

When `source_paths` has multiple entries, the processor merges data from all paths into **one record per primary key**:

```json
"is_multi_source": true,
"source_paths": [
  {"path": "BasicInformation"},
  {"path": "ActivationInformation"},
  {"path": "HistoricInformation"}
]
```

**How it works:**

1. The processor iterates each source path and extracts field values from that section of the API response.
2. All extracted values are merged into a single record, keyed by the configured `primary_key`.
3. The first non-null value wins — if `BasicInformation` provides `ProductName`, a later path won't overwrite it. But if `BasicInformation` doesn't have `LastModifiedDate` and `HistoricInformation` does, the value from `HistoricInformation` fills the gap.
4. The final merged record is validated against `not_null` constraints before being included in output.

**Example:** Given this API response:

```json
{
  "ProductID": "P001",
  "BasicInformation": {"ProductID": "P001", "ProductName": "Widget"},
  "ActivationInformation": {"ProductID": "P001", "StatusCode": "ACTIVE"},
  "HistoricInformation": {"ProductID": "P001", "LastModifiedDate": "2024-11-20"}
}
```

The processor produces **one row** with `ProductName`, `StatusCode`, and `LastModifiedDate` all populated.

### Single source (`is_multi_source: false` or omitted)

When `source_paths` has a single entry (or an empty path), the record itself is the source:

```json
"source_paths": [{"path": ""}]
```

If the single path points to an array, one output row is created per array element.

## Child Tables

Child tables extract nested arrays from the API response:

```json
{
  "name": "product_location",
  "source_path": "Addresses",
  "foreign_key": {
    "columns": ["product_id"],
    "references": "product"
  },
  "fields": [
    {"name": "product_id", "source": "ProductID", "parent_field": true},
    {"name": "city", "source": "City"}
  ]
}
```

- `source_path` — the array key in the parent object
- `foreign_key` — documents the relationship (used for deduplication context)
- `parent_field: true` — copies the value from the parent main table record

## Schema Validation Files

Optional but recommended. Define expected data types for each output column:

```json
{
  "name": "product_schema",
  "fields": [
    {"name": "product_id", "datatype": "STRING"},
    {"name": "sequence_id", "datatype": "INT"},
    {"name": "latitude", "datatype": "DOUBLE"},
    {"name": "last_modified", "datatype": "TIMESTAMP"}
  ]
}
```

**Supported types**: `STRING`, `INT`, `LONG`, `DOUBLE`, `TIMESTAMP`

**Behavior:**

- Schema files are loaded from `config/schemas/<entity>_schema.json` (in S3 alongside the config).
- Each record is validated and cast to the declared types after processing.
- Records that fail casting (e.g., a non-numeric value in an `INT` field) have the invalid field set to `null`. The original record plus its validation errors are written to the S3 error path (`error_data/<entity>/`).
- Validation never blocks the pipeline — valid fields are still written to the output CSV.

## Expected API Response Format

The Glue job expects your API to return:

```json
{
  "data": [ ... ],
  "response_metadata": {
    "next_cursor": "abc123"
  }
}
```

- `data` — Array of entity records
- `response_metadata.next_cursor` — Next page cursor (null/absent = last page)

If your API uses a different structure, modify `load_data()` in `src/glue/api_consumer_glue.py`.

## Pagination and Resumability

The Glue job supports cursor-based pagination with automatic resume:

- **Page tracking:** Each page's cursor, record count, and status are written to DynamoDB as they are consumed.
- **Resume on failure:** On the next run, the job queries DynamoDB for the last successful cursor and resumes from that page (instead of starting from page 1).
- **Manual override:** Pass `--start_cursor <value>` as a Glue job argument to resume from a specific cursor.

### Retry and circuit breaker

- **Per-page retries:** Each page is retried up to 5 times with exponential backoff (10s, 20s, 40s, 80s, 160s) for server errors (5xx).
- **Outer-loop linear backoff:** If a page still fails after inner retries, the outer loop waits (10s, 20s, ... up to 60s) before re-attempting.
- **Circuit breaker:** After 5 consecutive outer-loop failures, the job pauses for 5 minutes (cooldown), resets the failure counter, and tries again. This prevents hammering a degraded API.
- **Token refresh:** 401 responses trigger automatic OAuth2 token refresh without counting as a failure.
