import json
import os
import logging
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
TABLE_NAME = os.environ["AUDIT_TABLE"]
USER_POOL_ID = os.environ.get("USER_POOL_ID")
AUDIT_MONTHS = int(os.environ.get("AUDIT_MONTHS","12"))
MAX_AUDIT_RECORDS = int(os.environ.get("MAX_AUDIT_RECORDS","500"))
table = dynamodb.Table(TABLE_NAME)
CORS = {
    "Content-Type":"application/json",
    "Access-Control-Allow-Origin":os.environ.get("ALLOWED_ORIGIN","*"),
    "Access-Control-Allow-Headers":"Content-Type,Authorization",
    "Access-Control-Allow-Methods":"GET,OPTIONS"
}
def response(code,body): return {"statusCode":code,"headers":CORS,"body":json.dumps(body)}
def parse_groups(value):
    if isinstance(value,list): return [str(x).lower() for x in value]
    if isinstance(value,str):
        try:
            v=json.loads(value)
            if isinstance(v,list): return [str(x).lower() for x in v]
        except json.JSONDecodeError: pass
        return [value.lower()]
    return []
def admin_check(claims):
    if "admin" in parse_groups(claims.get("cognito:groups",[])): return True
    username=claims.get("cognito:username") or claims.get("username")
    if USER_POOL_ID and username:
        try:
            groups=cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID,Username=username,Limit=60).get("Groups",[])
            return any(g.get("GroupName","").lower()=="admin" for g in groups)
        except Exception:
            logger.exception("Cognito group lookup failed")
    return False
def previous_month(year,month):
    return (year-1,12) if month==1 else (year,month-1)
def partitions():
    now=datetime.now(timezone.utc); year,month=now.year,now.month; out=[]
    for _ in range(AUDIT_MONTHS):
        out.append(f"AUDIT#{year:04d}-{month:02d}");year,month=previous_month(year,month)
    return out
def query_partition(pk,remaining):
    items=[]; kwargs={"KeyConditionExpression":Key("pk").eq(pk),"ScanIndexForward":False,"Limit":min(remaining,100)}
    while len(items)<remaining:
        r=table.query(**kwargs);items.extend(r.get("Items",[]))
        if not r.get("LastEvaluatedKey"):break
        kwargs["ExclusiveStartKey"]=r["LastEvaluatedKey"];kwargs["Limit"]=min(remaining-len(items),100)
    return items[:remaining]
def handler(event,context):
    try:
        claims=event.get("requestContext",{}).get("authorizer",{}).get("jwt",{}).get("claims",{})
        if not claims.get("sub"):return response(401,{"message":"Unauthorized"})
        if not admin_check(claims):return response(403,{"message":"Admin access required"})
        if not 1<=AUDIT_MONTHS<=60 or not 1<=MAX_AUDIT_RECORDS<=5000:
            return response(500,{"message":"Invalid audit query configuration"})
        items=[]
        for pk in partitions():
            remaining=MAX_AUDIT_RECORDS-len(items)
            if remaining<=0:break
            items.extend(query_partition(pk,remaining))
        items.sort(key=lambda x:x.get("sk",""),reverse=True)
        return response(200,items[:MAX_AUDIT_RECORDS])
    except Exception:
        logger.exception("Audit retrieval failed")
        return response(500,{"message":"Unable to fetch audit logs"})
