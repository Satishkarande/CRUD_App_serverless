import json
import os
import boto3

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

        res = MENTIONS_TABLE.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={
                ":pk": f"USER#{username}"
            },
            ScanIndexForward=False
        )

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps(res.get("Items", []))
        }

    except Exception as e:
        print("GET MENTIONS ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Internal server error"})
        }