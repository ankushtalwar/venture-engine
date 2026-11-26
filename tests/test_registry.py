import httpx
import pytest
import yaml

from paindisc.http import PoliteClient
from paindisc.registry import Registry, RegistryError, RobotsDisallowed


def test_allowed_host_passes(settings):
    registry = Registry.load(settings.config_dir)
    assert registry.guard("https://hn.algolia.com/api/v1/search?query=x", "hn").name == "hn"


def test_unregistered_and_lookalike_hosts_are_blocked(settings):
    registry = Registry.load(settings.config_dir)
    with pytest.raises(RegistryError):
        registry.guard("https://evil.example.com/x", "hn")
    with pytest.raises(RegistryError):
        registry.guard("https://hn.algolia.com.evil.example.com/api", "hn")
    with pytest.raises(RegistryError):
        registry.guard("https://api.github.com/search/issues", "hn")  # right host, wrong source


def test_unapproved_sources_are_blocked_with_reason(settings):
    registry = Registry.load(settings.config_dir)
    for url, source in [("https://oauth.reddit.com/r/smallbusiness", "reddit"),
                        ("https://www.capterra.com/p/1/reviews", "capterra"),
                        ("https://api.upwork.com/graphql", "upwork")]:
        with pytest.raises(RegistryError, match="not permitted"):
            registry.guard(url, source)


def test_pricing_targets_need_terms_checked(config_copy):
    (config_copy / "pricing_targets.yaml").write_text(yaml.safe_dump({"targets": [
        {"vendor": "A", "url": "https://checked.example.com/pricing", "terms_checked": True},
        {"vendor": "B", "url": "https://unchecked.example.com/pricing", "terms_checked": False},
    ]}))
    registry = Registry.load(config_copy)
    registry.guard("https://checked.example.com/pricing", "pricing_pages")
    with pytest.raises(RegistryError):
        registry.guard("https://unchecked.example.com/pricing", "pricing_pages")


def test_redirect_to_unregistered_host_is_blocked(settings):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})

    client = PoliteClient(settings, Registry.load(settings.config_dir), transport=httpx.MockTransport(handler),
                          sleep=lambda _: None)
    with pytest.raises(RegistryError):
        client.get("https://hn.algolia.com/api/v1/search", source="hn")


def test_robots_txt_is_enforced_for_crawls(settings, config_copy):
    (config_copy / "pricing_targets.yaml").write_text(yaml.safe_dump({"targets": [
        {"vendor": "A", "url": "https://vendor.example.com/pricing", "terms_checked": True}]}))

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /pricing\n")
        return httpx.Response(200, text="<p>Starter $10 per month</p>")

    client = PoliteClient(settings, Registry.load(config_copy), transport=httpx.MockTransport(handler),
                          sleep=lambda _: None)
    with pytest.raises(RobotsDisallowed):
        client.get("https://vendor.example.com/pricing", source="pricing_pages", robots=True)
