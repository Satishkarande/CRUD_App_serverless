import json
import os
import boto3

dynamodb = boto3.resource("dynamodb")
COMMENTS_TABLE = dynamodb.Table(os.environ["COMMENTS_TABLE"])

def handler(event, context):
    try:
        task_id = event["pathParameters"]["id"]

        res = COMMENTS_TABLE.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={
                ":pk": f"TASK#{task_id}"
            },
            ScanIndexForward=True
        )

        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps(res.get("Items", []))
        }

    except Exception as e:
        print("GET COMMENTS ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"message": "Internal server error"})
        }