import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook} from '@oai/artifact-tool';

if(process.argv.includes('--csv-help')){
  const wb=Workbook.create();wb.worksheets.add('Probe');
  console.log(wb.help('SpreadsheetFile.exportCsv',{include:'index,examples,notes',maxChars:2000}).ndjson);
  process.exit(0);
}
const base=path.join(process.cwd(),'reports','MATCHING_V2_2_AI_REVIEW');
const tables=JSON.parse(await fs.readFile(path.join(base,'CSV_TABLES.json'),'utf8'));
const results=[];
for(const [filename,table] of Object.entries(tables)){
  const wb=Workbook.create();const sheet=wb.worksheets.add('Review');
  // All identity/evidence fields remain strings; no SKU or model coercion.
  const matrix=[table.columns,...table.rows.map(row=>table.columns.map(k=>{
    const value=row[k];return value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
  }))];
  sheet.getRangeByIndexes(0,0,matrix.length,table.columns.length).values=matrix;
  wb.recalculate();
  const values=sheet.getRangeByIndexes(0,0,matrix.length,table.columns.length).values;
  if(JSON.stringify(values)!==JSON.stringify(matrix))throw new Error('Raw value coercion: '+filename);
  // Public API documents CSV import but no CSV export. Serialize verified
  // literal range values as RFC4180 text rather than inventing an XLSX deliverable.
  const quote=value=>'"'+String(value).replaceAll('"','""')+'"';
  const body='\uFEFF'+values.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n';
  await fs.writeFile(path.join(base,filename),body,'utf8');
  const inspected=await wb.inspect({kind:'table',range:'Review!A1:D3',include:'values',tableMaxRows:3,tableMaxCols:4,maxChars:700});
  results.push({filename,records:matrix.length-1,columns:table.columns.length,raw_string_roundtrip:true,inspection:inspected.ndjson});
}
await fs.writeFile(path.join(base,'CSV_VERIFICATION.json'),JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify(results.map(({filename,records})=>({filename,records}))));
