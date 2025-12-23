import json
import os
import boto3
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
TASK_TABLE = dynamodb.Table(os.environ["TASK_TABLE"])
AUDIT_TABLE = dynamodb.Table(os.environ["AUDIT_TABLE"])

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*"
}

def handler(event, context):
    try:
        task_id = event["pathParameters"]["id"]
        body = json.loads(event.get("body") or "{}")

        # ===== AUTH =====
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        user_id = claims["sub"]
        username = (
            claims.get("cognito:username")
            or claims.get("username")
            or claims.get("email")
            or "unknown"
        )
        groups = claims.get("cognito:groups", [])

        pk = f"TASK#{task_id}"
        sk = "META"
        now = datetime.utcnow().isoformat()

        # ===== FETCH TASK =====
        res = TASK_TABLE.get_item(Key={"pk": pk, "sk": sk})
        task = res.get("Item")
        if not task:
            return {
                "statusCode": 404,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Task not found"})
            }

        # ===== AUTHZ =====
        if "admin" not in groups and user_id != task["ownerId"]:
            return {
                "statusCode": 403,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "Not allowed"})
            }

        update_expr = []
        names = {}
        values = {}
        audits = []

        # ===== STATUS =====
        if "status" in body and body["status"] != task.get("status"):
            update_expr.append("#s = :s")
            names["#s"] = "status"
            values[":s"] = body["status"]
            audits.append(("UPDATE_STATUS", task.get("status"), body["status"]))

        # ===== PRIORITY =====
        if "priority" in body and body["priority"] != task.get("priority"):
            update_expr.append("#p = :p")
            names["#p"] = "priority"
            values[":p"] = body["priority"]
            audits.append(("UPDATE_PRIORITY", task.get("priority"), body["priority"]))

        if not update_expr:
            return {
                "statusCode": 200,
                "headers": CORS_HEADERS,
                "body": json.dumps({"message": "No changes"})
            }

        update_expr += ["#ub = :ub", "#ua = :ua"]
        names["#ub"] = "updatedBy"
        names["#ua"] = "updatedAt"
        values[":ub"] = username
        values[":ua"] = now

        TASK_TABLE.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET " + ", ".join(update_expr),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values
        )

        # ===== AUDIT =====
        for a, old, new in audits:
            AUDIT_TABLE.put_item(
                Item={
                    "pk": "AUDIT",
                    "sk": f"{a}#{task_id}#{now}",
                    "action": a,
                    "taskId": task_id,
                    "taskTitle": task.get("title"),
                    "oldValue": old,
                    "newValue": new,
                    "updatedBy": username,
                    "updatedAt": now,
                    "createdAt": now
                }
            )

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Task updated"})
        }

    except Exception as e:
        print("TASK UPDATE ERROR:", str(e))
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Internal server error"})
        }