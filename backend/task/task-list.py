import json
import os
import boto3
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TASK_TABLE"])

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*"
}

# ======================================================
# 🔧 Decimal → JSON safe conversion (REQUIRED FIX)
# ======================================================
def decimal_to_native(obj):
    if isinstance(obj, list):
        return [decimal_to_native(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: decimal_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    else:
        return obj


def handler(event, context):
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        user_id = str(claims["sub"])
        groups = claims.get("cognito:groups", [])
    except Exception:
        return {
            "statusCode": 401,
            "headers": CORS_HEADERS,
            "body": json.dumps({"message": "Unauthorized"})
        }

    items = []
    scan_kwargs = {}

    # ======================================================
    # 🔁 FULL TABLE SCAN (unchanged)
    # ======================================================
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))

        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # ======================================================
    # 🔐 ROLE FILTERING (unchanged)
    # ======================================================
    if "admin" in groups:
        tasks = [
            t for t in items
            if t.get("pk", "").startswith("TASK#")
            and t.get("sk") == "META"
        ]
    else:
        tasks = [
            t for t in items
            if t.get("pk", "").startswith("TASK#")
            and t.get("sk") == "META"
            and user_id in t.get("participantIds", [])
        ]

    # ======================================================
    # 🧠 NORMALIZE ID (unchanged)
    # ======================================================
    for t in tasks:
        t["id"] = t.get("taskId")

    # ======================================================
    # 📅 SORT (unchanged)
    # ======================================================
    tasks.sort(
        key=lambda x: x.get("updatedAt", x.get("createdAt", "")),
        reverse=True
    )

    # ======================================================
    # ✅ SAFE JSON RESPONSE (FIXED)
    # ======================================================
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(decimal_to_native(tasks))
    }