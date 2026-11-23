
import json
import os
import logging

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")

COMMENTS_TABLE = os.environ["COMMENTS_TABLE"]
TASK_TABLE = os.environ["TASK_TABLE"]

comments_table = dynamodb.Table(COMMENTS_TABLE)
task_table = dynamodb.Table(TASK_TABLE)

USER_POOL_ID = os.environ.get("USER_POOL_ID")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS"
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body)
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
        path_parameters = event.get("pathParameters") or {}

        # Support both route parameter names.
        task_id = (
            path_parameters.get("taskId")
            or path_parameters.get("id")
        )

        if not task_id:
            return response(400, {"message": "Task ID is required"})

        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )

        user_id = claims.get("sub")

        if not user_id:
            return response(401, {"message": "Unauthorized"})

        groups = parse_groups(claims.get("cognito:groups", []))
        is_admin = "admin" in groups

        task_result = task_table.get_item(
            Key={
                "pk": f"TASK#{task_id}",
                "sk": "META"
            }
        )

        task = task_result.get("Item")

        if not task:
            return response(404, {"message": "Task not found"})

        participant_ids = set(task.get("participantIds", []))

        if task.get("ownerId"):
            participant_ids.add(task["ownerId"])

        if not is_admin and user_id not in participant_ids:
            return response(
                403,
                {"message": "You are not allowed to view these comments"}
            )

        query_kwargs = {
            "KeyConditionExpression": Key("pk").eq(f"TASK#{task_id}"),
            "ScanIndexForward": True
        }

        comments = []

        while True:
            result = comments_table.query(**query_kwargs)
            comments.extend(result.get("Items", []))

            last_key = result.get("LastEvaluatedKey")

            if not last_key:
                break

            query_kwargs["ExclusiveStartKey"] = last_key

        return response(200, comments)

    except Exception:
        logger.exception("Failed to retrieve task comments")
        return response(
            500,
            {"message": "Unable to retrieve comments"}
        )
