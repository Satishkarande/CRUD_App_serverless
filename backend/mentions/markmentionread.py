import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
MENTIONS_TABLE = dynamodb.Table(os.environ["MENTIONS_TABLE"])

def handler(event, context):
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
        )

        mention_sk = event["pathParameters"]["sk"]

        MENTIONS_TABLE.update_item(
            Key={
                "pk": f"USER#{username}",
                "sk": mention_sk
            },
            UpdateExpression="SET #s = :r, readAt = :t",
            ExpressionAttributeNames={
                "#s": "status"
            },
            ExpressionAttributeValues={
                ":r": "READ",
                ":t": datetime.utcnow().isoformat()
            }
        )

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Marked as read"})
        }

    except Exception as e:
        print("MARK READ ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Internal server error"})
        }