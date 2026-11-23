import json
import os
import logging
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
cognito = boto3.client("cognito-idp")
serializer = TypeSerializer()

COMMENTS_TABLE = os.environ["COMMENTS_TABLE"]
TASK_TABLE = os.environ["TASK_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]
USER_POOL_ID = os.environ.get("USER_POOL_ID")
comments_table = dynamodb.Table(COMMENTS_TABLE)
task_table = dynamodb.Table(TASK_TABLE)
CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "DELETE,OPTIONS"
}

def response(code, body):
    return {"statusCode": code, "headers": CORS, "body": json.dumps(body)}

def serialize(item):
    return {k: serializer.serialize(v) for k, v in item.items()}

def groups_list(value):
    if isinstance(value, list): return [str(x).lower() for x in value]
    if isinstance(value, str):
        try:
            v=json.loads(value)
            if isinstance(v,list): return [str(x).lower() for x in v]
        except json.JSONDecodeError: pass
        return [value.lower()]
    return []

def find_comment(task_id, comment_id):
    kwargs = {
        "KeyConditionExpression": Key("pk").eq(f"TASK#{task_id}"),
        "FilterExpression": Attr("commentId").eq(comment_id)
    }
    while True:
        result = comments_table.query(**kwargs)
        if result.get("Items"): return result["Items"][0]
        last = result.get("LastEvaluatedKey")
        if not last: return None
        kwargs["ExclusiveStartKey"] = last

def is_admin_user(claims):
    value = claims.get("cognito:groups", [])
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            groups = parsed if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            groups = [value]
    elif isinstance(value, list):
        groups = value
    else:
        groups = []
    if any(str(group).lower() == "admin" for group in groups):
        return True
    username = claims.get("cognito:username") or claims.get("username")
    if USER_POOL_ID and username:
        try:
            result = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=username, Limit=60)
            return any(g.get("GroupName", "").lower() == "admin" for g in result.get("Groups", []))
        except Exception:
            logger.exception("Cognito group lookup failed")
    return False

def handler(event, context):
    try:
        path = event.get("pathParameters") or {}
        task_id = path.get("taskId") or path.get("id")
        comment_id = path.get("commentId")
        if not task_id or not comment_id:
            return response(400, {"message": "Task ID and comment ID are required"})
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        user_id = claims.get("sub")
        if not user_id: return response(401, {"message": "Unauthorized"})
        is_admin = is_admin_user(claims)
        task_result = task_table.get_item(Key={"pk": f"TASK#{task_id}", "sk": "META"})
        task = task_result.get("Item")
        if not task or task.get("isDeleted", False): return response(404, {"message": "Task not found"})
        comment = find_comment(task_id, comment_id)
        if not comment: return response(404, {"message": "Comment not found"})
        if not is_admin and comment.get("userId") != user_id:
            return response(403, {"message": "Not allowed to delete this comment"})
        now = datetime.now(timezone.utc).isoformat()
        audit = {
            "pk": f"AUDIT#{now[:7]}",
            "sk": f"{now}#DELETE_COMMENT#{comment_id}",
            "action": "DELETE_COMMENT",
            "taskId": task_id,
            "taskTitle": task.get("title", "-"),
            "commentId": comment_id,
            "deletedBy": claims.get("cognito:username") or claims.get("username") or claims.get("email") or user_id,
            "actorId": user_id,
            "createdAt": now
        }
        tx = [
            {"Delete": {
                "TableName": COMMENTS_TABLE,
                "Key": serialize({"pk": comment["pk"], "sk": comment["sk"]}),
                "ConditionExpression": "attribute_exists(pk)"
            }},
            {"Update": {
                "TableName": TASK_TABLE,
                "Key": serialize({"pk": f"TASK#{task_id}", "sk": "META"}),
                "UpdateExpression": "SET commentCount = if_not_exists(commentCount, :one) - :one",
                "ConditionExpression": "attribute_exists(pk)",
                "ExpressionAttributeValues": serialize({":one": 1})
            }},
            {"Put": {
                "TableName": AUDIT_TABLE,
                "Item": serialize(audit),
                "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)"
            }}
        ]
        client.transact_write_items(TransactItems=tx)
        return response(200, {"message": "Comment deleted", "commentId": comment_id})
    except Exception:
        logger.exception("Comment deletion failed")
        return response(500, {"message": "Unable to delete comment"})
