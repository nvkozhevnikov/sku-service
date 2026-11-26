"""Exercise current urllib transport with an ASCII-target fake opener; HTTP=0."""
from pathlib import Path
import json
import hashlib
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from universal_supplier.http_capture import UrllibPublicHttpClient
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT=BASE/'UNICODE_RESUME_2026-10-14'


class AsciiTargetOpener:
    def open(self,request,timeout):
        request.selector.encode('ascii')
        raise AssertionError('Unexpected transport success before fix')


if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    url=json.loads((BASE/'RESUME_2026-10-14/LIVE_CANARY.json').read_text('utf-8'))['failure_row']['requested_url']
    receipt=dict(source_url=url,supplier_http=0,code_sha256=hashlib.sha256((ROOT/'universal_supplier/http_capture.py').read_bytes()).hexdigest())
    with patch('universal_supplier.http_capture.build_opener',return_value=AsciiTargetOpener()):
        try:
            UrllibPublicHttpClient().get(url,timeout_seconds=1)
            raise AssertionError('Defect not reproduced')
        except UnicodeEncodeError as error:
            receipt.update(status='VERIFIED_UNICODE_PATH_ENCODING_DEFECT',path='UrllibPublicHttpClient._get -> urllib.Request.selector -> HTTP ASCII request target',
                           error_type=type(error).__name__,bad_codepoints=[hex(ord(c)) for c in error.object[error.start:error.end]])
    (OUT/'TRANSPORT_BEFORE.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(receipt,ensure_ascii=True))
