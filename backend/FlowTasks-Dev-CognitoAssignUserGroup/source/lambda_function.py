import json
import boto3
import os

client = boto3.client("cognito-idp")

USER_POOL_ID = os.environ["USER_POOL_ID"]
DEFAULT_GROUP = "user"

def handler(event, context):
    try:
        # Cognito internal username (required by AdminAddUserToGroup)
        cognito_username = event["userName"]

        # Optional: human-readable username
        attrs = event.get("request", {}).get("userAttributes", {})
        display_name = (
            attrs.get("preferred_username")
            or attrs.get("name")
            or attrs.get("email")
            or cognito_username
        )

        print("Assigning user to group:", cognito_username, display_name)

        # Add user to default USER group
        client.admin_add_user_to_group(
            UserPoolId=USER_POOL_ID,
            Username=cognito_username,
            GroupName=DEFAULT_GROUP
        )

        return event

    except Exception as e:
        print("ERROR:", str(e))
        raise e