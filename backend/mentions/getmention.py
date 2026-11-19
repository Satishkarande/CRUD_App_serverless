
import json
import os
import logging

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

MENTIONS_TABLE = os.environ["MENTIONS_TABLE"]
table = dynamodb.Table(MENTIONS_TABLE)

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

        # Query all mentions for this Cognito user.
        # Keep READ and UNREAD records for frontend compatibility.
        query_kwargs = {
            "KeyConditionExpression": Key("pk").eq(f"USER#{user_id}"),
            "ScanIndexForward": False
        }

        mentions = []

        while True:
            result = table.query(**query_kwargs)
            mentions.extend(result.get("Items", []))

            last_key = result.get("LastEvaluatedKey")

            if not last_key:
                break

            query_kwargs["ExclusiveStartKey"] = last_key

        return response(200, mentions)

    except Exception:
        logger.exception("Failed to retrieve mentions")
        return response(
            500,
            {"message": "Unable to retrieve mentions"}
        )
