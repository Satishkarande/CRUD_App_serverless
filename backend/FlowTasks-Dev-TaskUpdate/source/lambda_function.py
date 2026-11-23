import json
import os
import uuid
import hashlib
import logging
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamodb = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
cognito = boto3.client("cognito-idp")
serializer = TypeSerializer()

TASK_TABLE = os.environ["TASK_TABLE"]
MEMBERSHIP_TABLE = os.environ["TASK_MEMBERSHIP_TABLE"]
AUDIT_TABLE = os.environ["AUDIT_TABLE"]
USER_POOL_ID = os.environ.get("USER_POOL_ID")
CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "PUT,OPTIONS"
}
VALID_STATUSES = {"todo", "in-progress", "done"}
VALID_PRIORITIES = {"low", "medium", "high"}
VALID_CATEGORIES = {"general", "work", "personal", "bug", "feature", "ops"}

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

def is_admin(claims):
    if "admin" in groups_list(claims.get("cognito:groups", [])): return True
    username = claims.get("cognito:username") or claims.get("username")
    if USER_POOL_ID and username:
        try:
            groups = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=username, Limit=60).get("Groups", [])
            return any(g.get("GroupName", "").lower()=="admin" for g in groups)
        except Exception:
            logger.exception("Could not resolve admin group from Cognito")
    return False

def shard(task_id):
    return int(hashlib.sha256(task_id.encode()).hexdigest(), 16) % 4

def handler(event, context):
    try:
        path = event.get("pathParameters") or {}
        task_id = path.get("id") or path.get("taskId")
        if not task_id: return response(400, {"message":"Task ID is required"})
        body = json.loads(event.get("body") or "{}")
        if not isinstance(body, dict) or not body: return response(400, {"message":"No update fields supplied"})
        claims = event.get("requestContext",{}).get("authorizer",{}).get("jwt",{}).get("claims",{})
        user_id = claims.get("sub")
        if not user_id: return response(401, {"message":"Unauthorized"})
        username = claims.get("cognito:username") or claims.get("username") or claims.get("email") or user_id
        task_table = dynamodb.Table(TASK_TABLE)
        membership_table = dynamodb.Table(MEMBERSHIP_TABLE)
        key = {"pk":f"TASK#{task_id}","sk":"META"}
        task = task_table.get_item(Key=key).get("Item")
        if not task or task.get("isDeleted",False): return response(404,{"message":"Task not found"})
        admin = is_admin(claims)
        allowed_editors = set(task.get("participantIds", []))
        if task.get("ownerId"):
            allowed_editors.add(task["ownerId"])
        if not admin and user_id not in allowed_editors:
            return response(403,{"message":"You are not a task participant or admin"})
        allowed = {"title","description","category","status","priority"}
        extra = set(body)-allowed
        if extra: return response(400,{"message":f"Unsupported fields: {', '.join(sorted(extra))}"})
        validation = {
            "title": lambda v: isinstance(v,str) and 1 <= len(v.strip()) <= 150,
            "description": lambda v: isinstance(v,str) and len(v) <= 5000,
            "category": lambda v: v in VALID_CATEGORIES,
            "status": lambda v: v in VALID_STATUSES,
            "priority": lambda v: v in VALID_PRIORITIES
        }
        for field,value in body.items():
            if not validation[field](value):
                return response(400,{"message":f"Invalid {field}"})
        updates = {}
        audit_changes = []
        for field,value in body.items():
            new_value = value.strip() if field in ("title","description") else value
            old_value = task.get(field, "")
            if new_value != old_value:
                updates[field] = new_value
                audit_changes.append((field, old_value, new_value))
        if not updates: return response(200,{"message":"No changes","taskId":task_id})
        now = datetime.now(timezone.utc).isoformat()
        updates["updatedAt"] = now
        updates["updatedBy"] = username
        updates["updatedById"] = user_id
        names = {f"#{k}":k for k in updates}
        vals = {f":{k}":v for k,v in updates.items()}
        vals[":false"] = False
        tx = [{
            "Update":{
                "TableName":TASK_TABLE,
                "Key":serialize(key),
                "UpdateExpression":"SET "+", ".join(f"#{k} = :{k}" for k in updates),
                "ConditionExpression":"attribute_exists(pk) AND (attribute_not_exists(isDeleted) OR isDeleted = :false)",
                "ExpressionAttributeNames":names,
                "ExpressionAttributeValues":serialize(vals)
            }
        }]
        new_shard = shard(task_id)
        gsi_sk = f"UPDATED#{now}#TASK#{task_id}"
        membership_updates = {
            "taskTitle": updates.get("title", task.get("title","")),
            "status": updates.get("status", task.get("status","todo")),
            "priority": updates.get("priority", task.get("priority","medium")),
            "updatedAt": now
        }
        for membership_key in [{"pk":f"TASK#{task_id}","sk":"ADMIN"}] + [
            {"pk":f"TASK#{task_id}","sk":f"USER#{uid}"}
            for uid in set(task.get("participantIds",[])) | {task.get("ownerId")}
            if uid
        ]:
            # Older task records may not have membership rows. Update only
            # existing rows; TaskList's legacy scan still makes those tasks visible.
            if not membership_table.get_item(Key=membership_key).get("Item"):
                continue
            is_admin_row = membership_key["sk"]=="ADMIN"
            set_values = dict(membership_updates)
            if is_admin_row:
                set_values.update({"gsi2pk":f"ADMIN#{new_shard}","gsi2sk":gsi_sk})
            else:
                uid = membership_key["sk"].split("#",1)[1]
                set_values.update({"gsi1pk":f"USER#{uid}","gsi1sk":gsi_sk})
            set_names = {f"#{k}":k for k in set_values}
            set_vals = {f":{k}":v for k,v in set_values.items()}
            tx.append({"Update":{
                "TableName":MEMBERSHIP_TABLE,
                "Key":serialize(membership_key),
                "UpdateExpression":"SET "+", ".join(f"#{k} = :{k}" for k in set_values),
                "ConditionExpression":"attribute_exists(pk)",
                "ExpressionAttributeNames":set_names,
                "ExpressionAttributeValues":serialize(set_vals)
            }})
        for field,old,new in audit_changes:
            audit = {
                "pk":f"AUDIT#{now[:7]}",
                "sk":f"{now}#UPDATE_{field.upper()}#{task_id}",
                "action":f"UPDATE_{field.upper()}",
                "taskId":task_id,
                "taskTitle":updates.get("title",task.get("title")),
                "oldValue":old,
                "newValue":new,
                "updatedBy":username,
                "actorName":username,
                "actorId":user_id,
                "createdAt":now,
                "updatedAt":now
            }
            tx.append({"Put":{
                "TableName":AUDIT_TABLE,
                "Item":serialize(audit),
                "ConditionExpression":"attribute_not_exists(pk) AND attribute_not_exists(sk)"
            }})
        client.transact_write_items(TransactItems=tx,ClientRequestToken=str(uuid.uuid4()))
        return response(200,{"message":"Task updated","taskId":task_id,"updatedFields":[x[0] for x in audit_changes]})
    except json.JSONDecodeError:
        return response(400,{"message":"Invalid JSON body"})
    except Exception:
        logger.exception("Task update failed")
        return response(500,{"message":"Unable to update task"})
