"""Read and render saved PDFs only; no network or source mutation."""
from pathlib import Path
import json
from pypdf import PdfReader
import pypdfium2 as pdfium
ROOT=Path(__file__).resolve().parents[1]
CAP=ROOT.parent/'sku-service-priority-near-complete-rc1/priority_near_complete/targeted_evidence_2026-10-02/captures'
OUT=ROOT/'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02/DOCUMENT_CHECK'
def main():
    OUT.mkdir(parents=True,exist_ok=True);evidence=[]
    for path in sorted(CAP.glob('*.pdf')):
        reader=PdfReader(path);texts=[p.extract_text() or '' for p in reader.pages]
        selected=[i for i,t in enumerate(texts) if
            (path.name.startswith('bekamak-') and i==2) or
            (path.name.startswith('optimum-') and ('TU 2807' in t or 'Net weight' in t)) or
            (path.name.startswith('smec-') and ('Spindle bore' in t or 'Power (cont' in t or 'S1' in t))]
        doc=pdfium.PdfDocument(path)
        for i in selected:
            png=OUT/f'{path.stem}-page{i+1}.png'
            if not png.exists():doc[i].render(scale=1.4).to_pil().save(png)
        evidence.append({'pdf':str(path),'pages':len(texts),'selected_pages':[i+1 for i in selected],
                         'text':[{'page':i+1,'text':texts[i]} for i in selected]})
    (OUT/'PDF_EXTRACTED.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    for e in evidence:print(e['pdf'],e['selected_pages'])
if __name__=='__main__':main()
