
import json
import os
import uuid
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

COMMENTS_TABLE = os.environ["COMMENTS_TABLE"]
TASK_TABLE = os.environ["TASK_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]

comments_table = dynamodb.Table(COMMENTS_TABLE)
task_table = dynamodb.Table(TASK_TABLE)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "PUT,OPTIONS"
}

MAX_COMMENT_LENGTH = 5000


def response(status_code, message):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps({"message": message})
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


def find_comment(task_id, comment_id):
    """
    Find the comment using commentId because the new sort key
    includes both the timestamp and comment ID.
    """

    query_kwargs = {
        "KeyConditionExpression": Key("pk").eq(f"TASK#{task_id}"),
        "FilterExpression": boto3.dynamodb.conditions.Attr(
            "commentId"
        ).eq(comment_id)
    }

    while True:
        result = comments_table.query(**query_kwargs)

        items = result.get("Items", [])

        if items:
            return items[0]

        last_key = result.get("LastEvaluatedKey")

        if not last_key:
            return None

        query_kwargs["ExclusiveStartKey"] = last_key


def handler(event, context):
    try:
        path_parameters = event.get("pathParameters") or {}

        task_id = path_parameters.get("taskId")
        comment_id = path_parameters.get("commentId")

        if not task_id or not comment_id:
            return response(400, "Task ID and comment ID are required")

        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return response(400, "Invalid JSON body")

        if not isinstance(body, dict):
            return response(400, "Invalid request body")

        new_comment = body.get("comment", "")

        if not isinstance(new_comment, str):
            return response(400, "Comment must be text")

        new_comment = new_comment.strip()

        if not new_comment:
            return response(400, "Comment cannot be empty")

        if len(new_comment) > MAX_COMMENT_LENGTH:
            return response(400, "Comment exceeds 5000 characters")

        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )

        user_id = claims.get("sub")

        if not user_id:
            return response(401, "Unauthorized")

        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
            or "unknown"
        )

        groups = parse_groups(claims.get("cognito:groups", []))
        is_admin = "admin" in groups

        # Retrieve the task.
        task_result = task_table.get_item(
            Key={
                "pk": f"TASK#{task_id}",
                "sk": "META"
            }
        )

        task = task_result.get("Item")

        if not task:
            return response(404, "Task not found")

        if task.get("isDeleted", False):
            return response(404, "Task is deleted")

        # Find comment using its commentId attribute.
        comment_item = find_comment(task_id, comment_id)

        if not comment_item:
            return response(404, "Comment not found")

        # Only the original author or an admin can edit.
        if not is_admin and comment_item.get("userId") != user_id:
            return response(403, "Not allowed to edit this comment")

        old_comment = comment_item.get("comment", "")
        comment_key = {
            "pk": comment_item["pk"],
            "sk": comment_item["sk"]
        }

        now = datetime.now(timezone.utc).isoformat()

        # Prepare the monthly audit record.
        audit_item = {
            "pk": f"AUDIT#{now[:7]}",
            "sk": f"{now}#EDIT_COMMENT#{comment_id}",
            "action": "EDIT_COMMENT",
            "taskId": task_id,
            "taskTitle": task.get("title", "-"),
            "commentId": comment_id,
            "oldValue": old_comment,
            "newValue": new_comment,
            "updatedBy": username,
            "userId": user_id,
            "createdAt": now,
            "updatedAt": now
        }

        # Update the comment and write audit atomically.
        update_expression_values = {
            ":comment": new_comment,
            ":updatedAt": now
        }

        condition_expression = "attribute_exists(pk)"

        if not is_admin:
            condition_expression += " AND userId = :userId"
            update_expression_values[":userId"] = user_id

        transaction = [
            {
                "Update": {
                    "TableName": COMMENTS_TABLE,
                    "Key": serialize_map(comment_key),
                    "UpdateExpression": (
                        "SET #comment = :comment, updatedAt = :updatedAt"
                    ),
                    "ConditionExpression": condition_expression,
                    "ExpressionAttributeNames": {
                        "#comment": "comment"
                    },
                    "ExpressionAttributeValues": serialize_map(
                        update_expression_values
                    )
                }
            },
            {
                "Put": {
                    "TableName": AUDIT_TABLE,
                    "Item": serialize_map(audit_item),
                    "ConditionExpression": (
                        "attribute_not_exists(pk) AND "
                        "attribute_not_exists(sk)"
                    )
                }
            }
        ]

        client.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=str(uuid.uuid4())
        )

        logger.info(
            "Comment updated: task_id=%s comment_id=%s user_id=%s",
            task_id,
            comment_id,
            user_id
        )

        return response(200, "Comment updated successfully")

    except Exception:
        logger.exception("Comment update failed")
        return response(500, "Unable to update comment")
