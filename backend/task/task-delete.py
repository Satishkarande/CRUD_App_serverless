import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
TASK_TABLE = dynamodb.Table(os.environ["TASK_TABLE"])
AUDIT_TABLE = dynamodb.Table(os.environ["AUDIT_TABLE"])

def handler(event, context):
    now = datetime.utcnow().isoformat()

    task_id = event["pathParameters"]["id"]

    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    user_id = claims["sub"]
    username = (
        claims.get("cognito:username")
        or claims.get("username")
        or claims.get("email")
        or "unknown"
    )
    groups = claims.get("cognito:groups", [])

    pk = f"TASK#{task_id}"
    sk = "META"

    # 1️⃣ GET TASK (FAST – SINGLE READ)
    res = TASK_TABLE.get_item(
        Key={"pk": pk, "sk": sk}
    )

    task = res.get("Item")
    if not task:
        return {
            "statusCode": 404,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Task not found"})
        }

    # 2️⃣ AUTH CHECK
    if "admin" not in groups and task["ownerId"] != user_id:
        return {
            "statusCode": 403,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Not allowed to delete this task"})
        }

    # 3️⃣ DELETE (FAST – SINGLE DELETE)
    TASK_TABLE.delete_item(
        Key={"pk": pk, "sk": sk}
    )

    # 4️⃣ AUDIT (OPTIONAL BUT SAFE)
    AUDIT_TABLE.put_item(
        Item={
            "pk": "AUDIT",
            "sk": f"DELETE#{task_id}#{now}",
            "action": "DELETE",
            "taskId": task_id,
            "taskTitle": task.get("title"),
            "deletedBy": username,
            "deletedAt": now,
            "createdAt": now
        }
    )

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps({"message": "Task deleted"})
    }