"""
Shared test fixtures and mocks for pr-statistics-report tests.
"""
import os
import sys
import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sigs_sample():
    """A minimal SIG → repositories mapping for tests."""
    return [
        {
            'name': 'sig-ai',
            'repositories': [
                'openeuler/ai-framework',
                'openeuler/ai-models',
            ],
        },
        {
            'name': 'sig-base',
            'repositories': [
                'src-openeuler/base-tools',
            ],
        },
    ]


@pytest.fixture
def sigs_list_sample(sigs_sample):
    """Flat list of SIG names."""
    return [s['name'] for s in sigs_sample]


@pytest.fixture
def repos_pulls_mapping_sample():
    """Minimal repo→pull mapping for PR tests."""
    import datetime
    return {
        'openeuler/ai-framework/main': {
            'title': 'Add AI feature X',
            'link': 'https://gitcode.com/openeuler/ai-framework/pulls/100',
            'created_at': (datetime.datetime.now() - datetime.timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S'),
            'draft': False,
            'labels': 'openeuler-cla/yes,kind/wait_for_update',
            'ref': 'main',
            'mergeable': True,
        },
        'openeuler/ai-framework/dev': {
            'title': 'Fix memory leak in inference',
            'link': 'https://gitcode.com/openeuler/ai-framework/pulls/101',
            'created_at': (datetime.datetime.now() - datetime.timedelta(days=365)).strftime('%Y-%m-%d %H:%M:%S'),
            'draft': True,
            'labels': 'ci_failed',
            'ref': 'dev',
            'mergeable': False,
        },
        'openeuler/ai-models/main': {
            'title': 'Update model weights to v2',
            'link': 'https://gitcode.com/openeuler/ai-models/pulls/102',
            'created_at': (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'draft': False,
            'labels': 'openeuler-cla/yes',
            'ref': 'main',
            'mergeable': True,
        },
    }


@pytest.fixture
def repos_issues_mapping_sample():
    """Minimal repo→issue mapping for issue tests."""
    import datetime
    return {
        'openeuler/ai-framework/feature': {
            'title': 'Broken GPU detection',
            'link': 'https://gitcode.com/openeuler/ai-framework/issues/200',
            'created_at': (datetime.datetime.now() - datetime.timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S'),
            'issue_type': '缺陷',
            'issue_state': '待确认',
            'assignee': '',
        },
        'openeuler/ai-models/main': {
            'title': 'Add new model arch support',
            'link': 'https://gitcode.com/openeuler/ai-models/issues/201',
            'created_at': (datetime.datetime.now() - datetime.timedelta(days=40)).strftime('%Y-%m-%d %H:%M:%S'),
            'issue_type': '特性',
            'issue_state': '进行中',
            'assignee': 'testuser',
        },
    }


@pytest.fixture
def compare_dict_sample():
    """Sample week-over-week comparison data."""
    return {
        'sig-ai': 'PR处理率为75.0%, 同比上周上升5.0%',
        'sig-base': 'PR处理率为60.0%, 同比上周不变',
    }


@pytest.fixture
def email_mappings_sample():
    """Sample gitee_id → email mapping."""
    return {
        'maintainer1': 'm1@example.com',
        'maintainer2': 'm2@example.com',
        'committer1': 'c1@example.com',
        'committer2': 'c2@example.com',
    }


@pytest.fixture(autouse=True)
def clean_test_env(monkeypatch):
    """Ensure every test starts with a clean env, no real SMTP or API calls."""
    monkeypatch.delenv('email_username', raising=False)
    monkeypatch.delenv('email_password', raising=False)
    monkeypatch.delenv('smtp_host', raising=False)
    monkeypatch.delenv('smtp_port', raising=False)
    monkeypatch.delenv('email_sender', raising=False)
    monkeypatch.delenv('test_reviever_email', raising=False)


@pytest.fixture
def set_smtp_env(monkeypatch):
    """Set SMTP environment variables for email tests."""
    monkeypatch.setenv('email_username', 'test_user')
    monkeypatch.setenv('email_password', 'test_pass')  # nosec B105
    monkeypatch.setenv('smtp_host', 'smtp.example.com')
    monkeypatch.setenv('smtp_port', '465')
    monkeypatch.setenv('email_sender', 'sender@example.com')
    return {
        'username': 'test_user',
        'password': 'test_pass',  # nosec B105
        'host': 'smtp.example.com',
        'port': '465',
        'sender': 'sender@example.com',
    }
