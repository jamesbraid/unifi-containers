"""The HTTP the CI lanes do: fetches, streamed downloads, checksums.

Ubiquiti's CDNs are picky about default client agents, and dl.ui.com answers a
302 to its CDN that httpx does not follow unless told to — so every call here
carries the same headers and `follow_redirects`.
"""

import hashlib

import httpx

CHUNK = 1 << 20
HEADERS = {"User-Agent": "Mozilla/5.0 (unifi-containers updater)"}


class ChecksumError(RuntimeError):
    """A downloaded artifact is not the one that was pinned."""


def fetch(url):
    """The whole body of `url`. Raises httpx's own errors on failure."""
    response = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content


def url_exists(url):
    """Whether a HEAD of `url` answers 200. A transport failure counts as "no"."""
    try:
        response = httpx.head(url, headers=HEADERS, timeout=30, follow_redirects=True)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def download(url, dest):
    """Stream `url` to `dest`, never holding the payload in memory."""
    with httpx.stream("GET", url, headers=HEADERS, timeout=300, follow_redirects=True) as response:
        response.raise_for_status()
        with open(str(dest), "wb") as out:
            for block in response.iter_bytes(CHUNK):
                out.write(block)
    return dest


def sha256_of_url(url):
    """Checksum `url` as it streams, never landing it on disk."""
    digest = hashlib.sha256()
    with httpx.stream("GET", url, headers=HEADERS, timeout=300, follow_redirects=True) as response:
        response.raise_for_status()
        for block in response.iter_bytes(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def sha256_of(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path, expected):
    """Fail loudly on a checksum mismatch, naming both digests."""
    actual = sha256_of(path)
    if actual != expected.lower():
        raise ChecksumError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")
    return actual
