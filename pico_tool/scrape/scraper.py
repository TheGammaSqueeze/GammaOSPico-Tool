"""Scraper clients (urllib through pico_tool.net, nothing else): ScreenScraper and TheGamesDB.

ScreenScraper.fr matches a ROM by its CRC32, size and file name (jeuInfos.php) and searches by
words (jeuRecherche.php). GammaOS's developer account is built into release builds (see
_devcreds); a user's own ScreenScraper account can be added for a bigger daily quota and more
threads. The cover is box-2D (box-3D when there is none), in the preferred region first.

TheGamesDB needs the user's own API key (none is bundled, to respect its quota). It matches by name
only; the cover is the front boxart.

Both have the same calls:
  match(game_name, rom_name, system, crc, size) -> Result     (automatic)
  search(words, system, limit) -> [Candidate]                  (a person picks)
  cover(candidate) -> Result                                    (the chosen one's picture)
"""

import html
import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__, net
from ._devcreds import builtin_credentials

SS_INFO_URL = "https://api.screenscraper.fr/api2/jeuInfos.php"
SS_SEARCH_URL = "https://api.screenscraper.fr/api2/jeuRecherche.php"
TGDB_BYNAME_URL = "https://api.thegamesdb.net/v1/Games/ByGameName"
TGDB_IMAGES_URL = "https://api.thegamesdb.net/v1/Games/Images"
DEFAULT_SOFTNAME = "gammaos-nano"
UA = "gammaos-pico-tool/" + __version__

SOURCES = (("ScreenScraper", "screenscraper"), ("TheGamesDB", "thegamesdb"))
REGIONS = (("World", "wor"), ("USA", "us"), ("Europe", "eu"), ("Japan", "jp"), ("France", "fr"),
           ("Germany", "de"), ("Spain", "sp"), ("Italy", "it"), ("Brazil", "br"), ("Korea", "kr"),
           ("Australia", "au"), ("United Kingdom", "uk"))


class ScrapeError(Exception):
    """A request that failed; the batch goes on with the next game."""


class StopScraping(ScrapeError):
    """A failure that ends a batch: credentials refused, the daily quota used up, the API closed."""


class Busy(ScrapeError):
    """The server asks to slow down; the request may be tried again after a pause."""


class Candidate(object):
    def __init__(self, source, ident, title, system="", year="", raw=None):
        self.source, self.ident, self.title, self.system, self.year, self.raw = source, ident, title, system, year, raw

    def __repr__(self):
        return "<Candidate %s %s %r>" % (self.source, self.ident, self.title)


class Result(object):
    def __init__(self):
        self.matched = False
        self.title = ""
        self.image = None       # picture bytes
        self.image_url = ""     # where it came from: may carry credentials, never shown to anyone
        self.error = ""


def region_order(region):
    region = (region or "wor").lower()
    base = ["wor", "us", "ss", "eu", "jp", "cus"]
    return [region] + [b for b in base if b != region]


def _sniff_image(data):
    if not data or len(data) < 16:
        return None
    if (data[:8].startswith(b"\x89PNG") or data[:3] == b"\xff\xd8\xff" or data[:6] in (b"GIF87a", b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[:2] == b"BM"):
        return data
    return None


# Query parameters that carry an account, a key or the built-in developer access. Their values, and
# any whole address (ScreenScraper's media addresses carry the credentials in their query string),
# never reach a message the person sees.
SECRET_PARAMS = ("devid", "devpassword", "softname", "ssid", "sspassword", "apikey")
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s'\"<>)]*")
_SECRET_QUERY_RE = re.compile(r"(?i)\b(%s)=[^&\s'\"<>)]*" % "|".join(SECRET_PARAMS))


def _secrets_in(url, params=None):
    """The secret values a request carries, in its params and in the url's own query string."""
    pairs = list(params or [])
    try:
        pairs += urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=False)
    except ValueError:
        pass
    return [v for k, v in pairs if k.lower() in SECRET_PARAMS and v and len(str(v)) >= 3]


def redact(text, secrets=()):
    """text with every address cut down to its host, "devpassword=..." style pairs removed and the
    given secret values replaced: safe to show."""
    text = str(text)

    def host_only(m):
        try:
            host = urllib.parse.urlsplit(m.group(0)).hostname
        except ValueError:
            host = None
        return host or "the server"
    text = _URL_RE.sub(host_only, text)
    text = _SECRET_QUERY_RE.sub("***", text)
    for value in sorted(set(str(v) for v in secrets if v), key=len, reverse=True):
        text = text.replace(value, "***")
        quoted = urllib.parse.quote_plus(value)
        if quoted != value:
            text = text.replace(quoted, "***")
    return text


def _get(url, params=None, timeout=30):
    """(status, body bytes); network failures raise ScrapeError, whose text never holds the address
    or a credential."""
    secrets = _secrets_in(url, params)
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    def fail(text):
        return ScrapeError(redact(text, secrets))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
    except ValueError:
        raise ScrapeError("the server gave an address that cannot be used")
    try:
        with net.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, body
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if net.is_certificate_problem(reason):
            raise fail("the server's certificate could not be checked (%s): %s" % (reason, net.CERTIFICATE_ADVICE))
        raise fail("the server could not be reached (%s)" % reason)
    except (socket.timeout, TimeoutError):
        raise ScrapeError("the server did not answer in time")
    except http.client.HTTPException as e:
        # InvalidURL and friends can quote the address: name only the kind of failure
        raise ScrapeError("the server's answer broke off (%s)" % e.__class__.__name__)
    except ValueError:
        raise ScrapeError("the server gave an address that cannot be used")
    except OSError as e:
        raise fail("the connection failed (%s)" % e)


class ScreenScraper(object):
    name = "screenscraper"
    label = "ScreenScraper"
    uses_crc = True

    def __init__(self, ssid=None, sspassword=None, region="wor", timeout=30, devid=None, devpw=None,
                 softname=None):
        builtin = builtin_credentials()
        if devid and devpw:
            self.devid, self.devpw = devid, devpw
            self.softname = softname or (builtin[2] if builtin else DEFAULT_SOFTNAME)
        elif builtin:
            self.devid, self.devpw, self.softname = builtin
        else:
            self.devid = self.devpw = ""
            self.softname = softname or DEFAULT_SOFTNAME
        self.ssid = ssid or ""
        self.sspassword = sspassword or ""
        self.region = (region or "wor").lower()
        self.timeout = timeout

    @property
    def ready(self):
        return bool(self.devid and self.devpw)

    def unready_reason(self):
        return ("This copy of the tool has no built-in ScreenScraper access (only release builds have it). "
                "Use TheGamesDB with your own API key, or run a release build.")

    def _params(self):
        p = [("devid", self.devid), ("devpassword", self.devpw), ("softname", self.softname or DEFAULT_SOFTNAME),
             ("output", "json")]
        if self.ssid and self.sspassword:
            p += [("ssid", self.ssid), ("sspassword", self.sspassword)]
        return p

    def _secrets(self):
        return [v for k, v in self._params() if k in SECRET_PARAMS]

    def _request(self, url, params):
        try:
            return self._request_raw(url, params)
        except ScrapeError as e:
            # the server's own text is quoted in some messages: it must not echo a credential
            raise e.__class__(redact(str(e), self._secrets())) from None

    def _request_raw(self, url, params):
        if not self.ready:
            raise StopScraping(self.unready_reason())
        status, body = _get(url, self._params() + params, self.timeout)
        text = body.decode("utf-8", "replace").strip()
        if status == 404:
            return None
        if status == 429:
            raise Busy("ScreenScraper is busy (too many requests at once); waiting a moment")
        if status == 430:
            raise StopScraping("Your ScreenScraper quota for today is used up. It resets at midnight "
                               "(Paris time); a ScreenScraper account raises it.")
        if status == 431:
            raise StopScraping("ScreenScraper stopped answering for today: too many games it did not "
                               "recognise. Try again tomorrow.")
        if status in (401, 423):
            raise StopScraping("ScreenScraper's API is closed for now (server busy or maintenance). Try "
                               "again later.")
        if status == 403:
            raise StopScraping("ScreenScraper refused the login. Check the user name and password in "
                               "Settings.")
        if status == 426:
            raise StopScraping("ScreenScraper no longer accepts this version of the tool.")
        if status >= 500:
            raise Busy("ScreenScraper answered with a server error (%d)" % status)
        if status != 200:
            raise ScrapeError("ScreenScraper answered %d" % status)
        if not text or text[0] not in "{[":
            low = text.lower()
            if "quota" in low or "maximum" in low:
                raise StopScraping("ScreenScraper: " + text[:200])
            if "identifiant" in low or "login" in low or "erreur de login" in low:
                raise StopScraping("ScreenScraper refused the login: " + text[:200])
            if "fermé" in low or "closed" in low:
                raise StopScraping("ScreenScraper's API is closed for now: " + text[:200])
            return None
        try:
            return json.loads(text)
        except ValueError:
            raise ScrapeError("ScreenScraper's answer could not be read")

    def _jeu(self, params):
        root = self._request(SS_INFO_URL, params)
        jeu = (root or {}).get("response", {}).get("jeu") if isinstance(root, dict) else None
        return jeu if isinstance(jeu, dict) else None

    def match(self, game_name, rom_name, system, crc=None, size=None):
        p = [("systemeid", str(system.ss_id)), ("romtype", "rom"), ("romnom", rom_name)]
        if crc is not None:
            p.append(("crc", "%08X" % crc))
        if size:
            p.append(("romtaille", str(size)))
        jeu = self._jeu(p)
        res = Result()
        if not jeu:
            res.error = "not found"
            return res
        return self._from_jeu(jeu)

    def search(self, words, system, limit=12):
        root = self._request(SS_SEARCH_URL, [("recherche", words), ("systemeid", str(system.ss_id))])
        jeux = (root or {}).get("response", {}).get("jeux") if isinstance(root, dict) else None
        out = []
        for jeu in (jeux or [])[:limit]:
            if not isinstance(jeu, dict) or not jeu.get("id"):
                continue
            sysname = (jeu.get("systeme") or {}).get("text", "") if isinstance(jeu.get("systeme"), dict) else ""
            year = ""
            for d in jeu.get("dates") or ():
                if isinstance(d, dict) and d.get("text"):
                    year = d["text"][:4]
                    break
            out.append(Candidate("screenscraper", str(jeu["id"]), self._title(jeu.get("noms")) or words,
                                 sysname, year, raw=jeu))
        return out

    def cover(self, cand):
        jeu = cand.raw if isinstance(cand.raw, dict) and isinstance(cand.raw.get("medias"), list) else None
        if jeu is None:
            jeu = self._jeu([("gameid", str(cand.ident))])
        if not jeu:
            r = Result()
            r.error = "not found"
            return r
        return self._from_jeu(jeu)

    def _from_jeu(self, jeu):
        res = Result()
        res.matched = True
        res.title = self._title(jeu.get("noms"))
        medias = jeu.get("medias")
        url = self._media(medias if isinstance(medias, list) else [], ("box-2D", "box-3D"))
        if not url:
            res.error = "no cover"
            return res
        res.image_url = url
        try:
            status, body = _get(url, timeout=max(self.timeout, 60))
        except ScrapeError as e:
            raise e.__class__(redact(str(e), self._secrets())) from None
        if status == 430:
            raise StopScraping("Your ScreenScraper quota for today is used up.")
        res.image = _sniff_image(body) if status == 200 else None
        if not res.image:
            res.error = "the cover could not be downloaded"
        return res

    def _media(self, medias, types):
        for want in region_order(self.region):
            for ty in types:
                for m in medias:
                    if isinstance(m, dict) and m.get("type") == ty and m.get("region") == want and m.get("url"):
                        return m["url"]
        for ty in types:
            for m in medias:
                if isinstance(m, dict) and m.get("type") == ty and m.get("url"):
                    return m["url"]
        return None

    def _title(self, noms):
        if not isinstance(noms, list):
            return ""
        order = [self.region, "wor", "us", "eu", "ss", "uk", "jp"]
        for want in order:
            for nm in noms:
                if isinstance(nm, dict) and nm.get("region") == want and nm.get("text"):
                    return html.unescape(nm["text"])
        for nm in noms:
            if isinstance(nm, dict) and nm.get("text"):
                return html.unescape(nm["text"])
        return ""


class TheGamesDB(object):
    name = "thegamesdb"
    label = "TheGamesDB"
    uses_crc = False

    def __init__(self, apikey=None, region="wor", timeout=30):
        self.apikey = (apikey or "").strip()
        self.region = region
        self.timeout = timeout

    @property
    def ready(self):
        return bool(self.apikey)

    def unready_reason(self):
        return ("TheGamesDB needs your own API key: make a free account at thegamesdb.net, ask for an "
                "API key, and paste it in Settings.")

    def _json(self, url, params):
        if not self.ready:
            raise StopScraping(self.unready_reason())
        status, body = _get(url, [("apikey", self.apikey)] + params, self.timeout)
        if status in (401, 403):
            raise StopScraping("TheGamesDB refused the API key. Check it in Settings.")
        if status == 429:
            raise Busy("TheGamesDB asks to slow down")
        if status >= 500:
            raise Busy("TheGamesDB answered with a server error (%d)" % status)
        if status != 200:
            raise ScrapeError("TheGamesDB answered %d" % status)
        try:
            root = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            raise ScrapeError("TheGamesDB's answer could not be read")
        if root.get("remaining_monthly_allowance") == 0:
            raise StopScraping("Your TheGamesDB allowance for this month is used up.")
        return root

    def search(self, words, system, limit=12):
        root = self._json(TGDB_BYNAME_URL, [("name", words), ("filter[platform]", str(system.tgdb_id)),
                                            ("fields", "release_date")])
        out = []
        for g in ((root.get("data") or {}).get("games") or [])[:limit]:
            if g.get("id") is None:
                continue
            out.append(Candidate("thegamesdb", str(g["id"]), html.unescape(g.get("game_title") or words),
                                 system.label, (g.get("release_date") or "")[:4], raw=g))
        return out

    def match(self, game_name, rom_name, system, crc=None, size=None):
        res = Result()
        cands = self.search(game_name, system, 8)
        if not cands:
            res.error = "not found"
            return res
        want = _simplify(game_name)
        best = next((c for c in cands if _simplify(c.title) == want), cands[0])
        return self.cover(best)

    def cover(self, cand):
        res = Result()
        res.matched = True
        res.title = cand.title
        root = self._json(TGDB_IMAGES_URL, [("games_id", str(cand.ident)), ("filter[type]", "boxart")])
        data = root.get("data") or {}
        base = ""
        bu = data.get("base_url") or {}
        for k in ("original", "large", "medium"):
            if bu.get(k):
                base = bu[k]
                break
        images = data.get("images") or {}
        arr = images.get(str(cand.ident)) if isinstance(images, dict) else None
        if not base or not isinstance(arr, list):
            res.error = "no cover"
            return res
        url = None
        for im in arr:
            if im.get("type") == "boxart" and im.get("side") == "front" and im.get("filename"):
                url = base + im["filename"]
                break
        if not url:
            for im in arr:
                if im.get("type") == "boxart" and im.get("filename"):
                    url = base + im["filename"]
                    break
        if not url:
            res.error = "no cover"
            return res
        res.image_url = url
        status, body = _get(url, timeout=max(self.timeout, 60))
        res.image = _sniff_image(body) if status == 200 else None
        if not res.image:
            res.error = "the cover could not be downloaded"
        return res


def _simplify(s):
    from ..card.names import clean_title
    return "".join(ch for ch in clean_title(s).lower() if ch.isalnum())


def make_scraper(settings):
    """The scraper the settings choose (a dict-like with the settings keys)."""
    src = (settings.get("scrape_source") or "screenscraper").lower()
    region = settings.get("scrape_region") or "wor"
    if src in ("thegamesdb", "tgdb"):
        return TheGamesDB(settings.get("tgdb_key"), region)
    return ScreenScraper(settings.get("ss_user"), settings.get("ss_password"), region)


def with_retry(call, tries=4, wait=2.0, cancel=None, note=None):
    """Run call(); a Busy server or a dropped connection is tried again after a growing pause."""
    for attempt in range(tries):
        try:
            return call()
        except StopScraping:
            raise
        except (Busy, ScrapeError) as e:
            if attempt == tries - 1:
                raise
            if note:
                note("%s; trying again" % e)
            pause = wait * (2 ** attempt)
            end = time.time() + pause
            while time.time() < end:
                if cancel is not None and cancel.is_set():
                    raise
                time.sleep(0.1)
