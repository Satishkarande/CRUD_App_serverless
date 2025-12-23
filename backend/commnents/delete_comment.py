import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")

COMMENTS = dynamodb.Table(os.environ["COMMENTS_TABLE"])
AUDIT = dynamodb.Table(os.environ["AUDIT_TABLE"])
TASKS = dynamodb.Table(os.environ["TASKS_TABLE"])

def handler(event, context):
    task_id = event["pathParameters"]["taskId"]
    comment_id = event["pathParameters"]["commentId"]

    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    user_id = claims["sub"]
    username = claims.get("username") or claims.get("email")
    groups = claims.get("cognito:groups", [])

    is_admin = "admin" in groups

    pk = f"TASK#{task_id}"
    sk = f"COMMENT#{comment_id}"

    # 1️⃣ Get comment
    res = COMMENTS.get_item(Key={"pk": pk, "sk": sk})
    if "Item" not in res:
        return {"statusCode": 404, "body": "Comment not found"}

    comment = res["Item"]

    if not is_admin and comment["userId"] != user_id:
        return {"statusCode": 403, "body": "Forbidden"}

    # 2️⃣ Get task meta (title)
    task = TASKS.get_item(
        Key={"pk": pk, "sk": "META"}
    ).get("Item", {})

    task_title = task.get("title", "Unknown Task")

    # 3️⃣ Delete comment
    COMMENTS.delete_item(Key={"pk": pk, "sk": sk})

    # 4️⃣ Decrement comment count safely
    TASKS.update_item(
        Key={
            "pk": pk,
            "sk": "META"
        },
        UpdateExpression="SET commentCount = if_not_exists(commentCount, :zero) - :one",
        ExpressionAttributeValues={
            ":one": 1,
            ":zero": 0
        }
    )

    # 5️⃣ Audit
    ts = datetime.utcnow().isoformat()

    AUDIT.put_item(
        Item={
            "pk": "AUDIT",
            "sk": f"DELETE_COMMENT#{comment_id}#{ts}",
            "action": "DELETE_COMMENT",
            "taskId": task_id,
            "taskTitle": task_title,
            "commentId": comment_id,
            "deletedComment": comment["comment"],
            "deletedBy": username,
            "createdAt": ts
        }
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Comment deleted"})
    }