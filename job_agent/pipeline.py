"""User-triggered collection and offline matching; no persistent scheduler."""
from .core import batch_match
from .sources import require_permission, sync_board


def collect_and_match(db, *, transport=None, sleeper=None):
    results=[]
    for permission in db.execute('SELECT id FROM source_permissions WHERE revoked_at IS NULL ORDER BY id').fetchall():
        ident=permission['id']
        try:
            require_permission(db,ident)
        except PermissionError:
            results.append({'permission_id':ident,'status':'skipped','reason':'授权已过期'})
            continue
        try:
            kwargs={'transport':transport}
            if sleeper is not None: kwargs['sleeper']=sleeper
            result=sync_board(db,ident,**kwargs)
            results.append({'permission_id':ident,'status':'success',**result})
        except Exception as exc:
            # Isolate failed boards; avoid persisting payloads, secrets or full error text.
            results.append({'permission_id':ident,'status':'failed','error_type':type(exc).__name__})
    return {'sources':results,'matches':batch_match(db),
            'notice':'匹配优先使用已手动导入的 LinkedIn/OpenWork JD；官方 JD 为回退。无授权来源不会访问网络。'}
