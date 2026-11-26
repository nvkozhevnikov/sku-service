import json
import subprocess
from pathlib import Path
import pytest
from openpyxl import load_workbook
from scripts.generate_proposed_supplier_files import _csv_cell
from scripts.assemble_final_rc import csv_row
from scripts.build_proposed_workbooks_server import build,_value

@pytest.mark.parametrize('text',['=1+1','+cmd','-cmd','@SUM(A1)','\t=cmd','\r=cmd'])
def test_csv_exports_neutralize_formula_strings(text):
    assert _csv_cell(text)=="'"+text
    assert csv_row({'name':text,'price':42})=={'name':"'"+text,'price':42}

def test_export_numeric_and_identity_types_are_not_rewritten():
    assert _csv_cell(42)==42 and _csv_cell('001282')=='001282'
    assert _value({'price':'42.25'},'price')==42.25
    assert _value({'match_conflicts':['=cmd',1]},'match_conflicts')=="'=cmd; 1"

def test_real_server_workbooks_store_source_text_as_literal(tmp_path):
    source=tmp_path/'input.json';out=tmp_path/'exports'
    source.write_text(json.dumps({'diagnostic_only':True,'esol_payload':False,'tables':{
        'existing':[{'sterbrust_product_id':'1282','model':'=1+1','price':42.25,'source_external_id':'001282'}],
        'new':[],'review':[{'name':'@SUM(A1)','match_conflicts':['=cmd']}]}}),encoding='utf-8')
    before=source.read_bytes();counts=build(source,out)
    assert source.read_bytes()==before and counts
    sheet=load_workbook(out/'STERBRUST_UPDATE_EXISTING.xlsx').active
    assert sheet['A6'].value=='1282' and sheet['B6'].value=="'=1+1" and sheet['B6'].data_type=='s'
    assert sheet['D6'].value==42.25 and sheet['H6'].value=='001282'
    for p in out.glob('*.xlsx'):
        assert all(c.data_type!='f' for row in load_workbook(p).active for c in row)

def test_desktop_workbook_guard_and_entrypoints_have_valid_javascript():
    root=Path(__file__).resolve().parents[1]
    module=(root/'scripts/spreadsheet_safe_text.mjs').as_uri()
    code='import {spreadsheetSafeValue as s} from '+json.dumps(module)+'; console.log(JSON.stringify([s("=1"),s("\\t=1"),s(42),s("001282")]));'
    result=subprocess.run(['node','--input-type=module','-e',code],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==["'=1","'\t=1",42,'001282']
    for name in ('build_proposed_workbooks.mjs','build_final_rc_workbook.mjs'):
        subprocess.run(['node','--check',str(root/'scripts'/name)],capture_output=True,check=True)
