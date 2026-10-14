# ADR 0015: Use mTLS device identities with token bootstrap

- Status: Accepted
- Date: 2026-10-13

## Context

The protocol-v1 media-agent WebSocket identifies an agent with a caller-chosen
string but does not authenticate that identity, authorize capabilities, or
support credential rotation and revocation. Discovery and a successful socket
connection therefore cannot establish trust. GUI-01 requires operators to
distinguish discovered, enrolled, suspended, and revoked devices and must
prevent unenrolled devices from receiving show commands.

Direct media agents and downstream controllers are long-lived, operator-owned
machines on a private network. They need stable identity across address
changes, mutual authentication without a human password on every connection,
and controller-enforced revocation.

## Decision

Protocol v2 uses mutually authenticated TLS. Each installation controller owns
an installation-scoped certificate authority and issues a distinct client
certificate for each enrolled device. The certificate identifies an opaque
controller-assigned device UUID in a URI subject alternative name. IP address,
hostname, display name, and caller-supplied agent ID are attributes, not
identity.

Enrollment uses an administrator-created, random, single-use pairing token:

1. The controller stores only the token digest, expected device kind, creator,
   creation time, and a maximum five-minute expiry.
2. The device generates its private key locally and sends a certificate signing
   request, proposed display name, device kind, and protocol-v2 capability
   document over server-authenticated TLS.
3. The controller atomically consumes the token, validates the request and
   capability document, creates the device record, and returns the signed
   client certificate and installation trust chain.
4. Every subsequent device connection requires mTLS. The controller resolves
   the certificate identity to an enrolled device record before accepting a
   protocol hello or sending a command.

Private keys never leave the device that generated them. The controller stores
certificate serial number, SHA-256 fingerprint, validity interval, enrollment
state, and audit metadata, but not device private keys or pairing-token
plaintext. Pairing tokens are scoped to one device kind and cannot be renewed,
listed in plaintext, or used as long-lived bearer credentials.

Certificates are short-lived relative to installation lifetime. An enrolled,
non-suspended device may rotate by sending a fresh signing request over its
existing authenticated connection. Rotation creates a new serial and
invalidates the superseded certificate after a bounded overlap. Expired,
revoked, suspended, unknown, or installation-foreign certificates cannot open
an operational protocol session. Revocation takes effect at the controller
authorization layer immediately and does not depend solely on certificate
revocation-list propagation.

Enrollment state, connection state, compatibility, assignment, and readiness
remain independent:

- `discovered` is untrusted network observation;
- `pairing` is a bounded in-progress enrollment;
- `enrolled` permits only operations declared by both device capability and
  controller policy;
- `suspended` preserves identity and assignments but denies operational
  sessions;
- `revoked` permanently denies that credential and requires a new enrollment.

Capability documents are typed and versioned. They declare supported protocol
range, device kind, software version, media formats, cache behavior, clock
features, output types, observations, and downstream-control operations as
applicable. Capabilities describe what a device can do; controller policy and
resource assignments decide what it may do.

Protocol v1 was retained only during migration and has now been removed.

## Consequences

Device identity survives DHCP and hostname changes, leaked pairing tokens have
a narrow useful lifetime, and database disclosure alone does not reveal
reusable device credentials. The controller can audit enrollment, rotation,
suspension, revocation, capability changes, and authorization failures against
a stable identity.

Deployment must protect the installation CA private key, provide server TLS
that devices can validate before presenting a pairing token, and support
certificate renewal before expiry. Clock validity matters for certificates, so
pairing and rotation errors must distinguish clock, trust-chain, expiry, and
revocation failures.

The initial implementation now includes the typed contracts, durable device
and certificate metadata, digest-only pairing-token persistence, encrypted
installation CA provisioning, administrator-only pairing-token creation, and a
rate-limited CSR enrollment endpoint. All are disabled by default and require
explicit TLS-termination configuration.

The mTLS operational listener, certificate-to-device session authorization,
two-phase rotation, and client migration are implemented. The operational
listener runs as a
dedicated mTLS-only port with an installation-CA-issued server certificate
whose DNS/IP SANs are explicitly configured. A custom Uvicorn protocol adapter
places only the TLS-stack-verified peer certificate into the isolated device
application. The application maps its device UUID SAN, serial, and SHA-256
fingerprint to the active durable credential before accepting a protocol-v2
hello. Heartbeats are fenced by device identity, device session, controller
epoch, and sequence; suspension or revocation terminates authorization on the
next message.

Media clients now use native protocol-v2 heartbeat, commands,
acknowledgements, telemetry, and credential rotation on one authenticated
connection. The unauthenticated protocol-v1 socket has been deleted.
