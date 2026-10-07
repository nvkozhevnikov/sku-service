import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const root=process.cwd();
const release=path.join(root,'release','KAMI_FEATURE_RC_2026-10-06');
const output=path.join(root,'outputs','KAMI_FEATURE_RC_2026-10-06');
await fs.mkdir(output,{recursive:true});
const data=JSON.parse(await fs.readFile(path.join(release,'WORKBOOK_DATA.json'),'utf8'));
const inputs=JSON.parse(await fs.readFile(path.join(root,'reports/KAMI_INTEGRATION_2026-10-05/SELECTION_RECONCILIATION_2026-10-06/EXPORT_INPUT_ROWS.json'),'utf8')).rows;
const byKey=new Map(inputs.map(r=>[`${r.source}\0${r.external_id}`,r]));
const wb=Workbook.create();const summary=wb.worksheets.add('Сводка');
const chunks=[];const sheets=[];
for(const [name,records] of Object.entries(data.tables)){
  const sheet=wb.worksheets.add(name);sheets.push([name,sheet,records]);
  if(name==='Review'||name==='Conflict') for(const record of records){
    const row=byKey.get(`${record.supplier}\0${record.external_id}`);
    Object.assign(record,{price:row?.price??null,price_state:row?.price_state??null,
      price_basis:row?.price_basis??null,currency:row?.currency??null,availability:row?.availability??null,
      blocking_reasons_summary:row?.kami_matching_evidence?.blocking_reasons?.join('; ')??null});
  }
}
const font={name:'Arial',size:10,color:'#172B4D'};
function column(index){let out='';for(index++;index;index=Math.floor((index-1)/26))out=String.fromCharCode(65+(index-1)%26)+out;return out;}
function clean(value,key,ref,sheetName){
  if(value===null||value===undefined)return null;
  if(key==='selected'&&typeof value==='boolean')return value?'ДА':'НЕТ';
  if(typeof value==='object')value=JSON.stringify(value);
  if(['price','numeric_price','quantity'].includes(key)&&value!==''){
    const number=Number(value);if(Number.isFinite(number))return number;
  }
  if(['observed_at','capture_time'].includes(key)&&value){const date=new Date(value);if(!Number.isNaN(+date))return date;}
  if(typeof value==='string'&&value.length>30000){
    for(let start=0,part=1;start<value.length;start+=4000,part++)chunks.push({sheet:sheetName,source_ref:ref,field:key,part,text:value.slice(start,start+4000)});
    return value.slice(0,500)+'… Полное значение: лист «Длинные значения», source_ref='+ref;
  }
  if(typeof value==='string'&&value.startsWith('='))return "'"+value;
  return value;
}
function fill(sheet,name,records){
  const keys=[...new Set(records.flatMap(r=>Object.keys(r)))];if(!keys.length)keys.push('source_ref');
  sheet.showGridLines=false;sheet.getRange('A2').values=[[name]];sheet.getRange('A2').format.font={...font,size:14,bold:true};
  const matrix=[keys,...records.map(r=>keys.map(k=>clean(r[k],k,r.source_ref??r.external_id??r.site_id,name)))];
  const area=sheet.getRangeByIndexes(4,0,matrix.length,keys.length);area.values=matrix;area.format.font=font;area.format.verticalAlignment='center';
  area.format.rowHeight=24;area.format.columnWidth=23;
  const header=sheet.getRangeByIndexes(4,0,1,keys.length);header.format={fill:'#243B5A',font:{...font,bold:true,color:'#FFFFFF'},wrapText:true,rowHeight:42};
  for(let i=0;i<keys.length;i++){
    const k=keys[i],range=sheet.getRangeByIndexes(5,i,Math.max(1,records.length),1);
    if(['name','description','blocker'].includes(k))range.format.columnWidth=54;
    if(k==='source_ref')range.format.columnWidth=35;
    if(['new_group_id','new_candidate_id'].includes(k))range.format.columnWidth=40;
    if(['model','execution','model_execution'].includes(k)){range.format.columnWidth=42;range.format.wrapText=true;range.format.rowHeight=42;}
    if(k==='external_id'){range.format.columnWidth=45;range.format.wrapText=true;range.format.rowHeight=42;}
    if(k==='blocking_reasons_summary'){range.format.columnWidth=74;range.format.wrapText=true;range.format.rowHeight=42;}
    if(name==='Длинные значения'&&k==='text'){range.format.columnWidth=140;range.format.wrapText=true;range.format.rowHeight=350;}
    if(name==='ROBOTS_EXCLUDED'&&k==='url'){range.format.columnWidth=62;range.format.wrapText=true;range.format.rowHeight=90;}
    if(k.includes('url')||k.includes('evidence')||k.includes('provenance'))range.format.columnWidth=46;
    if(k==='name'){range.format.wrapText=true;range.format.rowHeight=42;}
    if(['price','numeric_price','quantity'].includes(k))range.format.numberFormat='#,##0.####';
    else if(['observed_at','capture_time'].includes(k))range.format.numberFormat='yyyy-mm-dd hh:mm';
    else if(k.endsWith('_id')||k==='source_ref')range.format.numberFormat='@';
  }
  sheet.freezePanes.freezeRows(5);sheet.freezePanes.freezeColumns(3);
  sheet.tables.add(`A5:${column(keys.length-1)}${5+records.length}`,true,'T'+name.replace(/[^a-zA-Z0-9]/g,'')+'Records').showFilterButton=true;
  return keys;
}
for(const [name,sheet,records] of sheets)fill(sheet,name,records);
if(chunks.length){const sheet=wb.worksheets.add('Длинные значения');fill(sheet,'Длинные значения',chunks);sheets.push(['Длинные значения',sheet,chunks]);}
summary.showGridLines=false;summary.getRange('A2').values=[['Universal Supplier KAMI RC']];summary.getRange('A2').format.font={...font,size:14,bold:true};
summary.getRange('A4:B4').values=[['Статус / показатель','Карточек']];summary.getRange('A4:B4').format={fill:'#243B5A',font:{...font,bold:true,color:'#FFFFFF'}};
summary.getRange('A5:A13').values=[['Existing'],['FULL NEW'],['Review'],['Conflict'],['Все source identities'],['ROBOTS_EXCLUDED (не товары)'],['Selected offers'],['KAMI REVIEW'],['Source namespaces']];
const sourceRows=data.tables['Source trace'].length;const end=sourceRows+5;
summary.getRange('B5:B13').formulas=[
 [`=COUNTIFS('Source trace'!H6:H${end},"EXISTING_CONFIRMED")`],
 [`=COUNTIFS('Source trace'!H6:H${end},"READY_TO_CREATE_FULL")`],
 [`=COUNTIFS('Source trace'!H6:H${end},"REVIEW")`],
 [`=COUNTIFS('Source trace'!H6:H${end},"CONFLICT")`],['=SUM(B5:B8)'],
 [`=COUNTA(ROBOTS_EXCLUDED!A6:A${5+data.exclusions})`],
 [`=COUNTIFS(Offers!T6:T${5+data.tables.Offers.length},TRUE)`],
 [`=COUNTIFS('Source trace'!B6:B${end},"kami",'Source trace'!H6:H${end},"REVIEW")`],
 ['=COUNTA(A17:A22)']];
// Selected column is discovered from the actual source table, not assumed.
const offersKeys=Object.keys(data.tables.Offers[0]);const selectedColumn=column(offersKeys.indexOf('selected'));
summary.getRange('B11').formulas=[[`=COUNTIFS(Offers!${selectedColumn}6:${selectedColumn}${5+data.tables.Offers.length},"ДА")`]];
summary.getRange('A16:B16').values=[['Supplier namespace','Source identities']];
summary.getRange('A17:B22').values=Object.entries(data.summary.namespaces);
summary.getRange('A4:B22').format.font=font;summary.getRange('A4:A22').format.columnWidth=40;summary.getRange('B4:B22').format.columnWidth=19;
summary.getRange('B5:B22').format.numberFormat='#,##0';summary.getRange('A4:B22').format.rowHeight=24;
summary.getRange('A4:B4').format.font={...font,bold:true,color:'#FFFFFF'};
summary.getRange('A16:B16').format={fill:'#243B5A',font:{...font,bold:true,color:'#FFFFFF'}};
summary.getRange('D5').values=[['Frozen evidence; не live supplier refresh']];summary.getRange('D7').values=[['28 selected только с verified SQL eligibility']];
summary.getRange('D9').values=[['KAMI disabled / offers inactive; selected = 0']];summary.getRange('D11').values=[['Neutral XML не является ESOL import payload']];
summary.getRange('D5:D11').format={font:{...font,italic:true},columnWidth:66};
wb.recalculate();
const expected=[506,2,8904,259,9671,2,28,5264,6];const actual=summary.getRange('B5:B13').values.map(r=>r[0]);
if(JSON.stringify(actual)!==JSON.stringify(expected))throw new Error('Workbook reconciliation failed: '+JSON.stringify(actual));
const inspect=await wb.inspect({kind:'table',range:'Сводка!A4:B22',include:'values,formulas',tableMaxRows:22,tableMaxCols:2});
await fs.writeFile(path.join(output,'SUMMARY_INSPECT.ndjson'),inspect.ndjson);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100}});
await fs.writeFile(path.join(output,'ERROR_SCAN.ndjson'),errors.ndjson);
for(const [name] of [['Сводка'],...sheets]){
  const range=name==='Сводка'?'A1:E23':'A1:G12';
  const png=await wb.render({sheetName:name,range,scale:1,format:'png'});
  await fs.writeFile(path.join(output,name.replace(/[^a-zA-Z0-9а-яА-Я]/g,'_')+'.png'),new Uint8Array(await png.arrayBuffer()));
}
const xlsx=await SpreadsheetFile.exportXlsx(wb);const file='UNIVERSAL_SUPPLIER_KAMI_RC_QA.xlsx';
await xlsx.save(path.join(output,file));await fs.copyFile(path.join(output,file),path.join(release,file));
await fs.writeFile(path.join(output,'VERIFIED.json'),JSON.stringify({counts:actual,expected,table_counts:Object.fromEntries(sheets.map(([n,s,r])=>[n,r.length])),long_values_preserved:chunks.length,rendered_sheets:1+sheets.length,source_typed_values:true},null,2));
console.log(JSON.stringify({file:path.join(release,file),counts:actual,long_chunks:chunks.length,rendered_sheets:1+sheets.length}));
