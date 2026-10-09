"""
Tests for multi-community support: communities.yaml config loading,
GitCode API data source, and per-community SIG parsing.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import common
from common import (
    adapt_gitcode_issue,
    adapt_gitcode_pr,
    all_sigs_compare,
    get_sigs,
    gitcode_fetch_repo_items,
    gitcode_open_items,
    load_community_config,
    prepare_env,
)


# ---------------------------------------------------------------------------
# load_community_config
# ---------------------------------------------------------------------------

class TestLoadCommunityConfig:
    def test_default_is_openeuler(self, monkeypatch):
        """With no COMMUNITY env var, defaults to openeuler config with name injected."""
        monkeypatch.delenv('COMMUNITY', raising=False)
        config = load_community_config()
        assert config['name'] == 'openeuler'
        assert config['data_source'] == 'ipb'
        assert config['orgs'] == ['openeuler', 'src-openeuler']
        assert config['cla_label'] == 'openeuler-cla/yes'

    def test_boostkit_config(self):
        config = load_community_config('boostkit')
        assert config['name'] == 'boostkit'
        assert config['data_source'] == 'gitcode_api'
        assert config['orgs'] == ['boostkit']
        assert config['cla_label'] == 'boostkit-cla/yes'
        assert config['processed_rate'] == 'none'

    def test_unknown_community_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            load_community_config('no-such-community')
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# GitCode API adapters
# ---------------------------------------------------------------------------

class TestAdaptGitcodePr:
    def test_adapt_full_item(self):
        item = {
            'title': 'Fix kae build',
            'html_url': 'https://gitcode.com/boostkit/community/merge_requests/194',
            'draft': False,
            'labels': [{'name': 'boostkit-cla/yes'}, {'name': 'kind/wait_for_update'}],
            'base': {'ref': 'master'},
            'mergeable': True,
            'created_at': '2026-08-14T14:39:27+08:00',
        }
        adapted = adapt_gitcode_pr(item)
        assert adapted['link'] == 'https://gitcode.com/boostkit/community/merge_requests/194'
        assert adapted['labels'] == 'boostkit-cla/yes,kind/wait_for_update'
        assert adapted['ref'] == 'master'
        assert adapted['created_at'] == '2026-08-14 14:39:27'
        assert adapted['mergeable'] is True
        assert adapted['draft'] is False

    def test_adapt_missing_optional_fields(self):
        item = {
            'title': 'Draft PR',
            'html_url': 'https://gitcode.com/boostkit/kae/merge_requests/1',
            'created_at': '2026-08-14T14:39:27+08:00',
            'draft': True,
        }
        adapted = adapt_gitcode_pr(item)
        assert adapted['labels'] == ''
        assert adapted['ref'] == '-'
        assert adapted['mergeable'] is False
        assert adapted['draft'] is True


class TestAdaptGitcodeIssue:
    def test_adapt_full_item(self):
        item = {
            'title': 'waas feature request',
            'html_url': 'https://gitcode.com/boostkit/waas/issues/24',
            'created_at': '2026-08-14T14:39:27+08:00',
            'issue_type': '需求',
            'issue_state': '开发中',
            'assignees': [{'login': 'alice'}, {'login': 'bob'}],
        }
        adapted = adapt_gitcode_issue(item)
        assert adapted['link'] == 'https://gitcode.com/boostkit/waas/issues/24'
        assert adapted['issue_type'] == '需求'
        assert adapted['issue_state'] == '开发中'
        assert adapted['assignee'] == 'alice,bob'
        assert adapted['created_at'] == '2026-08-14 14:39:27'

    def test_empty_assignees(self):
        item = {
            'title': 'bug',
            'html_url': 'https://gitcode.com/boostkit/waas/issues/25',
            'created_at': '2026-08-14T14:39:27+08:00',
            'issue_type': '缺陷',
            'issue_state': '待确认',
            'assignees': [],
        }
        adapted = adapt_gitcode_issue(item)
        assert adapted['assignee'] == ''


# ---------------------------------------------------------------------------
# gitcode_fetch_repo_items
# ---------------------------------------------------------------------------

class TestGitcodeFetchRepoItems:
    def _mock_resp(self, status_code, payload):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = payload
        return resp

    def test_single_page_200(self, monkeypatch):
        payload = [{'title': 'PR 1'}, {'title': 'PR 2'}]
        monkeypatch.setattr('common.requests.get',
                            lambda url, params, timeout: self._mock_resp(200, payload))
        items = gitcode_fetch_repo_items('boostkit/community', 'pulls', 'token')
        assert items == payload

    def test_403_skipped(self, monkeypatch):
        monkeypatch.setattr('common.requests.get',
                            lambda url, params, timeout: self._mock_resp(403, []))
        assert gitcode_fetch_repo_items('boostkit/private-repo', 'pulls', 'token') == []

    def test_404_skipped(self, monkeypatch):
        monkeypatch.setattr('common.requests.get',
                            lambda url, params, timeout: self._mock_resp(404, []))
        assert gitcode_fetch_repo_items('boostkit/missing-repo', 'issues', 'token') == []

    def test_pagination(self, monkeypatch):
        page1 = [{'title': 'PR {}'.format(i)} for i in range(100)]
        page2 = [{'title': 'PR last'}]
        responses = iter([self._mock_resp(200, page1), self._mock_resp(200, page2)])
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: next(responses))
        items = gitcode_fetch_repo_items('boostkit/community', 'pulls', 'token')
        assert len(items) == 101


# ---------------------------------------------------------------------------
# gitcode_open_items
# ---------------------------------------------------------------------------

class TestGitcodeOpenItems:
    def _sample_pr(self):
        return {
            'title': 'Fix kae build',
            'html_url': 'https://gitcode.com/boostkit/community/merge_requests/194',
            'draft': False,
            'labels': [{'name': 'boostkit-cla/yes'}],
            'base': {'ref': 'master'},
            'mergeable': True,
            'created_at': '2026-08-14T14:39:27+08:00',
        }

    def test_pulls_mapping_key(self, monkeypatch):
        monkeypatch.setenv('GITCODE_TOKEN', 'fake-token')
        monkeypatch.setattr('common.gitcode_fetch_repo_items',
                            lambda repo, kind, token: [self._sample_pr()])
        sigs = [{'name': 'BoostCore', 'repositories': ['boostkit/community']}]
        mapping = gitcode_open_items(sigs, 'pulls')
        assert 'boostkit/community/merge_requests/194' in mapping
        assert mapping['boostkit/community/merge_requests/194']['title'] == 'Fix kae build'

    def test_missing_token_exits(self, monkeypatch):
        monkeypatch.delenv('GITCODE_TOKEN', raising=False)
        with pytest.raises(SystemExit) as exc_info:
            gitcode_open_items([], 'pulls')
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# all_sigs_compare with processed_rate == 'none'
# ---------------------------------------------------------------------------

class TestAllSigsCompareNone:
    def test_no_network_requests(self, monkeypatch):
        """processed_rate 'none' returns empty compare info without calling dsapi."""
        def boom(sig):
            raise AssertionError('compare_sig_processed_rate must not be called')
        monkeypatch.setattr('common.compare_sig_processed_rate', boom)
        config = load_community_config('boostkit')
        result = all_sigs_compare(['BoostCore', 'Infra'], config)
        assert result == {'BoostCore': '', 'Infra': ''}


# ---------------------------------------------------------------------------
# get_sigs with per-community directory layouts
# ---------------------------------------------------------------------------

class TestGetSigsCommunityLayouts:
    def test_boostkit_sig_info_source(self, tmp_path, monkeypatch):
        """BoostKit (repo_source: sig_info): repos come from sig-info.yaml, not dir yamls."""
        sig_info = (
            'name: BoostCore\n'
            'maintainers: []\n'
            'repositories:\n'
            '  - repo:\n'
            '      - boostkit/kae\n'
            '      - boostkit/zstd\n'
            '    committers: []\n'
        )
        (tmp_path / 'community/sig/BoostCore').mkdir(parents=True)
        (tmp_path / 'community/sig/BoostCore/sig-info.yaml').write_text(sig_info, encoding='utf-8')
        # stale dir yaml not listed in sig-info.yaml must NOT be picked up
        (tmp_path / 'community/sig/BoostCore/Boostkit/s').mkdir(parents=True)
        (tmp_path / 'community/sig/BoostCore/Boostkit/s/stale-repo.yaml').write_text('', encoding='utf-8')
        (tmp_path / 'community/sig/README.md').write_text('', encoding='utf-8')
        monkeypatch.chdir(tmp_path)

        sigs, sigs_list = get_sigs(load_community_config('boostkit'))
        repos = {repo for sig in sigs for repo in sig['repositories']}
        assert repos == {'boostkit/kae', 'boostkit/zstd'}
        assert 'BoostCore' in sigs_list

    def test_dir_walk_mixed_case_org_dirs(self, tmp_path, monkeypatch):
        """dir_walk source: mixed-case org dirs and shard subdirs are handled."""
        (tmp_path / 'community/sig/BoostCore/Boostkit/k').mkdir(parents=True)
        (tmp_path / 'community/sig/BoostCore/Boostkit/k/kae.yaml').write_text('', encoding='utf-8')
        (tmp_path / 'community/sig/Infra/boostkit').mkdir(parents=True)
        (tmp_path / 'community/sig/Infra/boostkit/community.yaml').write_text('', encoding='utf-8')
        monkeypatch.chdir(tmp_path)

        config = dict(load_community_config('boostkit'))
        config['repo_source'] = 'dir_walk'
        sigs, sigs_list = get_sigs(config)
        repos = {repo for sig in sigs for repo in sig['repositories']}
        assert 'boostkit/kae' in repos
        assert 'boostkit/community' in repos

    def test_openeuler_layout_unchanged(self, tmp_path, monkeypatch):
        """openEuler tree still resolves openeuler/ and src-openeuler/ repos."""
        (tmp_path / 'community/sig/sig-ai/openeuler').mkdir(parents=True)
        (tmp_path / 'community/sig/sig-ai/openeuler/ai-framework.yaml').write_text('', encoding='utf-8')
        (tmp_path / 'community/sig/sig-base/src-openeuler').mkdir(parents=True)
        (tmp_path / 'community/sig/sig-base/src-openeuler/base-tools.yaml').write_text('', encoding='utf-8')
        monkeypatch.chdir(tmp_path)

        sigs, sigs_list = get_sigs(load_community_config('openeuler'))
        repos = {repo for sig in sigs for repo in sig['repositories']}
        assert 'openeuler/ai-framework' in repos
        assert 'src-openeuler/base-tools' in repos


# ---------------------------------------------------------------------------
# prepare_env with community config
# ---------------------------------------------------------------------------

class TestPrepareEnvCommunity:
    def test_uses_config_community_repo(self, monkeypatch):
        """prepare_env clones the URL from the community config."""
        path_state = {'community': False, 'data': False}
        clone_cmds = []

        def fake_exists(path):
            return path_state.get(path, False)

        def fake_run(cmd, check=False, **kw):
            if 'git' in cmd and 'clone' in cmd:
                clone_cmds.append(cmd)
                path_state['community'] = True

        monkeypatch.setattr('common.shutil.rmtree', lambda p: None)
        monkeypatch.setattr('common.subprocess.run', fake_run)
        monkeypatch.setattr('common.os.makedirs', lambda p, exist_ok=False: path_state.update(data=True))
        monkeypatch.setattr('common.os.path.exists', fake_exists)

        result = prepare_env(load_community_config('boostkit'))
        assert result == 'data'
        assert any('https://gitcode.com/boostkit/community.git' in cmd for cmd in clone_cmds)
