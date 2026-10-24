from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .api import router
from .config import get_settings

settings=get_settings()
app=FastAPI(title='ModelForge API',version='0.2.0')
app.add_middleware(CORSMiddleware,allow_origins=[settings['frontend_origin'],'http://127.0.0.1:5173'],allow_credentials=True,allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'],allow_headers=['Content-Type','X-CSRF-Token'])

@app.middleware('http')
async def request_size_limit(request: Request, call_next):
    length=request.headers.get('content-length')
    if length and length.isdigit() and int(length)>2_000_000:
        return JSONResponse(status_code=413,content={'detail':{'message':'Request exceeds the 2 MB limit.'}})
    return await call_next(request)

app.include_router(router)
