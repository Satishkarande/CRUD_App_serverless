
import json
import os
import uuid
import hashlib
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()

TASK_TABLE = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE = os.environ["TASK_MEMBERSHIP_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "PUT,OPTIONS"
}

VALID_STATUSES = {"todo", "in-progress", "done"}
VALID_PRIORITIES = {"low", "medium", "high"}


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


def admin_shard(task_id):
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % 4


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


def handler(event, context):
    try:
        path_parameters = event.get("pathParameters") or {}
        task_id = path_parameters.get("id")

        if not task_id:
            return response(400, {"message": "Task ID is required"})

        body = json.loads(event.get("body") or "{}")

        if not isinstance(body, dict):
            return response(400, {"message": "Invalid request body"})

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
            return response(403, {"message": "Not allowed"})

        allowed_fields = {"status", "priority"}
        unexpected_fields = set(body.keys()) - allowed_fields

        if unexpected_fields:
            return response(
                400,
                {"message": "Only status and priority can be updated"}
            )

        updates = {}
        audits = []

        if "status" in body:
            new_status = body["status"]

            if new_status not in VALID_STATUSES:
                return response(400, {"message": "Invalid status"})

            old_status = task.get("status")

            if new_status != old_status:
                updates["status"] = new_status
                audits.append({
                    "action": "UPDATE_STATUS",
                    "oldValue": old_status,
                    "newValue": new_status
                })

        if "priority" in body:
            new_priority = body["priority"]

            if new_priority not in VALID_PRIORITIES:
                return response(400, {"message": "Invalid priority"})

            old_priority = task.get("priority")

            if new_priority != old_priority:
                updates["priority"] = new_priority
                audits.append({
                    "action": "UPDATE_PRIORITY",
                    "oldValue": old_priority,
                    "newValue": new_priority
                })

        if not updates:
            return response(200, {"message": "No changes"})

        now = datetime.now(timezone.utc).isoformat()
        updated_at_key = f"UPDATED#{now}#TASK#{task_id}"

        updates["updatedBy"] = username
        updates["updatedAt"] = now

        participant_ids = set(task.get("participantIds", []))
        participant_ids.add(task["ownerId"])

        shard = admin_shard(task_id)

        transaction = []

        # Update canonical task record.
        update_expression = "SET " + ", ".join(
            f"#{field} = :{field}" for field in updates
        )

        expression_names = {
            f"#{field}": field for field in updates
        }

        expression_values = {
            f":{field}": value for field, value in updates.items()
        }

        expression_values[":false"] = False

        transaction.append({
            "Update": {
                "TableName": TASK_TABLE,
                "Key": serialize_map(task_key),
                "UpdateExpression": update_expression,
                "ConditionExpression": (
                    "attribute_exists(pk) AND "
                    "(attribute_not_exists(isDeleted) OR isDeleted = :false)"
                ),
                "ExpressionAttributeNames": expression_names,
                "ExpressionAttributeValues": serialize_map(
                    expression_values
                )
            }
        })

        # Update admin listing membership.
        transaction.append({
            "Update": {
                "TableName": MEMBERSHIP_TABLE,
                "Key": serialize_map({
                    "pk": f"TASK#{task_id}",
                    "sk": "ADMIN"
                }),
                "UpdateExpression": (
                    "SET gsi2pk = :gsi2pk, gsi2sk = :gsi2sk"
                ),
                "ExpressionAttributeValues": serialize_map({
                    ":gsi2pk": f"ADMIN#{shard}",
                    ":gsi2sk": updated_at_key
                }),
                "ConditionExpression": "attribute_exists(pk)"
            }
        })

        # Update every participant's user-task listing index.
        for participant_id in participant_ids:
            transaction.append({
                "Update": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Key": serialize_map({
                        "pk": f"TASK#{task_id}",
                        "sk": f"USER#{participant_id}"
                    }),
                    "UpdateExpression": (
                        "SET gsi1pk = :gsi1pk, gsi1sk = :gsi1sk"
                    ),
                    "ExpressionAttributeValues": serialize_map({
                        ":gsi1pk": f"USER#{participant_id}",
                        ":gsi1sk": updated_at_key
                    }),
                    "ConditionExpression": "attribute_exists(pk)"
                }
            })

        # Write monthly audit records.
        month_partition = f"AUDIT#{now[:7]}"

        for audit in audits:
            audit_item = {
                "pk": month_partition,
                "sk": f"{now}#{audit['action']}#{task_id}",
                "action": audit["action"],
                "taskId": task_id,
                "taskTitle": task.get("title"),
                "oldValue": audit["oldValue"],
                "newValue": audit["newValue"],
                "updatedBy": username,
                "updatedAt": now,
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
            "Task updated successfully: task_id=%s user_id=%s",
            task_id,
            user_id
        )

        return response(200, {
            "message": "Task updated",
            "taskId": task_id
        })

    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON body"})

    except Exception:
        logger.exception("Task update failed")
        return response(
            500,
            {"message": "Unable to update task"}
        )
