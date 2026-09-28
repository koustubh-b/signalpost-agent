"""
Signalpost first-party website discovery and research.

Design goals:
- Brreg organisation number is the identity authority.
- Brreg registered website is preferred when available.
- If no website is registered, public search can discover candidates.
- Search engines/directories/social sites are never published as official sites.
- Candidate domains are independently fetched and identity-checked.
- Exact organisation-number evidence is strongest.
- Company-name + address evidence can verify a candidate when the
  organisation number is not displayed on the public website.
- Ambiguous candidates are rejected rather than guessed.
- Only verified first-party websites are crawled for enrichment.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote,
    urljoin,
    urldefrag,
    urlparse,
)

import requests


# ============================================================
# CONFIGURATION
# ============================================================

USER_AGENT = (
    "signalpost-agent/4.0 "
    "(public-company-research-agent)"
)

MAX_BYTES = 700_000
MAX_SEARCH_BYTES = 500_000
TIMEOUT = 7.0

MAX_DISCOVERY_RESULTS = 4
MAX_SEARCHES = 2


# ============================================================
# BLOCKED DOMAINS
# ============================================================

SEARCH_ENGINE_HOSTS = {
    "duckduckgo.com",
    "www.duckduckgo.com",
    "html.duckduckgo.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
    "yahoo.com",
    "www.yahoo.com",
}

SOCIAL_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "linkedin.com",
    "www.linkedin.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "youtube.com",
    "www.youtube.com",
    "tiktok.com",
    "www.tiktok.com",
    "glassdoor.com",
    "www.glassdoor.com",
}

DIRECTORY_HOSTS = {
    "proff.no",
    "www.proff.no",
    "1881.no",
    "www.1881.no",
    "gulesider.no",
    "www.gulesider.no",
    "purehelp.no",
    "www.purehelp.no",
    "brreg.no",
    "www.brreg.no",
    "data.brreg.no",
    "www.data.brreg.no",
}

BLOCKED_HOSTS = (
    SEARCH_ENGINE_HOSTS
    | SOCIAL_HOSTS
    | DIRECTORY_HOSTS
)


# ============================================================
# ENRICHMENT KEYWORDS
# ============================================================

JOB_TERMS = (
    "job",
    "jobs",
    "career",
    "careers",
    "karriere",
    "stilling",
    "stillinger",
    "ledige",
    "ledig",
    "arbeid",
    "arbeidsplass",
    "work-with-us",
    "join-us",
    "vacancy",
    "vacancies",
    "recruit",
    "recruitment",
    "hiring",
)

ACTIVITY_TERMS = (
    "news",
    "nyhet",
    "nyheter",
    "aktuelt",
    "press",
    "media",
    "blog",
    "insight",
    "innsikt",
    "article",
    "artikler",
    "events",
    "arrangement",
    "newsroom",
)

PRODUCT_TERMS = (
    "product",
    "products",
    "service",
    "services",
    "solutions",
    "tjeneste",
    "tjenester",
    "produkter",
    "løsning",
    "løsninger",
    "losninger",
    "what-we-do",
    "our-business",
    "business-areas",
)


SKIP_EXT = re.compile(
    r"\.(?:"
    r"pdf|jpg|jpeg|png|gif|svg|webp|"
    r"zip|doc|docx|xls|xlsx|ppt|pptx"
    r")$",
    re.I,
)


# ============================================================
# BASIC HELPERS
# ============================================================

def _now_iso() -> str:
    return dt.datetime.now(
        dt.timezone.utc
    ).isoformat(timespec="seconds")


def _norm(value: str) -> str:
    value = value.lower()
    value = value.replace("&", " and ")

    value = re.sub(
        r"[^\w\s]",
        " ",
        value,
        flags=re.UNICODE,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def _host(url: str) -> str:
    return (
        urlparse(url).hostname
        or ""
    ).lower().rstrip(".")


def _same_site(
    candidate: str,
    root: str,
) -> bool:
    a = _host(candidate)
    b = _host(root)

    if not a or not b:
        return False

    return (
        a == b
        or a.endswith("." + b)
        or b.endswith("." + a)
    )


def _is_blocked_host(
    host: str,
) -> bool:

    host = host.lower().rstrip(".")

    for blocked in BLOCKED_HOSTS:

        if (
            host == blocked
            or host.endswith(
                "." + blocked
            )
        ):
            return True

    return False


def _clean_url(
    url: str,
    base: str,
) -> Optional[str]:

    try:

        url = urldefrag(
            urljoin(
                base,
                url,
            )
        )[0]

        parsed = urlparse(url)

        if parsed.scheme not in {
            "http",
            "https",
        }:
            return None

        if not parsed.netloc:
            return None

        if _is_blocked_host(
            parsed.hostname or ""
        ):
            return None

        if SKIP_EXT.search(
            parsed.path
        ):
            return None

        return url

    except Exception:
        return None


def _normalise_root(
    url: str,
) -> str:

    if url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        return url

    return "https://" + url


# ============================================================
# HTML PARSER
# ============================================================

@dataclass
class Link:
    url: str
    text: str


class _Parser(HTMLParser):

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.title = ""
        self.text_parts: list[str] = []
        self.links: list[Link] = []
        self.meta: dict[str, str] = {}

        self._in_title = False
        self._skip = 0
        self._current_href: Optional[str] = None
        self._current_text: list[str] = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        attributes = dict(attrs)

        if tag in {
            "script",
            "style",
            "noscript",
            "svg",
            "template",
        }:
            self._skip += 1
            return

        if tag == "title":
            self._in_title = True

        if tag == "meta":

            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
            )

            value = attributes.get(
                "content"
            )

            if key and value:
                self.meta[
                    key.lower()
                ] = value.strip()

        if (
            tag == "a"
            and attributes.get("href")
        ):

            self._current_href = (
                attributes["href"]
            )

            self._current_text = []

    def handle_endtag(
        self,
        tag,
    ):

        if tag in {
            "script",
            "style",
            "noscript",
            "svg",
            "template",
        }:

            self._skip = max(
                0,
                self._skip - 1,
            )

            return

        if tag == "title":
            self._in_title = False

        if (
            tag == "a"
            and self._current_href
            is not None
        ):

            text = re.sub(
                r"\s+",
                " ",
                " ".join(
                    self._current_text
                ),
            ).strip()

            self.links.append(
                Link(
                    self._current_href,
                    text,
                )
            )

            self._current_href = None
            self._current_text = []

    def handle_data(
        self,
        data,
    ):

        if self._skip:
            return

        text = re.sub(
            r"\s+",
            " ",
            data,
        ).strip()

        if not text:
            return

        self.text_parts.append(text)

        if self._in_title:
            self.title += (
                " " + text
            )

        if (
            self._current_href
            is not None
        ):
            self._current_text.append(
                text
            )


# ============================================================
# HTTP FETCH
# ============================================================

def _fetch(
    url: str,
    session: requests.Session,
    max_bytes: int = MAX_BYTES,
):
    try:

        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        final_url = response.url

        if response.status_code >= 400:

            return (
                None,
                final_url,
                f"HTTP {response.status_code}",
            )

        content_type = (
            response.headers.get(
                "content-type"
            )
            or ""
        ).lower()

        if (
            "html" not in content_type
            and "text/" not in content_type
        ):

            return (
                None,
                final_url,
                (
                    "unsupported content-type: "
                    f"{content_type}"
                ),
            )

        data = bytearray()

        for chunk in response.iter_content(
            8192
        ):

            if not chunk:
                continue

            data.extend(chunk)

            if len(data) >= max_bytes:
                break

        encoding = (
            response.encoding
            or "utf-8"
        )

        return (
            bytes(
                data[:max_bytes]
            ).decode(
                encoding,
                errors="replace",
            ),
            final_url,
            None,
        )

    except requests.RequestException as exc:

        return (
            None,
            url,
            str(exc),
        )


# ============================================================
# COMPANY-NAME HELPERS
# ============================================================

def _company_tokens(
    company_name: str,
) -> set[str]:

    stop = {
        "as",
        "asa",
        "nuf",
        "iks",
        "sa",
        "da",
        "ab",
        "the",
        "and",
        "og",
        "a",
    }

    return {
        token
        for token in _norm(
            company_name
        ).split()
        if len(token) >= 3
        and token not in stop
    }


def _company_name_variants(
    company_name: str,
) -> list[str]:

    original = company_name.strip()

    variants = [
        original,
        re.sub(
            r"\s+(?:AS|ASA|SA|AB|DA|NUF|IKS)\s*$",
            "",
            original,
            flags=re.I,
        ).strip(),
    ]

    # Remove duplicates while preserving order.
    result = []

    seen = set()

    for value in variants:

        key = _norm(value)

        if (
            key
            and key not in seen
        ):

            seen.add(key)
            result.append(value)

    return result


# ============================================================
# SEARCH RESULT UNWRAPPING
# ============================================================

def _unwrap_search_result(
    raw_url: str,
) -> Optional[str]:

    try:

        parsed = urlparse(
            raw_url
        )

        host = (
            parsed.hostname
            or ""
        ).lower()

        # DuckDuckGo
        if (
            "duckduckgo.com"
            in host
        ):

            values = parse_qs(
                parsed.query
            )

            wrapped = values.get(
                "uddg"
            )

            if wrapped:
                return unquote(
                    wrapped[0]
                )

            return None

        # Google
        if (
            "google.com"
            in host
        ):

            values = parse_qs(
                parsed.query
            )

            wrapped = values.get(
                "url"
            )

            if wrapped:
                return unquote(
                    wrapped[0]
                )

        # Bing
        if (
            "bing.com"
            in host
        ):

            values = parse_qs(
                parsed.query
            )

            wrapped = values.get(
                "url"
            )

            if wrapped:
                return unquote(
                    wrapped[0]
                )

        return raw_url

    except Exception:
        return None


# ============================================================
# SEARCH CANDIDATES - DOMAIN INFERENCE + SEARCH
# ============================================================


def _inferred_domain_candidates(
    company_name: str,
) -> list[str]:
    """
    Generate plausible first-party domains from a legal company name.

    This function ONLY generates candidates. It never verifies or
    publishes a website. The later identity gate remains mandatory.
    """

    variants = _company_name_variants(company_name)

    stop_words = {
        "as",
        "asa",
        "sa",
        "ab",
        "da",
        "nuf",
        "iks",
        "group",
        "gruppen",
        "holding",
        "holdings",
        "international",
        "intl",
    }

    candidates: list[str] = []

    for variant in variants:
        tokens = [
            token
            for token in _norm(variant).split()
            if token
            and token not in stop_words
            and len(token) >= 2
        ]

        if not tokens:
            continue

        forms = [
            "".join(tokens),
            "-".join(tokens),
        ]

        # Brand-style shortened domain candidate.
        if len(tokens) >= 2:
            forms.append(tokens[0])

        for stem in forms:
            if not stem:
                continue

            for suffix in (".no", ".com", ".org"):
                candidates.append(
                    f"https://{stem}{suffix}/"
                )

    result: list[str] = []
    seen: set[str] = set()

    for url in candidates:
        key = url.lower()
        if key not in seen:
            seen.add(key)
            result.append(url)

    return result


def _search_result_target(
    raw_url: str,
) -> Optional[str]:
    """Convert a search-engine wrapper URL into its destination URL."""

    try:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower()

        if "duckduckgo.com" in host:
            values = parse_qs(parsed.query)
            wrapped = values.get("uddg")
            if wrapped:
                return unquote(wrapped[0])
            return None

        if "google.com" in host:
            values = parse_qs(parsed.query)
            for key in ("url", "q"):
                wrapped = values.get(key)
                if wrapped:
                    value = unquote(wrapped[0])
                    if value.startswith(("http://", "https://")):
                        return value

        if "bing.com" in host:
            values = parse_qs(parsed.query)
            for key in ("url", "u"):
                wrapped = values.get(key)
                if wrapped:
                    value = unquote(wrapped[0])
                    if value.startswith(("http://", "https://")):
                        return value

        return raw_url

    except Exception:
        return None


def _root_url(
    url: str,
) -> Optional[str]:
    """Convert a candidate page URL to its domain root."""

    try:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            return None

        if not parsed.hostname:
            return None

        if _is_blocked_host(parsed.hostname):
            return None

        return f"{parsed.scheme}://{parsed.netloc}/"

    except Exception:
        return None


def _candidate_score(
    url: str,
    link_text: str,
    company_name: str,
    orgnr: str,
    address: str,
) -> int:
    """
    Rank candidate domains before verification.

    Ranking never publishes a website. Final acceptance happens in
    _identity_score() after fetching the candidate.
    """

    parsed = urlparse(url)

    host = _norm(parsed.hostname or "")
    path = _norm(parsed.path)
    text = _norm(link_text)

    combined = " ".join(
        [host, path, text]
    )

    score = 0

    # Exact org number is the strongest discovery signal.
    if orgnr:
        orgnr_digits = re.sub(r"\D", "", orgnr)
        combined_digits = re.sub(r"\D", "", combined)
        if orgnr_digits and orgnr_digits in combined_digits:
            score += 120

    # Company-name tokens.
    tokens = _company_tokens(company_name)

    for token in tokens:
        if re.search(
            rf"\b{re.escape(token)}\b",
            combined,
        ):
            score += 12

    # Company/official hints.
    for term in (
        "official",
        "company",
        "corporate",
        "konsern",
        "bedrift",
        "group",
        "about",
        "kontakt",
        "contact",
    ):
        if term in combined:
            score += 3

    # Norwegian domain/context.
    if host.endswith(".no"):
        score += 5

    for term in ("norway", "norge"):
        if term in combined:
            score += 3

    # Address/city evidence.
    address_tokens = {
        token
        for token in _norm(address).split()
        if len(token) >= 4
    }

    for token in address_tokens:
        if re.search(
            rf"\b{re.escape(token)}\b",
            combined,
        ):
            score += 3

    # Penalize obvious non-homepage paths.
    for term in (
        "/login",
        "/signin",
        "/account",
        "/search",
        "/results",
        "/category",
        "/tag",
        "/author",
        "/feed",
    ):
        if term in path:
            score -= 20

    return score


def _build_search_queries(
    company_name: str,
    orgnr: str,
    address: str,
) -> list[str]:
    """Build diverse public-search queries for website discovery."""

    variants = _company_name_variants(company_name)
    queries: list[str] = []

    # Strong identity queries first.
    queries.append(
        f'"{company_name}" "{orgnr}"'
    )
    queries.append(
        f'"{orgnr}" "{company_name}"'
    )
    queries.append(
        f'"{orgnr}" Norway company website'
    )

    # Name variants.
    for variant in variants:
        queries.append(
            f'"{variant}" official website'
        )
        queries.append(
            f'"{variant}" Norway company'
        )
        queries.append(
            f'"{variant}" Norge hjemmeside'
        )

    # Address corroboration.
    if address:
        parts = [
            part.strip()
            for part in address.split(",")
            if part.strip()
        ]

        city = ""
        if len(parts) >= 3:
            city = parts[-2]
        elif len(parts) >= 2:
            city = parts[-1]

        if city:
            queries.append(
                f'"{company_name}" "{city}" Norway'
            )
            queries.append(
                f'"{orgnr}" "{city}"'
            )

    unique: list[str] = []
    seen: set[str] = set()

    for query in queries:
        key = _norm(query)
        if key and key not in seen:
            seen.add(key)
            unique.append(query)

    return unique


def _search_candidates(
    company_name: str,
    orgnr: str,
    address: str,
    session: requests.Session,
) -> list[str]:
    """
    Discover possible official websites using:
      1. Direct domain inference.
      2. Public search-engine discovery.

    Candidates are never treated as proof. Each candidate must pass
    the later identity-verification gate before publication.
    """

    candidates: dict[str, dict] = {}

    # ========================================================
    # STEP 1: DIRECT DOMAIN INFERENCE
    # ========================================================

    for url in _inferred_domain_candidates(company_name):
        host = _host(url)

        if not host or _is_blocked_host(host):
            continue

        score = 60
        if host.endswith(".no"):
            score += 5

        candidates[host] = {
            "url": url,
            "score": score,
            "query": "direct-domain-inference",
            "title": "",
        }

    # ========================================================
    # STEP 2: PUBLIC SEARCH
    # ========================================================

    queries = _build_search_queries(
        company_name,
        orgnr,
        address,
    )

    for query in queries[:MAX_SEARCHES]:
        search_url = (
            "https://html.duckduckgo.com/html/?q="
            + quote_plus(query)
        )

        html, _, _ = _fetch(
            search_url,
            session,
            MAX_SEARCH_BYTES,
        )

        if not html:
            continue

        parser = _Parser()

        try:
            parser.feed(html)
        except Exception:
            continue

        for link in parser.links:
            if not link.url:
                continue

            target = _search_result_target(
                link.url
            )

            if not target:
                continue

            target = _clean_url(
                target,
                "https://duckduckgo.com/",
            )

            if not target:
                continue

            host = _host(target)

            if not host or _is_blocked_host(host):
                continue

            parsed = urlparse(target)
            path_query = (
                parsed.path
                + "?"
                + parsed.query
            ).lower()

            if any(
                term in path_query
                for term in (
                    "/search",
                    "/results",
                    "/find",
                    "/query",
                    "?q=",
                    "?query=",
                    "?search=",
                )
            ):
                continue

            root = _root_url(target)

            if not root:
                continue

            score = _candidate_score(
                target,
                link.text,
                company_name,
                orgnr,
                address,
            )

            # Search evidence is discovery-only evidence.
            score += 10

            old = candidates.get(host)

            if old is None or score > old["score"]:
                candidates[host] = {
                    "url": root,
                    "score": score,
                    "query": query,
                    "title": link.text,
                }

    # ========================================================
    # STEP 3: RANK
    # ========================================================

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -item["score"],
            item["url"],
        ),
    )

    return [
        item["url"]
        for item in ranked[:MAX_DISCOVERY_RESULTS]
    ]


# ============================================================
# IDENTITY VERIFICATION
# ============================================================

def _identity_score(
    text: str,
    title: str,
    company_name: str,
    orgnr: str,
    address: str,
):
    """
    Verify whether a candidate site belongs to the exact company.

    Priority:
    1. Exact organisation number.
    2. Full distinctive company name.
    3. Company name + address corroboration.
    """

    combined = (
        title
        + " "
        + text[:25000]
    )

    hay = _norm(
        combined
    )

    # Match the organisation number as a local digit group rather than
    # concatenating every digit found on the page. This supports common
    # forms such as 984 661 185, 984-661-185 and 984.661.185.
    orgnr_digits = re.sub(
        r"\D",
        "",
        orgnr or "",
    )

    number_matches = re.findall(
        r"(?<!\d)(?:\d[\s.\-]?){8}\d(?!\d)",
        combined,
    )

    exact_orgnr_match = any(
        re.sub(r"\D", "", match) == orgnr_digits
        for match in number_matches
    )

    # --------------------------------------------------------
    # STRONGEST: EXACT ORG NUMBER
    # --------------------------------------------------------

    if orgnr_digits and exact_orgnr_match:

        return (
            True,
            (
                "Exact organisation number "
                "found on the candidate site."
            ),
            100,
        )

    # --------------------------------------------------------
    # COMPANY NAME
    # --------------------------------------------------------

    tokens = _company_tokens(
        company_name
    )

    if not tokens:

        return (
            False,
            (
                "No distinctive company-name "
                "tokens were available."
            ),
            0,
        )

    name_hits = 0

    for token in tokens:

        if re.search(
            rf"\b{re.escape(token)}\b",
            hay,
        ):
            name_hits += 1

    name_ratio = (
        name_hits / len(tokens)
    )

    # Full normalized company name is useful when the site carries
    # the exact legal/public name but omits the organisation number.
    normalized_company = _norm(company_name)
    full_name_match = bool(
        normalized_company
        and normalized_company in hay
    )

    # --------------------------------------------------------
    # ADDRESS
    # --------------------------------------------------------

    address_tokens = {
        token
        for token in _norm(
            address
        ).split()
        if len(token) >= 4
    }

    address_hits = 0

    for token in address_tokens:

        if re.search(
            rf"\b{re.escape(token)}\b",
            hay,
        ):
            address_hits += 1

    # --------------------------------------------------------
    # IDENTITY RULES
    # --------------------------------------------------------

    if len(tokens) == 1:

        # Generic one-token company names are dangerous.
        verified = (
            name_hits == 1
            and (
                not address_tokens
                or address_hits >= 1
            )
        )

    elif len(tokens) == 2:

        verified = (
            name_hits == 2
            and (
                not address_tokens
                or address_hits >= 1
            )
        )

    else:

        required = max(
            2,
            int(
                round(
                    len(tokens)
                    * 0.60
                )
            ),
        )

        verified = (
            name_hits >= required
            and (
                not address_tokens
                or address_hits >= 1
            )
        )

    score = min(
        70,
        int(
            name_ratio * 70
        ),
    )

    if full_name_match:
        score += 15

    score += min(
        30,
        address_hits * 10,
    )

    score = min(100, score)

    if verified:

        return (
            True,
            (
                f"Matched {name_hits}/"
                f"{len(tokens)} company-name "
                f"tokens and "
                f"{address_hits} address token(s)."
            ),
            score,
        )

    return (
        False,
        (
            "Insufficient identity evidence: "
            f"{name_hits}/{len(tokens)} "
            f"company-name tokens and "
            f"{address_hits} address tokens."
        ),
        score,
    )


# ============================================================
# INTERNAL PAGE DISCOVERY
# ============================================================

def _candidate_links(
    links: list[Link],
    root: str,
    terms: tuple[str, ...],
    limit: int,
) -> list[str]:

    scored: list[
        tuple[int, str]
    ] = []

    seen: set[str] = set()

    for link in links:

        url = _clean_url(
            link.url,
            root,
        )

        if not url:
            continue

        if not _same_site(
            url,
            root,
        ):
            continue

        if url in seen:
            continue

        seen.add(url)

        text = _norm(
            link.text
        )

        path = _norm(
            urlparse(url).path
        )

        hay = (
            text
            + " "
            + path
        )

        score = 0

        for term in terms:

            normalized_term = _norm(
                term
            )

            if (
                normalized_term
                in hay
            ):
                score += 2

            if (
                normalized_term
                in text
            ):
                score += 1

        if score:
            scored.append(
                (
                    score,
                    url,
                )
            )

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        url
        for _, url in scored[
            :limit
        ]
    ]


# ============================================================
# FALLBACK INTERNAL PAGE DISCOVERY
# ============================================================

def _guessed_internal_links(
    root: str,
    kind: str,
) -> list[str]:
    """Generate common same-site paths when navigation links hide content."""

    paths = {
        "products": (
            "/products",
            "/services",
            "/solutions",
            "/our-services",
            "/what-we-do",
            "/business-areas",
            "/tjenester",
            "/produkter",
            "/losninger",
        ),
        "jobs": (
            "/jobs",
            "/careers",
            "/career",
            "/karriere",
            "/ledige-stillinger",
            "/stillinger",
            "/work-with-us",
            "/join-us",
        ),
        "activity": (
            "/news",
            "/newsroom",
            "/press",
            "/media",
            "/aktuelt",
            "/nyheter",
            "/blog",
            "/insights",
            "/innsikt",
        ),
    }

    result: list[str] = []

    for path in paths.get(kind, ()):
        url = _clean_url(
            urljoin(root, path),
            root,
        )

        if url and _same_site(url, root):
            result.append(url)

    return result


def _merge_page_candidates(
    linked: list[str],
    guessed: list[str],
    limit: int,
) -> list[str]:
    """Merge navigation and fallback candidates without duplicates."""

    result: list[str] = []
    seen: set[str] = set()

    for url in linked + guessed:
        if url in seen:
            continue
        seen.add(url)
        result.append(url)

        if len(result) >= limit:
            break

    return result


def _round_robin_queue(
    groups: list[tuple[str, list[str]]],
    limit: int,
) -> list[tuple[str, str]]:
    """Interleave products/jobs/activity so one category cannot starve another."""

    queues = [
        [
            (url, kind)
            for url in urls
        ]
        for kind, urls in groups
    ]

    output: list[tuple[str, str]] = []
    seen: set[str] = set()

    while len(output) < limit:
        progress = False

        for queue in queues:
            while queue:
                url, kind = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                output.append((url, kind))
                progress = True
                break

            if len(output) >= limit:
                break

        if not progress:
            break

    return output


# ============================================================
# DATE EXTRACTION
# ============================================================

def _extract_date(
    parser: _Parser,
) -> Optional[str]:

    keys = (
        "article:published_time",
        "datepublished",
        "date",
        "publishdate",
        "dc.date",
        "article:modified_time",
        "datemodified",
    )

    for key in keys:

        value = parser.meta.get(
            key.lower()
        )

        if value:
            return value

    return None


# ============================================================
# MAIN RESEARCH FUNCTION
# ============================================================

def research(
    website: Optional[str],
    company_name: str,
    orgnr: str,
    address: str = "",
    max_pages: int = 4,
    allow_discovery: bool = True,
) -> dict:

    result = {
        "state": "not_available",
        "source_url": website,
        "retrieved_at": _now_iso(),
        "identity_verified": False,
        "identity_explanation": "",
        "discovery_used": False,
        "discovery_query": None,
        "candidate_urls": [],
        "official_website": None,
        "description": None,
        "products_services": [],
        "jobs": [],
        "activity": [],
        "pages_checked": [],
        "errors": [],
    }

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml"
            ),
        }
    )

    candidates: list[str] = []

    # ========================================================
    # REGISTERED WEBSITE
    # ========================================================

    if website:

        root = _normalise_root(
            website
        )

        if _is_blocked_host(
            _host(root)
        ):

            result["state"] = (
                "ambiguous"
            )

            result[
                "identity_explanation"
            ] = (
                "The supplied website belongs "
                "to a search engine, social "
                "network, or directory and "
                "was rejected."
            )

            return result

        candidates.append(
            root
        )

    # ========================================================
    # PUBLIC DISCOVERY
    # ========================================================

    elif (
        allow_discovery
        and os.environ.get(
            "SIGNALPOST_ENABLE_DISCOVERY",
            "1",
        ) != "0"
    ):

        result[
            "discovery_used"
        ] = True

        result[
            "discovery_query"
        ] = (
            f'"{company_name}" '
            f'"{orgnr}"'
        )

        candidates = (
            _search_candidates(
                company_name,
                orgnr,
                address,
                session,
            )
        )

        result[
            "candidate_urls"
        ] = candidates[:]

        if not candidates:

            result[
                "state"
            ] = "not_available"

            result[
                "identity_explanation"
            ] = (
                "No candidate official "
                "website was discovered."
            )

            return result

    else:

        result[
            "identity_explanation"
        ] = (
            "No website was registered "
            "and website discovery is "
            "disabled."
        )

        return result

    best_ambiguous = None
    discovery_failures = 0

    # ========================================================
    # FETCH AND VERIFY CANDIDATES
    # ========================================================

    for candidate in candidates:

        html, final_url, err = _fetch(
            candidate,
            session,
        )

        if not html:

            # Discovery candidates are speculative. Their individual
            # DNS/HTTP failures should not pollute the final company
            # profile when another candidate succeeds.
            if err:
                discovery_failures += 1

                if website:
                    result[
                        "errors"
                    ].append(
                        f"{candidate}: {err}"
                    )

            continue

        final_url = (
            final_url
            or candidate
        )

        # Never accept cross-domain redirects.
        if not _same_site(
            final_url,
            candidate,
        ):
            continue

        if _is_blocked_host(
            _host(final_url)
        ):
            continue

        parser = _Parser()

        parser.feed(
            html
        )

        text = " ".join(
            parser.text_parts
        )

        verified, why, score = (
            _identity_score(
                text,
                parser.title,
                company_name,
                orgnr,
                address,
            )
        )

        if not verified:

            if (
                best_ambiguous
                is None
                or score
                > best_ambiguous[2]
            ):

                best_ambiguous = (
                    final_url,
                    why,
                    score,
                )

            continue

        # ====================================================
        # VERIFIED OFFICIAL WEBSITE
        # ====================================================

        root = final_url

        result.update(
            {
                "state": "available",
                "source_url": root,
                "official_website": root,
                "identity_verified": True,
                "identity_explanation": why,
            }
        )

        result[
            "pages_checked"
        ].append(root)

        # ====================================================
        # DESCRIPTION
        # ====================================================

        description = (
            parser.meta.get(
                "description"
            )
            or parser.meta.get(
                "og:description"
            )
        )

        if description:

            result[
                "description"
            ] = description[
                :1500
            ]

        elif text:

            result[
                "description"
            ] = text[:900]

        # ====================================================
        # FIND INTERNAL PAGES
        # ====================================================

        product_limit = max(2, max_pages // 3)
        job_limit = max(2, max_pages // 3)
        activity_limit = max(2, max_pages // 3)

        product_links = _merge_page_candidates(
            _candidate_links(
                parser.links,
                root,
                PRODUCT_TERMS,
                product_limit,
            ),
            _guessed_internal_links(root, "products"),
            product_limit,
        )

        job_links = _merge_page_candidates(
            _candidate_links(
                parser.links,
                root,
                JOB_TERMS,
                job_limit,
            ),
            _guessed_internal_links(root, "jobs"),
            job_limit,
        )

        activity_links = _merge_page_candidates(
            _candidate_links(
                parser.links,
                root,
                ACTIVITY_TERMS,
                activity_limit,
            ),
            _guessed_internal_links(root, "activity"),
            activity_limit,
        )

        queue = _round_robin_queue(
            [
                ("products", product_links),
                ("jobs", job_links),
                ("activity", activity_links),
            ],
            max(
                0,
                max_pages - 1,
            ),
        )

        # ====================================================
        # FETCH INTERNAL PAGES
        # ====================================================

        for page_url, kind in queue:

            page_html, page_final, page_err = (
                _fetch(
                    page_url,
                    session,
                )
            )

            if not page_html:

                if page_err:

                    result[
                        "errors"
                    ].append(
                        f"{page_url}: {page_err}"
                    )

                continue

            page_final = (
                page_final
                or page_url
            )

            if not _same_site(
                page_final,
                root,
            ):
                continue

            page_parser = _Parser()

            page_parser.feed(
                page_html
            )

            page_text = " ".join(
                page_parser.text_parts
            )

            title = (
                page_parser.title.strip()
                or urlparse(
                    page_final
                ).path.strip(
                    "/"
                ).replace(
                    "-",
                    " ",
                )
            )

            item = {
                "title": title[:240],
                "url": page_final,
                "date": _extract_date(
                    page_parser
                ),
                "summary": (
                    page_text[:900]
                    if page_text
                    else None
                ),
            }

            result[
                "pages_checked"
            ].append(
                page_final
            )

            if kind == "products":

                result[
                    "products_services"
                ].append(
                    item
                )

            elif kind == "jobs":

                result[
                    "jobs"
                ].append(
                    item
                )

            else:

                result[
                    "activity"
                ].append(
                    item
                )

        # ====================================================
        # DEDUPLICATION
        # ====================================================

        for key in (
            "products_services",
            "jobs",
            "activity",
        ):

            unique = {}

            for item in result[key]:

                unique[
                    item["url"]
                ] = item

            result[key] = list(
                unique.values()
            )[:8]

        result[
            "pages_checked"
        ] = list(
            dict.fromkeys(
                result[
                    "pages_checked"
                ]
            )
        )

        return result

    # ========================================================
    # NO VERIFIED WEBSITE
    # ========================================================

    if best_ambiguous:

        result[
            "state"
        ] = "ambiguous"

        result[
            "source_url"
        ] = best_ambiguous[0]

        result[
            "identity_explanation"
        ] = best_ambiguous[1]

    else:

        if discovery_failures:
            result[
                "state"
            ] = "blocked"
            result[
                "identity_explanation"
            ] = (
                "Candidate websites could not be fetched "
                "because the public web source was unavailable "
                "or refused the request."
            )
        else:
            result[
                "state"
            ] = "not_available"
            result[
                "identity_explanation"
            ] = (
                "Candidate websites were checked but none "
                "passed the identity verification gate."
            )

    return result