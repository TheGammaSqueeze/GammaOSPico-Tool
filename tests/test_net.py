"""pico_tool.net: which certificates the TLS context trusts, and that requests keep urllib's proxies."""

import os
import ssl
import sys
import types
import urllib.error
import urllib.request

import pytest

from pico_tool import net


@pytest.fixture(autouse=True)
def fresh():
    net.reset()
    yield
    net.reset()


def fake_truststore(monkeypatch, fail=None):
    """A stand-in truststore module whose SSLContext is a plain ssl one (or raises `fail`)."""
    mod = types.ModuleType("truststore")
    made = []

    def SSLContext(protocol=None):
        if fail is not None:
            raise fail
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        made.append(ctx)
        return ctx
    mod.SSLContext = SSLContext
    mod.made = made
    monkeypatch.setitem(sys.modules, "truststore", mod)
    return mod


def no_module(monkeypatch, name):
    # None in sys.modules makes `import name` raise ImportError
    monkeypatch.setitem(sys.modules, name, None)


def test_truststore_first(monkeypatch):
    ts = fake_truststore(monkeypatch)
    monkeypatch.setattr(net, "_uses_openssl_store", lambda: False)
    ctx, source, notes = net.build_context()
    assert source == net.TRUSTSTORE and notes == []
    assert ctx is ts.made[0]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_truststore_missing_uses_certifi(monkeypatch):
    no_module(monkeypatch, "truststore")
    ctx, source, notes = net.build_context()
    assert source == net.CERTIFI
    assert notes and notes[0].startswith("truststore")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50          # certifi's roots are loaded


def test_truststore_failing_uses_certifi(monkeypatch):
    fake_truststore(monkeypatch, fail=OSError("Security framework could not be loaded"))
    ctx, source, notes = net.build_context()
    assert source == net.CERTIFI
    assert "Security framework" in notes[0]


def test_linux_without_any_ca_bundle_uses_certifi(monkeypatch, tmp_path):
    fake_truststore(monkeypatch)
    monkeypatch.setattr(net, "_uses_openssl_store", lambda: True)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(net, "_LINUX_CA_BUNDLES", (str(tmp_path / "none.crt"),))
    assert not net.system_ca_found()
    ctx, source, notes = net.build_context()
    assert source == net.CERTIFI and "no CA bundle" in notes[0]


def test_linux_frozen_path_missing_loads_the_distribution_bundle(monkeypatch, tmp_path):
    """What the frozen build meets: OpenSSL's built in path is gone, the distribution's bundle is
    elsewhere. truststore is used, with that bundle loaded."""
    import certifi
    ts = fake_truststore(monkeypatch)
    monkeypatch.setattr(net, "_uses_openssl_store", lambda: True)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(net, "_LINUX_CA_BUNDLES", (str(tmp_path / "none.crt"), certifi.where()))
    assert net.system_ca_found()
    ctx, source, notes = net.build_context()
    assert source == net.TRUSTSTORE
    assert ctx is ts.made[0] and ctx.cert_store_stats()["x509_ca"] > 50


def test_linux_openssl_defaults_present_are_left_to_truststore(monkeypatch, tmp_path):
    import certifi
    ts = fake_truststore(monkeypatch)
    monkeypatch.setattr(net, "_uses_openssl_store", lambda: True)
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    monkeypatch.setattr(net, "_LINUX_CA_BUNDLES", ())
    ctx, source, _notes = net.build_context()
    assert source == net.TRUSTSTORE
    assert ctx.cert_store_stats()["x509_ca"] == 0          # nothing loaded by us: truststore does it


def test_hashed_cert_dir(tmp_path):
    assert not net._hashed_cert_dir(str(tmp_path / "nope"))
    (tmp_path / "README").write_text("x")
    assert not net._hashed_cert_dir(str(tmp_path))
    (tmp_path / "5ad8a5d6.0").write_text("x")
    assert net._hashed_cert_dir(str(tmp_path))


def test_both_missing_falls_back_to_python(monkeypatch):
    no_module(monkeypatch, "truststore")
    no_module(monkeypatch, "certifi")
    ctx, source, notes = net.build_context()
    assert source == net.PYTHON_DEFAULT and len(notes) == 2
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_context_is_built_once(monkeypatch):
    no_module(monkeypatch, "truststore")
    first = net.context()
    assert net.context() is first and net.source() == net.CERTIFI
    assert net.passed_over()


def test_opener_keeps_proxies_and_uses_the_context(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    no_module(monkeypatch, "truststore")
    _setup, opener = net._ensure()
    handlers = {type(h): h for h in opener.handlers}
    assert urllib.request.ProxyHandler in handlers
    assert handlers[urllib.request.ProxyHandler].proxies.get("https") == "http://proxy.invalid:3128"
    https = handlers[urllib.request.HTTPSHandler]
    assert https._context is net.context()


def test_certificate_problem_detection():
    err = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    assert net.is_certificate_problem(err)
    assert net.is_certificate_problem(urllib.error.URLError(err))
    assert net.is_tls_problem(urllib.error.URLError(err))
    assert not net.is_certificate_problem(urllib.error.URLError(OSError("Name or service not known")))
    assert not net.is_tls_problem(OSError("Connection refused"))


def test_probe_reports(monkeypatch):
    def answer(exc):
        def urlopen(req, timeout=30):
            raise exc
        monkeypatch.setattr(net, "urlopen", urlopen)

    answer(urllib.error.HTTPError("https://x", 403, "rate", {}, None))
    assert net.probe("https://x") == (True, False, "HTTP 403")
    answer(urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")))
    reached, tls, _detail = net.probe("https://x")
    assert not reached and tls
    answer(urllib.error.URLError(OSError("Name or service not known")))
    assert net.probe("https://x")[:2] == (False, False)


@pytest.mark.skipif(not os.environ.get("PICO_TOOL_NET_TESTS"), reason="set PICO_TOOL_NET_TESTS=1 to reach GitHub")
def test_reaches_github_with_a_hostile_environment(monkeypatch, tmp_path):
    """The user's situation: Python's own CA paths do not exist. GitHub must still be reached."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    reached, tls, detail = net.probe("https://api.github.com/")
    assert reached and not tls, detail
