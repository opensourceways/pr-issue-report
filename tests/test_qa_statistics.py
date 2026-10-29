"""
Tests for qa_statistics.py — community wide (unfiltered) reports sent to the QA receivers.
"""
import datetime

import pytest
import yaml

from docs_statistics import resolve_recipients
from qa_statistics import dedupe_rows, main, qa_statistics

QA_RECEIVERS = ['mengjiefamily@163.com', 'lcxzydhyy@163.com']


def _config(**qa_overrides):
    """Community config shaped like communities.yaml, with the QA report enabled."""
    qa_report = {
        'enabled': True,
        'workdir': 'boostkit-qa',
        'receivers': list(QA_RECEIVERS),
        'nickname': 'BoostKit QA SIG 负责人',
        'subject_pr': 'BoostKit 社区待处理 PR 汇总（全量）',
        'body_pr': 'PR 正文',
        'subject_issue': 'BoostKit 社区待处理 Issue 汇总（全量）',
        'body_issue': 'Issue 正文',
    }
    qa_report.update(qa_overrides)
    return {
        'name': 'boostkit',
        'display_name': 'BoostKit',
        'orgs': ['boostkit'],
        'skip_sigs': [],
        'cla_label': 'boostkit-cla/yes',
        'ci_failed_label': '',
        'wait_update_label': 'kind/wait_for_update',
        'qa_report': qa_report,
    }


def _pull(number, labels='boostkit-cla/yes', days_ago=3, repo='boostkit/BoostCore'):
    return {
        'title': 'pr {}'.format(number),
        'link': 'https://gitcode.com/{}/pulls/{}'.format(repo, number),
        'created_at': (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
            '%Y-%m-%d %H:%M:%S'),
        'draft': False,
        'labels': labels,
        'ref': 'main',
        'mergeable': True,
    }


def _issue(number, repo='boostkit/BoostCore'):
    return {
        'title': 'issue {}'.format(number),
        'link': 'https://gitcode.com/{}/issues/{}'.format(repo, number),
        'created_at': (datetime.datetime.now() - datetime.timedelta(days=3)).strftime(
            '%Y-%m-%d %H:%M:%S'),
        'issue_type': '需求',
        'issue_state': '开发中',
        'assignee': '',
    }


@pytest.fixture
def sigs_sample():
    return [
        {'name': 'BoostCore', 'repositories': ['boostkit/BoostCore']},
        {'name': 'QA', 'repositories': ['boostkit/QA']},
    ]


class TestDedupeRows:
    def test_same_pull_under_two_sigs_is_kept_once(self):
        row_a = ['BoostCore', 'boostkit/CubeSandbox', 'main', "<a href='u'>#1</a>", 'title', '待合入', '3']
        row_b = ['BoostVirt', 'boostkit/CubeSandbox', 'main', "<a href='u'>#1</a>", 'title', '待合入', '3']
        rows = dedupe_rows([row_a, row_b], 3)
        assert rows == [row_a]

    def test_distinct_pulls_are_all_kept(self):
        rows = [['S', 'r', 'main', "<a href='u1'>#1</a>", 't', '待合入', '3'],
                ['S', 'r', 'main', "<a href='u2'>#2</a>", 't', '待合入', '3']]
        assert dedupe_rows(rows, 3) == rows

    def test_issue_rows_dedupe_on_number_column(self):
        row_a = ['QA', 'boostkit/QA', "<a href='u'>#1</a>", 'title', '需求', '3']
        row_b = ['BoostCore', 'boostkit/QA', "<a href='u'>#1</a>", 'title', '需求', '3']
        assert dedupe_rows([row_a, row_b], 2) == [row_a]


class TestQaStatistics:
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
        return sent

    def _mappings(self):
        return (
            {'boostkit/BoostCore/main': _pull(100),
             'boostkit/QA/main': _pull(101)},
            {'boostkit/BoostCore/main': _issue(200),
             'boostkit/QA/main': _issue(201)},
        )

    def test_sends_two_community_wide_emails_to_qa_receivers(self, tmp_path, sigs_sample,
                                                             compare_dict_sample, reports):
        pulls, issues = self._mappings()
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())

        # one mail per receiver for each report → 2 reports × 2 receivers
        assert len(reports) == 4
        assert sorted(s[2] for s in reports) == sorted(
            ['BoostKit 社区待处理 PR 汇总（全量）'] * 2 +
            ['BoostKit 社区待处理 Issue 汇总（全量）'] * 2)
        for _, receivers, _, _ in reports:
            assert receivers in ([QA_RECEIVERS[0]], [QA_RECEIVERS[1]])

    def test_every_item_is_reported_without_doc_filtering(self, tmp_path, sigs_sample,
                                                          compare_dict_sample, reports):
        pulls, issues = self._mappings()
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())

        pr_csv = (tmp_path / 'qa_statistics_qa_pr.csv').read_text(encoding='utf-8')
        issue_csv = (tmp_path / 'qa_statistics_qa_issue.csv').read_text(encoding='utf-8')
        assert 'pulls/100' in pr_csv and 'pulls/101' in pr_csv
        assert 'issues/200' in issue_csv and 'issues/201' in issue_csv

    def test_no_extra_doc_status_column(self, tmp_path, sigs_sample, compare_dict_sample, reports):
        pulls, issues = self._mappings()
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert all(c['extra_header'] is None and c['extra_fills'] is None
                   for c in self.render_calls)

    def test_unsubscribe_note_mentions_community_summary(self, tmp_path, sigs_sample,
                                                         compare_dict_sample, reports):
        pulls, issues = self._mappings()
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert '退订社区全量汇总' in reports[0][3]

    def test_skips_mail_without_rows(self, tmp_path, sigs_sample, compare_dict_sample, reports):
        pulls, _ = self._mappings()
        qa_statistics(str(tmp_path), sigs_sample, pulls, {}, compare_dict_sample, _config())
        assert len(reports) == 2                     # only the PR mail, fanned out per receiver
        assert all(s[2] == 'BoostKit 社区待处理 PR 汇总（全量）' for s in reports)

    def test_controls_can_unsubscribe(self, tmp_path, monkeypatch, sigs_sample,
                                      compare_dict_sample, reports):
        pulls, issues = self._mappings()
        from common import expand_controls
        controls = {QA_RECEIVERS[0]: expand_controls({'qa_pr': False}),
                    QA_RECEIVERS[1]: expand_controls({})}
        monkeypatch.setattr('qa_statistics.load_email_controls', lambda: controls)
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        # QA_RECEIVERS[0] opted out of the PR mail → only the other receiver gets it
        assert [s[1] for s in reports if 'PR' in s[2]] == [[QA_RECEIVERS[1]]]

    def test_dry_run_writes_local_html(self, tmp_path, monkeypatch, sigs_sample,
                                       compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setenv('DRY_RUN', 'true')
        monkeypatch.chdir(tmp_path)
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert reports == []
        assert (tmp_path / 'test_output' / 'qa_pr_all.html').exists()
        assert (tmp_path / 'test_output' / 'qa_issue_all.html').exists()


class TestDynamicReceivers:
    """Membership changes must be picked up from sig-info.yaml on every run."""

    @staticmethod
    def _write_sig_info(workdir, maintainers, committers=None):
        sig_dir = workdir / 'community' / 'sig' / 'QA'
        sig_dir.mkdir(parents=True, exist_ok=True)
        sig_info = {'name': 'QA', 'maintainers': maintainers}
        if committers:
            sig_info['repositories'] = [{'repo': ['boostkit/QA'], 'committers': committers}]
        with open(sig_dir / 'sig-info.yaml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(sig_info, f, allow_unicode=True)

    @staticmethod
    def _member(gitcode_id, email):
        return {'gitcode_id': gitcode_id, 'name': gitcode_id, 'email': email}

    def test_reads_current_maintainers_from_sig_info(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_sig_info(tmp_path, [self._member('mengjie16', QA_RECEIVERS[0]),
                                        self._member('lcxzydhyy', QA_RECEIVERS[1])])
        config = _config(receivers_sig='QA', receiver_roles=['maintainer'], receivers=[])
        assert resolve_recipients(config, 'qa_report') == [
            ('mengjie16', QA_RECEIVERS[0]), ('lcxzydhyy', QA_RECEIVERS[1])]

    def test_membership_change_needs_no_config_edit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        maintainers = [self._member('mengjie16', QA_RECEIVERS[0])]
        self._write_sig_info(tmp_path, maintainers)
        config = _config(receivers_sig='QA', receiver_roles=['maintainer'], receivers=[])
        assert resolve_recipients(config, 'qa_report') == [('mengjie16', QA_RECEIVERS[0])]

        # sig maintainer changes upstream → next run sees the new list, same config
        maintainers.append(self._member('newcomer', 'newcomer@example.com'))
        self._write_sig_info(tmp_path, maintainers)
        assert resolve_recipients(config, 'qa_report') == [
            ('mengjie16', QA_RECEIVERS[0]), ('newcomer', 'newcomer@example.com')]

    def test_committer_role_is_supported(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_sig_info(tmp_path, [self._member('mengjie16', QA_RECEIVERS[0])],
                             committers=[self._member('lishutian', 'lishutian1@huawei.com')])
        config = _config(receivers_sig='QA', receiver_roles=['maintainer', 'committer'],
                         receivers=[])
        assert resolve_recipients(config, 'qa_report') == [
            ('mengjie16', QA_RECEIVERS[0]), ('lishutian', 'lishutian1@huawei.com')]

    def test_explicit_receivers_are_added_and_deduped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_sig_info(tmp_path, [self._member('mengjie16', QA_RECEIVERS[0])])
        # duplicate of the maintainer's email + one extra fixed receiver
        config = _config(receivers_sig='QA', receiver_roles=['maintainer'],
                         receivers=[QA_RECEIVERS[0], 'extra@example.com'])
        assert resolve_recipients(config, 'qa_report') == [
            ('mengjie16', QA_RECEIVERS[0]), ('extra@example.com', 'extra@example.com')]

    def test_missing_sig_info_falls_back_to_explicit_receivers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _config(receivers_sig='QA', receivers=[QA_RECEIVERS[0]])
        assert resolve_recipients(config, 'qa_report') == [(QA_RECEIVERS[0], QA_RECEIVERS[0])]


class TestMain:
    def test_main_runs(self, monkeypatch):
        calls = []
        config = _config()
        monkeypatch.setattr('qa_statistics.load_community_config', lambda: config)
        monkeypatch.setattr('qa_statistics.setup_community',
                            lambda c, workdir=None: calls.append('setup:{}'.format(workdir)) or config)
        monkeypatch.setattr('qa_statistics.prepare_env',
                            lambda *a: calls.append('env') or '/tmp/data')
        monkeypatch.setattr('qa_statistics.get_sigs',
                            lambda *a: (calls.append('sigs'), (['sig'], []))[1])
        monkeypatch.setattr('qa_statistics.all_sigs_compare',
                            lambda *a: calls.append('comp') or {})
        monkeypatch.setattr('qa_statistics.get_repos_pulls_mapping',
                            lambda *a: calls.append('pulls') or {})
        monkeypatch.setattr('qa_statistics.get_repos_issues_mapping',
                            lambda *a: calls.append('issues') or {})
        monkeypatch.setattr('qa_statistics.qa_statistics', lambda *a: calls.append('stats'))

        main()

        assert calls == ['setup:boostkit-qa', 'env', 'sigs', 'comp', 'pulls', 'issues', 'stats']

    def test_main_does_nothing_when_disabled(self, monkeypatch):
        calls = []
        monkeypatch.setattr('qa_statistics.load_community_config',
                            lambda: _config(enabled=False))
        monkeypatch.setattr('qa_statistics.setup_community',
                            lambda *a, **kw: calls.append('setup') or {})

        main()

        assert calls == []
