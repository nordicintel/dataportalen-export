import hashlib


def stable_id(kind: str, identity: str) -> str:
    """Stable exact identities, independent of readable file labels."""
    return hashlib.sha256(f"{kind}\0{identity}".encode()).hexdigest()
