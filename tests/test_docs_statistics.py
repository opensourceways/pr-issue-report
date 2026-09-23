"""
Tests for docs_statistics.py — doc filtering, receivers, community wide reports and main entry point.
"""
import datetime
import os

import pytest

from docs_statistics import (
    build_issue_row,
    build_pr_row,
    collect_doc_issue_rows,
    collect_doc_pr_rows,
    doc_status,
    doc_status_fills,
    docs_statistics,
    is_doc_issue,
    is_doc_pr,
    main,
    resolve_recipients,
)

DOC_LABELS = ['need-doc-sig-review', 'doc-sig-reviewed', 'sig/doc', 'sig/Doc', 'ai-docs-only']


def _config(**docs_overrides):
    """Community config shaped like communities.yaml, with the docs report enabled."""
    docs_report = {
        'enabled': True,
        'workdir': 'openeuler-docs',
        'pr_labels': DOC_LABELS,
        'issue_title_prefix': '[资料]:',
        'issue_type': '资料',
        'status_header': '资料状态',
        'status_labels': {
            'doc-sig-reviewed': {'text': '资料已评审', 'color': 'C6EFCE'},
            'need-doc-sig-review': {'text': '待资料评审', 'color': 'FFFF00'},
        },
        'receivers': ['maintainer1'],
        'nickname': '资料经理',
        'subject_pr': '资料相关 PR 汇总',
        'body_pr': 'PR 正文',
        'subject_issue': '资料相关 Issue 汇总',
        'body_issue': 'Issue 正文',
    }
    docs_report.update(docs_overrides)
    return {
        'name': 'openeuler',
        'display_name': 'openEuler',
        'orgs': ['openeuler', 'src-openeuler'],
        'skip_sigs': [],
        'cla_label': 'openeuler-cla/yes',
        'ci_failed_label': 'ci_failed',
        'wait_update_label': 'kind/wait_for_update',
        'docs_report': docs_report,
    }


def _pull(labels, days_ago=3, draft=False, mergeable=True, title='doc pr'):
    return {
        'title': title,
        'link': 'https://gitcode.com/openeuler/ai-framework/pulls/100',
        'created_at': (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
            '%Y-%m-%d %H:%M:%S'),
        'draft': draft,
        'labels': labels,
        'ref': 'main',
        'mergeable': mergeable,
    }


def _issue(title, issue_type='需求', issue_state='开发中', assignee=''):
    return {
        'title': title,
        'link': 'https://gitcode.com/openeuler/ai-framework/issues/200',
        'created_at': (datetime.datetime.now() - datetime.timedelta(days=3)).strftime(
            '%Y-%m-%d %H:%M:%S'),
        'issue_type': issue_type,
        'issue_state': issue_state,
        'assignee': assignee,
    }


# ---------------------------------------------------------------------------
# doc filtering
# ---------------------------------------------------------------------------

class TestIsDocPr:
    @pytest.mark.parametrize('label', DOC_LABELS)
    def test_each_doc_label_matches(self, label):
        assert is_doc_pr(_pull('openeuler-cla/yes,{}'.format(label)), DOC_LABELS) is True

    def test_doc_label_among_others(self):
        item = _pull('openeuler-cla/yes,ci_failed,doc-sig-reviewed,approved')
        assert is_doc_pr(item, DOC_LABELS) is True

    def test_cic_pipeline_labels_do_not_match(self):
        """docs-ci-pipeline-* are per-repo CI status labels, never a doc signal."""
        for label in ('docs-ci-pipeline-success', 'docs-ci-pipeline-failed', 'docs-ci-pipeline-running'):
            assert is_doc_pr(_pull('boostkit-cla/yes,{}'.format(label)), DOC_LABELS) is False

    def test_no_labels(self):
        assert is_doc_pr(_pull(''), DOC_LABELS) is False
        assert is_doc_pr(_pull('openeuler-cla/yes'), DOC_LABELS) is False

    def test_empty_doc_labels_config(self):
        assert is_doc_pr(_pull('need-doc-sig-review'), []) is False


class TestIsDocIssue:
    def test_title_prefix_matches(self):
        assert is_doc_issue(_issue('[资料]: 合入930翻译回稿'), '[资料]:', '资料') is True

    def test_title_prefix_without_space(self):
        assert is_doc_issue(_issue('[资料]:快速入门文档问题'), '[资料]:', '资料') is True

    def test_issue_type_matches_even_without_prefix(self):
        """Union of both signals: some issues only carry the type."""
        assert is_doc_issue(_issue('表格序号重复', issue_type='资料'), '[资料]:', '资料') is True

    def test_title_mentioning_docs_without_prefix_or_type(self):
        assert is_doc_issue(_issue('README 中 ISA-L 特性描述存在错别字'), '[资料]:', '资料') is False

    def test_prefix_not_at_start(self):
        assert is_doc_issue(_issue('修复 [资料]: 遗留问题'), '[资料]:', '资料') is False

    def test_plain_issue(self):
        assert is_doc_issue(_issue('普通需求', issue_type='需求'), '[资料]:', '资料') is False


# ---------------------------------------------------------------------------
# row building (column layout and status wording must match the weekly reports)
# ---------------------------------------------------------------------------

class TestDocStatus:
    def test_pending_review(self):
        cfg = _config()['docs_report']
        assert doc_status(_pull('boostkit-cla/yes,need-doc-sig-review'), cfg) == ('待资料评审', 'FFFF00')

    def test_already_reviewed(self):
        cfg = _config()['docs_report']
        assert doc_status(_pull('boostkit-cla/yes,doc-sig-reviewed'), cfg) == ('资料已评审', 'C6EFCE')

    def test_reviewed_wins_when_both_present(self):
        """Labels are matched in config order; the most final state comes first."""
        cfg = _config()['docs_report']
        item = _pull('need-doc-sig-review,doc-sig-reviewed')
        assert doc_status(item, cfg) == ('资料已评审', 'C6EFCE')

    def test_no_review_label(self):
        cfg = _config()['docs_report']
        assert doc_status(_pull('boostkit-cla/yes,ai-docs-only'), cfg) == ('', None)

    def test_fills_map(self):
        assert doc_status_fills(_config()['docs_report']) == {'资料已评审': 'C6EFCE', '待资料评审': 'FFFF00'}


class TestBuildRows:
    def test_pr_row_layout_and_status(self):
        config = _config()
        row = build_pr_row('sig-ai', 'openeuler/ai-framework',
                           _pull('openeuler-cla/yes,need-doc-sig-review'), config)
        assert len(row) == 8                      # includes the doc label state column
        assert row[0] == 'sig-ai'
        assert row[1] == 'openeuler/ai-framework'
        assert row[2] == 'main'
        assert "pulls/100'>#100</a>" in row[3]
        assert row[5] == '待合入'
        assert row[6] == '3'
        assert row[7] == '待资料评审'

    def test_pr_row_without_review_label(self):
        config = _config()
        row = build_pr_row('sig-ai', 'openeuler/ai-framework', _pull('openeuler-cla/yes,ai-docs-only'), config)
        assert row[7] == ''

    def test_pr_row_flags(self):
        config = _config()
        row = build_pr_row('sig-ai', 'openeuler/ai-framework',
                           _pull('kind/wait_for_update', draft=True, mergeable=False), config)
        assert '草稿' in row[5]
        assert 'CLA认证失败' in row[5]
        assert '存在冲突' in row[5]
        assert '等待更新' in row[5]

    def test_issue_row_layout_and_status(self):
        """Status shows the item's own type — a title-prefixed issue can be any type."""
        row = build_issue_row('sig-ai', 'openeuler/ai-framework',
                              _issue('[资料]: 文档问题', issue_type='需求',
                                     issue_state='修复中', assignee='user1'))
        assert len(row) == 6          # no branch column
        assert row[0] == 'sig-ai'
        assert row[1] == 'openeuler/ai-framework'
        assert "issues/200'>#200</a>" in row[2]
        assert row[4] == '需求 / 修复中 / user1'
        assert row[5] == '3'

    def test_issue_row_status_without_state_or_assignee(self):
        row = build_issue_row('sig-ai', 'openeuler/ai-framework',
                              _issue('无前缀', issue_type='资料', issue_state='', assignee=''))
        assert row[4] == '资料'


# ---------------------------------------------------------------------------
# collecting
# ---------------------------------------------------------------------------

class TestCollectRows:
    def test_keeps_only_doc_pulls(self, sigs_sample):
        pulls = {
            'openeuler/ai-framework/main': _pull('openeuler-cla/yes,doc-sig-reviewed'),
            'openeuler/ai-models/main': _pull('openeuler-cla/yes'),
        }
        rows = collect_doc_pr_rows(sigs_sample, pulls, _config())
        assert len(rows) == 1
        assert rows[0][1] == 'openeuler/ai-framework'

    def test_keeps_only_doc_issues(self, sigs_sample):
        issues = {
            'openeuler/ai-framework/feature': _issue('[资料]: 文档问题'),
            'openeuler/ai-models/main': _issue('普通需求'),
        }
        rows = collect_doc_issue_rows(sigs_sample, issues, _config())
        assert len(rows) == 1
        assert rows[0][1] == 'openeuler/ai-framework'

    def test_skips_configured_sigs(self, sigs_sample):
        pulls = {'openeuler/ai-framework/main': _pull('need-doc-sig-review')}
        config = _config()
        config['skip_sigs'] = ['sig-ai']
        assert collect_doc_pr_rows(sigs_sample, pulls, config) == []

    def test_ignores_repos_outside_orgs(self):
        sigs = [{'name': 'sig-ai', 'repositories': ['other/ai-framework']}]
        pulls = {'other/ai-framework/main': _pull('need-doc-sig-review')}
        assert collect_doc_pr_rows(sigs, pulls, _config()) == []

    def test_ignores_repos_not_listed_in_any_sig(self, sigs_sample):
        pulls = {'openeuler/unknown-repo/main': _pull('need-doc-sig-review')}
        assert collect_doc_pr_rows(sigs_sample, pulls, _config()) == []


# ---------------------------------------------------------------------------
# receivers
# ---------------------------------------------------------------------------

class TestResolveRecipients:
    def test_gitcode_id_resolved_through_email_mapping(self, monkeypatch):
        monkeypatch.setattr('docs_statistics.get_email_mappings',
                            lambda: {'gengxueping': 'gxp@e.com'})
        config = _config(receivers=['gengxueping'])
        assert resolve_recipients(config) == [('gengxueping', 'gxp@e.com')]

    def test_email_entry_used_directly(self, monkeypatch):
        def fail():
            raise AssertionError('email mapping should not be loaded for direct emails')
        monkeypatch.setattr('docs_statistics.get_email_mappings', fail)
        config = _config(receivers=['someone@huawei.com'])
        assert resolve_recipients(config) == [('someone@huawei.com', 'someone@huawei.com')]

    def test_mixed_list_and_unknown_id_skipped(self, monkeypatch):
        monkeypatch.setattr('docs_statistics.get_email_mappings',
                            lambda: {'gengxueping': 'gxp@e.com'})
        config = _config(receivers=['gengxueping', 'not-in-sig', '', 'other@e.com'])
        assert resolve_recipients(config) == [('gengxueping', 'gxp@e.com'), ('other@e.com', 'other@e.com')]

    def test_empty_receivers(self, monkeypatch):
        monkeypatch.setattr('docs_statistics.get_email_mappings', lambda: {})
        assert resolve_recipients(_config(receivers=[])) == []

    def test_id_without_email_is_skipped(self, monkeypatch):
        monkeypatch.setattr('docs_statistics.get_email_mappings', lambda: {'x': ''})
        assert resolve_recipients(_config(receivers=['x'])) == []


# ---------------------------------------------------------------------------
# docs_statistics (core logic)
# ---------------------------------------------------------------------------

class TestDocsStatistics:
    @pytest.fixture
    def reports(self, monkeypatch):
        """Stub the rendering chain and capture what would be sent."""
        sent = []
        self.render_calls = []
        monkeypatch.setattr('docs_statistics.csv_to_xlsx', lambda c: c.replace('.csv', '.xlsx'))

        def fake_excel(xlsx_path, compare_dict, is_issue=True, extra_header=None, extra_fills=None):
            self.render_calls.append({'is_issue': is_issue, 'extra_header': extra_header,
                                      'extra_fills': extra_fills})
            with open(xlsx_path.replace('.xlsx', '.html'), 'w', encoding='utf-8') as f:
                f.write('<html><body><table>{}</table></body></html>'.format(
                    'Issue Report' if is_issue else 'PR Report'))
        monkeypatch.setattr('docs_statistics.excel_optimization', fake_excel)
        monkeypatch.setattr('docs_statistics.send_email',
                            lambda x, n, r, s, **kw: sent.append((n, r, s, kw.get('html_content'))))
        monkeypatch.setattr('docs_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@example.com'})
        return sent

    def _mappings(self):
        return (
            {'openeuler/ai-framework/main': _pull('need-doc-sig-review'),
             'openeuler/ai-framework/dev': _pull('openeuler-cla/yes')},
            {'openeuler/ai-framework/feature': _issue('[资料]: 文档问题'),
             'openeuler/ai-models/main': _issue('普通需求')},
        )

    def test_sends_two_community_wide_emails(self, tmp_path, monkeypatch, sigs_sample,
                                             compare_dict_sample, reports):
        pulls, issues = self._mappings()
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())

        assert len(reports) == 2
        assert sorted(s[2] for s in reports) == ['资料相关 Issue 汇总', '资料相关 PR 汇总']
        for _, receivers, _, _ in reports:
            assert receivers == ['m1@example.com']

    def test_pr_report_gets_doc_status_column_issue_report_does_not(self, tmp_path, monkeypatch,
                                                                   sigs_sample, compare_dict_sample,
                                                                   reports):
        pulls, issues = self._mappings()
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())

        pr_call = [c for c in self.render_calls if not c['is_issue']][0]
        issue_call = [c for c in self.render_calls if c['is_issue']][0]
        assert pr_call['extra_header'] == '资料状态'
        assert pr_call['extra_fills'] == {'资料已评审': 'C6EFCE', '待资料评审': 'FFFF00'}
        assert issue_call['extra_header'] is None and issue_call['extra_fills'] is None

    def test_only_doc_rows_reach_the_reports(self, tmp_path, monkeypatch, sigs_sample,
                                             compare_dict_sample, reports):
        pulls, issues = self._mappings()
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())

        pr_email = [s for s in reports if 'PR' in s[2]][0]
        issue_email = [s for s in reports if 'Issue' in s[2]][0]
        assert 'PR Report' in pr_email[3] and 'Issue Report' in issue_email[3]
        pr_csv = (tmp_path / 'doc_statistics_docs_pr.csv').read_text(encoding='utf-8')
        issue_csv = (tmp_path / 'doc_statistics_docs_issue.csv').read_text(encoding='utf-8')
        assert 'pulls/100' in pr_csv and 'pulls/101' not in pr_csv
        assert 'issues/200' in issue_csv and 'issues/201' not in issue_csv

    def test_unsubscribe_note_mentions_doc_reports(self, tmp_path, monkeypatch, sigs_sample,
                                                   compare_dict_sample, reports):
        pulls, issues = self._mappings()
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert '退订资料汇总' in reports[0][3]

    def test_skips_mail_without_rows(self, tmp_path, monkeypatch, sigs_sample,
                                     compare_dict_sample, reports):
        pulls, _ = self._mappings()
        docs_statistics(str(tmp_path), sigs_sample, pulls, {}, compare_dict_sample, _config())
        assert len(reports) == 1
        assert reports[0][2] == '资料相关 PR 汇总'

    def test_controls_can_unsubscribe(self, tmp_path, monkeypatch, sigs_sample,
                                      compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setattr('docs_statistics.load_email_controls',
                            lambda: {'maintainer1': {'docs_pr': {'receiver': False},
                                                     'docs_issue': {'receiver': True}}})
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert [s[2] for s in reports] == ['资料相关 Issue 汇总']

    def test_test_user_filters_receivers(self, tmp_path, monkeypatch, sigs_sample,
                                         compare_dict_sample, reports):
        pulls, issues = self._mappings()
        config = _config(receivers=['maintainer1', 'someone@huawei.com'])
        monkeypatch.setenv('TEST_USER', 'someone@huawei.com')
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, config)
        assert all(s[1] == ['someone@huawei.com'] for s in reports)

    def test_test_mode_redirects_both_mails(self, tmp_path, monkeypatch, sigs_sample,
                                            compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setenv('test_reviever_email', 'test@example.com')
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert len(reports) == 2
        assert all(s[1] == ['test@example.com'] for s in reports)

    def test_dry_run_writes_local_html(self, tmp_path, monkeypatch, sigs_sample,
                                       compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setenv('DRY_RUN', 'true')
        monkeypatch.chdir(tmp_path)
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert reports == []
        assert (tmp_path / 'test_output' / 'docs_pr_all.html').exists()
        assert (tmp_path / 'test_output' / 'docs_issue_all.html').exists()

    def test_no_receiver_configured_sends_nothing(self, tmp_path, monkeypatch, sigs_sample,
                                                  compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setattr('docs_statistics.get_email_mappings', lambda: {})
        reports.clear()
        docs_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample,
                        _config(receivers=['not-in-sig']))
        assert reports == []


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_runs(self, monkeypatch):
        """main() orchestrates setup_community → prepare_env → get_sigs → fetches → docs_statistics."""
        calls = []
        config = _config()
        monkeypatch.setattr('docs_statistics.load_community_config', lambda: config)
        monkeypatch.setattr('docs_statistics.setup_community',
                            lambda c, workdir=None: calls.append('setup:{}'.format(workdir)) or config)
        monkeypatch.setattr('docs_statistics.prepare_env',
                            lambda *a: calls.append('env') or '/tmp/data')
        monkeypatch.setattr('docs_statistics.get_sigs',
                            lambda *a: (calls.append('sigs'), (['sig'], []))[1])
        monkeypatch.setattr('docs_statistics.all_sigs_compare',
                            lambda *a: calls.append('comp') or {})
        monkeypatch.setattr('docs_statistics.get_repos_pulls_mapping',
                            lambda *a: calls.append('pulls') or {})
        monkeypatch.setattr('docs_statistics.get_repos_issues_mapping',
                            lambda *a: calls.append('issues') or {})
        monkeypatch.setattr('docs_statistics.docs_statistics',
                            lambda *a: calls.append('stats'))

        main()

        assert calls == ['setup:openeuler-docs', 'env', 'sigs', 'comp', 'pulls', 'issues', 'stats']

    def test_main_does_nothing_when_disabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr('docs_statistics.load_community_config',
                            lambda: _config(enabled=False))
        monkeypatch.setattr('docs_statistics.setup_community',
                            lambda *a, **kw: calls.append('setup') or {})

        main()

        assert calls == []
