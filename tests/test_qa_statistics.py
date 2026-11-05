"""
Tests for qa_statistics.py — community wide (unfiltered) reports sent to the QA receivers.
"""
import datetime

import pytest
import yaml

from docs_statistics import resolve_recipients
from qa_statistics import (build_closure_trend, build_sig_trends, closure_trend_enabled,
                           dedupe_rows, last_wednesday, main, qa_statistics,
                           render_closure_trend, render_sig_trends, repo_of_link,
                           repo_sig_mapping, trend_windows)

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

        def fake_excel(xlsx_path, compare_dict, is_issue=True, extra_header=None, extra_fills=None,
                       highlight_sig_title=False, **kwargs):
            self.render_calls.append({'is_issue': is_issue, 'extra_header': extra_header,
                                      'extra_fills': extra_fills,
                                      'sig_stats': kwargs.get('sig_stats')})
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
        for nickname, receivers, _, _ in reports:
            assert receivers in ([QA_RECEIVERS[0]], [QA_RECEIVERS[1]])
            # the greeting is the receiver key, so each maintainer can tell the mail is theirs
            assert nickname == receivers[0]

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

    def test_closure_trend_prepended_when_enabled(self, tmp_path, monkeypatch, sigs_sample,
                                                   compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setenv('QA_CLOSURE_TREND', 'true')     # independent of the weekday
        monkeypatch.setattr('qa_statistics.build_trends', lambda *a, **kw: ({}, {}))
        monkeypatch.setattr('qa_statistics.render_closure_trend',
                            lambda trend, label: '<h3>TREND-{}</h3>'.format(label))
        monkeypatch.setattr('qa_statistics.render_sig_trends',
                            lambda trends, label: '<h3>SIGTREND-{}</h3>'.format(label))
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample,
                      _config(closure_trend=True))
        pr_mail = [s for s in reports if 'PR' in s[2]][0]
        issue_mail = [s for s in reports if 'Issue' in s[2]][0]
        assert '<body><h3>TREND-PR</h3>' in pr_mail[3]
        assert 'SIGTREND-PR' in pr_mail[3]
        assert '<body><h3>TREND-Issue</h3>' in issue_mail[3]

    def test_no_trend_without_the_config_flag(self, tmp_path, monkeypatch, sigs_sample,
                                               compare_dict_sample, reports):
        pulls, issues = self._mappings()
        monkeypatch.setattr('qa_statistics.build_trends',
                            lambda *a, **kw: ({}, {}))
        monkeypatch.setattr('qa_statistics.render_closure_trend',
                            lambda trend, label: '<h3>TREND</h3>')
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, _config())
        assert all('TREND' not in s[3] for s in reports)

    def test_plain_reminder_has_neither_trend_nor_sig_stats(self, tmp_path, monkeypatch,
                                                            sigs_sample, compare_dict_sample,
                                                            reports):
        """The Friday run must stay a plain PR/Issue reminder."""
        pulls, issues = self._mappings()
        monkeypatch.setattr('qa_statistics.closure_trend_enabled', lambda cfg: False)
        config = _config(closure_trend=True)
        config['sig_header_stats'] = True
        qa_statistics(str(tmp_path), sigs_sample, pulls, issues, compare_dict_sample, config)
        assert all('闭环趋势' not in (s[3] or '') for s in reports)
        assert all(c['sig_stats'] is None for c in self.render_calls)


# ---------------------------------------------------------------------------
# Closure trend
# ---------------------------------------------------------------------------

def _history(link, created, closed=None):
    """Build one adapted history item like common.adapt_gitcode_history_item()."""
    def parse(value):
        return datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
    return {'link': link, 'created_at': parse(created),
            'closed_at': parse(closed) if closed else None,
            'state': 'closed' if closed else 'open'}


class TestClosureTrendWindow:
    def test_anchor_is_the_last_wednesday(self):
        # 2026-11-05 is a Thursday, so the anchor is the 4th
        assert last_wednesday(datetime.date(2026, 11, 5)) == datetime.date(2026, 11, 4)
        assert last_wednesday(datetime.date(2026, 11, 4)) == datetime.date(2026, 11, 4)

    def test_windows_are_the_two_weeks_between_wednesdays(self):
        assert trend_windows(datetime.date(2026, 11, 4)) == [
            (datetime.date(2026, 10, 21), datetime.date(2026, 10, 28)),   # end exclusive
            (datetime.date(2026, 10, 28), datetime.date(2026, 11, 5)),
        ]


class TestClosureTrendSchedule:
    def test_only_the_wednesday_run_has_the_trend(self):
        assert closure_trend_enabled({'closure_trend': True},
                                     now=datetime.datetime(2026, 11, 4, 8, 0)) is True
        assert closure_trend_enabled({'closure_trend': True},
                                     now=datetime.datetime(2026, 11, 6, 8, 0)) is False

    def test_env_switch_forces_a_preview_on_any_day(self, monkeypatch):
        friday = datetime.datetime(2026, 11, 6, 8, 0)
        monkeypatch.setenv('QA_CLOSURE_TREND', 'true')
        assert closure_trend_enabled({'closure_trend': True}, now=friday) is True
        # an unset/false switch keeps the weekday rule (this is what a Jenkins
        # boolean parameter with the box unchecked must behave like)
        monkeypatch.setenv('QA_CLOSURE_TREND', 'false')
        assert closure_trend_enabled({'closure_trend': True}, now=friday) is False
        assert closure_trend_enabled(
            {'closure_trend': True}, now=datetime.datetime(2026, 11, 4, 8, 0)) is True

    def test_disabled_config_wins(self):
        assert closure_trend_enabled({}, now=datetime.datetime(2026, 11, 4, 8, 0)) is False


class TestClosureTrendNumbers:
    def test_denominator_is_the_window_backlog_plus_its_closures(self):
        """Closed in window / (closed in window + still open at its end)."""
        open_items = [
            {'link': 'u1', 'created_at': '2026-09-01 10:00:00'},   # inherited backlog
            {'link': 'u2', 'created_at': '2026-10-01 10:00:00'},   # inherited backlog
        ]
        history = [
            _history('u3', '2026-11-01 09:00:00', '2026-11-02 09:00:00'),   # new + closed
            _history('u4', '2026-11-02 09:00:00'),                          # new, open
            _history('u5', '2026-10-20 09:00:00', '2026-11-03 09:00:00'),   # inherited, closed
        ]
        trend = build_closure_trend(open_items, history, today=datetime.date(2026, 11, 5))

        assert [r['label'] for r in trend['rows']] == [
            '10-21~10-27（上周）', '10-28~11-04（本周）']
        last = trend['rows'][-1]
        assert last['period_closed'] == 2             # u3, u5 closed inside the window
        assert last['backlog'] == 3                   # u1, u2, u4 still open on 11-04
        assert round(last['rate'], 4) == round(2 / 5, 4)

    def test_each_week_uses_its_own_base(self):
        open_items = [{'link': 'u1', 'created_at': '2026-09-01 10:00:00'}]
        history = [_history('u2', '2026-10-22 09:00:00', '2026-10-24 09:00:00')]
        trend = build_closure_trend(open_items, history, today=datetime.date(2026, 11, 5))
        first = trend['rows'][0]
        assert first['label'] == '10-21~10-27（上周）'
        assert first['period_closed'] == 1
        assert first['backlog'] == 1                  # only u1 is still open on 10-27
        assert round(first['rate'], 4) == 0.5         # 1 / (1 closed + 1 still open)
        assert trend['rows'][1]['period_closed'] == 0

    def test_reopened_item_counts_as_open(self):
        open_items = [{'link': 'u1', 'created_at': '2026-10-01 10:00:00'}]
        history = [_history('u1', '2026-10-01 10:00:00', '2026-11-02 10:00:00')]
        trend = build_closure_trend(open_items, history, today=datetime.date(2026, 11, 5))
        assert trend['rows'][-1]['period_closed'] == 0
        assert trend['rows'][-1]['backlog'] == 1

    def test_empty_period_has_no_rate(self):
        trend = build_closure_trend([], [], today=datetime.date(2026, 11, 5))
        assert trend['rows'][-1]['rate'] is None


class TestSigTrends:
    def test_repository_maps_to_the_first_sig_listing_it(self):
        sigs = [{'name': 'A', 'repositories': ['boostkit/x', 'boostkit/shared']},
                {'name': 'B', 'repositories': ['boostkit/shared', 'boostkit/y']}]
        assert repo_sig_mapping(sigs, {'orgs': ['boostkit'], 'skip_sigs': []}) == {
            'boostkit/x': 'A', 'boostkit/shared': 'A', 'boostkit/y': 'B'}

    def test_skipped_sigs_and_foreign_orgs_are_dropped(self):
        sigs = [{'name': 'Kernel', 'repositories': ['boostkit/k']},
                {'name': 'A', 'repositories': ['other/x', 'boostkit/y']}]
        assert repo_sig_mapping(sigs, {'orgs': ['boostkit'], 'skip_sigs': ['Kernel']}) == {
            'boostkit/y': 'A'}

    def test_repo_of_link(self):
        assert repo_of_link('https://gitcode.com/boostkit/community/pulls/1') == 'boostkit/community'
        assert repo_of_link('') == ''

    def test_each_sig_gets_its_own_series(self):
        open_items = [
            {'link': 'https://gitcode.com/boostkit/a/issues/1', 'created_at': '2026-09-01 10:00:00'},
            {'link': 'https://gitcode.com/boostkit/b/issues/2', 'created_at': '2026-11-02 10:00:00'},
        ]
        trends = build_sig_trends(open_items, [], {'boostkit/a': 'A', 'boostkit/b': 'B'},
                                  today=datetime.date(2026, 11, 5))
        assert set(trends) == {'A', 'B'}
        assert trends['A']['rows'][-1]['backlog'] == 1
        assert trends['B']['rows'][-1]['backlog'] == 1
        # only the September item is severely overdue on 11-05
        assert trends['A']['rows'][-1]['severe'] == 1
        assert trends['B']['rows'][-1]['severe'] == 0

    def test_overdue_buckets_use_7_and_30_days(self):
        open_items = [
            {'link': 'https://gitcode.com/boostkit/a/issues/1', 'created_at': '2026-11-01 10:00:00'},
            {'link': 'https://gitcode.com/boostkit/a/issues/2', 'created_at': '2026-10-20 10:00:00'},
            {'link': 'https://gitcode.com/boostkit/a/issues/3', 'created_at': '2026-09-01 10:00:00'},
        ]
        trends = build_sig_trends(open_items, [], {'boostkit/a': 'A'},
                                  today=datetime.date(2026, 11, 5))
        point = trends['A']['rows'][-1]
        # 4 days / 16 days / 65 days → only the last two are overdue, the last one severe
        assert (point['backlog'], point['overdue'], point['severe']) == (3, 2, 1)

    def test_sig_without_items_in_the_window_is_omitted(self):
        trends = build_sig_trends([], [], {'boostkit/a': 'A'},
                                  today=datetime.date(2026, 11, 5))
        assert trends == {}


class TestClosureTrendRendering:
    def test_table_uses_the_same_columns_as_the_sig_tables(self):
        trend = {
            'window_label': '10-22~11-04',
            'rows': [{'label': '10-22~10-28（上周）', 'backlog': 6, 'overdue': 3,
                      'severe': 1, 'period_closed': 2, 'rate': 0.5,
                      'rate_text': '50.0%'}],
        }
        html = render_closure_trend(trend, 'PR')
        assert '全社区 PR 闭环趋势（10-22~11-04）' in html
        for header in ('统计区间', '待处理', '超期', '严重超期', '期间闭环', '闭环率'):
            assert '>{}<'.format(header) in html
        assert '>累计闭环<' not in html
        assert '50.0%' in html
        assert 'width:50%' in html            # bar width follows the rate

    def test_zero_rate_shows_dash(self):
        trend = {'window_label': 'x',
                 'rows': [{'label': 'x', 'backlog': 0, 'overdue': 0, 'severe': 0,
                           'period_closed': 0, 'rate': None, 'rate_text': '-'}]}
        html = render_closure_trend(trend, 'Issue')
        assert '>-<' in html

    def test_sig_trends_repeat_the_header_per_sig_and_are_sorted(self):
        point = {'label': '10-28~11-04（本周）', 'backlog': 3, 'overdue': 1,
                 'severe': 0, 'period_closed': 1, 'rate': 0.25,
                 'rate_text': '25.0%'}
        sig_trends = {
            'BoostVirt': {'window_label': '10-22~11-04', 'rows': [point]},
            'BoostCore': {'window_label': '10-22~11-04', 'rows': [point]},
        }
        html = render_sig_trends(sig_trends, 'PR')
        assert '各 SIG PR 闭环趋势' in html
        assert html.count('统计区间') == 2                 # header repeated per SIG block
        assert html.index('BoostCore') < html.index('BoostVirt')
        assert 'colspan="6"' in html

    def test_sig_trends_empty_renders_nothing(self):
        assert render_sig_trends({}, 'PR') == ''


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
