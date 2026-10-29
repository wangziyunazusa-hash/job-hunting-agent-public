"""Explicitly authorized, read-only ATS board synchronization. No application API."""
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, parse_qs
from .core import safe_url, import_job, savepoint

PROVIDERS = {
    'greenhouse': ('boards-api.greenhouse.io', '/v1/boards/{board}/jobs'),
    'lever': ('api.lever.co', '/v0/postings/{board}'),
    'lever_eu': ('api.eu.lever.co', '/v0/postings/{board}'),
    'ashby': ('api.ashbyhq.com', '/posting-api/job-board/{board}'),
}
MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 100
PAGE_SIZE = 100
CACHE_SECONDS = 300

def now():
    return datetime.now(timezone.utc)

def stamp(value=None):
    return (value or now()).isoformat()

def parse_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return result.astimezone(timezone.utc)

def endpoint(provider, board, offset=0):
    if provider not in PROVIDERS or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', board):
        raise ValueError('未知 ATS 或非法 board 名称')
    host, path = PROVIDERS[provider]
    query = {'content':'true'} if provider == 'greenhouse' else ({'mode':'json','skip':offset,'limit':PAGE_SIZE} if provider.startswith('lever') else {})
    return 'https://' + host + path.format(board=board) + ('?'+urlencode(query) if query else '')

def register_permission(db, *, provider, board, company, evidence, official_url, expires_at, confirmed=False):
    endpoint(provider,board)
    official_url=safe_url(official_url)
    host=urlsplit(official_url).hostname
    if any(host == d or host.endswith('.'+d) for d in ('linkedin.com','openwork.jp')):
        raise ValueError('LinkedIn/OpenWork 不可作为官方 API 授权')
    if not confirmed or not company.strip() or not evidence.strip() or parse_time(expires_at)<=now():
        raise ValueError('需要公司、权限证据、未来失效时间及明确确认')
    with db:
        ident=db.execute('INSERT INTO source_permissions(provider,board,company,evidence,official_url,expires_at) VALUES(?,?,?,?,?,?)',
                         (provider,board,company.strip(),evidence.strip(),official_url,stamp(parse_time(expires_at)))).lastrowid
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('permission_registered',ident))
    return ident

def revoke_permission(db, permission_id):
    with db:
        db.execute('UPDATE source_permissions SET revoked_at=? WHERE id=?',(stamp(),permission_id))
        db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('permission_revoked',permission_id))

def require_permission(db, ident):
    row=db.execute('SELECT * FROM source_permissions WHERE id=?',(ident,)).fetchone()
    if row is None or row['revoked_at'] or parse_time(row['expires_at'])<=now():
        raise PermissionError('授权缺失、已撤销或已过期')
    return row

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden+=1
        elif not self.hidden and tag in ('p','br','li','div'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
        elif not self.hidden: self.parts.append(' ')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def plain(value):
    if not isinstance(value,str): raise ValueError('JD 类型错误')
    parser=PlainText(); parser.feed(html.unescape(value))
    return ''.join(parser.parts).strip()

def normalize(provider, payload):
    rows=payload if provider.startswith('lever') else payload.get('jobs') if isinstance(payload,dict) else None
    if not isinstance(rows,list): raise ValueError('接口响应格式错误')
    if provider=='greenhouse' and 'meta' in payload:
        total=payload['meta'].get('total')
        if not isinstance(total,int) or total!=len(rows):
            raise ValueError('岗位列表不完整；拒绝执行下架检测')
    result=[]; seen=set()
    for item in rows:
        if not isinstance(item,dict): raise ValueError('岗位记录格式错误')
        if provider=='ashby' and item.get('isListed') is False: continue
        if provider=='greenhouse':
            ident=item.get('id'); url=item.get('absolute_url'); app=url
            loc=item.get('location',{}).get('name'); jd=plain(item.get('content',''))
            title=item.get('title')
        elif provider.startswith('lever'):
            ident=item.get('id'); url=item.get('hostedUrl'); app=item.get('applyUrl')
            loc=item.get('categories',{}).get('location'); title=item.get('text')
            parts=[item.get('descriptionPlain') or plain(item.get('description',''))]
            for section in item.get('lists',[]):
                parts.extend([section.get('text',''),plain(section.get('content',''))])
            parts.append(item.get('additionalPlain') or plain(item.get('additional','')))
            jd='\n'.join(parts).strip()
        else:
            url=item.get('jobUrl'); ident=url; app=item.get('applyUrl'); title=item.get('title')
            loc=item.get('location'); jd=item.get('descriptionPlain') or plain(item.get('descriptionHtml',''))
        if not isinstance(ident,(str,int)) or not str(ident) or not isinstance(title,str) or not title.strip() or not isinstance(jd,str) or not jd.strip() or len(jd)>200000:
            raise ValueError('岗位缺少稳定标识、标题或完整 JD；整次刷新取消')
        url=safe_url(url or ''); app=safe_url(app) if app else ''
        # Do not mistake ATS posting IDs for company requisition IDs.
        if str(ident) in seen: raise ValueError('重复 API 岗位标识')
        seen.add(str(ident))
        result.append({'external_id':str(ident),'title':title,'url':url,'application_url':app,
                       'location':loc or '未注明','jd':jd})
    return result

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host,timeout=15,context=ssl.create_default_context()); self.address=address
    def connect(self):
        sock=socket.create_connection((self.address,443),timeout=self.timeout)
        try: self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception:
            sock.close(); raise

class ReadOnlyTransport:
    """Fixed hosts; public DNS pinned to connection; no proxy, redirects or writes."""
    def get(self, url):
        u=urlsplit(url)
        if u.scheme!='https' or u.hostname not in {v[0] for v in PROVIDERS.values()} or u.port not in (None,443) or u.username or u.password:
            raise PermissionError('不允许的 API 地址')
        provider=next(k for k,v in PROVIDERS.items() if v[0]==u.hostname)
        template=PROVIDERS[provider][1]
        pattern=re.escape(template).replace(re.escape('{board}'),r'[A-Za-z0-9_-]{1,100}')
        query=parse_qs(u.query,keep_blank_values=True)
        expected={'content'} if provider=='greenhouse' else ({'mode','skip','limit'} if provider.startswith('lever') else set())
        if not re.fullmatch(pattern,u.path) or set(query)!=expected or u.fragment:
            raise PermissionError('仅允许固定岗位列表接口')
        if provider=='greenhouse' and query!={'content':['true']}:
            raise PermissionError('非法查询参数')
        if provider.startswith('lever') and (query.get('mode')!=['json'] or query.get('limit')!=[str(PAGE_SIZE)] or len(query.get('skip',[]))!=1 or not query['skip'][0].isdigit()):
            raise PermissionError('非法分页参数')
        addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
        ips=[info[4][0] for info in addresses]
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise PermissionError('API 解析到非公网地址')
        conn=PinnedHTTPS(u.hostname,ips[0])
        try:
            conn.request('GET',u.path+('?' + u.query if u.query else ''),headers={'Accept':'application/json','User-Agent':'JobHuntingAgent/0.2'})
            response=conn.getresponse()
            if response.status!=200:
                return response.status, None, dict(response.getheaders())
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES: raise ValueError('响应超过大小限制')
            if 'json' not in response.getheader('Content-Type','').lower(): raise ValueError('API 未返回 JSON')
            return response.status,json.loads(raw),dict(response.getheaders())
        finally: conn.close()

def read_page(db, permission, url, transport, *, force=False, sleeper=time.sleep):
    # Exact path/query generated by this service, not arbitrary permission URLs.
    permission=require_permission(db,permission['id'])
    allowed={endpoint(permission['provider'],permission['board'],page*PAGE_SIZE)
             for page in range(MAX_PAGES if permission['provider'].startswith('lever') else 1)}
    if url not in allowed:
        raise PermissionError('请求超出该 board 的授权范围')
    cached=db.execute('SELECT * FROM api_cache WHERE permission_id=? AND url=?',(permission['id'],url)).fetchone()
    if not force and cached and (now()-parse_time(cached['fetched_at'])).total_seconds()<CACHE_SECONDS:
        return json.loads(cached['payload_json'])
    for attempt in range(3):
        require_permission(db,permission['id'])
        status,payload,headers=transport.get(url)
        if status==200:
            if len(json.dumps(payload).encode())>MAX_BYTES: raise ValueError('响应超过大小限制')
            with db:
                db.execute('INSERT OR REPLACE INTO api_cache VALUES(?,?,?,?)',(permission['id'],url,json.dumps(payload),stamp()))
            return payload
        if status==429 or 500<=status<600:
            retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
            # Refuse long/server-date cooldowns rather than retry too early.
            if retry is not None and (not retry.isdigit() or int(retry)>30):
                raise ValueError('服务器要求较长等待；请稍后重试')
            if attempt<2:
                sleeper(max(2**attempt,int(retry or 0))); continue
        raise ValueError(f'API HTTP {status}；未修改岗位状态')
    raise ValueError('API 重试次数耗尽')

def sync_board(db, permission_id, *, transport=None, force=False, sleeper=time.sleep):
    permission=require_permission(db,permission_id)
    # Small minimum interval also protects force-refresh from accidental repeated clicks.
    recent=db.execute('SELECT started_at FROM sync_runs WHERE permission_id=? ORDER BY id DESC LIMIT 1',(permission_id,)).fetchone()
    if recent and (now()-parse_time(recent['started_at'])).total_seconds()<5:
        raise ValueError('请至少等待 5 秒后再刷新')
    with db:
        run_id=db.execute("INSERT INTO sync_runs(permission_id,started_at,status) VALUES(?,?,'running')",(permission_id,stamp())).lastrowid
    try:
        transport=transport or ReadOnlyTransport()
        provider=permission['provider']; posts=[]
        for page in range(MAX_PAGES):
            payload=read_page(db,permission,endpoint(provider,permission['board'],page*PAGE_SIZE),transport,force=force or provider.startswith('lever'),sleeper=sleeper)
            posts.extend(normalize(provider,payload))
            if not provider.startswith('lever') or len(payload)<PAGE_SIZE: break
            sleeper(1)
        else: raise ValueError('分页超过上限；不执行下架检测')
        if len({r['external_id'] for r in posts})!=len(posts): raise ValueError('分页出现重复岗位；刷新取消')
        require_permission(db,permission_id)
        with savepoint(db):
            for post in posts:
                job_id=import_job(db,company=permission['company'],title=post['title'],location=post['location'],
                                  source='official',url=post['url'],jd=post['jd'],application_url=post['application_url'],method='api')
                link_id=db.execute('SELECT id FROM job_source_links WHERE url=?',(post['url'],)).fetchone()[0]
                db.execute('INSERT INTO source_postings VALUES(?,?,?,1,?) ON CONFLICT(permission_id,external_id) DO UPDATE SET source_link_id=excluded.source_link_id,active=1,last_seen_run=excluded.last_seen_run',
                           (permission_id,post['external_id'],link_id,run_id))
            # Availability is per source, never global job expiry.
            db.execute('UPDATE source_postings SET active=0 WHERE permission_id=? AND last_seen_run<>?',(permission_id,run_id))
            db.execute("UPDATE sync_runs SET status='success',finished_at=?,item_count=? WHERE id=?",(stamp(),len(posts),run_id))
            db.execute('INSERT INTO audit_events(action,entity_id) VALUES(?,?)',('board_sync_success',run_id))
        return {'run_id':run_id,'count':len(posts)}
    except Exception:
        with db:
            db.execute("UPDATE sync_runs SET status='failed',finished_at=?,error=? WHERE id=?",(stamp(),'刷新失败；未执行整批岗位或下架更新',run_id))
        raise
