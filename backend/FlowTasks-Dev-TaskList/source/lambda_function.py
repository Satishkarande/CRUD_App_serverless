import json
import os
import logging
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
cognito = boto3.client("cognito-idp")

TASK_TABLE_NAME = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE_NAME = os.environ["TASK_MEMBERSHIP_TABLE"]
USER_POOL_ID = os.environ.get("USER_POOL_ID")
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

def response(code, body):
    return {"statusCode": code, "headers": CORS_HEADERS, "body": json.dumps(body, default=decimal_default)}

def decimal_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Unsupported type: {type(value).__name__}")

def parse_groups(value):
    if isinstance(value, list):
        return [str(x).lower() for x in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x).lower() for x in parsed]
        except json.JSONDecodeError:
            pass
        return [value.lower()]
    return []

def is_admin_user(claims):
    groups = parse_groups(claims.get("cognito:groups", []))
    if "admin" in groups:
        return True
    # Fallback to Cognito's current group membership when the JWT has
    # stale/missing group claims. The Lambda role needs cognito-idp:AdminListGroupsForUser.
    username = claims.get("cognito:username") or claims.get("username")
    if USER_POOL_ID and username:
        try:
            result = cognito.admin_list_groups_for_user(
                UserPoolId=USER_POOL_ID, Username=username, Limit=60
            )
            return any(g.get("GroupName", "").lower() == "admin"
                       for g in result.get("Groups", []))
        except Exception:
            logger.exception("Unable to resolve Cognito groups for user")
    return False

def query_memberships(index_name, key_name, value):
    items = []
    kwargs = {"IndexName": index_name, "KeyConditionExpression": Key(key_name).eq(value)}
    while True:
        result = membership_table.query(**kwargs)
        items.extend(result.get("Items", []))
        if not result.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return items

def ids_from_memberships(items):
    return {str(x.get("taskId") or x.get("pk", "").removeprefix("TASK#"))
            for x in items if x.get("taskId") or str(x.get("pk", "")).startswith("TASK#")}

def batch_get(task_ids):
    found = []
    ids = list(task_ids)
    for start in range(0, len(ids), 100):
        pending = {TASK_TABLE_NAME: {"Keys": [
            {"pk": {"S": f"TASK#{tid}"}, "sk": {"S": "META"}}
            for tid in ids[start:start + 100]
        ]}}
        attempts = 0
        while pending and attempts < 5:
            result = client.batch_get_item(RequestItems=pending)
            for raw in result.get("Responses", {}).get(TASK_TABLE_NAME, []):
                found.append({k: deserializer.deserialize(v) for k, v in raw.items()})
            pending = result.get("UnprocessedKeys", {})
            attempts += 1
        if pending:
            raise RuntimeError("DynamoDB returned unprocessed task keys after five attempts")
    return found

def scan_legacy_tasks(user_id=None):
    """Recovery path for older task records created before membership rows existed."""
    items = []
    kwargs = {}
    if user_id:
        kwargs["FilterExpression"] = Attr("ownerId").eq(user_id) | Attr("participantIds").contains(user_id)
    while True:
        result = task_table.scan(**kwargs)
        items.extend(result.get("Items", []))
        if not result.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return items

def handler(event, context):
    try:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        user_id = claims.get("sub")
        if not user_id:
            return response(401, {"message": "Unauthorized"})

        admin = is_admin_user(claims)
        if admin:
            membership_items = []
            for shard in range(4):
                membership_items.extend(query_memberships(
                    ADMIN_TASKS_INDEX, "gsi2pk", f"ADMIN#{shard}"
                ))
            tasks = batch_get(ids_from_memberships(membership_items))
            # Merge pre-membership / historical task records; de-duplicate by taskId.
            tasks.extend(scan_legacy_tasks())
        else:
            memberships = query_memberships(
                USER_TASKS_INDEX, "gsi1pk", f"USER#{user_id}"
            )
            tasks = batch_get(ids_from_memberships(memberships))
            tasks.extend(scan_legacy_tasks(user_id))

        unique = {}
        for task in tasks:
            tid = task.get("taskId") or task.get("id")
            if tid and not task.get("isDeleted", False):
                task["taskId"] = tid
                task["id"] = tid
                unique[tid] = task

        result = list(unique.values())
        result.sort(key=lambda x: x.get("updatedAt") or x.get("createdAt") or "", reverse=True)
        logger.info("Task list returned %s tasks for user %s (admin=%s)", len(result), user_id, admin)
        return response(200, result)
    except Exception:
        logger.exception("Failed to retrieve tasks")
        return response(500, {"message": "Unable to retrieve tasks"})
