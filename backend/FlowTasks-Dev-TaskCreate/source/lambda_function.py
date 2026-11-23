
import json
import os
import re
import uuid
import hashlib
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Use a dedicated low-level DynamoDB client
client = boto3.client(
    "dynamodb",
    region_name=os.environ.get("AWS_REGION", "ap-south-1")
)

serializer = TypeSerializer()

TASK_TABLE = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE = os.environ["TASK_MEMBERSHIP_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]
MENTIONS_TABLE = os.environ["MENTIONS_TABLE"]
USER_POOL_ID = os.environ["USER_POOL_ID"]

cognito = boto3.client("cognito-idp")

MAX_MENTIONS = 20

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST"
}


def response(status, body):
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body)
    }


def serialize_item(item):
    return {
        key: serializer.serialize(value)
        for key, value in item.items()
    }


def cognito_display_name(user, attributes):
    preferred = (
        attributes.get("preferred_username")
        or attributes.get("custom:display_name")
        or attributes.get("name")
    )
    if preferred and preferred.strip():
        return preferred.strip()
    full_name = " ".join(
        part.strip() for part in
        (attributes.get("given_name"), attributes.get("family_name"))
        if part and part.strip()
    )
    if full_name:
        return full_name
    email = attributes.get("email", "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    cognito_username = user.get("Username", "").strip()
    if cognito_username and cognito_username != attributes.get("sub"):
        return cognito_username
    return "User"


def resolve_mentions(text):
    """Resolve @display-name tokens to Cognito sub IDs; supports spaces."""
    aliases = {}
    token = None
    while True:
        params = {"UserPoolId": USER_POOL_ID, "Limit": 60}
        if token:
            params["PaginationToken"] = token
        result = cognito.list_users(**params)
        for user in result.get("Users", []):
            attrs = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
            sub = attrs.get("sub")
            if not sub:
                continue
            display = cognito_display_name(user, attrs)
            email = attrs.get("email", "")
            candidates = {
                display.strip(), user.get("Username", "").strip(),
                email.split("@", 1)[0].strip() if "@" in email else ""
            }
            for alias in candidates:
                alias = alias.strip()
                if not alias or not re.fullmatch(r"[A-Za-z0-9_. -]+", alias):
                    continue
                key = alias.casefold()
                entry = {"userId": sub, "userName": display}
                if key in aliases and aliases[key] and aliases[key]["userId"] != sub:
                    aliases[key] = None  # ambiguous names are never guessed
                elif key not in aliases:
                    aliases[key] = entry
        token = result.get("PaginationToken")
        if not token:
            break
    names = sorted((name for name, value in aliases.items() if value), key=len, reverse=True)
    if not names:
        return []
    pattern = re.compile(
        r"(?<![A-Za-z0-9_@])@(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_])",
        re.IGNORECASE
    )
    found, seen = [], set()
    for match in pattern.finditer(text or ""):
        user = aliases.get(match.group(1).casefold())
        if user and user["userId"] not in seen:
            seen.add(user["userId"])
            found.append(user)
    return found

def get_current_display_name(user_id, claims):
    claim_name = (claims.get("preferred_username") or
                  claims.get("cognito:username") or claims.get("username"))
    try:
        result = cognito.list_users(
            UserPoolId=USER_POOL_ID,
            Filter=f'sub = "{user_id}"',
            Limit=1
        )
        users = result.get("Users", [])
        if users:
            user = users[0]
            attributes = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
            return cognito_display_name(user, attributes)
    except Exception:
        logger.exception("Unable to resolve display name for user sub")
    email = claims.get("email", "")
    if email and "@" in email:
        return email.split("@", 1)[0]
    if claim_name and claim_name != user_id:
        return claim_name
    return "User"

def admin_shard(task_id):
    digest = hashlib.sha256(task_id.encode()).hexdigest()
    return int(digest[:8], 16) % 4


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
        username = get_current_display_name(user_id, claims)

        body = json.loads(event.get("body") or "{}")

        title = body.get("title", "")
        if not isinstance(title, str):
            return response(400, {"message": "Invalid title"})

        title = title.strip()

        if not title or len(title) > 150:
            return response(400, {
                "message": "Title must contain 1 to 150 characters"
            })

        description = body.get("description", "")
        if not isinstance(description, str) or len(description) > 5000:
            return response(400, {"message": "Invalid description"})

        category = body.get("category", "general")
        status = body.get("status", "todo")
        priority = body.get("priority", "medium")

        if category not in [
            "general", "work", "personal", "bug", "feature", "ops"
        ]:
            return response(400, {"message": "Invalid category"})

        if status not in ["todo", "in-progress", "done"]:
            return response(400, {"message": "Invalid status"})

        if priority not in ["low", "medium", "high"]:
            return response(400, {"message": "Invalid priority"})

        mentioned_users = [
            user for user in resolve_mentions(description)
            if user["userId"] != user_id
        ]
        if len(mentioned_users) > MAX_MENTIONS:
            return response(400, {
                "message": f"Maximum {MAX_MENTIONS} mentions allowed"
            })

        participants = [{"userId": user_id, "userName": username}]
        for user in mentioned_users:
            if user["userId"] not in [p["userId"] for p in participants]:
                participants.append(user)

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        task_pk = f"TASK#{task_id}"
        shard = admin_shard(task_id)

        task = {
            "pk": task_pk,
            "sk": "META",
            "taskId": task_id,
            "id": task_id,
            "title": title,
            "description": description,
            "category": category,
            "status": status,
            "priority": priority,
            "ownerId": user_id,
            "ownerName": username,
            "participants": participants,
            "participantIds": [
                participant["userId"] for participant in participants
            ],
            "commentCount": 0,
            "isDeleted": False,
            "createdBy": username,
            "createdAt": now,
            "updatedBy": username,
            "updatedAt": now
        }

        audit = {
            "pk": f"AUDIT#{now[:7]}",
            "sk": f"{now}#CREATE#{task_id}",
            "action": "CREATE",
            "taskId": task_id,
            "taskTitle": title,
            "actorId": user_id,
            "actorName": username,
            "createdAt": now
        }

        transaction = [
            {
                "Put": {
                    "TableName": TASK_TABLE,
                    "Item": serialize_item(task),
                    "ConditionExpression": "attribute_not_exists(pk)"
                }
            },
            {
                "Put": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Item": serialize_item({
                        "pk": task_pk,
                        "sk": "ADMIN",
                        "gsi2pk": f"ADMIN#{shard}",
                        "gsi2sk": f"UPDATED#{now}#TASK#{task_id}",
                        "taskId": task_id,
                        "updatedAt": now
                    })
                }
            },
            {
                "Put": {
                    "TableName": AUDIT_TABLE,
                    "Item": serialize_item(audit)
                }
            }
        ]

        for participant in participants:
            participant_id = participant["userId"]

            membership = {
                "pk": task_pk,
                "sk": f"USER#{participant_id}",
                "gsi1pk": f"USER#{participant_id}",
                "gsi1sk": f"UPDATED#{now}#TASK#{task_id}",
                "taskId": task_id,
                "userId": participant_id,
                "role": (
                    "OWNER" if participant_id == user_id
                    else "PARTICIPANT"
                ),
                "taskTitle": title,
                "status": status,
                "priority": priority,
                "updatedAt": now
            }

            transaction.append({
                "Put": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Item": serialize_item(membership)
                }
            })

            if participant_id != user_id:
                mention = {
                    "pk": f"USER#{participant_id}",
                    "sk": f"MENTION#{now}#{task_id}",
                    "gsi1pk": f"USER#{participant_id}#UNREAD",
                    "gsi1sk": f"{now}#{task_id}",
                    "taskId": task_id,
                    "taskTitle": title,
                    "comment": "You were mentioned in task description",
                    "mentionedBy": username,
                    "mentionedById": user_id,
                    "status": "UNREAD",
                    "createdAt": now
                }

                transaction.append({
                    "Put": {
                        "TableName": MENTIONS_TABLE,
                        "Item": serialize_item(mention)
                    }
                })

        if len(transaction) > 100:
            return response(400, {
                "message": "Too many participants for one transaction"
            })

        # Diagnostic logging
        logger.info("DYNAMODB_CLIENT_TYPE: %s", type(client))
        logger.info(
            "TRANSACTION_PK_VALUE: %s",
            json.dumps(transaction[0]["Put"]["Item"]["pk"])
        )

        # Execute transaction
        client.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=task_id
        )

        logger.info(
            "Task created successfully: task_id=%s actor_id=%s participants=%s",
            task_id,
            user_id,
            len(participants)
        )

        return response(201, {
            "message": "Task created",
            "taskId": task_id
        })

    except Exception as e:
        logger.exception("CREATE_TASK_FAILED")

        if hasattr(e, "response"):
            logger.error(
                "DYNAMODB_ERROR_DETAILS: %s",
                json.dumps(e.response, default=str)
            )

        return response(500, {
            "message": "Failed to create task"
        })
