// Desktop QA authoring with the bundled Artifact Tool, not server runtime.
import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const output=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(output,'WORKBOOK_TABLES.json'),'utf8'));
const freeze=JSON.parse(await fs.readFile(path.join(output,'FREEZE_MANIFEST.json'),'utf8'));
const wb=Workbook.create();
const summary=wb.worksheets.add('Сводка');
for (const t of data) wb.worksheets.add(t.name);
const font={name:'Arial',size:10,color:'#172033'};
summary.showGridLines=false;
summary.getRange('A2').values=[['Universal Supplier FINAL RC']];
summary.getRange('A2').format.font={...font,size:14,bold:true};
summary.getRange('A4:B10').values=[['Состояние','Source rows'],['Existing',506],['NEW FULL',2],['Review',3640],['Conflict',259],['Всего',null],['Уникальные Sterbrust ID',458]];
summary.getRange('B5:B8').formulas=[
  ["=COUNTA('Existing'!A6:A511)"],
  ["=COUNTA('NEW full cards'!A6:A7)"],
  ["=COUNTA('Review'!A6:A3645)"],
  ["=COUNTA('Conflict'!A6:A264)"]];
summary.getRange('B9').formulas=[['=SUM(B5:B8)']];
summary.getRange('D4:E9').values=[['Namespace','Товаров'],...Object.entries(freeze.summary.namespaces)];
summary.getRange('A12').values=[['Цена по запросу: NULL. Наличие и цена независимы.']];
summary.getRange('A13').values=[['Предложения, не ESOL payload. Import и scheduler выключены.']];
summary.getRange('A14').values=[['Offers selection — только сравнительный public-price view, не цена Sterbrust.']];
summary.getRange('A4:E14').format.font=font;
summary.getRange('A4:B4').format={fill:'#243954',font:{...font,bold:true,color:'#FFFFFF'}};
summary.getRange('D4:E4').format={fill:'#243954',font:{...font,bold:true,color:'#FFFFFF'}};
summary.getRange('A:A').format.columnWidth=38;
summary.getRange('B:B').format.columnWidth=16;
summary.getRange('C:C').format.columnWidth=3;
summary.getRange('D:D').format.columnWidth=25;
summary.getRange('E:E').format.columnWidth=16;
summary.getRange('B5:B10').setNumberFormat('#,##0');
summary.getRange('A12:A14').format.font={...font,italic:true};
summary.tabColor='#243954';
const previews=[];
function literal(v) {
  if(typeof v==='string' && /^[=+@-]/.test(v)) return "'"+v;
  return v;
}
for (const t of data) {
  if(t.name==='NEW full cards') {
    const priority=['source_ref','supplier','name','brand','model_execution','SECTION_ID','section_name','price','price_state','availability'];
    const order=[...priority,...t.headers.filter(h=>!priority.includes(h))].map(h=>t.headers.indexOf(h));
    t.headers=order.map(i=>t.headers[i]);t.rows=t.rows.map(r=>order.map(i=>r[i]));
  }
  const sheet=wb.worksheets.getItem(t.name);
  sheet.showGridLines=false;
  sheet.getRange('A2').values=[[t.name]];
  sheet.getRange('A2').format.font={...font,size:14,bold:true};
  sheet.getRange('A3').values=[[`${t.rows.length} строк. Frozen 2026-10-02.`]];
  // Excel's cell text limit is explicit; never silently truncate source data.
  const specs=t.headers.map((h,i)=>({h,i,parts:Math.max(1,...t.rows.map(r=>Math.ceil(String(r[i]??'').length/30000)))}));
  const headers=specs.flatMap(s=>Array.from({length:s.parts},(_,j)=>s.parts===1?s.h:`${s.h} part ${j+1}/${s.parts}`));
  const rows=t.rows.map(r=>specs.flatMap(s=>Array.from({length:s.parts},(_,j)=>{
    const v=r[s.i];return literal(s.parts===1?v:(String(v??'').slice(j*30000,(j+1)*30000)||null));
  })));
  sheet.getRangeByIndexes(4,0,1,headers.length).values=[headers];
  sheet.getRangeByIndexes(5,0,rows.length,headers.length).values=rows;
  const used=sheet.getRangeByIndexes(4,0,rows.length+1,headers.length);
  used.format.font=font;
  used.format.rowHeight=62;
  used.format.horizontalAlignment='left';
  used.format.verticalAlignment='center';
  used.format.columnWidth=24;
  sheet.getRangeByIndexes(4,0,1,headers.length).format={fill:'#243954',font:{...font,bold:true,color:'#FFFFFF'},wrapText:true,rowHeight:46};
  for(let c=0;c<headers.length;c++) {
    const h=headers[c];
    if(['name','description','identity_evidence','blocker','source_url','provenance','candidate_evidence','external_id'].includes(h)) {
      sheet.getRangeByIndexes(4,c,rows.length+1,1).format.columnWidth=h==='name'?48:52;
    }
    if(['name','external_id','source_ref','new_group_id','new_candidate_id'].includes(h)) sheet.getRangeByIndexes(5,c,rows.length,1).format.wrapText=true;
    if(h==='source_ref'||h==='new_group_id'||h==='new_candidate_id')sheet.getRangeByIndexes(4,c,rows.length+1,1).format.columnWidth=34;
    if(h==='supplier')sheet.getRangeByIndexes(4,c,rows.length+1,1).format.columnWidth=20;
    if(h==='model'||h==='model_execution')sheet.getRangeByIndexes(4,c,rows.length+1,1).format.columnWidth=32;
    if(h==='numeric_price') sheet.getRangeByIndexes(5,c,rows.length,1).setNumberFormat('#,##0.00');
    if(h==='SECTION_ID') sheet.getRangeByIndexes(5,c,rows.length,1).setNumberFormat('0');
  }
  // Long machine evidence remains fully available in cells/CSV/XML, not oversized row heights.
  sheet.tables.add(sheet.getRangeByIndexes(4,0,rows.length+1,headers.length),true,`RC_${t.name.replace(/\W/g,'_')}`);
  sheet.getRangeByIndexes(5,0,rows.length,headers.length).format.horizontalAlignment='left';
  sheet.freezePanes.freezeRows(5);
  sheet.freezePanes.freezeColumns(2);
  if(t.name==='Review')sheet.tabColor='#B58928';
  if(t.name==='Conflict')sheet.tabColor='#AD4343';
  if(t.name==='NEW full cards')sheet.tabColor='#426B42';
}
wb.recalculate();
const inspect=await wb.inspect({kind:'region',sheetId:'Сводка',range:'A4:E10',maxChars:3000});
await fs.writeFile(path.join(output,'WORKBOOK_INSPECTION.json'),inspect.ndjson);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:3000});
await fs.writeFile(path.join(output,'WORKBOOK_ERROR_SCAN.json'),errors.ndjson);
await fs.mkdir(path.join(output,'previews'),{recursive:true});
for(const name of ['Сводка',...data.map(t=>t.name)]) {
  const png=await wb.render({sheetName:name,range:name==='Сводка'?'A1:F15':'A1:F10',scale:1.4,format:'png'});
  const filename=`${name.replace(/\W/g,'_')||'summary'}.png`;
  await fs.writeFile(path.join(output,'previews',filename),new Uint8Array(await png.arrayBuffer()));
  previews.push({sheet:name,file:filename});
}
const xlsx=await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(path.join(output,'UNIVERSAL_SUPPLIER_RC_QA.xlsx'));
await fs.writeFile(path.join(output,'WORKBOOK_VERIFICATION.json'),JSON.stringify({sheets:['Сводка',...data.map(t=>t.name)],previews,summary_cells:summary.getRange('B5:B10').values},null,2));
console.log('WORKBOOK_EXPORTED');
// All asynchronous exports and verification records completed before explicit exit.
process.exit(0);
