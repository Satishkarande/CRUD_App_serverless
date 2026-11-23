import json
import os
import logging
import uuid
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
serializer = TypeSerializer()
MENTIONS_TABLE = os.environ["MENTIONS_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]
table = dynamodb.Table(MENTIONS_TABLE)
CORS = {
    "Content-Type":"application/json",
    "Access-Control-Allow-Origin":os.environ.get("ALLOWED_ORIGIN","*"),
    "Access-Control-Allow-Headers":"Content-Type,Authorization",
    "Access-Control-Allow-Methods":"PATCH,OPTIONS"
}
def response(code,body): return {"statusCode":code,"headers":CORS,"body":json.dumps(body)}
def serialize(item): return {k:serializer.serialize(v) for k,v in item.items()}
def handler(event,context):
    try:
        claims=event.get("requestContext",{}).get("authorizer",{}).get("jwt",{}).get("claims",{})
        user_id=claims.get("sub")
        if not user_id:return response(401,{"message":"Unauthorized"})
        sk=(event.get("pathParameters") or {}).get("sk")
        if not sk:return response(400,{"message":"Mention ID is required"})
        mention=table.get_item(Key={"pk":f"USER#{user_id}","sk":sk}).get("Item")
        if not mention:return response(404,{"message":"Mention not found"})
        now=datetime.now(timezone.utc).isoformat()
        username=claims.get("cognito:username") or claims.get("username") or claims.get("email") or user_id
        audit={
            "pk":f"AUDIT#{now[:7]}",
            "sk":f"{now}#MARK_MENTION_READ#{mention.get('commentId') or sk.split('#')[-1]}",
            "action":"MARK_MENTION_READ",
            "taskId":mention.get("taskId"),
            "taskTitle":mention.get("taskTitle","-"),
            "actorId":user_id,
            "actorName":username,
            "createdAt":now
        }
        tx=[
            {"Update":{
                "TableName":MENTIONS_TABLE,
                "Key":serialize({"pk":f"USER#{user_id}","sk":sk}),
                "UpdateExpression":"SET #status = :read, readAt = :readAt REMOVE gsi1pk, gsi1sk",
                "ConditionExpression":"attribute_exists(pk)",
                "ExpressionAttributeNames":{"#status":"status"},
                "ExpressionAttributeValues":serialize({":read":"READ",":readAt":now})
            }},
            {"Put":{
                "TableName":AUDIT_TABLE,
                "Item":serialize(audit),
                "ConditionExpression":"attribute_not_exists(pk) AND attribute_not_exists(sk)"
            }}
        ]
        client.transact_write_items(TransactItems=tx,ClientRequestToken=str(uuid.uuid4()))
        return response(200,{"message":"Mention marked as read"})
    except Exception:
        logger.exception("Mark mention read failed")
        return response(500,{"message":"Unable to update mention"})
