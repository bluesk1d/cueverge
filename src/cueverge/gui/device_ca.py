import fcntl
import ipaddress
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr

from cueverge.protocol.device_v2 import DeviceCertificate


class DeviceCertificateAuthorityError(RuntimeError):
    pass


class DeviceCertificateAuthority:
    def __init__(self, root: Path, password: SecretStr) -> None:
        if not password.get_secret_value():
            raise ValueError("device CA password must not be empty")
        self.root = root
        self.password = password
        self.key_path = root / "ca-key.pem"
        self.certificate_path = root / "ca-certificate.pem"
        self.server_key_path = root / "device-listener-key.pem"
        self.server_certificate_path = root / "device-listener-certificate.pem"
        self.lock_path = root / ".ca.lock"

    def provision(self, *, now: datetime | None = None) -> x509.Certificate:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        with self._lock():
            key_exists = self.key_path.exists()
            certificate_exists = self.certificate_path.exists()
            if key_exists != certificate_exists:
                raise DeviceCertificateAuthorityError(
                    "device CA key and certificate must either both exist or both be absent"
                )
            if key_exists:
                _, certificate = self._load()
                return certificate

            issued_at = _utc(now)
            key = ec.generate_private_key(ec.SECP384R1())
            subject = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "Cueverge Installation Device CA")]
            )
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(issued_at - timedelta(minutes=1))
                .not_valid_after(issued_at + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=True,
                        crl_sign=True,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .add_extension(
                    x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                    critical=False,
                )
                .sign(key, hashes.SHA384())
            )
            self._atomic_write(
                self.key_path,
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.BestAvailableEncryption(
                        self.password.get_secret_value().encode("utf-8")
                    ),
                ),
                mode=0o600,
            )
            self._atomic_write(
                self.certificate_path,
                certificate.public_bytes(serialization.Encoding.PEM),
                mode=0o644,
            )
            return certificate

    def issue_device_certificate(
        self,
        *,
        device_id: UUID,
        certificate_signing_request_pem: str,
        now: datetime | None = None,
        validity: timedelta = timedelta(days=30),
    ) -> DeviceCertificate:
        if validity <= timedelta(0) or validity > timedelta(days=90):
            raise ValueError("device certificate validity must be between 0 and 90 days")
        issued_at = _utc(now)
        key, authority = self._load()
        if authority.not_valid_after_utc <= issued_at + validity:
            raise DeviceCertificateAuthorityError(
                "device CA expires before the requested device certificate"
            )
        try:
            request = x509.load_pem_x509_csr(certificate_signing_request_pem.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise DeviceCertificateAuthorityError(
                "invalid PEM certificate signing request"
            ) from error
        if not request.is_signature_valid:
            raise DeviceCertificateAuthorityError(
                "certificate signing request signature is invalid"
            )
        public_key = request.public_key()
        _validate_device_public_key(public_key)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, f"Cueverge device {device_id}")]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(authority.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(issued_at - timedelta(minutes=1))
            .not_valid_after(issued_at + validity)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=isinstance(public_key, rsa.RSAPublicKey),
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(f"urn:cueverge:device:{device_id}")]
                ),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA384())
        )
        return DeviceCertificate(
            device_id=device_id,
            certificate_serial=format(certificate.serial_number, "x"),
            fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
            certificate_pem=certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            trust_chain_pem=(authority.public_bytes(serialization.Encoding.PEM).decode("ascii"),),
            issued_at=issued_at,
            expires_at=issued_at + validity,
        )

    def provision_server_certificate(
        self,
        identities: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> tuple[Path, Path]:
        if not identities:
            raise ValueError("device listener certificate requires at least one identity")
        issued_at = _utc(now)
        key, authority = self._load()
        expected_names = _server_names(identities)
        with self._lock():
            key_exists = self.server_key_path.exists()
            certificate_exists = self.server_certificate_path.exists()
            if key_exists != certificate_exists:
                raise DeviceCertificateAuthorityError(
                    "device listener key and certificate must both exist or both be absent"
                )
            if key_exists:
                certificate = x509.load_pem_x509_certificate(
                    self.server_certificate_path.read_bytes()
                )
                existing_names = certificate.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
                if existing_names != x509.SubjectAlternativeName(expected_names):
                    raise DeviceCertificateAuthorityError(
                        "device listener certificate identities do not match configuration"
                    )
                if certificate.not_valid_after_utc <= issued_at + timedelta(days=30):
                    raise DeviceCertificateAuthorityError(
                        "device listener certificate requires rotation"
                    )
                return self.server_certificate_path, self.server_key_path

            server_key = ec.generate_private_key(ec.SECP384R1())
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identities[0])]))
                .issuer_name(authority.subject)
                .public_key(server_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(issued_at - timedelta(minutes=1))
                .not_valid_after(issued_at + timedelta(days=365))
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=True,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                    critical=True,
                )
                .add_extension(
                    x509.SubjectAlternativeName(expected_names),
                    critical=False,
                )
                .sign(key, hashes.SHA384())
            )
            self._atomic_write(
                self.server_key_path,
                server_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(
                        self.password.get_secret_value().encode("utf-8")
                    ),
                ),
                mode=0o600,
            )
            certificate_chain = certificate.public_bytes(
                serialization.Encoding.PEM
            ) + authority.public_bytes(serialization.Encoding.PEM)
            self._atomic_write(
                self.server_certificate_path,
                certificate_chain,
                mode=0o644,
            )
            return self.server_certificate_path, self.server_key_path

    def _load(self) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        if not self.key_path.exists() or not self.certificate_path.exists():
            raise DeviceCertificateAuthorityError("device CA has not been provisioned")
        try:
            loaded_key = serialization.load_pem_private_key(
                self.key_path.read_bytes(),
                password=self.password.get_secret_value().encode("utf-8"),
            )
            certificate = x509.load_pem_x509_certificate(self.certificate_path.read_bytes())
        except (OSError, ValueError) as error:
            raise DeviceCertificateAuthorityError("device CA could not be loaded") from error
        if not isinstance(loaded_key, ec.EllipticCurvePrivateKey):
            raise DeviceCertificateAuthorityError("device CA key has an unsupported type")
        key_public = loaded_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        certificate_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if key_public != certificate_public:
            raise DeviceCertificateAuthorityError("device CA key does not match its certificate")
        return loaded_key, certificate

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a+b") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                _write_and_sync(stream, content)
            os.replace(temporary, path)
            os.chmod(path, mode)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _validate_device_public_key(
    public_key: CertificatePublicKeyTypes,
) -> None:
    if isinstance(public_key, rsa.RSAPublicKey):
        if public_key.key_size < 3072:
            raise DeviceCertificateAuthorityError("device RSA keys must contain at least 3072 bits")
        return
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        if not isinstance(public_key.curve, (ec.SECP256R1, ec.SECP384R1)):
            raise DeviceCertificateAuthorityError("device EC keys must use P-256 or P-384")
        return
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return
    raise DeviceCertificateAuthorityError("device public key type is unsupported")


def _write_and_sync(stream: BinaryIO, content: bytes) -> None:
    stream.write(content)
    stream.flush()
    os.fsync(stream.fileno())


def _utc(value: datetime | None) -> datetime:
    selected = value or datetime.now(UTC)
    if selected.tzinfo is None:
        raise ValueError("certificate timestamps must be timezone-aware")
    return selected.astimezone(UTC)


def _server_names(
    identities: tuple[str, ...],
) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = []
    seen: set[str] = set()
    for identity in identities:
        normalized = identity.strip().lower()
        if not normalized or normalized in seen:
            raise ValueError("device listener identities must be non-empty and unique")
        seen.add(normalized)
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            if "*" in normalized or "." not in normalized:
                raise ValueError(
                    "device listener DNS identities must be fully qualified without wildcards"
                ) from None
            names.append(x509.DNSName(normalized))
        else:
            names.append(x509.IPAddress(address))
    return names
