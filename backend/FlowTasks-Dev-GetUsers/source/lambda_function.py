import json
import logging
import os

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
    "Access-Control-Allow-Methods": "OPTIONS,GET",
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def display_name(user, attributes):
    """Return a human-readable label; never prefer the Cognito sub UUID."""
    preferred = (
        attributes.get("preferred_username")
        or attributes.get("custom:display_name")
        or attributes.get("name")
    )
    if preferred and preferred.strip():
        return preferred.strip()

    full_name = " ".join(
        part.strip()
        for part in (attributes.get("given_name"), attributes.get("family_name"))
        if part and part.strip()
    )
    if full_name:
        return full_name

    email = attributes.get("email", "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]

    cognito_username = user.get("Username", "").strip()
    sub = attributes.get("sub", "")
    if cognito_username and cognito_username != sub:
        return cognito_username

    return "User"


def handler(event, context):
    try:
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )
        if not claims.get("sub"):
            return response(401, {"message": "Unauthorized"})

        users = []
        pagination_token = None
        while True:
            params = {"UserPoolId": USER_POOL_ID, "Limit": 60}
            if pagination_token:
                params["PaginationToken"] = pagination_token
            result = cognito.list_users(**params)

            for user in result.get("Users", []):
                attributes = {
                    item["Name"]: item["Value"]
                    for item in user.get("Attributes", [])
                }
                sub = attributes.get("sub")
                if not sub:
                    continue

                name = display_name(user, attributes)
                users.append({
                    "sub": sub,
                    "username": name,
                    "displayName": name,
                    "cognitoUsername": user.get("Username", ""),
                    "email": attributes.get("email", ""),
                })

            pagination_token = result.get("PaginationToken")
            if not pagination_token:
                break

        logger.info("Returned %s Cognito users with display names", len(users))
        return response(200, users)

    except Exception:
        logger.exception("Failed to fetch Cognito users")
        return response(500, {"message": "Failed to fetch users"})
