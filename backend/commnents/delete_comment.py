
import json
import os
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cognito = boto3.client("cognito-idp")

USER_POOL_ID = os.environ["USER_POOL_ID"]
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,GET"
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

        # Only authenticated users can retrieve the user directory.
        if not claims.get("sub"):
            return response(401, {"message": "Unauthorized"})

        users = []
        pagination_token = None

        while True:
            params = {
                "UserPoolId": USER_POOL_ID,
                "Limit": 60
            }

            if pagination_token:
                params["PaginationToken"] = pagination_token

            result = cognito.list_users(**params)

            for user in result.get("Users", []):
                username = user.get("Username")
                attributes = {
                    attr["Name"]: attr["Value"]
                    for attr in user.get("Attributes", [])
                }

                user_sub = attributes.get("sub")

                if username and user_sub:
                    users.append({
                        "username": username,
                        "sub": user_sub
                    })

            pagination_token = result.get("PaginationToken")

            if not pagination_token:
                break

        return response(200, users)

    except Exception:
        logger.exception("Failed to fetch Cognito users")
        return response(
            500,
            {"message": "Failed to fetch users"}
        )
