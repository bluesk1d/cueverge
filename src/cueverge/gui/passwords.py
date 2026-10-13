import getpass
import hashlib
import hmac
import secrets

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    password_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=password_salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${password_salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_text),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def main() -> None:
    password = getpass.getpass("GUI administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    if not password:
        raise SystemExit("password must not be empty")
    print(hash_password(password))
