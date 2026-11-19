
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
USER_POOL_ID = os.environ["USER_POOL_ID"]

MAX_COMMENT_LENGTH = 5000
MAX_MENTIONS = 20

MENTION_REGEX = r"@([a-zA-Z0-9_.-]+)"

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


def get_user(username):
    result = cognito.list_users(
        UserPoolId=USER_POOL_ID,
        Filter=f'username = "{username}"',
        Limit=1
    )

    users = result.get("Users", [])

    if not users:
        return None

    user = users[0]

    attributes = {
        attr["Name"]: attr["Value"]
        for attr in user.get("Attributes", [])
    }

    user_sub = attributes.get("sub")

    if not user_sub:
        return None

    return {
        "sub": user_sub,
        "username": user.get("Username", username)
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


def handler(event, context):
    try:
        # --------------------------------------------------
        # 1. Validate request
        # --------------------------------------------------

        path_parameters = event.get("pathParameters") or {}
        task_id = path_parameters.get("id")

        if not task_id:
            return response(400, {"message": "Task ID is required"})

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

        groups = parse_groups(claims.get("cognito:groups", []))
        is_admin = "admin" in groups

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

        mentioned_usernames = list(dict.fromkeys(
            re.findall(MENTION_REGEX, comment_text)
        ))

        mentioned_usernames = [
            name for name in mentioned_usernames
            if name.lower() != username.lower()
        ]

        if len(mentioned_usernames) > MAX_MENTIONS:
            return response(
                400,
                {"message": "Maximum 20 mentions are allowed"}
            )

        resolved_mentions = []
        resolved_subs = set()

        for mentioned_username in mentioned_usernames:
            mentioned_user = get_user(mentioned_username)

            if not mentioned_user:
                continue

            mentioned_sub = mentioned_user["sub"]

            if mentioned_sub == user_id:
                continue

            if mentioned_sub in resolved_subs:
                continue

            resolved_subs.add(mentioned_sub)
            resolved_mentions.append(mentioned_user)

        # Only users not already associated with the task need
        # new participant and membership records.
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

            transaction.append({
                "Put": {
                    "TableName": MEMBERSHIP_TABLE,
                    "Item": serialize_map(membership_item),
                    "ConditionExpression": (
                        "attribute_not_exists(pk) AND "
                        "attribute_not_exists(sk)"
                    )
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
