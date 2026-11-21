import subprocess

import pytest
from scripts import pages_release
from scripts import us_close_candidate as candidate


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).strip().decode()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / 'live'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'fixture')
    git(repo, 'config', 'user.email', 'fixture@example.test')
    (repo / 'public/data').mkdir(parents=True)
    (repo / 'public/data/us.json').write_text('owned-close')
    (repo / 'public/data/foreign.json').write_text('published-foreign')
    (repo / 'site.py').write_text('published-source')
    (repo / '.gitignore').write_text('dist/\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    return repo


OWNED = ('public/data/us.json',)


def test_dirties_never_enter_build_inputs_or_dist(repo):
    (repo / 'site.py').write_text('foreign-draft-source')
    (repo / 'public/data/foreign.json').write_text('foreign-draft-data')
    (repo / 'public/data/new.json').write_text('untracked-draft')
    before = git(repo, 'status', '--porcelain')
    sha = git(repo, 'rev-parse', 'HEAD')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pages_release, 'ROOT', repo)
        with pytest.raises(RuntimeError, match='foreign dirty paths.*site.py'):
            pages_release.ensure_release_scope()
    with candidate.committed_tree(repo, sha, OWNED) as tree:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(pages_release, 'ROOT', tree)
            pages_release.ensure_release_scope()
        assert git(tree, 'rev-parse', 'HEAD') == sha
        assert (tree / 'site.py').read_text() == 'published-source'
        assert (tree / 'public/data/foreign.json').read_text() == 'published-foreign'
        assert not (tree / 'public/data/new.json').exists()
        (tree / 'dist/data').mkdir(parents=True)
        (tree / 'dist/data/us.json').write_text('owned-close')
        candidate.verify_dist(tree, sha, OWNED)
        (tree / 'dist/data/us.json').write_text('tampered')
        with pytest.raises(RuntimeError, match='dist bytes'):
            candidate.verify_dist(tree, sha, OWNED)
    assert git(repo, 'status', '--porcelain') == before


@pytest.mark.parametrize('path,staged', [('site.py', True), ('public/data/us.json', False), ('public/data/us.json', True)])
def test_staging_and_owned_ambiguity_block(repo, path, staged):
    sha = git(repo, 'rev-parse', 'HEAD')
    (repo / path).write_text('dirty')
    if staged:
        git(repo, 'add', '--', path)
    with pytest.raises(RuntimeError, match='index|owned'):
        with candidate.committed_tree(repo, sha, OWNED):
            pytest.fail('exposed an unsafe candidate')


def test_staged_gitlink_hidden_by_config_blocks(repo):
    sha = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'config', 'diff.ignoreSubmodules', 'all')
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{sha},staged-module')
    with pytest.raises(RuntimeError, match='index'):
        with candidate.committed_tree(repo, sha, OWNED):
            pytest.fail('exposed an unsafe candidate')


def test_wrong_commit_blocks(repo):
    with pytest.raises(RuntimeError, match='HEAD'):
        with candidate.committed_tree(repo, 'f' * 40, OWNED):
            pytest.fail('exposed an unsafe candidate')


def test_recheck_staging_after_isolation(repo):
    sha = git(repo, 'rev-parse', 'HEAD')
    with candidate.committed_tree(repo, sha, OWNED):
        (repo / 'site.py').write_text('concurrent edit')
        git(repo, 'add', '--', 'site.py')
        with pytest.raises(RuntimeError, match='index'):
            candidate.check_source(repo, sha, OWNED)
