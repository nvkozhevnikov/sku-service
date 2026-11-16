"""Server-side encrypted credentials; master key is provisioned outside data/Git.

Missing Linux credentials require an administrator to reconnect. DPAPI bytes are
never imported or decrypted. No raw key is returned to API clients or exporters.
"""
import os
import re
import stat
from pathlib import Path
from .storage import uid


class ServerVault:
    def __init__(self,directory,key_file=None):
        self.root=Path(directory).resolve();self.key_file=Path(key_file).resolve() if key_file else None
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name!='nt' and self.root.stat().st_mode & 0o077:
            raise ValueError('Каталог ключей должен иметь права0700.')

    def _cipher(self):
        if not self.key_file or not self.key_file.is_file():raise ValueError('Администратор должен настроить серверное хранилище и повторно подключить ключ.')
        if os.name!='nt' and self.key_file.stat().st_mode & 0o077:raise ValueError('Файл master key должен иметь права0600/0400.')
        from cryptography.fernet import Fernet
        return Fernet(self.key_file.read_bytes().strip())

    def available(self,ref):
        return bool(isinstance(ref,str) and re.fullmatch('[0-9a-f]{32}',ref) and self.key_file and self.key_file.is_file() and (self.root/(ref+'.fernet')).is_file())

    def put(self,value):
        if not isinstance(value,str) or not 16<=len(value)<=4096 or not re.fullmatch(r'[\x21-\x7e]+',value):raise ValueError('Некорректный ключ API.')
        encrypted=self._cipher().encrypt(value.encode());ref=uid()
        path=self.root/(ref+'.fernet')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(encrypted);f.flush();os.fsync(f.fileno())
        return ref

    def get(self,ref):
        if not self.available(ref):raise ValueError('Ключ не перенесён. Повторно подключите его в настройках администратора.')
        try:return self._cipher().decrypt((self.root/(ref+'.fernet')).read_bytes()).decode()
        except Exception:raise ValueError('Не удалось открыть серверный ключ. Обратитесь к администратору.') from None
