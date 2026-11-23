
import json
import os
import re
import uuid
import logging
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
cognito = boto3.client("cognito-idp")
serializer = TypeSerializer()

COMMENTS_TABLE = os.environ["COMMENTS_TABLE"]
TASK_TABLE = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE = os.environ["TASK_MEMBERSHIP_TABLE"]
MENTIONS_TABLE = os.environ["MENTIONS_TABLE"]
# AddComment writes its audit event to the existing monthly audit table.
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "FlowTasks-Dev-Audit")
USER_POOL_ID = os.environ["USER_POOL_ID"]

MAX_COMMENT_LENGTH = 5000
MAX_MENTIONS = 20


CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "POST,OPTIONS"
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


def cognito_display_name(user, attributes):
    preferred = (attributes.get("preferred_username") or attributes.get("custom:display_name") or attributes.get("name"))
    if preferred and preferred.strip():
        return preferred.strip()
    full_name = " ".join(part.strip() for part in (attributes.get("given_name"), attributes.get("family_name")) if part and part.strip())
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
        # --------------------------------------------------
        # 1. Validate request
        # --------------------------------------------------

        path_parameters = event.get("pathParameters") or {}
        # API Gateway route is POST /tasks/{taskId}/comments.
        # Keep `id` as a backward-compatible fallback for older routes.
        task_id = (
            path_parameters.get("taskId")
            or path_parameters.get("id")
        )

        logger.info(
            "AddComment request path_parameters=%s task_id_present=%s",
            json.dumps(path_parameters),
            bool(task_id)
        )

        if not task_id:
            return response(400, {
                "message": "Task ID is required",
                "receivedPathParameters": list(path_parameters.keys())
            })

        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return response(400, {"message": "Invalid JSON body"})

        if not isinstance(body, dict):
            return response(400, {"message": "Invalid request body"})

        comment_text = body.get("comment", "")

        if not isinstance(comment_text, str):
            return response(400, {"message": "Comment must be text"})

        comment_text = comment_text.strip()

        if not comment_text:
            return response(400, {"message": "Comment is required"})

        if len(comment_text) > MAX_COMMENT_LENGTH:
            return response(
                400,
                {"message": "Comment exceeds 5000 characters"}
            )

        # --------------------------------------------------
        # 2. Get authenticated Cognito user
        # --------------------------------------------------

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

        is_admin = is_admin_user(claims)

        # --------------------------------------------------
        # 3. Retrieve task
        # --------------------------------------------------

        task_key = {
            "pk": f"TASK#{task_id}",
            "sk": "META"
        }

        task_result = dynamodb.Table(TASK_TABLE).get_item(
            Key=task_key
        )

        task = task_result.get("Item")

        if not task or task.get("isDeleted", False):
            return response(404, {"message": "Task not found"})

        # Only owner, participant or admin can comment.
        existing_participant_ids = set(
            task.get("participantIds", [])
        )

        owner_id = task.get("ownerId")

        if owner_id:
            existing_participant_ids.add(owner_id)

        if not is_admin and user_id not in existing_participant_ids:
            return response(
                403,
                {"message": "You are not allowed to comment on this task"}
            )

        # --------------------------------------------------
        # 4. Resolve mentioned users
        # --------------------------------------------------

        resolved_mentions = resolve_mentions(comment_text)
        resolved_mentions = [
            {"sub": user["userId"], "username": user["userName"]}
            for user in resolved_mentions
            if user["userId"] != user_id
        ]
        if len(resolved_mentions) > MAX_MENTIONS:
            return response(400, {"message": "Maximum 20 mentions are allowed"})

        new_participants = [
            user for user in resolved_mentions
            if user["sub"] not in existing_participant_ids
        ]

        # --------------------------------------------------
        # 5. Prepare comment and timestamps
        # --------------------------------------------------

        now = datetime.now(timezone.utc).isoformat()
        comment_id = str(uuid.uuid4())

        task_title = task.get("title", "Untitled Task")

        comment_item = {
            "pk": f"TASK#{task_id}",
            "sk": f"COMMENT#{now}#{comment_id}",
            "taskId": task_id,
            "commentId": comment_id,
            "comment": comment_text,
            "userId": user_id,
            "userName": username,
            "createdAt": now
        }

        transaction = []

        # --------------------------------------------------
        # 6. Save comment
        # --------------------------------------------------

        transaction.append({
            "Put": {
                "TableName": COMMENTS_TABLE,
                "Item": serialize_map(comment_item),
                "ConditionExpression": (
                    "attribute_not_exists(pk) AND "
                    "attribute_not_exists(sk)"
                )
            }
        })

        # --------------------------------------------------
        # 7. Update comment count and participants together
        # --------------------------------------------------

        update_expression = (
            "SET commentCount = "
            "if_not_exists(commentCount, :zero) + :one"
        )

        expression_values = {
            ":zero": 0,
            ":one": 1,
            ":false": False
        }

        if new_participants:
            participant_details = [
                {
                    "userId": item["sub"],
                    "userName": item["username"]
                }
                for item in new_participants
            ]

            participant_ids = [
                item["sub"] for item in new_participants
            ]

            update_expression += (
                ", participants = "
                "list_append(if_not_exists(participants, :empty), :people)"
                ", participantIds = "
                "list_append(if_not_exists(participantIds, :empty), :ids)"
            )

            expression_values.update({
                ":empty": [],
                ":people": participant_details,
                ":ids": participant_ids
            })

        transaction.append({
            "Update": {
                "TableName": TASK_TABLE,
                "Key": serialize_map(task_key),
                "UpdateExpression": update_expression,
                "ConditionExpression": (
                    "attribute_exists(pk) AND "
                    "(attribute_not_exists(isDeleted) OR isDeleted = :false)"
                ),
                "ExpressionAttributeValues": serialize_map(
                    expression_values
                )
            }
        })

        # --------------------------------------------------
        # 8. Create membership records for new participants
        # --------------------------------------------------

        membership_sort_key = (
            f"UPDATED#{task.get('updatedAt', now)}#TASK#{task_id}"
        )

        for participant in new_participants:
            participant_id = participant["sub"]

            membership_item = {
                "pk": f"TASK#{task_id}",
                "sk": f"USER#{participant_id}",
                "taskId": task_id,
                "userId": participant_id,
                "userName": participant["username"],
                "role": "PARTICIPANT",
                "gsi1pk": f"USER#{participant_id}",
                "gsi1sk": membership_sort_key
            }

            membership_key = {"pk": f"TASK#{task_id}", "sk": f"USER#{participant_id}"}
            membership_values = {
                ":taskId": task_id,
                ":userId": participant_id,
                ":userName": participant["username"],
                ":role": "PARTICIPANT",
                ":gsi1pk": f"USER#{participant_id}",
                ":gsi1sk": membership_sort_key,
            }
            transaction.append({
                "Update": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Key": serialize_map(membership_key),
                    "UpdateExpression": (
                        "SET taskId = :taskId, userId = :userId, "
                        "userName = :userName, #role = :role, "
                        "gsi1pk = :gsi1pk, gsi1sk = :gsi1sk"
                    ),
                    "ExpressionAttributeNames": {"#role": "role"},
                    "ExpressionAttributeValues": serialize_map(membership_values),
                }
            })

        # --------------------------------------------------
        # 9. Create unread mention records
        # --------------------------------------------------

        for mentioned_user in resolved_mentions:
            mentioned_sub = mentioned_user["sub"]

            mention_item = {
                "pk": f"USER#{mentioned_sub}",
                "sk": f"MENTION#{now}#{comment_id}",
                "taskId": task_id,
                "taskTitle": task_title,
                "commentId": comment_id,
                "comment": comment_text,
                "mentionedBy": username,
                "createdAt": now,
                "status": "UNREAD",
                "gsi1pk": f"USER#{mentioned_sub}#UNREAD",
                "gsi1sk": f"{now}#{comment_id}"
            }

            transaction.append({
                "Put": {
                    "TableName": MENTIONS_TABLE,
                    "Item": serialize_map(mention_item),
                    "ConditionExpression": (
                        "attribute_not_exists(pk) AND "
                        "attribute_not_exists(sk)"
                    )
                }
            })

        # --------------------------------------------------
        # 10. Execute transaction
        # --------------------------------------------------

        audit_item = {
            "pk": f"AUDIT#{now[:7]}",
            "sk": f"{now}#ADD_COMMENT#{comment_id}",
            "action": "ADD_COMMENT",
            "taskId": task_id,
            "taskTitle": task_title,
            "commentId": comment_id,
            "actorId": user_id,
            "actorName": username,
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
            "Comment created successfully: task_id=%s comment_id=%s user_id=%s",
            task_id,
            comment_id,
            user_id
        )

        return response(201, {
            "message": "Comment added",
            "commentId": comment_id
        })

    except Exception:
        logger.exception("Comment creation failed")

        return response(
            500,
            {"message": "Unable to add comment"}
        )
