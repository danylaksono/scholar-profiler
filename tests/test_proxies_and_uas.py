from scholar_scraper import GoogleScholarScraper
import tempfile


def test_load_user_agents_and_proxies(tmp_path):
    ua_file = tmp_path / 'uas.txt'
    ua_file.write_text('\n'.join([
        'UA-ONE',
        'UA-TWO',
    ]), encoding='utf-8')

    proxy_file = tmp_path / 'proxies.txt'
    proxy_file.write_text('\n'.join([
        'http://127.0.0.1:8888',
        'http://127.0.0.1:8889',
    ]), encoding='utf-8')

    s = GoogleScholarScraper()
    s.load_user_agents_from_file(str(ua_file))
    s.load_proxies_from_file(str(proxy_file))

    assert 'UA-ONE' in s.user_agents
    assert 'http://127.0.0.1:8888' in s.proxies

    # pickers should return a value from the list
    assert s._pick_user_agent() in s.user_agents
    assert s._pick_proxy() in s.proxies
