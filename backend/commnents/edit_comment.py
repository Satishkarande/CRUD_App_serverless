import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")

COMMENTS_TABLE = dynamodb.Table(os.environ["COMMENTS_TABLE"])
AUDIT_TABLE = dynamodb.Table(os.environ["AUDIT_TABLE"])
TASKS_TABLE = dynamodb.Table(os.environ["TASKS_TABLE"])

def handler(event, context):
    try:
        # ==============================
        # PATH PARAMS
        # ==============================
        task_id = event["pathParameters"]["taskId"]
        comment_id = event["pathParameters"]["commentId"]

        body = json.loads(event["body"])
        new_comment = body.get("comment", "").strip()

        if not new_comment:
            return response(400, "Comment cannot be empty")

        # ==============================
        # AUTH CONTEXT
        # ==============================
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        user_id = claims["sub"]
        username = claims.get("username") or claims.get("email")
        groups = claims.get("cognito:groups", [])

        is_admin = "admin" in groups

        pk = f"TASK#{task_id}"
        sk = f"COMMENT#{comment_id}"

        # ==============================
        # FETCH EXISTING COMMENT
        # ==============================
        res = COMMENTS_TABLE.get_item(
            Key={"pk": pk, "sk": sk}
        )

        if "Item" not in res:
            return response(404, "Comment not found")

        comment_item = res["Item"]

        # ==============================
        # AUTHORIZATION
        # ==============================
        if not is_admin and comment_item["userId"] != user_id:
            return response(403, "Not allowed to edit this comment")

        old_comment = comment_item["comment"]

        # ==============================
        # UPDATE COMMENT (🔥 FIXED)
        # ==============================
        timestamp = datetime.utcnow().isoformat()

        COMMENTS_TABLE.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET #c = :c, updatedAt = :u",
            ExpressionAttributeNames={
                "#c": "comment"  # 🔥 reserved keyword fix
            },
            ExpressionAttributeValues={
                ":c": new_comment,
                ":u": timestamp
            }
        )

        # ==============================
        # FETCH TASK TITLE (for audit)
        # ==============================
        task_res = TASKS_TABLE.get_item(
            Key={
                "pk": pk,
                "sk": "META"
            }
        )

        task_title = task_res.get("Item", {}).get("title", "-")

        # ==============================
        # AUDIT LOG
        # ==============================
        audit_sk = f"EDIT_COMMENT#{comment_id}#{timestamp}"

        AUDIT_TABLE.put_item(
            Item={
                "pk": "AUDIT",
                "sk": audit_sk,
                "action": "EDIT_COMMENT",
                "taskId": task_id,
                "taskTitle": task_title,
                "commentId": comment_id,
                "oldValue": old_comment,
                "newValue": new_comment,
                "updatedBy": username,
                "userId": user_id,
                "createdAt": timestamp,
                "updatedAt": timestamp
            }
        )

        return response(200, "Comment updated successfully")

    except Exception as e:
        print("EDIT COMMENT ERROR:", str(e))
        return response(500, "Internal server error")


# ==============================
# HELPER
# ==============================
def response(code, message):
    return {
        "statusCode": code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps({"message": message})
    }