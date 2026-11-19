
import json
import os
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["AUDIT_TABLE"]
AUDIT_MONTHS = int(os.environ.get("AUDIT_MONTHS", "12"))
MAX_AUDIT_RECORDS = int(os.environ.get("MAX_AUDIT_RECORDS", "500"))

table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS"
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body)
    }


def parse_groups(value):
    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            return [value]

    return []


def previous_month(year, month):
    if month == 1:
        return year - 1, 12

    return year, month - 1


def get_month_partitions():
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    partitions = []

    for _ in range(AUDIT_MONTHS):
        partitions.append(f"AUDIT#{year:04d}-{month:02d}")
        year, month = previous_month(year, month)

    return partitions


def query_month(partition_key, remaining):
    """
    Query one monthly partition, newest records first.
    Paginate until the requested number of records is collected.
    """

    items = []

    query_kwargs = {
        "KeyConditionExpression": Key("pk").eq(partition_key),
        "ScanIndexForward": False,
        "Limit": min(remaining, 100)
    }

    while len(items) < remaining:
        result = table.query(**query_kwargs)
        items.extend(result.get("Items", []))

        last_key = result.get("LastEvaluatedKey")

        if not last_key:
            break

        query_kwargs["ExclusiveStartKey"] = last_key
        query_kwargs["Limit"] = min(remaining - len(items), 100)

    return items[:remaining]


def handler(event, context):
    try:
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )

        user_id = claims.get("sub")

        if not user_id:
            return response(401, {"message": "Unauthorized"})

        groups = parse_groups(claims.get("cognito:groups", []))

        if "admin" not in groups:
            return response(403, {"message": "Admin access required"})

        if AUDIT_MONTHS < 1 or AUDIT_MONTHS > 60:
            return response(
                500,
                {"message": "Invalid audit retention query configuration"}
            )

        if MAX_AUDIT_RECORDS < 1 or MAX_AUDIT_RECORDS > 5000:
            return response(
                500,
                {"message": "Invalid audit result limit configuration"}
            )

        items = []

        for partition_key in get_month_partitions():
            remaining = MAX_AUDIT_RECORDS - len(items)

            if remaining <= 0:
                break

            month_items = query_month(partition_key, remaining)
            items.extend(month_items)

        # Combine monthly partitions and return the newest records first.
        items.sort(
            key=lambda item: item.get("sk", ""),
            reverse=True
        )

        items = items[:MAX_AUDIT_RECORDS]

        return response(200, items)

    except Exception:
        logger.exception("Failed to retrieve audit logs")
        return response(
            500,
            {"message": "Failed to fetch audit logs"}
        )
