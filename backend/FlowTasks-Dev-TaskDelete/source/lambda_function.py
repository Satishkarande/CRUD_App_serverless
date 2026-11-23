
import json
import os
import uuid
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

TASK_TABLE = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE = os.environ["TASK_MEMBERSHIP_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]

USER_POOL_ID = os.environ.get("USER_POOL_ID")

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "DELETE,OPTIONS"
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body)
    }


def serialize_map(item):
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
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



def is_admin_user(claims):
    value = claims.get("cognito:groups", [])
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            groups = parsed if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            groups = [value]
    elif isinstance(value, list):
        groups = value
    else:
        groups = []
    if any(str(group).lower() == "admin" for group in groups):
        return True
    username = claims.get("cognito:username") or claims.get("username")
    if USER_POOL_ID and username:
        try:
            result = cognito.admin_list_groups_for_user(
                UserPoolId=USER_POOL_ID, Username=username, Limit=60
            )
            return any(g.get("GroupName", "").lower() == "admin"
                       for g in result.get("Groups", []))
        except Exception:
            logger.exception("Cognito group lookup failed")
    return False

def handler(event, context):
    try:
        path_parameters = event.get("pathParameters") or {}
        task_id = path_parameters.get("id")

        if not task_id:
            return response(400, {"message": "Task ID is required"})

        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )

        user_id = claims.get("sub")

        if not user_id:
            return response(401, {"message": "Unauthorized"})

        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
            or "unknown"
        )

        groups = parse_groups(claims.get("cognito:groups", []))
        is_admin = "admin" in groups

        task_table = dynamodb.Table(TASK_TABLE)

        task_key = {
            "pk": f"TASK#{task_id}",
            "sk": "META"
        }

        result = task_table.get_item(Key=task_key)
        task = result.get("Item")

        if not task or task.get("isDeleted", False):
            return response(404, {"message": "Task not found"})

        if not is_admin and user_id != task.get("ownerId"):
            return response(
                403,
                {"message": "Not allowed to delete this task"}
            )

        now = datetime.now(timezone.utc).isoformat()

        participant_ids = set(task.get("participantIds", []))
        participant_ids.add(task["ownerId"])

        transaction = []

        # Soft-delete the canonical task record.
        transaction.append({
            "Update": {
                "TableName": TASK_TABLE,
                "Key": serialize_map(task_key),
                "UpdateExpression": (
                    "SET isDeleted = :deleted, "
                    "deletedAt = :deletedAt, "
                    "deletedBy = :deletedBy, "
                    "updatedAt = :updatedAt, "
                    "updatedBy = :updatedBy"
                ),
                "ConditionExpression": (
                    "attribute_exists(pk) AND "
                    "(attribute_not_exists(isDeleted) OR isDeleted = :false)"
                ),
                "ExpressionAttributeValues": serialize_map({
                    ":deleted": True,
                    ":false": False,
                    ":deletedAt": now,
                    ":deletedBy": username,
                    ":updatedAt": now,
                    ":updatedBy": username
                })
            }
        })

        # Remove the admin listing membership.
        transaction.append({
            "Delete": {
                "TableName": MEMBERSHIP_TABLE,
                "Key": serialize_map({
                    "pk": f"TASK#{task_id}",
                    "sk": "ADMIN"
                })
            }
        })

        # Remove each participant's active task membership.
        for participant_id in participant_ids:
            transaction.append({
                "Delete": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Key": serialize_map({
                        "pk": f"TASK#{task_id}",
                        "sk": f"USER#{participant_id}"
                    })
                }
            })

        # Keep a permanent audit record in the monthly partition.
        audit_item = {
            "pk": f"AUDIT#{now[:7]}",
            "sk": f"{now}#DELETE#{task_id}",
            "action": "DELETE",
            "taskId": task_id,
            "taskTitle": task.get("title"),
            "deletedBy": username,
            "deletedAt": now,
            "createdAt": now
        }

        transaction.append({
            "Put": {
                "TableName": AUDIT_TABLE,
                "Item": serialize_map(audit_item),
                "ConditionExpression": (
                    "attribute_not_exists(pk) AND "
                    "attribute_not_exists(sk)"
                )
            }
        })

        client.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=str(uuid.uuid4())
        )

        logger.info(
            "Task soft-deleted: task_id=%s deleted_by=%s",
            task_id,
            username
        )

        return response(200, {
            "message": "Task deleted",
            "taskId": task_id
        })

    except Exception:
        logger.exception("Task deletion failed")
        return response(
            500,
            {"message": "Unable to delete task"}
        )
