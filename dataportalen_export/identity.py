import hashlib


def stable_id(kind: str, identity: str) -> str:
    """Preserve exact identity semantics; use a digest only for storage names."""
    return hashlib.sha256(f"{kind}\0{identity}".encode()).hexdigest()
