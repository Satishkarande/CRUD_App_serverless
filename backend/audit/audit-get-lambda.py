import json
import os
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["AUDIT_TABLE"]
table = dynamodb.Table(TABLE_NAME)

def handler(event, context):
    try:
        print("EVENT:", json.dumps(event))

        response = table.query(
            KeyConditionExpression=Key("pk").eq("AUDIT"),
            ScanIndexForward=False  # latest first
        )

        items = response.get("Items", [])

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(items)
        }

    except Exception as e:
        print("AUDIT GET ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"message": "Failed to fetch audit logs"})
        }