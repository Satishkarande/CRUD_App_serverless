
import json
import os
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

MENTIONS_TABLE = os.environ["MENTIONS_TABLE"]
table = dynamodb.Table(MENTIONS_TABLE)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "PATCH,OPTIONS"
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

        path_parameters = event.get("pathParameters") or {}
        mention_sk = path_parameters.get("sk")

        if not mention_sk:
            return response(400, {"message": "Mention ID is required"})

        now = datetime.now(timezone.utc).isoformat()

        result = table.update_item(
            Key={
                "pk": f"USER#{user_id}",
                "sk": mention_sk
            },
            UpdateExpression=(
                "SET #status = :read, readAt = :readAt "
                "REMOVE gsi1pk, gsi1sk"
            ),
            ConditionExpression=(
                "attribute_exists(pk) AND attribute_exists(sk)"
            ),
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":read": "READ",
                ":readAt": now
            },
            ReturnValues="ALL_NEW"
        )

        logger.info(
            "Mention marked as read: user_id=%s mention_sk=%s",
            user_id,
            mention_sk
        )

        return response(200, {
            "message": "Marked as read",
            "mention": result.get("Attributes", {})
        })

    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return response(404, {"message": "Mention not found"})

        logger.exception("DynamoDB error while marking mention as read")
        return response(500, {"message": "Unable to update mention"})

    except Exception:
        logger.exception("Failed to mark mention as read")
        return response(500, {"message": "Internal server error"})
