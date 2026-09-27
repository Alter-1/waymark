import importlib.util
import json
from pathlib import Path
import subprocess
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('workflow', str(Path(__file__).resolve().parents[1] / '.tools/workflow.py'))
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='workflow-test-')).resolve()
    def tearDown(self):
        # Git objects are read-only on Windows/Python 3.7. Only this allocated test tree is removed.
        def retry(operation, path, exc):
            if self.root not in Path(path).resolve().parents: raise exc[1]
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD); operation(path)
        self.assertTrue(self.root.name.startswith('workflow-test-'))
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        shutil.rmtree(str(self.root), onerror=retry)
    def config(self, value):
        p = self.root / 'config.json'; p.write_text(json.dumps(value)); return p
    def repo(self, name):
        p = self.root / name; p.mkdir()
        w.git(p, 'init'); w.git(p, 'config', 'user.email', 'test@example.invalid'); w.git(p, 'config', 'user.name', 'Test')
        return p
    def test_expected_failure_then_pass_and_resume(self):
        marker = self.root / 'marker'
        p = self.config({'steps': [
            {'id':'baseline','argv':[sys.executable,'-c','raise SystemExit(1)'],'expect':1},
            {'id':'fixed','argv':[sys.executable,'-c','from pathlib import Path; Path(%r).write_text("done")' % str(marker)]}]})
        out=self.root/'evidence'; self.assertEqual(w.run_plan(p,out),0)
        marker.unlink(); self.assertEqual(w.run_plan(p,out,True),0); self.assertFalse(marker.exists())
        p.write_text(p.read_text()+' ')
        with self.assertRaises(ValueError): w.run_plan(p,out,True)
    def test_unexpected_compile_failure_stops(self):
        p=self.config({'steps':[{'id':'compile','argv':[sys.executable,'-c','raise SystemExit(2)']},
                               {'id':'baseline','argv':[sys.executable,'-c','raise SystemExit(1)'],'expect':1}]})
        out=self.root/'e'; self.assertEqual(w.run_plan(p,out),1)
        self.assertEqual(len(json.loads((out/'receipt.json').read_text())['steps']),1)
    def test_timeout_never_matches_expected_failure(self):
        p=self.config({'steps':[{'id':'timeout','argv':[sys.executable,'-c','import time; time.sleep(10)'],
                                 'timeout_seconds':0.05,'expect':1}]})
        self.assertEqual(w.run_plan(p,self.root/'e'),1)
    def test_literal_arguments_no_shell_expansion(self):
        arg='a b $HOME `echo bad` & (value)'
        p=self.config({'steps':[{'id':'literal','argv':[sys.executable,'-c','import sys; print(sys.argv[1])',arg]}]})
        out=self.root/'e'; self.assertEqual(w.run_plan(p,out),0)
        self.assertEqual((out/'literal-1.log').read_text().strip(),arg)
    def test_snapshot_missing_file_retains_other_evidence(self):
        f=self.root/'sample'; f.write_bytes(b'0123456789')
        p=self.config({'files':[{'path':'sample','mode':'tail','bytes':4},{'path':'absent','mode':'copy'}]})
        out=self.root/'e'; self.assertEqual(w.snapshot(p,out),1)
        self.assertEqual((out/'000-sample').read_bytes(),b'6789')
        self.assertTrue(json.loads((out/'snapshot.json').read_text())['errors'])
    def test_sparse_checkout_excludes_bundles_and_preserves_source(self):
        repo=self.repo('source'); (repo/'src').mkdir(); (repo/'src'/'vendor').mkdir(); (repo/'bundled').mkdir()
        (repo/'src'/'main.py').write_text('source'); (repo/'src'/'vendor'/'big.bin').write_text('bundle')
        (repo/'bundled'/'big.bin').write_text('bundle'); w.git(repo,'add','.'); w.git(repo,'commit','-m','fixture')
        dest=self.root/'checkout with spaces'
        w.sparse_worktree(repo,dest,'HEAD','fix/test',['src'],['src/vendor'])
        self.assertTrue((dest/'src'/'main.py').exists()); self.assertFalse((dest/'bundled').exists())
        self.assertFalse((dest/'src'/'vendor').exists()); self.assertTrue((repo/'bundled'/'big.bin').exists())
        with self.assertRaises(ValueError): w.sparse_worktree(repo,dest,'HEAD','fix/other',['src'])
        w.git(repo,'worktree','remove',str(dest))
    def test_compare_does_not_certify_matching_files(self):
        source=self.repo('source'); target=self.repo('target')
        for repo in (source,target):
            (repo/'a').write_text('same'); w.git(repo,'add','.');w.git(repo,'commit','-m','fixture')
        p=self.config({'repositories':{'source':str(source),'target':str(target)},'items':[
            {'id':'fix','source':'source','files':['a'],'targets':{'target':{}}},
            {'id':'excluded','source':'source','files':['a'],'targets':{'target':{'decision':'will-not-port','reason':'architecture'}}}]})
        out=self.root/'e';w.compare(p,out);report=json.loads((out/'comparison.json').read_text())
        self.assertEqual(report['items'][0]['targets']['target']['decision'],'review-required')
        self.assertEqual(report['items'][1]['targets']['target']['decision'],'will-not-port')

    def test_context_discovers_satellite_and_does_not_rebuild_index(self):
        source=self.repo('source'); target=self.repo('target')
        for repo in (source,target):
            (repo/'README.md').write_text('instructions')
            w.git(repo,'add','.'); w.git(repo,'commit','-m','fixture')
        branch=w.git(source,'branch','--show-current')
        (source/'kb').mkdir(); (source/'kb'/'entry.md').write_text('knowledge')
        (source/'tool.py').write_text('pass')
        (source/'tool.md').write_text('tool docs')
        (source/'protected').mkdir()
        db=source/'index.sqlite'
        con=sqlite3.connect(str(db)); con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        con.executemany('INSERT INTO meta VALUES(?,?)', [('root',str(source)),('branch',branch),
                        ('build_started_at','fixture'),('file_count','1'),('index_schema','test')])
        con.commit(); con.close(); before=db.stat().st_mtime_ns
        sidecars=[Path(str(db)+suffix) for suffix in ('-wal','-shm')]
        self.assertFalse(any(path.exists() for path in sidecars))
        manifest=source/'waymark.project.json'
        manifest.write_text(json.dumps({
            'schema_version':1, 'project':'fixture',
            'repositories':[
                {'id':'source','path':'.','role':'owner','branch':branch},
                {'id':'target','path':'../target','role':'peer','branch':branch}],
            'instructions':['README.md'],
            'knowledge_roots':[{'path':'kb','authority':'source'}],
            'indexes':[{'path':'index.sqlite','repository_id':'source','authority':'cache'}],
            'tools':[{'path':'tool.py','documentation':'tool.md','cwd':'.','mutation':'read-only'}],
            'protected_paths':[{'path':'protected','reason':'fixture guard'}]}))
        (target/'waymark.project.json').write_text(json.dumps({
            'schema_version':1, 'extends':str(manifest), 'repository_id':'target'}))
        nested=target/'nested'; nested.mkdir()
        report=w.project_context(cwd=nested,task='find prior work')
        self.assertEqual(report['selected_repository'],'target')
        self.assertEqual(report['manifest'],str(manifest))
        self.assertEqual(report['task'],'find prior work')
        self.assertTrue(report['read_only'])
        self.assertFalse(report['errors'])
        self.assertEqual(report['indexes'][0]['authority'],'cache')
        self.assertEqual(report['indexes'][0]['status'],'present-unverified')
        self.assertEqual(db.stat().st_mtime_ns,before)
        self.assertFalse(any(path.exists() for path in sidecars))

    def test_context_reports_branch_mismatch_and_missing_required_resource(self):
        repo=self.repo('repo')
        (repo/'seed').write_text('seed'); w.git(repo,'add','.'); w.git(repo,'commit','-m','fixture')
        manifest=repo/'waymark.project.json'
        manifest.write_text(json.dumps({'schema_version':1,'project':'fixture',
            'repositories':[{'id':'repo','path':'.','branch':'not-the-current-branch'}],
            'instructions':['missing.md']}))
        report=w.project_context(manifest,cwd=repo)
        self.assertEqual(len(report['errors']),2)
        self.assertIn('branch is',report['errors'][0])
        self.assertIn('instructions is missing',report['errors'][1])

    def test_context_rejects_manifest_include_cycle(self):
        one=self.root/'one.json'; two=self.root/'two.json'
        one.write_text(json.dumps({'schema_version':1,'extends':'two.json'}))
        two.write_text(json.dumps({'schema_version':1,'extends':'one.json'}))
        with self.assertRaisesRegex(ValueError,'include cycle'):
            w.load_context_manifest(one)


if __name__ == '__main__': unittest.main()
