from binascii import hexlify, unhexlify
from hashlib import md5, sha1

from ..exceptions import SSLError


SSLContext = None
HAS_SNI = False
create_default_context = None

import ssl

try:  # Test for SSL features
    from ssl import wrap_socket, CERT_NONE, PROTOCOL_SSLv23
    from ssl import HAS_SNI  # Has SNI?
except ImportError:
    pass


try:
    from ssl import OP_NO_SSLv2, OP_NO_SSLv3, OP_NO_COMPRESSION
except ImportError:
    OP_NO_SSLv2, OP_NO_SSLv3 = 0x1000000, 0x2000000
    OP_NO_COMPRESSION = 0x20000

try:
    from ssl import _RESTRICTED_SERVER_CIPHERS
except ImportError:
    _RESTRICTED_SERVER_CIPHERS = (
        'ECDH+AESGCM:DH+AESGCM:ECDH+AES256:DH+AES256:ECDH+AES128:DH+AES:ECDH+HIGH:'
        'DH+HIGH:ECDH+3DES:DH+3DES:RSA+AESGCM:RSA+AES:RSA+HIGH:RSA+3DES:!aNULL:'
        '!eNULL:!MD5:!DSS:!RC4'
    )

try:
    from ssl import SSLContext  # Modern SSL?
except ImportError:
    class FakeSSLContext(object):  # Platform-specific: Python 2 & 3.1
        def __init__(self, protocol_version):
            self.protocol_version = protocol_version
            # Use default values from a real SSLContext
            self.check_hostname = False
            self.verify_mode = ssl.CERT_NONE
            self.ca_certs = None
            self.options = 0
            self.certfile = None
            self.keyfile = None

        def load_cert_chain(self, certfile, keyfile):
            self.certfile = certfile
            self.keyfile = keyfile

        def load_verify_locations(self, location):
            self.ca_certs = location

        def set_ciphers(self, cipher_suite):
            pass

        def wrap_socket(self, socket, server_hostname=None):
            return wrap_socket(socket, keyfile=self.keyfile,
                               certfile=self.certfile,
                               ca_certs=self.ca_certs,
                               cert_reqs=self.verify_mode,
                               ssl_version=self.protocol_version)


def assert_fingerprint(cert, fingerprint):
    """
    Checks if given fingerprint matches the supplied certificate.

    :param cert:
        Certificate as bytes object.
    :param fingerprint:
        Fingerprint as string of hexdigits, can be interspersed by colons.
    """

    # Maps the length of a digest to a possible hash function producing
    # this digest.
    hashfunc_map = {
        16: md5,
        20: sha1
    }

    fingerprint = fingerprint.replace(':', '').lower()
    digest_length, odd = divmod(len(fingerprint), 2)

    if odd or digest_length not in hashfunc_map:
        raise SSLError('Fingerprint is of invalid length.')

    # We need encode() here for py32; works on py2 and p33.
    fingerprint_bytes = unhexlify(fingerprint.encode())

    hashfunc = hashfunc_map[digest_length]

    cert_digest = hashfunc(cert).digest()

    if not cert_digest == fingerprint_bytes:
        raise SSLError('Fingerprints did not match. Expected "{0}", got "{1}".'
                       .format(hexlify(fingerprint_bytes),
                               hexlify(cert_digest)))


def resolve_cert_reqs(candidate):
    """
    Resolves the argument to a numeric constant, which can be passed to
    the wrap_socket function/method from the ssl module.
    Defaults to :data:`ssl.CERT_NONE`.
    If given a string it is assumed to be the name of the constant in the
    :mod:`ssl` module or its abbrevation.
    (So you can specify `REQUIRED` instead of `CERT_REQUIRED`.
    If it's neither `None` nor a string we assume it is already the numeric
    constant which can directly be passed to wrap_socket.
    """
    if candidate is None:
        return CERT_NONE

    if isinstance(candidate, str):
        res = getattr(ssl, candidate, None)
        if res is None:
            res = getattr(ssl, 'CERT_' + candidate)
        return res

    return candidate


def resolve_ssl_version(candidate):
    """
    like resolve_cert_reqs
    """
    if candidate is None:
        return PROTOCOL_SSLv23

    if isinstance(candidate, str):
        res = getattr(ssl, candidate, None)
        if res is None:
            res = getattr(ssl, 'PROTOCOL_' + candidate)
        return res

    return candidate


def create_urllib3_context(ssl_version=None, cert_reqs=ssl.CERT_REQUIRED,
                           options=None, ciphers=None):
    """All arguments have the same meaning as ``ssl_wrap_socket``.

    By default, this function does a lot of the same work that
    ``ssl.create_default_context`` does on Python 3.4+. It:

    - Disables SSLv2, SSLv3, and compression
    - Sets a restricted set of server ciphers

    If you wish to enable SSLv3, you can do::

        from urllib3.util import ssl_
        context = ssl_.create_urllib3_context()
        context.options &= ~ssl_.OP_NO_SSLv3

    You can do the same to enable compression (substituting ``COMPRESSION``
    for ``SSLv3`` in the last line above).

    :param ssl_version:
        The desired protocol version to use. This will default to
        PROTOCOL_SSLv23 which will negotiate the highest protocol that both
        the server and your installation of OpenSSL support.
    :param cert_reqs:
        Whether to require the certificate verification. This defaults to
        ``ssl.CERT_REQUIRED``.
    :param options:
        Specific OpenSSL options. These default to ``ssl.OP_NO_SSLv2``,
        ``ssl.OP_NO_SSLv3``, ``ssl.OP_NO_COMPRESSION``.
    :param ciphers:
        Which cipher suites to allow the server to select.
    :returns:
        Constructed SSLContext object with specified options
    :rtype: SSLContext
    """
    try:
        context = SSLContext(ssl_version or ssl.PROTOCOL_SSLv23)
    except (NameError, TypeError):
        context = FakeSSLContext(ssl_version or ssl.PROTOCOL_SSLv23)
    if options is None:
        options = 0
        # SSLv2 is considered harmful and dangerous
        options |= OP_NO_SSLv2
        # SSLv3 has several problems and is now dangerous
        options |= OP_NO_SSLv3
        # Disable compression to prevent CRIME attacks for OpenSSL 1.0+
        # (issue #309)
        options |= OP_NO_COMPRESSION

    context.options |= options

    context.set_ciphers(ciphers or _RESTRICTED_SERVER_CIPHERS)

    context.verify_mode = cert_reqs
    if getattr(context, 'check_hostname', None) is not None:  # Platform-specific: Python 3.2
        context.check_hostname = (context.verify_mode == ssl.CERT_REQUIRED)
    return context


def ssl_wrap_socket(sock, keyfile=None, certfile=None, cert_reqs=None,
                    ca_certs=None, server_hostname=None,
                    ssl_version=None, ssl_context=None):
    context = ssl_context
    if context is None:
        context = create_urllib3_context(ssl_version, cert_reqs)

    if ca_certs:
        try:
            context.load_verify_locations(ca_certs)
        # Py32 raises IOError
        # Py33 raises FileNotFoundError
        except Exception as e:  # Reraise as SSLError
            raise SSLError(e)
    if certfile:
        # FIXME: This block needs a test.
        context.load_cert_chain(certfile, keyfile)
    if HAS_SNI:  # Platform-specific: OpenSSL with enabled SNI
        return context.wrap_socket(sock, server_hostname=server_hostname)
    return context.wrap_socket(sock)
