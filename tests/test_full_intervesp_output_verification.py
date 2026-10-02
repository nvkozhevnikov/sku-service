import json
import zipfile
import pytest
from scripts.verify_full_intervesp_outputs import workbook_rows, publish_panel, sha, FILES


def xlsx(path, cells):
    body = ''.join(f'<c r="{col}6" t="inlineStr"><is><t>{value}</t></is></c>' for col, value in cells.items())
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                         f'<sheetData><row r="6">{body}</row></sheetData></worksheet>')


def test_workbook_rows_and_empty_placeholder(tmp_path):
    path = tmp_path / 'test.xlsx'
    xlsx(path, {'A': '123', 'B': 'Станок'})
    assert workbook_rows(path, 1) == [{'A': '123', 'B': 'Станок'}]
    with pytest.raises(ValueError, match='row count'): workbook_rows(path, 2)
    xlsx(path, {'A': 'Подтверждённых записей для этого файла нет.'})
    assert workbook_rows(path, 0) == []


def test_workbook_formulas_are_rejected(tmp_path):
    path = tmp_path / 'test.xlsx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                         '<sheetData><row r="6"><c r="A6"><f>1+1</f><v>2</v></c></row></sheetData></worksheet>')
    with pytest.raises(ValueError, match='formula'): workbook_rows(path, 1)


def test_versioned_publication_preserves_previous_index_and_files(tmp_path):
    final, public = tmp_path / 'final', tmp_path / 'public'
    final.mkdir(); public.mkdir()
    (final / 'PROPOSAL_TABLES.json').write_text(json.dumps({'diagnostic_only': True, 'esol_payload': False}), encoding='utf-8')
    old = b'{"old":true}'
    (public / 'PROPOSAL_TABLES.json').write_bytes(old)
    for name in FILES: (final / name).write_bytes(name.encode())
    hashes = {name: sha(final / name) for name in FILES}
    result = publish_panel(final, {'output_sha256': hashes}, public)
    index = json.loads((public / 'PROPOSAL_TABLES.json').read_text())
    assert index['artifact_dir'] == result['artifact_dir']
    assert index['verification']['full_live_run2'] == 'DEFERRED'
    assert next(public.glob('PROPOSAL_TABLES.before-*.json')).read_bytes() == old
    for name in FILES: assert sha(public / result['artifact_dir'] / name) == hashes[name]
    with pytest.raises(ValueError, match='already exists'): publish_panel(final, {'output_sha256': hashes}, public)


def test_panel_publication_rejects_changed_bytes_without_replacing_index(tmp_path):
    final, public = tmp_path / 'final', tmp_path / 'public'
    final.mkdir(); public.mkdir()
    (final / 'PROPOSAL_TABLES.json').write_text('{}')
    (public / 'PROPOSAL_TABLES.json').write_text('{"old":true}')
    for name in FILES: (final / name).write_bytes(b'test')
    hashes = {name: sha(final / name) for name in FILES}
    (final / FILES[0]).write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'): publish_panel(final, {'output_sha256': hashes}, public)
    assert json.loads((public / 'PROPOSAL_TABLES.json').read_text()) == {'old': True}


def test_review_names_from_saved_html_are_observations_not_auto_identity():
    from scripts.publish_full_intervesp import saved_review_titles
    assert saved_review_titles('<h1> BMSY 325DGH </h1><h1>BMSY 320DGH</h1>') == ['BMSY 325DGH','BMSY 320DGH']
    assert saved_review_titles('<p>Missing H1</p>') == []
    assert saved_review_titles('') == []
