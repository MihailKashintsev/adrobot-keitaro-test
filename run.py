import os
from pathlib import Path
if Path('.env').exists():
    for line in Path('.env').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            k,v=line.split('=',1);os.environ.setdefault(k,v)
if __name__=='__main__':
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=8787)
