from pathlib import Path

path = Path('server.py')
text = path.read_text(encoding='utf-8')
old_import = 'from tools.http_security import RequestLimitsMiddleware\n'
new_import = (
    'from tools.http_security import RequestLimitsMiddleware, StreamingRequestLimitsMiddleware\n'
)
if old_import not in text:
    raise SystemExit('http security import marker missing')
text = text.replace(old_import, new_import, 1)

old_block = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            RequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
'''
new_block = '''    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=MCP_ALLOWED_HOSTS),
        Middleware(
            StreamingRequestLimitsMiddleware,
            max_body_bytes=MCP_HTTP_MAX_BODY_BYTES,
            max_header_bytes=MCP_HTTP_MAX_HEADER_BYTES,
        ),
        Middleware(
            CORSMiddleware,
'''
if old_block not in text:
    raise SystemExit('MCP middleware block marker missing')
text = text.replace(old_block, new_block, 1)

old_uvicorn = '''        log_level="warning",
        access_log=False,
        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,
'''
new_uvicorn = '''        log_level=LOG_LEVEL.lower(),
        access_log=LOG_LEVEL.upper() == "DEBUG",
        limit_concurrency=MCP_HTTP_CONNECTION_LIMIT,
'''
if old_uvicorn not in text:
    raise SystemExit('MCP uvicorn logging marker missing')
text = text.replace(old_uvicorn, new_uvicorn, 1)

path.write_text(text, encoding='utf-8')
Path('.github/patch_server_mcp_stream.py').unlink(missing_ok=True)
Path('.github/workflows/patch-server-mcp-stream.yml').unlink(missing_ok=True)
