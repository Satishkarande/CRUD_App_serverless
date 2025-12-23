import json
import os
import boto3
import uuid
import re
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")

TASK_TABLE = dynamodb.Table(os.environ["TASK_TABLE"])
AUDIT_TABLE = dynamodb.Table(os.environ["AUDIT_TABLE"])
MENTIONS_TABLE = dynamodb.Table(os.environ["MENTIONS_TABLE"])

USER_POOL_ID = os.environ["USER_POOL_ID"]

MENTION_REGEX = r'@([a-zA-Z0-9_.-]+)'

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST"
}

# -------------------------
# Helper: username → sub
# -------------------------
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
        if event.get("httpMethod") == "OPTIONS":
            return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

        body = json.loads(event.get("body") or "{}")

        # =====================
        # AUTH
        # =====================
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        user_sub = claims["sub"]
        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
            or "unknown"
        )

        # =====================
        # INPUT
        # =====================
        title = body.get("title", "").strip()
        if not title:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Title required"})
            }

        description = body.get("description", "")
        category = body.get("category", "general")
        status = body.get("status", "todo").strip().lower()
        priority = body.get("priority", "medium")

        # =====================
        # EXTRACT MENTIONS
        # =====================
        mentioned_usernames = list(set(
            u for u in re.findall(MENTION_REGEX, description)
            if u != username
        ))

        # =====================
        # BASE PARTICIPANTS
        # =====================
        participants = [{
            "userId": user_sub,
            "userName": username
        }]
        participant_ids = [user_sub]

        # =====================
        # ADD MENTIONED USERS
        # =====================
        for u in mentioned_usernames:
            sub = get_user_sub(u)
            if not sub or sub in participant_ids:
                continue

            participants.append({
                "userId": sub,
                "userName": u
            })
            participant_ids.append(sub)

        # =====================
        # CREATE TASK
        # =====================
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        TASK_TABLE.put_item(
            Item={
                "pk": f"TASK#{task_id}",
                "sk": "META",
                "taskId": task_id,
                "id": task_id,
                "title": title,
                "description": description,
                "category": category,
                "status": status,
                "priority": priority,
                "ownerId": user_sub,
                "ownerName": username,
                "participants": participants,
                "participantIds": participant_ids,
                "createdBy": username,
                "createdAt": now,
                "updatedBy": username,
                "updatedAt": now
            }
        )

        # =====================
        # AUDIT
        # =====================
        AUDIT_TABLE.put_item(
            Item={
                "pk": "AUDIT",
                "sk": f"CREATE#{task_id}#{now}",
                "action": "CREATE",
                "taskId": task_id,
                "taskTitle": title,
                "createdBy": username,
                "createdAt": now
            }
        )

        # =====================
        # MENTIONS
        # =====================
        for u in mentioned_usernames:
            MENTIONS_TABLE.put_item(
                Item={
                    "pk": f"USER#{u}",
                    "sk": f"MENTION#{now}#{task_id}",
                    "taskId": task_id,
                    "taskTitle": title,
                    "comment": "You were mentioned in task description",
                    "mentionedBy": username,
                    "status": "UNREAD",
                    "createdAt": now
                }
            )

        return {
            "statusCode": 201,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "message": "Task created",
                "taskId": task_id
            })
        }

    except Exception as e:
        print("CREATE TASK ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Failed to create task"})
        }