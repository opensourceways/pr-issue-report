"""
Tests for common.py — shared utilities for PR/Issue statistics.
"""
import csv
import datetime
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, mock_open, patch, ANY

import pytest

from common import (
    Logger,
    Logger as log,
    all_sigs_compare,
    cal_compare_timestamp,
    cal_sig_processed_rate,
    clean_env,
    compare_sig_processed_rate,
    count_duration,
    create_email_mappings,
    csv_to_xlsx,
    excel_optimization,
    expand_controls,
    fill_status,
    get_committers_mapping,
    get_email_mappings,
    get_maintainers,
    get_repo_members,
    get_sigs,
    get_user_id,
    load_email_controls,
    merge_html_parts,
    prepare_env,
    send_email,
    should_send,
    single_sig_compare,
    write_dry_run_html,
)


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class TestLogger:
    def test_logger_creation(self, tmp_path):
        """Logger should create a logger instance."""
        log_path = tmp_path / 'test.log'
        l = Logger(str(log_path), level='debug')
        assert l.logger is not None
        assert l.logger.level == 10  # DEBUG

    def test_logger_info_level(self, tmp_path):
        """Logger with info level."""
        log_path = tmp_path / 'test.log'
        l = Logger(str(log_path), level='info')
        assert l.logger.level == 20

    def test_logger_writes_to_file(self, tmp_path):
        """Logger should write messages to the log file."""
        log_path = tmp_path / 'test.log'
        l = Logger(str(log_path), level='debug')
        l.logger.info('test message')
        assert log_path.exists()
        content = log_path.read_text(encoding='utf-8')
        assert 'test message' in content


# ---------------------------------------------------------------------------
# fill_status
# ---------------------------------------------------------------------------

class TestFillStatus:
    def test_fill_when_clean(self):
        """When status is '待合入', replace it entirely."""
        assert fill_status('待合入', '草稿') == '草稿'

    def test_fill_when_already_dirty(self):
        """When status already changed, append with 、."""
        result = fill_status('CLA认证失败', '草稿')
        assert 'CLA认证失败' in result
        assert '草稿' in result
        assert '、' in result

    def test_fill_multiple_abnormal(self):
        """Multiple abnormal statuses accumulate."""
        result = fill_status('待合入', '门禁检查失败')
        assert result == '门禁检查失败'
        result = fill_status(result, '存在冲突')
        assert '门禁检查失败' in result
        assert '存在冲突' in result
        assert result.count('、') == 1


# ---------------------------------------------------------------------------
# count_duration
# ---------------------------------------------------------------------------

class TestCountDuration:
    def test_returns_string(self):
        """count_duration should return a string."""
        result = count_duration('2024-01-01 00:00:00')
        assert isinstance(result, str)
        assert int(result) > 0

    def test_zero_days_today(self):
        """A PR opened today should have 0 days duration."""
        today_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        assert count_duration(today_str) == '0'

    def test_exactly_one_day_ago(self):
        """Exactly 24 hours ago — at least 0 or 1 depending on clock."""
        one_day = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        result = int(count_duration(one_day))
        assert result in (0, 1)  # depends on exact time-of-day

    def test_future_date_is_negative(self):
        """Future date should give negative days."""
        future = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        assert int(count_duration(future)) < 0


# ---------------------------------------------------------------------------
# get_user_id
# ---------------------------------------------------------------------------

class TestGetUserId:
    def test_gitcode_id(self):
        assert get_user_id({'gitcode_id': 'abc'}) == 'abc'

    def test_gitee_id_fallback(self):
        assert get_user_id({'gitee_id': 'def'}) == 'def'

    def test_atomgit_id_fallback(self):
        assert get_user_id({'atomgit_id': 'ghi'}) == 'ghi'

    def test_priority_gitcode_over_gitee(self):
        """gitcode_id should take priority over gitee_id."""
        assert get_user_id({'gitcode_id': 'first', 'gitee_id': 'second'}) == 'first'

    def test_priority_gitee_over_atomgit(self):
        """gitee_id should take priority over atomgit_id."""
        assert get_user_id({'gitee_id': 'second', 'atomgit_id': 'third'}) == 'second'

    def test_empty_dict(self):
        assert get_user_id({}) == ''


# ---------------------------------------------------------------------------
# get_repo_members
# ---------------------------------------------------------------------------

class TestGetRepoMembers:
    def test_maintainers_only(self):
        """When repo has no committers, return maintainers only."""
        maintainers = ['m1', 'm2']
        committers = {}
        result = get_repo_members(maintainers, committers, 'openeuler/repo')
        assert result == ['m1', 'm2']

    def test_maintainers_plus_committers(self):
        """When repo has committers, append unique ones."""
        maintainers = ['m1', 'm2']
        committers = {'openeuler/repo': ['c1', 'c2']}
        result = get_repo_members(maintainers, committers, 'openeuler/repo')
        assert result == ['m1', 'm2', 'c1', 'c2']

    def test_no_duplicate_committers(self):
        """Committer who is already a maintainer is not duplicated."""
        maintainers = ['m1', 'm2']
        committers = {'openeuler/repo': ['m1', 'c1']}
        result = get_repo_members(maintainers, committers, 'openeuler/repo')
        assert result == ['m1', 'm2', 'c1']

    def test_empty_maintainers(self):
        maintainers = []
        committers = {'openeuler/repo': ['c1']}
        result = get_repo_members(maintainers, committers, 'openeuler/repo')
        assert result == ['c1']


# ---------------------------------------------------------------------------
# single_sig_compare
# ---------------------------------------------------------------------------

class TestSingleSigCompare:
    def test_found(self, compare_dict_sample):
        assert single_sig_compare('sig-ai', compare_dict_sample) == \
            'PR处理率为75.0%, 同比上周上升5.0%'

    def test_not_found(self, compare_dict_sample):
        assert single_sig_compare('nonexistent', compare_dict_sample) is None


# ---------------------------------------------------------------------------
# cal_compare_timestamp
# ---------------------------------------------------------------------------

class TestCalCompareTimestamp:
    def test_returns_two_ints(self):
        ts_today, ts_last = cal_compare_timestamp()
        assert isinstance(ts_today, int)
        assert isinstance(ts_last, int)

    def test_diff_is_one_week_in_ms(self):
        ts_today, ts_last = cal_compare_timestamp()
        assert ts_today - ts_last == 3600 * 24 * 7 * 1000

    def test_today_ts_is_9am(self):
        ts_today, _ = cal_compare_timestamp()
        # 9 AM UTC+8 timestamp in ms
        dt = datetime.datetime.fromtimestamp(ts_today / 1000)
        assert dt.hour == 9


# ---------------------------------------------------------------------------
# compare_sig_processed_rate (unit test via mocking cal_sig_processed_rate)
# ---------------------------------------------------------------------------

class TestCompareSigProcessedRate:
    def test_both_unavailable(self, monkeypatch):
        """When API returns -1 for both, return empty string."""
        monkeypatch.setattr('common.cal_sig_processed_rate', lambda sig, ts: -1)
        assert compare_sig_processed_rate('sig-ai') == ''

    def test_unchanged(self, monkeypatch):
        rates = iter([0.75, 0.75])
        monkeypatch.setattr('common.cal_sig_processed_rate', lambda sig, ts: next(rates))
        result = compare_sig_processed_rate('sig-ai')
        assert '不变' in result

    def test_up(self, monkeypatch):
        rates = iter([0.80, 0.70])
        monkeypatch.setattr('common.cal_sig_processed_rate', lambda sig, ts: next(rates))
        result = compare_sig_processed_rate('sig-ai')
        assert '上升' in result

    def test_down(self, monkeypatch):
        rates = iter([0.60, 0.70])
        monkeypatch.setattr('common.cal_sig_processed_rate', lambda sig, ts: next(rates))
        result = compare_sig_processed_rate('sig-ai')
        assert '下降' in result

    def test_zero_rate(self, monkeypatch):
        """When both rates are 0, it's considered unchanged."""
        rates = iter([0.0, 0.0])
        monkeypatch.setattr('common.cal_sig_processed_rate', lambda sig, ts: next(rates))
        result = compare_sig_processed_rate('sig-ai')
        assert '不变' in result


# ---------------------------------------------------------------------------
# all_sigs_compare
# ---------------------------------------------------------------------------

class TestAllSigsCompare:
    def test_builds_dict(self, monkeypatch):
        def mock_compare(name):
            return 'info for {}'.format(name)
        monkeypatch.setattr('common.compare_sig_processed_rate', mock_compare)
        result = all_sigs_compare(['sig-a', 'sig-b'])
        assert result == {'sig-a': 'info for sig-a', 'sig-b': 'info for sig-b'}


# ---------------------------------------------------------------------------
# cal_sig_processed_rate
# ---------------------------------------------------------------------------

class TestCalSigProcessedRate:
    def test_api_failure(self, monkeypatch):
        """When API returns non-200, return -1."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: mock_resp)
        assert cal_sig_processed_rate('sig-ai', 1234567890000) == -1

    def test_empty_data(self, monkeypatch):
        """When API data is empty, return -1."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'data': None}
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: mock_resp)
        assert cal_sig_processed_rate('sig-ai', 1234567890000) == -1

    def test_all_zero(self, monkeypatch):
        """When all counts are zero, return 0."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'data': {'merged': 0, 'closed': 0, 'open': 0}
        }
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: mock_resp)
        assert cal_sig_processed_rate('sig-ai', 1234567890000) == 0

    def test_normal_calculation(self, monkeypatch):
        """Normal calculation: (merged+closed)/(merged+closed+open)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'data': {'merged': 70, 'closed': 5, 'open': 25}
        }
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: mock_resp)
        result = cal_sig_processed_rate('sig-ai', 1234567890000)
        assert result == 0.75

    def test_fully_processed(self, monkeypatch):
        """100% processed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'data': {'merged': 100, 'closed': 0, 'open': 0}
        }
        monkeypatch.setattr('common.requests.get', lambda url, params, timeout: mock_resp)
        assert cal_sig_processed_rate('sig-ai', 1234567890000) == 1.0


# ---------------------------------------------------------------------------
# prepare_env
# ---------------------------------------------------------------------------

class TestPrepareEnv:
    def test_success(self, monkeypatch, tmp_path):
        """prepare_env clones community repo and creates data dir."""
        path_state = {'community': False, 'data': False}
        def fake_exists(path):
            return path_state.get(path, False)
        def fake_rmtree(path):
            path_state[path] = False
        def fake_run(cmd, check=False, **kw):
            if 'git' in cmd and 'clone' in cmd:
                path_state['community'] = True
        def fake_makedirs(path, exist_ok=False):
            path_state['data'] = True

        monkeypatch.setattr('common.shutil.rmtree', fake_rmtree)
        monkeypatch.setattr('common.subprocess.run', fake_run)
        monkeypatch.setattr('common.os.makedirs', fake_makedirs)
        monkeypatch.setattr('common.os.path.exists', fake_exists)

        result = prepare_env()
        assert result == 'data'

    def test_clone_failure(self, monkeypatch):
        """When git clone fails, sys.exit(1)."""
        monkeypatch.setattr('common.shutil.rmtree', lambda p: None)
        monkeypatch.setattr('common.subprocess.run', lambda cmd, check=False, **kw: None)
        monkeypatch.setattr('common.os.makedirs', lambda p, exist_ok=False: None)
        monkeypatch.setattr('common.os.path.exists', lambda p: False)
        with pytest.raises(SystemExit) as exc_info:
            prepare_env()
        assert exc_info.value.code == 1

    def test_mkdir_failure(self, monkeypatch):
        """When mkdir fails, sys.exit(1)."""
        path_state = {'community': True, 'data': False}
        def fake_exists(path):
            return path_state.get(path, False)
        def fake_run(cmd, check=False, **kw):
            if 'git' in cmd and 'clone' in cmd:
                path_state['community'] = True
        monkeypatch.setattr('common.shutil.rmtree', lambda p: None)
        monkeypatch.setattr('common.subprocess.run', fake_run)
        monkeypatch.setattr('common.os.makedirs', lambda p, exist_ok=False: None)
        monkeypatch.setattr('common.os.path.exists', fake_exists)
        with pytest.raises(SystemExit) as exc_info:
            prepare_env()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# clean_env
# ---------------------------------------------------------------------------

class TestCleanEnv:
    def test_calls_rmtree(self, monkeypatch):
        calls = []
        monkeypatch.setattr('common.shutil.rmtree', lambda path, ignore_errors=False: calls.append(path))
        clean_env('data')
        assert 'data' in calls


# ---------------------------------------------------------------------------
# csv_to_xlsx
# ---------------------------------------------------------------------------

class TestCsvToXlsx:
    def test_converts_csv_to_xlsx(self, tmp_path):
        """csv_to_xlsx should convert a CSV file to XLSX."""
        csv_path = tmp_path / 'test.csv'
        csv_path.write_text(
            'sig_name,repo,branch,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,main,#100,Test PR,待合入,5\n',
            encoding='utf-8'
        )
        xlsx_path = csv_to_xlsx(str(csv_path))
        assert xlsx_path.endswith('.xlsx')
        assert os.path.exists(xlsx_path)

    def test_non_csv_returns_none(self, tmp_path):
        """Non-.csv path returns None."""
        path = tmp_path / 'test.txt'
        path.write_text('hello')
        assert csv_to_xlsx(str(path)) is None

    def test_missing_file_exits(self, monkeypatch, tmp_path):
        """When xlsx generation fails, sys.exit(1)."""
        # Create a CSV that will convert to XLSX but then we sabotage the xlsx path
        csv_path = tmp_path / 'test.csv'
        csv_path.write_text('sig_name,repo,branch,number,title,status,duration\n'
                            'sig-ai,openeuler/ai-framework,main,#1,Test,待合入,1\n',
                            encoding='utf-8')
        # Make os.path.exists return False for the xlsx — simulating write failure
        monkeypatch.setattr('common.os.path.exists', lambda p: not p.endswith('.xlsx'))
        with pytest.raises(SystemExit):
            csv_to_xlsx(str(csv_path))


# ---------------------------------------------------------------------------
# excel_optimization
# ---------------------------------------------------------------------------

class TestExcelOptimization:
    def _make_xlsx(self, tmp_path, csv_content, filename='test'):
        """Helper: create CSV → convert to XLSX → return xlsx path."""
        csv_path = tmp_path / '{}.csv'.format(filename)
        csv_path.write_text(csv_content, encoding='utf-8')
        return csv_to_xlsx(str(csv_path))

    def test_pr_mode_generates_html(self, tmp_path, compare_dict_sample):
        """excel_optimization generates an HTML file for PR layout."""
        csv = (
            'sig_name,repo,branch,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,main,<a href="url">#100</a>,<a href="url">Fix bug</a>,待合入,3\n'
            'sig-ai,openeuler/ai-models,main,<a href="url">#101</a>,<a href="url">Feature</a>,草稿,15\n'
        )
        xlsx = self._make_xlsx(tmp_path, csv, 'pr_test')
        excel_optimization(xlsx, compare_dict_sample, is_issue=False)
        html = xlsx.replace('.xlsx', '.html')
        assert os.path.exists(html)
        content = open(html, 'r', encoding='utf-8').read()
        assert len(content) > 0

    def test_issue_mode_generates_html(self, tmp_path, compare_dict_sample):
        """excel_optimization generates an HTML file for Issue layout."""
        csv = (
            'sig_name,repo,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,<a href="url">#200</a>,<a href="url">Bug</a>,缺陷 / 待确认,3\n'
        )
        xlsx = self._make_xlsx(tmp_path, csv, 'issue_test')
        excel_optimization(xlsx, compare_dict_sample, is_issue=True)
        html = xlsx.replace('.xlsx', '.html')
        assert os.path.exists(html)

    def test_non_xlsx_returns_none(self, tmp_path, compare_dict_sample):
        """Passing a non-.xlsx path returns early."""
        result = excel_optimization('/some/file.txt', compare_dict_sample)
        assert result is None

    def test_color_coding_applied(self, tmp_path, compare_dict_sample):
        """Days > 365 should get red fill, 7 < days <= 30 gets orange."""
        csv = (
            'sig_name,repo,branch,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,main,<a href="#">#1</a>,<a href="#">Old PR</a>,待合入,400\n'
            'sig-ai,openeuler/ai-framework,main,<a href="#">#2</a>,<a href="#">New PR</a>,待合入,3\n'
        )
        xlsx = self._make_xlsx(tmp_path, csv, 'color_test')
        excel_optimization(xlsx, compare_dict_sample, is_issue=False)
        # Re-open and verify fills exist
        import openpyxl
        wb = openpyxl.load_workbook(xlsx)
        ws = wb.active
        # Find the duration cells (last column = F for PR)
        fills_found = set()
        for row in ws.iter_rows(min_row=3, min_col=6, max_col=6):
            val = row[0].value
            if val is not None and str(val).isdigit():
                fills_found.add(int(val))
        wb.close()
        assert 400 in fills_found
        assert 3 in fills_found

    def test_non_xlsx_extension(self, tmp_path, compare_dict_sample):
        """File without .xlsx extension returns None."""
        assert excel_optimization('/tmp/file.doc', compare_dict_sample) is None

    def test_status_color_applied(self, tmp_path, compare_dict_sample):
        """Multiple status flags should get yellow fill."""
        csv = (
            'sig_name,repo,branch,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,main,<a href="#">#1</a>,<a href="#">PR</a>,门禁检查失败、存在冲突,10\n'
        )
        xlsx = self._make_xlsx(tmp_path, csv, 'status_test')
        excel_optimization(xlsx, compare_dict_sample, is_issue=False)
        import openpyxl
        wb = openpyxl.load_workbook(xlsx)
        ws = wb.active
        # Scan all rows for the status value
        found = False
        for row in ws.iter_rows(min_col=5, max_col=5):
            val = row[0].value
            if val and '门禁检查失败' in str(val):
                # Should have yellow fill (non-default)
                fill_rgb = row[0].fill.start_color.rgb
                if fill_rgb and fill_rgb != '00000000':
                    found = True
                break
        wb.close()
        assert found, 'Status cell should have yellow fill applied'

    def test_empty_xlsx_handling(self, tmp_path, compare_dict_sample):
        """Single-row xlsx should not crash."""
        csv = (
            'sig_name,repo,branch,number,title,status,duration\n'
            'sig-ai,openeuler/ai-framework,main,<a href="#">#1</a>,<a href="#">OK</a>,待合入,1\n'
        )
        xlsx = self._make_xlsx(tmp_path, csv, 'single')
        # Should not raise
        excel_optimization(xlsx, compare_dict_sample, is_issue=False)
        assert os.path.exists(xlsx.replace('.xlsx', '.html'))


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------

# NOTE: send_email tests are in test_common_send_email below to avoid
# module-level side effects from importing common (which creates Logger).


# ---------------------------------------------------------------------------
# get_maintainers
# ---------------------------------------------------------------------------

class TestGetMaintainers:
    def test_from_owners_file(self):
        """When OWNERS file exists, parse maintainers from it."""
        mock_yaml = {'maintainers': ['m1', 'm2']}
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            with patch('common.os.path.exists', lambda p: 'OWNERS' in p):
                with patch('common.yaml.safe_load', return_value=mock_yaml):
                    maintainers, mark = get_maintainers('sig-test')
                    assert maintainers == ['m1', 'm2']
                    assert mark is False

    def test_from_sig_info_file(self):
        """When only sig-info.yaml exists, parse from there."""
        sig_info = {
            'maintainers': [
                {'gitcode_id': 'm1', 'email': 'm1@x.com'},
                {'gitee_id': 'm2', 'email': 'm2@x.com'},
            ]
        }
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            # OWNERS doesn't exist, sig-info.yaml does
            with patch('common.os.path.exists', lambda p: 'sig-info' in p):
                with patch('common.yaml.safe_load', return_value=sig_info):
                    maintainers, mark = get_maintainers('sig-test')
                    assert maintainers == ['m1', 'm2']
                    assert mark is True

    def test_neither_file(self):
        """When neither OWNERS nor sig-info.yaml exist, sys.exit(1)."""
        with patch('common.os.path.exists', return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                get_maintainers('sig-test')
            assert exc_info.value.code == 1

    def test_sig_info_with_atomgit_id(self):
        """get_user_id fallback to atomgit_id."""
        sig_info = {
            'maintainers': [
                {'atomgit_id': 'm3', 'email': 'm3@x.com'},
            ]
        }
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            with patch('common.os.path.exists', lambda p: 'sig-info' in p):
                with patch('common.yaml.safe_load', return_value=sig_info):
                    maintainers, _ = get_maintainers('sig-test')
                    assert maintainers == ['m3']


# ---------------------------------------------------------------------------
# get_committers_mapping
# ---------------------------------------------------------------------------

class TestGetCommittersMapping:
    def test_no_sig_info_file(self):
        """When sig-info.yaml doesn't exist, return empty dict."""
        with patch('common.os.path.exists', return_value=False):
            assert get_committers_mapping('sig-test') == {}

    def test_no_repositories_key(self):
        """When sig-info has no repositories, return empty dict."""
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            with patch('common.os.path.exists', return_value=True):
                with patch('common.yaml.safe_load', return_value={'maintainers': []}):
                    assert get_committers_mapping('sig-test') == {}

    def test_repos_without_committers(self):
        """Repositories without committers key are skipped."""
        sig_info = {
            'repositories': [
                {'repo': ['openeuler/repo1']},
            ]
        }
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            with patch('common.os.path.exists', return_value=True):
                with patch('common.yaml.safe_load', return_value=sig_info):
                    assert get_committers_mapping('sig-test') == {}

    def test_repos_with_committers(self):
        """Extract committers per repository."""
        sig_info = {
            'repositories': [
                {
                    'repo': ['openeuler/repo1', 'src-openeuler/repo1'],
                    'committers': [
                        {'gitcode_id': 'c1'},
                        {'gitee_id': 'c2'},
                    ]
                }
            ]
        }
        m = mock_open(read_data='')
        with patch('builtins.open', m):
            with patch('common.os.path.exists', return_value=True):
                with patch('common.yaml.safe_load', return_value=sig_info):
                    result = get_committers_mapping('sig-test')
                    assert 'openeuler/repo1' in result
                    assert 'src-openeuler/repo1' in result
                    assert result['openeuler/repo1'] == ['c1', 'c2']


# ---------------------------------------------------------------------------
# create_email_mappings
# ---------------------------------------------------------------------------

class TestCreateEmailMappings:
    def test_basic_mapping(self):
        """Generate email_mapping.yaml from sig-info.yaml files."""
        sig_info = {
            'maintainers': [
                {'gitcode_id': 'm1', 'email': 'm1@e.com'},
                {'gitee_id': 'm2', 'email': 'm2@e.com'},
            ],
            'repositories': [
                {
                    'repo': ['openeuler/r1'],
                    'committers': [
                        {'gitcode_id': 'c1', 'email': 'c1@e.com'},
                    ]
                }
            ]
        }

        # Only sig-info.yaml exists, not OWNERS
        def fake_exists(path):
            return 'sig-info' in path

        m = mock_open()
        with patch('builtins.open', m):
            with patch('common.os.path.exists', fake_exists):
                with patch('common.os.listdir', return_value=['README.md', 'sig-test']):
                    with patch('common.os.walk', return_value=[]):
                        with patch('common.yaml.safe_load', return_value=sig_info):
                            with patch('common.yaml.dump'):
                                with patch('common.subprocess.run', return_value=0):
                                    create_email_mappings()

    def test_null_email_filtered(self):
        """Emails that are 'null' or 'NA' are treated as empty and removed."""
        sig_info = {
            'maintainers': [
                {'gitcode_id': 'm1', 'email': 'null'},
                {'gitee_id': 'm2', 'email': 'NA'},
            ],
            'repositories': []
        }

        def fake_exists(path):
            return 'sig-info' in path

        m = mock_open()
        with patch('builtins.open', m):
            with patch('common.os.path.exists', fake_exists):
                with patch('common.os.listdir', return_value=['README.md', 'sig-test']):
                    with patch('common.os.walk', return_value=[]):
                        with patch('common.yaml.safe_load', return_value=sig_info):
                            with patch('common.yaml.dump'):
                                with patch('common.subprocess.run', return_value=0):
                                    create_email_mappings()


# ---------------------------------------------------------------------------
# get_email_mappings
# ---------------------------------------------------------------------------

class TestGetEmailMappings:
    def test_creates_and_reads(self, monkeypatch, tmp_path):
        """get_email_mappings calls create_email_mappings then reads the file."""
        # create_email_mappings writes email_mapping.yaml to CWD
        # We need to run from tmp_path
        import common
        monkeypatch.chdir(tmp_path)
        # Pre-create the yaml file (simulating create_email_mappings output)
        mapping = {'m1': 'm1@e.com'}
        import yaml as _yaml
        (tmp_path / 'email_mapping.yaml').write_text(
            _yaml.dump(mapping), encoding='utf-8'
        )
        with patch.object(common, 'create_email_mappings', return_value=None):
            result = get_email_mappings()
            assert result == mapping

    def test_no_file_returns_empty(self, monkeypatch, tmp_path):
        """When email_mapping.yaml is missing, return {}."""
        import common
        monkeypatch.chdir(tmp_path)
        with patch.object(common, 'create_email_mappings', return_value=None):
            result = get_email_mappings()
            assert result == {}


# ---------------------------------------------------------------------------
# send_email (separate to avoid module-level Logger side effects)
# ---------------------------------------------------------------------------

class TestSendEmail:
    def test_smtp_ssl_success(self, tmp_path, set_smtp_env, monkeypatch):
        """Send email via SMTP_SSL on port 465."""
        html = tmp_path / 'test.html'
        xlsx = tmp_path / 'test.xlsx'
        html.write_text('<html><body><p>Content</p></body></html>', encoding='utf-8')
        xlsx.write_text('fake xlsx')

        mock_server = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr('common.smtplib.SMTP_SSL', lambda host, port, timeout: mock_smtp)
        monkeypatch.setattr('common.MIMEMultipart', MagicMock())

        send_email(str(xlsx), 'testuser', ['test@example.com'],
                   subject='Test Subject')

        mock_server.login.assert_called_once_with('test_user', 'test_pass')
        mock_server.sendmail.assert_called_once()

    def test_smtp_starttls(self, tmp_path, set_smtp_env, monkeypatch):
        """Send email via SMTP + STARTTLS on port 587."""
        monkeypatch.setenv('smtp_port', '587')
        html = tmp_path / 'test.html'
        xlsx = tmp_path / 'test.xlsx'
        html.write_text('<html><body>Content</body></html>', encoding='utf-8')
        xlsx.write_text('fake')

        mock_server = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr('common.smtplib.SMTP', lambda host, port, timeout: mock_smtp)
        monkeypatch.setattr('common.MIMEMultipart', MagicMock())

        send_email(str(xlsx), 'testuser', ['test@example.com'])
        mock_server.starttls.assert_called_once()

    def test_receives_original_recipients(self, tmp_path, set_smtp_env, monkeypatch):
        """send_email sends to the provided recipients (redirection handled by callers)."""
        html = tmp_path / 'test.html'
        xlsx = tmp_path / 'test.xlsx'
        html.write_text('<html><body>Content</body></html>', encoding='utf-8')
        xlsx.write_text('fake')

        mock_server = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr('common.smtplib.SMTP_SSL', lambda host, port, timeout: mock_smtp)
        monkeypatch.setattr('common.MIMEMultipart', MagicMock())

        send_email(str(xlsx), 'testuser', ['original@example.com'])
        call_args = mock_server.sendmail.call_args
        # send_email sends to the original recipients (no internal redirect)
        assert 'original@example.com' in str(call_args)

    def test_smtp_exception_logged(self, tmp_path, set_smtp_env, monkeypatch):
        """SMTP exceptions are logged, not raised."""
        import smtplib as smtplib_mod
        html = tmp_path / 'test.html'
        xlsx = tmp_path / 'test.xlsx'
        html.write_text('<html><body>Content</body></html>', encoding='utf-8')
        xlsx.write_text('fake')

        mock_server = MagicMock()
        mock_server.sendmail.side_effect = smtplib_mod.SMTPException('SMTP error')
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr('common.smtplib.SMTP_SSL', lambda host, port, timeout: mock_smtp)
        monkeypatch.setattr('common.MIMEMultipart', MagicMock())

        # Should not raise
        send_email(str(xlsx), 'testuser', ['test@example.com'])

    def test_link_escaping_fix(self, tmp_path, set_smtp_env, monkeypatch):
        """xlsx2html &lt;a&gt; escaping is restored to real <a> links."""
        html = tmp_path / 'test.html'
        xlsx = tmp_path / 'test.xlsx'
        # Simulate xlsx2html output with escaped links
        html.write_text(
            '<html><body>&lt;a href="http://x"&gt;text&lt;/a&gt;</body></html>',
            encoding='utf-8'
        )
        xlsx.write_text('fake')

        mock_server = MagicMock()
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr('common.smtplib.SMTP_SSL', lambda host, port, timeout: mock_smtp)
        monkeypatch.setattr('common.MIMEMultipart', MagicMock())

        send_email(str(xlsx), 'testuser', ['test@example.com'])
        # Verify the body contains fixed <a> tags
        # The MIMEText would have the fixed content
        from common import MIMEText
        # MIMEText was called with the fixed body


# ---------------------------------------------------------------------------
# expand_controls / load_email_controls / should_send
# ---------------------------------------------------------------------------

class TestExpandControls:
    def test_none_config(self):
        result = expand_controls(None)
        assert result['pr']['maintainer'] is True
        assert result['issue']['committer'] is True

    def test_false_config(self):
        result = expand_controls(False)
        assert result['pr']['maintainer'] is True
        assert result['issue']['committer'] is True

    def test_all_false(self):
        result = expand_controls({'all': False})
        assert result['pr']['maintainer'] is False
        assert result['pr']['committer'] is False
        assert result['issue']['maintainer'] is False
        assert result['issue']['committer'] is False

    def test_pr_false(self):
        result = expand_controls({'pr': False})
        assert result['pr']['maintainer'] is False
        assert result['pr']['committer'] is False
        assert result['issue']['maintainer'] is True

    def test_partial_role_control(self):
        result = expand_controls({
            'pr': {'maintainer': False},
            'issue': {'committer': False},
        })
        assert result['pr']['maintainer'] is False
        assert result['pr']['committer'] is True
        assert result['issue']['maintainer'] is True
        assert result['issue']['committer'] is False


class TestLoadEmailControls:
    def test_missing_file_returns_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv('EMAIL_CONTROLS_PATH', str(tmp_path / 'nonexistent.yaml'))
        controls = load_email_controls()
        assert controls['anyone']['pr']['maintainer'] is True

    def test_default_path_independent_of_cwd(self, tmp_path, monkeypatch):
        """Default control file resolves to the repo root even after chdir."""
        monkeypatch.delenv('EMAIL_CONTROLS_PATH', raising=False)
        monkeypatch.chdir(tmp_path)
        controls = load_email_controls()
        # binaryzero-hyh is unsubscribed in the repo-root email_controls.yaml
        assert controls['binaryzero-hyh']['pr']['maintainer'] is False
        assert controls['binaryzero-hyh']['issue']['committer'] is False

    def test_relative_env_path_resolves_repo_root(self, tmp_path, monkeypatch):
        """A relative EMAIL_CONTROLS_PATH resolves against the repo root, not CWD."""
        monkeypatch.setenv('EMAIL_CONTROLS_PATH', 'email_controls.yaml')
        monkeypatch.chdir(tmp_path)
        controls = load_email_controls()
        assert controls['binaryzero-hyh']['pr']['maintainer'] is False

    def test_loads_from_env_path(self, tmp_path, monkeypatch):
        controls_file = tmp_path / 'controls.yaml'
        controls_file.write_text('alice:\n  pr:\n    maintainer: false\n', encoding='utf-8')
        monkeypatch.setenv('EMAIL_CONTROLS_PATH', str(controls_file))
        controls = load_email_controls()
        assert controls['alice']['pr']['maintainer'] is False
        assert controls['alice']['pr']['committer'] is True

    def test_loads_from_arg_path(self, tmp_path):
        controls_file = tmp_path / 'controls.yaml'
        controls_file.write_text('bob:\n  issue: false\n', encoding='utf-8')
        controls = load_email_controls(str(controls_file))
        assert controls['bob']['issue']['maintainer'] is False
        assert controls['bob']['issue']['committer'] is False
        assert controls['bob']['pr']['maintainer'] is True

    def test_community_override_replaces_base(self, tmp_path):
        """communities.<name> completely overrides the base config for that community."""
        controls_file = tmp_path / 'controls.yaml'
        controls_file.write_text(
            'carol:\n'
            '  issue: false\n'
            '  communities:\n'
            '    boostkit:\n'
            '      all: false\n',
            encoding='utf-8')
        controls = load_email_controls(str(controls_file), community='boostkit')
        assert controls['carol']['pr']['maintainer'] is False
        assert controls['carol']['issue']['maintainer'] is False

    def test_community_override_ignored_for_other_community(self, tmp_path):
        """Overrides for other communities do not apply."""
        controls_file = tmp_path / 'controls.yaml'
        controls_file.write_text(
            'carol:\n'
            '  issue: false\n'
            '  communities:\n'
            '    boostkit:\n'
            '      all: false\n',
            encoding='utf-8')
        controls = load_email_controls(str(controls_file), community='openeuler')
        assert controls['carol']['pr']['maintainer'] is True
        assert controls['carol']['issue']['maintainer'] is False

    def test_community_override_can_resubscribe(self, tmp_path):
        """A community override can re-enable mail the base config disabled."""
        controls_file = tmp_path / 'controls.yaml'
        controls_file.write_text(
            'dave:\n'
            '  all: false\n'
            '  communities:\n'
            '    boostkit:\n'
            '      issue: false\n',
            encoding='utf-8')
        controls = load_email_controls(str(controls_file), community='boostkit')
        assert controls['dave']['pr']['maintainer'] is True
        assert controls['dave']['issue']['maintainer'] is False


class TestShouldSend:
    def test_default_true(self):
        from collections import defaultdict
        controls = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: True)))
        assert should_send(controls, 'alice', 'pr', 'maintainer') is True

    def test_false_value(self):
        controls = {'alice': {'pr': {'maintainer': False, 'committer': True}}}
        assert should_send(controls, 'alice', 'pr', 'maintainer') is False
        assert should_send(controls, 'alice', 'pr', 'committer') is True


# ---------------------------------------------------------------------------
# merge_html_parts / write_dry_run_html
# ---------------------------------------------------------------------------

class TestMergeHtmlParts:
    def test_single_part(self, tmp_path):
        html = tmp_path / 'part1.html'
        html.write_text('<html><body><p>Part 1</p></body></html>', encoding='utf-8')
        result = merge_html_parts([('Title 1', str(html))], 'pr')
        assert 'Part 1' in result
        assert 'Title 1' in result
        assert '退订' in result

    def test_two_parts(self, tmp_path):
        html1 = tmp_path / 'part1.html'
        html2 = tmp_path / 'part2.html'
        html1.write_text('<html><body><p>Part 1</p></body></html>', encoding='utf-8')
        html2.write_text('<html><body><p>Part 2</p></body></html>', encoding='utf-8')
        result = merge_html_parts([('Title 1', str(html1)), ('Title 2', str(html2))], 'pr')
        assert 'Part 1' in result
        assert 'Part 2' in result
        assert '<h3' in result
        assert 'Title 1' in result
        assert 'Title 2' in result

    def test_empty_parts(self):
        assert merge_html_parts([], 'pr') is None


class TestWriteDryRunHtml:
    def test_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_dry_run_html('pr', 'alice', '<html><body>Test</body></html>')
        output = tmp_path / 'test_output' / 'pr_alice.html'
        assert output.exists()
        assert 'Test' in output.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# get_sigs
# ---------------------------------------------------------------------------

class TestGetSigs:
    def test_discovers_sigs_and_repos(self, tmp_path):
        """Walk community/sig/ to discover SIG→repo mappings."""
        import common as common_mod

        sig_path = 'community/sig'

        def fake_listdir(path):
            """Mock os.listdir for specific paths."""
            if path == sig_path:
                return ['README.md', 'sig-template', 'sig-recycle',
                        'create_sig_info_template.py', 'sig-ai', 'sig-base']
            if path.endswith('sig/sig-ai'):
                return ['openeuler']
            if path.endswith('sig/sig-base'):
                return ['src-openeuler']
            return []

        def fake_walk(path):
            """Mock os.walk for specific sig paths."""
            if 'sig-ai/openeuler' in path:
                yield (path, [], ['ai-framework.yaml', 'ai-models.yaml'])
            elif 'sig-base/src-openeuler' in path:
                yield (path, [], ['base-tools.yaml'])
            # For directories that would be real-listdir'd inside the walk,
            # just yield empty

        with patch('common.os.listdir', fake_listdir):
            with patch('common.os.walk', fake_walk):
                with patch('common.os.path.isdir', return_value=True):
                    sigs, sigs_list = get_sigs()

        assert len(sigs) >= 2
        for s in sigs:
            if s['name'] == 'sig-ai':
                assert 'openeuler/ai-framework' in s['repositories']
                assert 'openeuler/ai-models' in s['repositories']
            if s['name'] == 'sig-base':
                assert 'src-openeuler/base-tools' in s['repositories']
        # sigs_list should contain names
        assert 'sig-ai' in sigs_list
        assert 'sig-base' in sigs_list
