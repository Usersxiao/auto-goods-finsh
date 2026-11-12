#!/usr/bin/env python3
"""Desktop chat adapter. Credentials stay inside the running operations service."""
import argparse
import json
import subprocess
from pathlib import Path

SCRIPT = r'''
import asyncio,json,sys,logging
from datetime import timedelta
from loguru import logger
logger.remove();logging.disable(logging.CRITICAL)
from sqlalchemy import select
from common.db.session import async_session_maker,async_engine
from common.models.user import User,UserStatus
from app.core.config import get_settings
from app.core.security import create_access_token
from app.services.jwt_secret_service import ensure_jwt_secret_key
import httpx
async def main():
 request=json.load(sys.stdin)
 try:
  await ensure_jwt_secret_key(get_settings())
  async with async_session_maker() as s:
   user=await s.get(User,request['owner_id'])
   assert user and user.status==UserStatus.ACTIVE,'本地操作用户不存在或已停用'
  token=create_access_token(str(user.id),expires_delta=timedelta(minutes=2))
  async with httpx.AsyncClient(base_url='http://127.0.0.1:9010',headers={'Authorization':'Bearer '+token},timeout=30) as client:
   response=await client.request(request['method'],request['path'],json=request.get('body'))
   if request.get('download') and response.is_success:
    import base64
    print(json.dumps({'download':base64.b64encode(response.content).decode()}))
   else:
    print(response.text)
   if not response.is_success:sys.exit(1)
 finally:await async_engine.dispose()
asyncio.run(main())
'''


def call(method,path,body=None,download=False,owner=1):
    request={"owner_id":owner,"method":method,"path":"/api/v1/operations"+path,"body":body,"download":download}
    result=subprocess.run(["docker","exec","-i","-w","/app","xianyu-operations","python","-c",SCRIPT],
                          input=json.dumps(request),text=True,capture_output=True)
    if result.returncode:
        raise SystemExit(result.stdout.strip() or "运营服务调用失败，请确认服务已启动")
    return json.loads(result.stdout)


def main():
    parser=argparse.ArgumentParser(description="闲鱼商品运营助手")
    parser.add_argument("--owner",type=int,default=1)
    sub=parser.add_subparsers(dest="command",required=True)
    start=sub.add_parser("start");start.add_argument("keyword");start.add_argument("--sample",type=int,default=300)
    start.add_argument("--pages",type=int,default=15);start.add_argument("--details",type=int,default=100)
    start.add_argument("--request-key");start.add_argument("--folder")
    chat=sub.add_parser("chat");chat.add_argument("message");chat.add_argument("--job")
    for name in ["status","resume","cancel"]:
        cmd=sub.add_parser(name);cmd.add_argument("job")
    assets=sub.add_parser("assets");assets.add_argument("job");assets.add_argument("folder")
    download=sub.add_parser("download");download.add_argument("job");download.add_argument("output")
    sub.add_parser("list")
    args=parser.parse_args()
    if args.command=="start":
        body={"keyword":args.keyword,"folder":args.folder,"overrides":{"sample_target":args.sample,"max_pages":args.pages,"detail_limit":args.details}}
        if args.request_key:body["request_key"]=args.request_key
        data=call("POST","/jobs",body,owner=args.owner)
    elif args.command=="chat":
        data=call("POST","/chat",{"message":args.message,"job_id":args.job},owner=args.owner)
    elif args.command=="list":
        data=call("GET","/jobs",owner=args.owner)
    elif args.command=="status":
        data=call("GET","/jobs/"+args.job,owner=args.owner)
        result=data.get("result",{})
        data={k:v for k,v in data.items() if k not in {"result","parameters"}}
        data["summary"]=result.get("analysis",{}).get("summary")
        data["price"]=result.get("price")
        data["titles"]=result.get("draft",{}).get("titles",[])
    elif args.command in {"resume","cancel"}:
        data=call("POST","/jobs/"+args.job+"/"+args.command,owner=args.owner)
    elif args.command=="assets":
        data=call("POST","/jobs/"+args.job+"/assets",{"folder":str(Path(args.folder).expanduser().resolve())},owner=args.owner)
    elif args.command=="download":
        import base64
        from urllib.parse import quote
        response=call("GET","/jobs/"+args.job+"/files/"+quote("商品运营包.zip"),download=True,owner=args.owner)
        out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
        target=out/(args.job+"_商品运营包.zip")
        target.write_bytes(base64.b64decode(response["download"]))
        data={"saved":str(target)}
    print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
