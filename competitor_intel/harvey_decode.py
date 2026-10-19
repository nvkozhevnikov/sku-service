"""Strict charset decoding; one evidence-verified truncated teaser byte fallback."""
import codecs,re
from bs4 import BeautifulSoup

def decode_harvey(data,content_type=None):
    candidates=[]
    header=re.search(r'charset\s*=\s*["\x27]?([\w-]+)',content_type or '',re.I)
    if header:candidates.append(('HTTP_CHARSET',header[1]))
    for bom,codec in ((codecs.BOM_UTF32_LE,'utf-32'),(codecs.BOM_UTF32_BE,'utf-32'),(codecs.BOM_UTF8,'utf-8-sig'),(codecs.BOM_UTF16_LE,'utf-16'),(codecs.BOM_UTF16_BE,'utf-16')):
        if data.startswith(bom):candidates.append(('BOM',codec));break
    meta=re.search(br'<meta[^>]+charset\s*=\s*["\x27]?([\w-]+)',data[:12000],re.I)
    if meta:candidates.append(('HTML_META',meta[1].decode('ascii')))
    candidates.append(('SOURCE_VERIFIED_UTF8','utf-8'))
    for method,codec in candidates:
        try:return data.decode(codec),{'method':method,'encoding':codec,'damaged_spans':[]}
        except (UnicodeDecodeError,LookupError):pass
    # Retained three responses each contain exactly one orphan UTF8 Cyrillic
    # lead byte before a literal ellipsis in .new-detail__title-info. Neither
    # prices nor identifiers may be repaired. Every other byte must be valid.
    try:data.decode('utf-8')
    except UnicodeDecodeError as error:
        pos=error.start;bad=data[pos:error.end]
        if bad not in (b'\xd0',b'\xd1') or data[error.end:error.end+3]!=b'...':raise ValueError('AMBIGUOUS_HARVEY_ENCODING') from error
        try:prefix=data[:pos].decode('utf-8');suffix=data[error.end:].decode('utf-8')
        except UnicodeDecodeError as other:raise ValueError('MULTIPLE_OR_UNVERIFIED_DAMAGED_SPANS') from other
        marker='[INCOMPLETE_UTF8_'+bad.hex().upper()+']'
        decoded=prefix+marker+suffix;soup=BeautifulSoup(decoded,'html.parser')
        node=soup.find(string=lambda s:s and marker in s)
        if node is None or node.find_parent(class_='new-detail__title-info') is None:raise ValueError('DAMAGE_OUTSIDE_VERIFIED_TEASER')
        if node.find_parent(class_=re.compile(r'price|cost|title-price')) is not None:raise ValueError('DAMAGE_IN_COMMERCIAL_FACT')
        return decoded,{'method':'VERIFIED_TRUNCATED_UTF8_TEASER','encoding':'utf-8','damaged_spans':[{'offset':pos,'raw_hex':bad.hex(),'representation':marker}],'missing_character':'UNKNOWN_NOT_RECONSTRUCTED'}
    raise ValueError('AMBIGUOUS_HARVEY_ENCODING')
