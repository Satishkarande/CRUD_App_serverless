
import json
import os
import boto3

cognito = boto3.client("cognito-idp")
USER_POOL_ID = os.environ["USER_POOL_ID"]

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization,Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,GET"
}

def handler(event, context):
    try:
        # =====================
        # CORS PREFLIGHT
        # =====================
        if event.get("httpMethod") == "OPTIONS":
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": ""
            }

        users = []
        pagination_token = None

        # =====================
        # LIST USERS (PAGINATED)
        # =====================
        while True:
            params = {
                "UserPoolId": USER_POOL_ID,
                "Limit": 60
            }

            if pagination_token:
                params["PaginationToken"] = pagination_token

            response = cognito.list_users(**params)

            for user in response.get("Users", []):
                username = user.get("Username")
                sub = None

                for attr in user.get("Attributes", []):
                    if attr["Name"] == "sub":
                        sub = attr["Value"]
                        break

                if username and sub:
                    users.append({
                        "username": username,
                        "sub": sub
                    })

            pagination_token = response.get("PaginationToken")
            if not pagination_token:
                break

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(users)
        }

    except Exception as e:
        print("GET USERS ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({
                "message": "Failed to fetch users"
            })
        }