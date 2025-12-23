import json
import os
import boto3
import uuid
import re
from datetime import datetime
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

COMMENTS_TABLE = dynamodb.Table(os.environ["COMMENTS_TABLE"])
TASK_TABLE = dynamodb.Table(os.environ["TASK_TABLE"])
MENTIONS_TABLE = dynamodb.Table(os.environ["MENTIONS_TABLE"])

cognito = boto3.client("cognito-idp")
USER_POOL_ID = os.environ["USER_POOL_ID"]

MENTION_REGEX = r'@([a-zA-Z0-9_.-]+)'

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*"
}

# =========================
# Helper: username → sub
# =========================
def get_user_sub(username):
    resp = cognito.list_users(
        UserPoolId=USER_POOL_ID,
        Filter=f'username = "{username}"'
    )
    users = resp.get("Users", [])
    if not users:
        return None

    for attr in users[0]["Attributes"]:
        if attr["Name"] == "sub":
            return attr["Value"]
    return None


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        comment_text = body.get("comment", "").strip()

        if not comment_text:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Comment required"})
            }

        # ✅ FIXED: correct path param name
        task_id = event.get("pathParameters", {}).get("id")
        if not task_id:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Task ID required"})
            }

        # =========================
        # Cognito authorizer
        # =========================
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        user_sub = claims["sub"]
        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
            or "unknown"
        )

        now = datetime.utcnow().isoformat()
        comment_id = str(uuid.uuid4())

        # =========================
        # Fetch task META
        # =========================
        task_res = TASK_TABLE.get_item(
            Key={
                "pk": f"TASK#{task_id}",
                "sk": "META"
            }
        )

        task = task_res.get("Item")
        if not task:
            return {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Task not found"})
            }

        task_title = task.get("title", "Untitled Task")

        # =========================
        # Extract mentions
        # =========================
        mentioned_usernames = list(set(re.findall(MENTION_REGEX, comment_text)))
        mentioned_usernames = [u for u in mentioned_usernames if u != username]

        # =========================
        # Store comment
        # =========================
        COMMENTS_TABLE.put_item(
            Item={
                "pk": f"TASK#{task_id}",
                "sk": f"COMMENT#{comment_id}",
                "taskId": task_id,
                "commentId": comment_id,
                "comment": comment_text,
                "userId": user_sub,
                "userName": username,
                "createdAt": now
            }
        )

        # =========================
        # Increment comment count (SAFE)
        # =========================
        TASK_TABLE.update_item(
            Key={
                "pk": f"TASK#{task_id}",
                "sk": "META"
            },
            UpdateExpression="SET commentCount = if_not_exists(commentCount, :zero) + :inc",
            ExpressionAttributeValues={
                ":inc": Decimal(1),
                ":zero": Decimal(0)
            }
        )

        # =========================
        # Handle mentions + sharing
        # =========================
        for mentioned_username in mentioned_usernames:
            mentioned_sub = get_user_sub(mentioned_username)
            if not mentioned_sub:
                continue

            # Mention
            MENTIONS_TABLE.put_item(
                Item={
                    "pk": f"USER#{mentioned_username}",
                    "sk": f"MENTION#{now}#{comment_id}",
                    "taskId": task_id,
                    "taskTitle": task_title,
                    "commentId": comment_id,
                    "comment": comment_text,
                    "mentionedBy": username,
                    "createdAt": now,
                    "status": "UNREAD"
                }
            )

            # Share task
            if mentioned_sub not in task.get("participantIds", []):
                TASK_TABLE.update_item(
                    Key={
                        "pk": f"TASK#{task_id}",
                        "sk": "META"
                    },
                    UpdateExpression="""
                        SET participants = list_append(
                                if_not_exists(participants, :p0),
                                :p1
                            ),
                            participantIds = list_append(
                                if_not_exists(participantIds, :i0),
                                :i1
                            ),
                            updatedAt = :t
                    """,
                    ExpressionAttributeValues={
                        ":p0": [],
                        ":p1": [{
                            "userId": mentioned_sub,
                            "userName": mentioned_username
                        }],
                        ":i0": [],
                        ":i1": [mentioned_sub],
                        ":t": now
                    }
                )

        return {
            "statusCode": 201,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Comment added"})
        }

    except Exception as e:
        print("ADD COMMENT ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Internal server error"})
        }