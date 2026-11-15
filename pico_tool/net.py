"""Every request the tool sends over the network goes through here, with one TLS setup for all.

Where the certificates that prove a server is who it says come from, in order:

  1. the operating system's own trust store, through the truststore package: Security.framework on
     macOS, the Windows certificate store, and the distribution's CA bundle on Linux. It is what the
     browser on the same computer trusts, including a company's own root certificate.
  2. certifi's bundle (Mozilla's list, shipped inside the tool), when truststore is missing (Python
     older than 3.10), cannot start, or finds no CA bundle on a Linux system.
  3. Python's own default locations, only when neither package is installed (running from source).

A frozen build cannot rely on Python's defaults alone: OpenSSL then looks for the CA file at the
path it was built with on the build machine, which is not there on the person's computer, and every
HTTPS request fails with CERTIFICATE_VERIFY_FAILED. Verification is never switched off.

Requests are sent with an urllib opener that carries this context, so the system's proxy settings
(environment variables, and the Windows and macOS proxy settings) still apply.
"""

import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request

TRUSTSTORE = "truststore"
CERTIFI = "certifi"
PYTHON_DEFAULT = "python"

SOURCE_TEXT = {
    TRUSTSTORE: "the system's trust store (truststore)",
    CERTIFI: "the bundled certificate list (certifi)",
    PYTHON_DEFAULT: "Python's default certificate locations",
}

# Where Linux and BSD distributions keep their CA bundle, for when the frozen OpenSSL's own path
# does not exist. One file each; the first one present is enough.
_LINUX_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                    # Debian, Ubuntu, Arch, Gentoo
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",     # Fedora, RHEL, CentOS
    "/etc/pki/tls/certs/ca-bundle.crt",                      # older Fedora and RHEL
    "/etc/pki/tls/cert.pem",
    "/etc/ssl/ca-bundle.pem",                                # openSUSE
    "/etc/ssl/cert.pem",                                     # Alpine, Arch, the BSDs
    "/var/lib/ca-certificates/ca-bundle.pem",                # openSUSE
)

_lock = threading.Lock()
_setup = None        # (ssl.SSLContext, source, why the better sources were not used)
_opener = None


def _uses_openssl_store():
    """True where truststore reads CA files with OpenSSL (Linux, the BSDs), not a native verifier."""
    return not (sys.platform == "darwin" or sys.platform.startswith("win"))


def _hashed_cert_dir(path):
    """A directory OpenSSL can use as a CA path: it holds at least one "<hash>.<n>" entry."""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for name in names:
        stem, dot, num = name.partition(".")
        if dot and num.isdigit() and len(stem) == 8:
            try:
                int(stem, 16)
                return True
            except ValueError:
                pass
    return False


def _openssl_defaults_usable():
    """OpenSSL's own CA locations (or SSL_CERT_FILE / SSL_CERT_DIR) exist and hold certificates."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return True
    return bool(paths.capath and _hashed_cert_dir(paths.capath))


def distribution_ca_bundle():
    """The first of the usual distribution CA bundles that exists, or None."""
    for path in _LINUX_CA_BUNDLES:
        if os.path.isfile(path):
            return path
    return None


def system_ca_found():
    """On Linux: can the system's trust store be read? OpenSSL's own locations count when they
    exist, then the usual distribution bundles."""
    return _openssl_defaults_usable() or distribution_ca_bundle() is not None


def _truststore_context():
    import truststore
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if _uses_openssl_store() and not _openssl_defaults_usable():
        # the frozen OpenSSL's built in path is not on this computer: name the distribution's
        # bundle ourselves, so what is loaded does not depend on where truststore looks
        bundle = distribution_ca_bundle()
        if bundle is None:
            raise RuntimeError("no CA bundle found on this system")
        ctx.load_verify_locations(cafile=bundle)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _certifi_context():
    import certifi
    path = certifi.where()
    if not os.path.isfile(path):
        raise RuntimeError("certifi's bundle is missing (%s)" % path)
    return ssl.create_default_context(cafile=path)


def _python_context():
    return ssl.create_default_context()


_CHAIN = ((TRUSTSTORE, _truststore_context), (CERTIFI, _certifi_context), (PYTHON_DEFAULT, _python_context))


def build_context():
    """(context, source, notes): the first source in the chain that starts. notes says, for the
    sources passed over, why (for the network check's report)."""
    notes = []
    for source, make in _CHAIN:
        try:
            return make(), source, notes
        except Exception as e:      # ImportError, OSError from ctypes, RuntimeError, ssl.SSLError
            notes.append("%s: %s" % (source, e or e.__class__.__name__))
    raise RuntimeError("no TLS setup could be made (%s)" % "; ".join(notes))


def _ensure():
    global _setup, _opener
    with _lock:
        if _setup is None:
            _setup = build_context()
            _opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_setup[0]))
        return _setup, _opener


def context():
    """The SSL context every request uses."""
    return _ensure()[0][0]


def source():
    """TRUSTSTORE, CERTIFI or PYTHON_DEFAULT: where the certificates come from."""
    return _ensure()[0][1]


def passed_over():
    """Why the sources ahead of the one in use were not used ([] when the first one is in use)."""
    return list(_ensure()[0][2])


def reset():
    """Forget the context (tests)."""
    global _setup, _opener
    with _lock:
        _setup = None
        _opener = None


def urlopen(request, timeout=30):
    """urllib.request.urlopen with the tool's TLS setup and the system's proxy settings."""
    return _ensure()[1].open(request, timeout=timeout)


def is_certificate_problem(reason):
    """True when a failure (an exception or a URLError's reason) is a certificate check that failed."""
    if isinstance(reason, urllib.error.URLError) and not isinstance(reason, urllib.error.HTTPError):
        reason = getattr(reason, "reason", reason)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(reason)


def is_tls_problem(reason):
    """True for any failure of the secure connection itself (certificates, handshake)."""
    if isinstance(reason, urllib.error.URLError) and not isinstance(reason, urllib.error.HTTPError):
        reason = getattr(reason, "reason", reason)
    return isinstance(reason, ssl.SSLError) or is_certificate_problem(reason)


CERTIFICATE_ADVICE = ("check the computer's date and time, and any proxy or antivirus program that inspects "
                      "secure connections")


def probe(url, timeout=20, user_agent="gammaos-pico-tool"):
    """Open url once. Returns (reached, tls_problem, detail). Any HTTP answer, an error status too,
    counts as reached: the secure connection was made and checked."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout) as r:
            return True, False, "HTTP %d" % (getattr(r, "status", None) or r.getcode())
    except urllib.error.HTTPError as e:
        return True, False, "HTTP %d" % e.code
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return False, is_tls_problem(reason), str(reason)
    except (socket.timeout, TimeoutError):
        return False, False, "no answer in time"
    except ssl.SSLError as e:
        return False, True, str(e)
    except OSError as e:
        return False, False, str(e)
