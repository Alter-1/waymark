#!/usr/bin/env python3
"""Reusable evidence workflows. Standard library only; Python 3.7+; no shell strings."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(str(temporary), str(path))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def absolute(path, base):
    p = Path(path)
    return (p if p.is_absolute() else base / p).resolve()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo)] + list(args), stderr=subprocess.STDOUT).decode('utf-8', 'replace').strip()


def repo_state(repo):
    return {'path': str(repo), 'head': git(repo, 'rev-parse', 'HEAD'),
            'branch': git(repo, 'branch', '--show-current'),
            'status': git(repo, 'status', '--porcelain')}


def discover_context_manifest(cwd, name='waymark.project.json'):
    """Find project context without changing the repository or rebuilding an index."""
    start = Path(cwd).resolve()
    if start.is_file():
        start = start.parent
    for folder in (start,) + tuple(start.parents):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise ValueError('No %s found from %s or its parents' % (name, start))


def load_context_manifest(path, seen=None):
    """Load one canonical manifest or a small satellite pointer to it."""
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError('Context manifest include cycle at ' + str(path))
    seen.add(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Context manifest must be a JSON object: ' + str(path))
    if 'extends' not in data:
        return data, path
    parent, canonical = load_context_manifest(absolute(data['extends'], path.parent), seen)
    allowed = {'schema_version', 'extends', 'repository_id'}
    extra = sorted(set(data) - allowed)
    if extra:
        raise ValueError('Satellite context manifest may only select repository_id; unexpected: ' + ', '.join(extra))
    merged = dict(parent)
    if data.get('repository_id'):
        merged['repository_id'] = data['repository_id']
    return merged, canonical


def _resource_entries(items, base):
    result = []
    for raw in items:
        item = {'path': raw} if isinstance(raw, str) else dict(raw)
        if not item.get('path'):
            raise ValueError('Context resource is missing path')
        path = absolute(item['path'], base)
        item['path'] = str(path)
        item['exists'] = path.exists()
        item['required'] = bool(item.get('required', True))
        for field in ('documentation', 'cwd'):
            if item.get(field):
                item[field] = str(absolute(item[field], base))
        result.append(item)
    return result


def _procedure_entries(items, repository_ids, selected_repository, task):
    """Validate action procedures and select the ones named by the current task."""
    if not isinstance(items, list):
        raise ValueError('Context procedures must be an array')
    result = []
    ids = set()
    task_text = task.casefold()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError('Every context procedure must be an object')
        item = dict(raw)
        procedure_id = item.get('id')
        if not procedure_id or procedure_id in ids:
            raise ValueError('Every procedure needs a unique nonempty id')
        ids.add(procedure_id)
        triggers = item.get('triggers', [])
        if not isinstance(triggers, list) or not triggers or not all(isinstance(value, str) and value.strip() for value in triggers):
            raise ValueError('Procedure %s needs nonempty string triggers' % procedure_id)
        targets = item.get('repository_ids', [])
        if not isinstance(targets, list) or not all(value in repository_ids for value in targets):
            raise ValueError('Procedure %s names an unknown repository_id' % procedure_id)
        canonical = item.get('canonical', {})
        argv = canonical.get('argv') if isinstance(canonical, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
            raise ValueError('Procedure %s canonical.argv must be a nonempty string array' % procedure_id)
        if canonical.get('cwd') != 'repository':
            raise ValueError('Procedure %s canonical.cwd must be repository' % procedure_id)
        alternatives = item.get('diagnostic_only', [])
        if not isinstance(alternatives, list):
            raise ValueError('Procedure %s diagnostic_only must be an array' % procedure_id)
        for alternative in alternatives:
            alt_argv = alternative.get('argv') if isinstance(alternative, dict) else None
            if (not isinstance(alt_argv, list) or not alt_argv or
                    not all(isinstance(value, str) for value in alt_argv) or not alternative.get('reason')):
                raise ValueError('Procedure %s diagnostic_only entries need argv and reason' % procedure_id)
        item['matches_task'] = bool(task_text and any(trigger.casefold() in task_text for trigger in triggers))
        item['matches_repository'] = not targets or selected_repository in targets
        result.append(item)
    return result


def _git_context(path):
    try:
        state = repo_state(path)
    except (OSError, subprocess.CalledProcessError) as exc:
        return {'error': str(exc)}
    lines = [line for line in state.pop('status').splitlines() if line]
    state['dirty'] = bool(lines)
    state['status_count'] = len(lines)
    state['status_preview'] = lines[:20]
    try:
        state['upstream'] = git(path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    except (OSError, subprocess.CalledProcessError):
        state['upstream'] = ''
    return state


def _index_context(item, repositories):
    path = Path(item['path'])
    result = dict(item)
    result['authority'] = item.get('authority', 'cache')
    result['status'] = 'missing' if not path.is_file() else 'present-unverified'
    if not path.is_file():
        return result
    try:
        # mode=ro can still create -wal/-shm lock sidecars for a WAL-mode database.  Context is a
        # receipt, not an index session, so immutable is required to make the no-write contract
        # true at the filesystem level as well.
        con = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            meta = dict(con.execute('SELECT key, value FROM meta'))
        finally:
            con.close()
        result['meta'] = {key: meta.get(key, '') for key in
                          ('root', 'branch', 'build_started_at', 'file_count', 'index_schema')}
        repo_id = item.get('repository_id', '')
        repo = repositories.get(repo_id)
        if repo and repo.get('git') and not repo['git'].get('error'):
            reasons = []
            if meta.get('branch') and meta['branch'] != repo['git'].get('branch'):
                reasons.append('index branch is %s; checkout branch is %s' %
                               (meta['branch'], repo['git'].get('branch')))
            if meta.get('root') and Path(meta['root']).resolve() != Path(repo['path']).resolve():
                reasons.append('index root does not match repository path')
            if reasons:
                result['status'] = 'stale'
                result['reasons'] = reasons
    except (OSError, sqlite3.DatabaseError) as exc:
        result['status'] = 'invalid'
        result['error'] = str(exc)
    return result


def project_context(manifest=None, cwd='.', task=''):
    """Return a read-only cold-session receipt for a repository family."""
    entry = Path(manifest).resolve() if manifest else discover_context_manifest(cwd)
    data, canonical = load_context_manifest(entry)
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported context schema_version (expected 1)')
    if not data.get('project'):
        raise ValueError('Context manifest is missing project')
    specs = data.get('repositories', [])
    if not isinstance(specs, list) or not specs:
        raise ValueError('Context manifest needs a nonempty repositories array')
    ids = [item.get('id') for item in specs if isinstance(item, dict)]
    if any(not value for value in ids) or len(ids) != len(specs) or len(set(ids)) != len(ids):
        raise ValueError('Every repository needs a unique nonempty id')

    base = canonical.parent
    repositories = {}
    errors = []
    for spec in specs:
        item = dict(spec)
        path = absolute(item.pop('path'), base)
        item['path'] = str(path)
        item['exists'] = path.exists()
        item['required'] = bool(item.get('required', True))
        if item['exists']:
            item['git'] = _git_context(path)
            if item['git'].get('error'):
                errors.append('%s is not a readable Git checkout' % item['id'])
            if item.get('branch') and item['git'].get('branch') != item['branch']:
                errors.append('%s branch is %s, expected %s' %
                              (item['id'], item['git'].get('branch'), item['branch']))
            if item.get('upstream') and item['git'].get('upstream') != item['upstream']:
                errors.append('%s upstream is %s, expected %s' %
                              (item['id'], item['git'].get('upstream') or '(none)', item['upstream']))
        elif item['required']:
            errors.append('%s repository is missing: %s' % (item['id'], path))
        repositories[item['id']] = item

    selected = data.get('repository_id', '')
    if selected and selected not in repositories:
        raise ValueError('repository_id is not present in repositories: ' + selected)
    if not selected:
        here = Path(cwd).resolve()
        candidates = [item for item in repositories.values()
                      if item['exists'] and (here == Path(item['path']) or Path(item['path']) in here.parents)]
        if candidates:
            selected = max(candidates, key=lambda item: len(Path(item['path']).parts))['id']

    resources = {}
    for key in ('instructions', 'knowledge_roots', 'tools', 'protected_paths', 'indexes'):
        resources[key] = _resource_entries(data.get(key, []), base)
        for item in resources[key]:
            if item['required'] and not item['exists']:
                errors.append('%s is missing: %s' % (key, item['path']))
    resources['indexes'] = [_index_context(item, repositories) for item in resources['indexes']]
    procedures = _procedure_entries(data.get('procedures', []), set(repositories), selected, task)

    return {
        'schema_version': 1,
        'project': data['project'],
        'task': task,
        'entry_manifest': str(entry),
        'manifest': str(canonical),
        'selected_repository': selected,
        'repositories': [repositories[key] for key in ids],
        'instructions': resources['instructions'],
        'knowledge_roots': resources['knowledge_roots'],
        'indexes': resources['indexes'],
        'tools': resources['tools'],
        'protected_paths': resources['protected_paths'],
        'procedures': procedures,
        # WHAT THE AGENT OWES IS KEYED TO THE REPOSITORY, NOT TO ITS OWN WORDING OF THE TASK.
        # `relevant_procedures` is matched by substring against task text the CALLER writes, so a
        # paraphrase ("build the firmware" against a trigger "release build") returns an empty list
        # -- and an empty list reads as permission at exactly the boundary this exists to guard.
        # `applicable_procedures` drops the task from the test: these are the declarations that own
        # actions in the selected checkout, whatever the request was called. Matching still ranks.
        'applicable_procedures': [item for item in procedures if item['matches_repository']],
        'relevant_procedures': [item for item in procedures
                                if item['matches_task'] and item['matches_repository']],
        'retrieval_order': ['instructions', 'project manifest', 'knowledge sources',
                            'tool documentation', 'cross-branch history', 'source'],
        'index_policy': 'Query an existing index first. Rebuild only when missing, proven stale, or after authored knowledge/source changes that must be indexed.',
        'action_policy': 'At an action boundary, a canonical procedure in applicable_procedures owns that action: use its argv/cwd and verify its declared success evidence. relevant_procedures ranks them against the task text and is a hint, not the obligation -- an empty list is not permission.',
        'read_only': True,
        'errors': errors,
    }


def new_evidence(path):
    out = Path(path).resolve()
    out.mkdir(parents=True, exist_ok=False)
    return out


def run_plan(plan_path, out, resume=False):
    """Run ordered argv arrays. A receipt survives failures; resume requires identical inputs."""
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
    base = plan_path.parent
    out = Path(out).resolve()
    steps = plan['steps']
    names = [s['id'] for s in steps]
    if len(set(names)) != len(names) or any(not n or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in n) for n in names):
        raise ValueError('Step ids must be unique alphanumeric/underscore/hyphen names')
    for step in steps:
        if not isinstance(step['argv'], list) or not step['argv'] or not all(isinstance(a, str) for a in step['argv']):
            raise ValueError('argv must be a nonempty array of strings, never a shell command')
        if not isinstance(step.get('expect', 0), int):
            raise ValueError('expect must be an exact exit code (use a separate successful compile step)')
    inputs = {str(absolute(p, base)): digest(absolute(p, base)) for p in plan.get('inputs', [])}
    repos = [repo_state(absolute(r['path'], base)) for r in plan.get('repositories', [])]
    for spec, state in zip(plan.get('repositories', []), repos):
        if spec.get('branch') and spec['branch'] != state['branch']:
            raise ValueError('Unexpected branch: ' + state['path'])
    fingerprint = {'plan': digest(plan_path), 'inputs': inputs, 'repositories': repos}
    receipt_path = out / 'receipt.json'
    if resume:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['fingerprint'] != fingerprint:
            raise ValueError('Plan, inputs, or repository state changed; use a new evidence directory')
    else:
        new_evidence(out)
        shutil.copy2(str(plan_path), str(out / 'plan.json'))
        receipt = {'fingerprint': fingerprint, 'steps': [], 'complete': False}
    completed = {s['id'] for s in receipt['steps'] if s['passed']}
    write_json(receipt_path, receipt)
    for step in steps:
        if step['id'] in completed:
            continue
        cwd = absolute(step.get('cwd', '.'), base)
        # Only this explicit placeholder is expanded; no shell/env interpolation.
        argv = [a.replace('{evidence}', str(out)) for a in step['argv']]
        attempt = 1 + sum(s['id'] == step['id'] for s in receipt['steps'])
        log = out / ('%s-%d.log' % (step['id'], attempt))
        result = {'id': step['id'], 'argv': argv, 'cwd': str(cwd), 'log': log.name,
                  'started_utc': datetime.datetime.utcnow().isoformat() + 'Z', 'passed': False}
        with log.open('wb') as stream:
            try:
                code = subprocess.call(argv, cwd=str(cwd), stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=step.get('timeout_seconds', 600))
                result['exit_code'] = code
                result['passed'] = code == step.get('expect', 0)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result['error'] = str(exc)
        receipt['steps'].append(result)
        write_json(receipt_path, receipt)
        print('%s: %s (%s)' % (step['id'], 'PASS' if result['passed'] else 'FAIL', log), flush=True)
        if not result['passed']:
            return 1
    receipt['complete'] = True
    write_json(receipt_path, receipt)
    return 0


def snapshot(config_path, out):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    out = new_evidence(out)
    report = {'utc': datetime.datetime.utcnow().isoformat() + 'Z', 'repositories': [], 'files': [], 'errors': []}
    for r in config.get('repositories', []):
        try:
            report['repositories'].append(repo_state(absolute(r, config_path.parent)))
        except (OSError, subprocess.CalledProcessError) as exc:
            report['errors'].append(str(exc))
    for index, spec in enumerate(config.get('files', [])):
        path = absolute(spec['path'], config_path.parent)
        try:
            stat = path.stat()
            item = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            mode = spec.get('mode', 'metadata')
            dest = out / ('%03d-%s' % (index, path.name))
            if mode == 'tail':
                with path.open('rb') as stream:
                    size = max(0, min(int(spec.get('bytes', 65536)), stat.st_size))
                    stream.seek(stat.st_size - size)
                    dest.write_bytes(stream.read(size))
                item['artifact'] = dest.name
            elif mode == 'copy':
                shutil.copy2(str(path), str(dest)); item['artifact'] = dest.name
                item['sha256'] = digest(dest)
            elif mode == 'hash':
                item['sha256'] = digest(path)
            elif mode != 'metadata':
                raise ValueError('Unknown snapshot mode: ' + mode)
            report['files'].append(item)
        except (OSError, ValueError) as exc:
            report['errors'].append('%s: %s' % (path, exc))
    write_json(out / 'snapshot.json', report)
    return 1 if report['errors'] else 0


def sparse_worktree(repo, dest, ref, branch, includes, excludes=()):
    repo, dest = Path(repo).resolve(), Path(dest).resolve()
    if dest.exists():
        raise ValueError('Destination already exists; never overwrite an existing worktree')
    def pattern(path):
        path = path.replace('\\', '/').strip('/')
        if not path or any(p in ('', '.', '..') for p in path.split('/')) or any(c in path for c in '*?[]!\n\r') or ':' in path:
            raise ValueError('Use literal repository-relative paths, not globs: ' + path)
        return '/' + path
    patterns = [pattern(p) + '/' for p in includes] + ['!' + pattern(p) + '/' for p in excludes]
    # --no-checkout is essential: do not unpack bundled environments before sparsity exists.
    git(repo, 'worktree', 'add', '--no-checkout', '-b', branch, str(dest), ref)
    try:
        # Worktree-local configuration also supports Git versions predating sparse-checkout.
        git(repo, 'config', 'extensions.worktreeConfig', 'true')
        git(dest, 'config', '--worktree', 'core.sparseCheckout', 'true')
        sparse_file = absolute(git(dest, 'rev-parse', '--git-path', 'info/sparse-checkout'), dest)
        sparse_file.parent.mkdir(parents=True, exist_ok=True)
        sparse_file.write_text('\n'.join(patterns) + '\n', encoding='utf-8')
        git(dest, 'read-tree', '-mu', 'HEAD')
    except Exception:
        print('Partial worktree retained for inspection: ' + str(dest), file=sys.stderr)
        raise
    return repo_state(dest)


def compare(config_path, out):
    config_path = Path(config_path).resolve(); base = config_path.parent
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    report = {'repositories': {}, 'items': [], 'limitation': 'Byte comparisons are triage, not semantic port certification.'}
    repos = {key: absolute(path, base) for key, path in config['repositories'].items()}
    for key, repo in repos.items():
        report['repositories'][key] = repo_state(repo)
    for spec in config['items']:
        result = {'id': spec['id'], 'source': spec['source'], 'targets': {}}
        source_root = repos[spec['source']]
        for target, policy in spec['targets'].items():
            if policy.get('decision') == 'will-not-port':
                if not policy.get('reason'):
                    raise ValueError('will-not-port requires a reason')
                result['targets'][target] = dict(policy)
                continue
            files = []
            for relative in spec['files']:
                paths = [absolute(relative, root) for root in (source_root, repos[target])]
                for path, root in zip(paths, (source_root, repos[target])):
                    if root not in path.parents:
                        raise ValueError('Comparison path escapes repository')
                hashes = [digest(p) if p.is_file() else None for p in paths]
                files.append({'path': relative, 'source_sha256': hashes[0], 'target_sha256': hashes[1],
                              'result': 'identical' if hashes[0] is not None and hashes[0] == hashes[1] else 'review-required'})
            result['targets'][target] = {'files': files, 'decision': 'review-required', 'evidence': policy.get('evidence', '')}
        report['items'].append(result)
    out = new_evidence(out); write_json(out / 'comparison.json', report)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'snapshot', 'compare'):
        p = sub.add_parser(name); p.add_argument('config'); p.add_argument('--out', required=True)
        if name == 'run': p.add_argument('--resume', action='store_true')
    p = sub.add_parser('worktree'); p.add_argument('--repo', required=True); p.add_argument('--dest', required=True)
    p.add_argument('--ref', required=True); p.add_argument('--branch', required=True)
    p.add_argument('--include', action='append', required=True); p.add_argument('--exclude', action='append', default=[])
    p = sub.add_parser('context')
    p.add_argument('--manifest', help='Manifest path; otherwise discover waymark.project.json from --cwd')
    p.add_argument('--cwd', default='.', help='Current project path used for discovery and repository selection')
    # REQUIRED: the default of '' made every matches_task false, so the cheapest possible
    # invocation returned an empty relevant_procedures and satisfied the action rule vacuously,
    # silently, and with exit code 0. A missing task is now a usage error.
    p.add_argument('--task', required=True, help='The exact request, retained in the receipt and used to rank procedures')
    p.add_argument('--out', help='Optional JSON receipt path; stdout is always written')
    args = parser.parse_args()
    try:
        if args.command == 'run': return run_plan(args.config, args.out, args.resume)
        if args.command == 'snapshot': return snapshot(args.config, args.out)
        if args.command == 'compare': return compare(args.config, args.out)
        if args.command == 'context':
            report = project_context(args.manifest, args.cwd, args.task)
            if args.out:
                write_json(Path(args.out).resolve(), report)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 1 if report['errors'] else 0
        print(json.dumps(sparse_worktree(args.repo, args.dest, args.ref, args.branch, args.include, args.exclude), indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr); return 2


if __name__ == '__main__':
    sys.exit(main())
