import src.invoices.retrieval as rt


def test_plan_no_recipe():
    t = rt.plan_retrieval(
        [{'vendor': 'Zoom', 'invoice_number': 'Z-1', 'period': '2026-03'}]
    )[0]
    assert t['recipe'] is None
    assert t['suggested_sources'] == rt._DEFAULT_SOURCES
    assert 'Z-1' in t['instructions']


def test_plan_with_recipe():
    vs = {'acme': {'portal_url': 'https://billing.acme.com', 'login_hint': 'SSO'}}
    t = rt.plan_retrieval([{'vendor': 'Acme Corp OÜ', 'invoice_number': '7'}], vs)[0]
    assert t['recipe']['matched_vendor'] == 'acme'
    assert t['suggested_sources'] == ['https://billing.acme.com']
    assert 'billing.acme.com' in t['instructions']


def test_plan_tolerates_non_string_recipe():
    vs = {'acme': {'portal_url': {'oops': 1}}}
    t = rt.plan_retrieval([{'vendor': 'Acme'}], vs)[0]
    assert t['suggested_sources'] == rt._DEFAULT_SOURCES
    assert isinstance(t['instructions'], str)


def test_match_source_min_len_and_longest():
    vs = {
        'as': {'portal_url': 'http://wrong'},
        'atlassian': {'portal_url': 'http://right'},
    }
    assert rt._match_source('Atlassian Pty', vs)['matched_vendor'] == 'atlassian'
    assert rt._match_source('Comcast', {'co': {'portal_url': 'x'}}) is None
