
import json
import os
import logging

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")

TASK_TABLE_NAME = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE_NAME = os.environ["TASK_MEMBERSHIP_TABLE"]

# These must match the actual index names created on TaskMembership.
USER_TASKS_INDEX = os.environ.get("USER_TASKS_INDEX", "GSI1")
ADMIN_TASKS_INDEX = os.environ.get("ADMIN_TASKS_INDEX", "GSI2")

task_table = dynamodb.Table(TASK_TABLE_NAME)
membership_table = dynamodb.Table(MEMBERSHIP_TABLE_NAME)

deserializer = TypeDeserializer()

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


def decimal_to_native(obj):
    from decimal import Decimal

    if isinstance(obj, list):
        return [decimal_to_native(item) for item in obj]

    if isinstance(obj, dict):
        return {
            key: decimal_to_native(value)
            for key, value in obj.items()
        }

    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)

    return obj


def get_all_memberships(index_name, partition_key, partition_value):
    """
    Query every page from a DynamoDB GSI partition.
    """

    items = []
    query_kwargs = {
        "IndexName": index_name,
        "KeyConditionExpression": Key(partition_key).eq(partition_value)
    }

    while True:
        result = membership_table.query(**query_kwargs)
        items.extend(result.get("Items", []))

        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break

        query_kwargs["ExclusiveStartKey"] = last_key

    return items


def get_task_ids(membership_items):
    """
    Extract unique task IDs from membership records.
    """

    task_ids = set()

    for item in membership_items:
        task_id = item.get("taskId")

        if not task_id:
            pk = item.get("pk", "")
            if pk.startswith("TASK#"):
                task_id = pk.split("#", 1)[1]

        if task_id:
            task_ids.add(str(task_id))

    return list(task_ids)


def batch_get_tasks(task_ids):
    """
    Retrieve task metadata using DynamoDB BatchGetItem.
    Handles batches of 100 and retries unprocessed keys.
    """

    tasks = []

    for start in range(0, len(task_ids), 100):
        batch_ids = task_ids[start:start + 100]

        keys = [
            {
                "pk": {"S": f"TASK#{task_id}"},
                "sk": {"S": "META"}
            }
            for task_id in batch_ids
        ]

        request_items = {
            TASK_TABLE_NAME: {
                "Keys": keys
            }
        }

        attempts = 0

        while request_items and attempts < 5:
            result = client.batch_get_item(
                RequestItems=request_items
            )

            raw_items = result.get("Responses", {}).get(
                TASK_TABLE_NAME, []
            )

            for raw_item in raw_items:
                item = {
                    key: deserializer.deserialize(value)
                    for key, value in raw_item.items()
                }
                tasks.append(item)

            request_items = result.get("UnprocessedKeys", {})
            attempts += 1

        if request_items:
            raise RuntimeError(
                "DynamoDB could not process all task retrieval keys."
            )

    return tasks


def get_user_tasks(user_id):
    """
    Retrieve tasks belonging to a user through GSI1.
    """

    memberships = get_all_memberships(
        index_name=USER_TASKS_INDEX,
        partition_key="gsi1pk",
        partition_value=f"USER#{user_id}"
    )

    task_ids = get_task_ids(memberships)
    return batch_get_tasks(task_ids)


def get_admin_tasks():
    """
    Retrieve all tasks through the four admin GSI2 shards.
    """

    memberships = []

    for shard in range(4):
        shard_items = get_all_memberships(
            index_name=ADMIN_TASKS_INDEX,
            partition_key="gsi2pk",
            partition_value=f"ADMIN#{shard}"
        )

        memberships.extend(shard_items)

    task_ids = get_task_ids(memberships)
    return batch_get_tasks(task_ids)


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

        groups = claims.get("cognito:groups", [])

        if isinstance(groups, str):
            try:
                groups = json.loads(groups)
            except json.JSONDecodeError:
                groups = [groups]

        is_admin = "admin" in groups

        if is_admin:
            tasks = get_admin_tasks()
        else:
            tasks = get_user_tasks(user_id)

        # Exclude soft-deleted tasks.
        tasks = [
            task for task in tasks
            if not task.get("isDeleted", False)
        ]

        # Preserve the frontend's expected task ID field.
        for task in tasks:
            task["id"] = task.get("taskId")

        # Sort newest updates first.
        tasks.sort(
            key=lambda task: task.get(
                "updatedAt",
                task.get("createdAt", "")
            ),
            reverse=True
        )

        return response(200, decimal_to_native(tasks))

    except Exception:
        logger.exception("Failed to retrieve tasks")
        return response(
            500,
            {"message": "Unable to retrieve tasks"}
        )
