"""Offline RUN2 preparation tests; no PostgreSQL or supplier access."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import urllib.request
import requests

from scripts import run2_kami_offline as offline


class KamiOfflineRun2Tests(unittest.TestCase):
    def test_http_robots_product_listing_and_fallback_impossible(self):
        with offline.network_guard() as attempts:
            for path in ('robots.txt','catalog/x/y/','catalog/x/'):
                with self.subTest(path=path), self.assertRaises(offline.OfflineContractError):
                    requests.get('https://www.stanki.ru/'+path)
            with self.assertRaises(offline.OfflineContractError):
                urllib.request.urlopen('https://www.stanki.ru/robots.txt')
            with self.assertRaises(offline.OfflineContractError):
                http.client.HTTPSConnection('www.stanki.ru').request('GET','/catalog/x/y/')
        self.assertEqual(attempts['HTTP'],0)
        self.assertEqual(attempts['blocked_HTTP_attempts'],5)

    def test_preparation_blocks_even_database_socket(self):
        with offline.network_guard(), socket.socket() as sock:
            for address in (('127.0.0.1',55451),('127.0.0.1',55449),('1.2.3.4',443)):
                with self.subTest(address=address), self.assertRaises(offline.OfflineContractError):
                    sock.connect(address)

    def test_execution_blocks_old_db_and_outbound_socket(self):
        with offline.network_guard(database_allowed=True), socket.socket() as sock:
            with self.assertRaises(offline.OfflineContractError):
                sock.connect_ex(('127.0.0.1',55449))
            with self.assertRaises(offline.OfflineContractError):
                sock.connect(('www.stanki.ru',443))

    def test_missing_capture_fails_without_http_or_database(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(offline.psycopg,'connect') as db:
            with offline.network_guard(), self.assertRaisesRegex(offline.OfflineContractError,'Missing frozen'):
                offline.verified_bytes(Path(tmp)/'missing.capture','0'*64)
            db.assert_not_called()

    def test_capture_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'saved.capture'; path.write_bytes(b'changed')
            with self.assertRaisesRegex(offline.OfflineContractError,'hash mismatch'):
                offline.verified_bytes(path,hashlib.sha256(b'original').hexdigest())

    def synthetic_documents(self, count=5264):
        rows=[]; applied={}
        for i in range(count):
            key='site:'+str(i)
            rows.append({'card':{'supplier_code':'kami','external_id':key},
                         'capture':{'url':'https://www.stanki.ru/catalog/x/'+str(i)+'/',
                                    'status':200,'capture':str(i)+'.capture','sha256':'a'*64,
                                    'content_type':'text/html','observed_at':'2026-10-05T12:00:00+00:00'},
                         'evidence_ref':str(Path('reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION')/(str(i)+'.capture'))})
            applied[key]={'source_product_id':i+1,'capture_id':i+1,'exact_replay_noop':True}
        raw='\n'.join(json.dumps(x) for x in rows).encode()
        sha=hashlib.sha256(raw).hexdigest()
        docs={'SOURCE_CONTINUATION/CHECKPOINT.json':{'status':'SOURCE_RECONCILIATION_VERIFIED_STOP'},
              'RECONCILED/FINAL_DISCOVERY_VERIFIED.json':{'status':'REPOSITORY_VERIFIED_FULL_RECONCILIATION',
                'source_completion':'COMPLETE_WITH_DECLARED_EXCLUSIONS','proven_source_identities':5264,
                'robots_excluded':2,'unresolved':0,'cards_sha256':sha},
              'RECONCILED/CARDS.jsonl':raw,
              'FULL_RUN1_CHECKPOINT.json':{'status':'LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY',
                 'applied_source_rows':5264,'cards_sha256':sha,'applied':applied,'full_saved_replay_hashes':{}},
              'DATABASE/RESTORE_VERIFICATION.json':{},'VARIANT_CANARY_VERIFIED.json':{'baseline_hashes':{}}}
        return {str(offline.BASE/k):(v if isinstance(v,bytes) else json.dumps(v).encode()) for k,v in docs.items()}

    def load_synthetic(self, docs, inventory_sha=None):
        parents=str(Path('reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION'))
        expected=offline.digest({parents:[]}) if inventory_sha is None else inventory_sha
        def read(path,sha):
            key=str(path.relative_to(Path.cwd()))
            if key in docs:
                return docs[key]
            return b'fixture capture; separate hash tests cover real verifier'
        with ExitStack() as stack:
            stack.enter_context(patch.object(offline,'verified_bytes',side_effect=read))
            stack.enter_context(patch.object(offline.ProductCard,'from_jsonable',side_effect=lambda v:SimpleNamespace(**v)))
            stack.enter_context(patch.object(offline,'prepare_observation',side_effect=lambda card,cap:(card,None)))
            stack.enter_context(patch.object(Path,'glob',return_value=[]))
            return offline.load_frozen(Path.cwd(),pins={k:'fixture' for k in docs},inventory_sha=expected)

    def test_exactly_5264_unique_frozen_identities_accepted(self):
        prepared,_,_=self.load_synthetic(self.synthetic_documents())
        self.assertEqual(len(prepared),5264)
        self.assertEqual(len({p[0].external_id for p in prepared}),5264)

    def test_wrong_count_rejected(self):
        with self.assertRaisesRegex(offline.OfflineContractError,'Exactly 5264'):
            self.load_synthetic(self.synthetic_documents(5263))

    def test_checkpoint_mismatch_rejected(self):
        docs=self.synthetic_documents(1)
        key=str(offline.BASE/'FULL_RUN1_CHECKPOINT.json')
        checkpoint=json.loads(docs[key]); checkpoint['status']='IN_PROGRESS';docs[key]=json.dumps(checkpoint).encode()
        with self.assertRaisesRegex(offline.OfflineContractError,'checkpoint mismatch'):
            self.load_synthetic(docs)

    def test_unexpected_extra_capture_inventory_rejected(self):
        with self.assertRaisesRegex(offline.OfflineContractError,'extra capture inventory'):
            self.load_synthetic(self.synthetic_documents(),inventory_sha='not-the-pinned-inventory')

    def mutate_first_cards(self, action):
        docs=self.synthetic_documents()
        key=str(offline.BASE/'RECONCILED/CARDS.jsonl')
        rows=[json.loads(line) for line in docs[key].splitlines()]; action(rows)
        raw='\n'.join(json.dumps(x) for x in rows).encode(); docs[key]=raw
        sha=hashlib.sha256(raw).hexdigest()
        for relative in ('RECONCILED/FINAL_DISCOVERY_VERIFIED.json','FULL_RUN1_CHECKPOINT.json'):
            k=str(offline.BASE/relative); obj=json.loads(docs[k]); obj['cards_sha256']=sha; docs[k]=json.dumps(obj).encode()
        return docs

    def test_duplicate_identity_rejected(self):
        docs=self.mutate_first_cards(lambda rows:rows[1]['card'].update(external_id=rows[0]['card']['external_id']))
        with self.assertRaisesRegex(offline.OfflineContractError,'duplicate frozen identity'):
            self.load_synthetic(docs)

    def test_non_kami_identity_rejected(self):
        docs=self.mutate_first_cards(lambda rows:rows[0]['card'].update(supplier_code='optimum'))
        with self.assertRaisesRegex(offline.OfflineContractError,'Supplier scoping'):
            self.load_synthetic(docs)

    def test_live_runner_available_and_byte_unchanged(self):
        self.assertEqual(hashlib.sha256((offline.ROOT/'scripts/run2_kami_products.py').read_bytes()).hexdigest(),offline.LIVE_SHA)

    def test_validation_never_opens_database(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(offline,'ROOT',Path(tmp)), \
             patch.object(offline,'load_frozen',return_value=([],{},{})), \
             patch.object(offline.subprocess,'check_output',side_effect=[
                 'feature/universal-supplier-kami-rc1','deefb595868d19ba471b9b943ff779194e231c26',
                 '36924836bb02965e857173773cdb70f302914643','feature-head']), \
             patch.object(offline,'execute_frozen') as execute, patch.object(offline.psycopg,'connect') as connect:
            (Path(tmp)/offline.PREP).mkdir(parents=True)
            offline.main([])
            execute.assert_not_called();connect.assert_not_called()

    def test_wrong_database_config_cannot_reach_writer(self):
        for code in ('universal_supplier_server','production'):
            with self.subTest(code=code),self.assertRaises(offline.OfflineContractError):
                offline.validate_runtime_config({'DB_HOST':'127.0.0.1','DB_PORT':'55449','DB_NAME':code,'DB_USER':'kami_app'})

    def test_old_namespaces_drift_blocks_replay(self):
        conn=MagicMock()
        with patch.object(offline,'fingerprint',return_value={'old':'changed'}):
            with self.assertRaisesRegex(offline.OfflineContractError,'Old namespace'):
                offline.assert_previous_state(conn,[],{'old':'original'})
        conn.execute.assert_not_called()

    def test_non_kami_cannot_call_persistence(self):
        repo=MagicMock();preflight=MagicMock()
        with self.assertRaisesRegex(offline.OfflineContractError,'Non-KAMI'):
            offline.replay_exact(repo,[(SimpleNamespace(supplier_code='partner_st'),None,{})],preflight)
        repo.persist_kami_observation.assert_not_called()

    def test_repeated_in_memory_replay_has_same_semantic_dataset(self):
        dataset={'site:1':{'source_id':7,'capture_id':8,'facts':{'model':'TU2304V','price':None}}}
        original=deepcopy(dataset)
        class Repository:
            def persist_kami_observation(self,card,capture):
                row=dataset[card.external_id]
                return {'exact_noop':True,'source_product_id':row['source_id'],'capture_id':row['capture_id']}
        prepared=[(SimpleNamespace(supplier_code='kami',external_id='site:1'),None,{'source_product_id':7,'capture_id':8})]
        for _ in range(2):
            offline.replay_exact(Repository(),prepared,lambda:None)
        self.assertEqual(dataset,original)

    def test_noop_false_is_failure_not_fallback(self):
        repo=MagicMock();repo.persist_kami_observation.return_value={'exact_noop':False}
        with self.assertRaisesRegex(offline.OfflineContractError,'Replay not exact'):
            offline.replay_exact(repo,[(SimpleNamespace(supplier_code='kami'),None,{})],lambda:None)
        self.assertEqual(repo.persist_kami_observation.call_count,1)

    def test_writer_check_inside_outer_transaction_does_not_change_transaction_mode(self):
        repo=MagicMock();repo.connection.execute.return_value.fetchone.return_value=(
            'universal_supplier_kami_rc','127.0.0.1',55451,'17.11','kami_app',False)
        offline.writer_identity_in_transaction(repo)
        self.assertNotIn('SET TRANSACTION',repo.connection.execute.call_args.args[0])

    def test_global_or_cross_supplier_drift_rolls_back_outer_transaction(self):
        repo=MagicMock(); manager=repo.connection.transaction.return_value
        with patch.object(offline,'assert_previous_state'), patch.object(offline,'replay_exact'), \
             patch.object(offline,'full_state_hashes',side_effect=[{'optimum':'original'},{'optimum':'changed'}]):
            with self.assertRaisesRegex(offline.OfflineContractError,'entire replay rejected'):
                offline.atomic_replay(repo,[],{},lambda:None)
        self.assertIs(manager.__exit__.call_args.args[0],offline.OfflineContractError)

    def test_exact_atomic_replay_can_finish_with_zero_changes(self):
        repo=MagicMock();manager=repo.connection.transaction.return_value
        with patch.object(offline,'assert_previous_state'), patch.object(offline,'replay_exact'), \
             patch.object(offline,'full_state_hashes',return_value={'all':'unchanged'}):
            self.assertEqual(offline.atomic_replay(repo,[],{},lambda:None),{'all':'unchanged'})
        self.assertIsNone(manager.__exit__.call_args.args[0])

    def test_protected_branch_cannot_execute_or_validate(self):
        with patch.object(offline.subprocess,'check_output',return_value='develop'), \
             patch.object(offline,'load_frozen') as frozen, patch.object(offline.psycopg,'connect') as db:
            with self.assertRaisesRegex(offline.OfflineContractError,'KAMI feature branch'):
                offline.main([])
            frozen.assert_not_called();db.assert_not_called()


if __name__ == '__main__':
    unittest.main()
