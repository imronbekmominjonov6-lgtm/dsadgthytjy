"""Kassani ishga tushirish. Telefon kamerasi uchun lokal HTTPS ishlatiladi.

  python run.py          -> https://<kompyuter-IP>:8443
  python run.py --http   -> http://<kompyuter-IP>:8000 (kamera ishlamaydi)

Kompyuterning IP manzili o'zgarsa, cert.pem va key.pem fayllarini o'chirib qayta ishga tushiring.
"""
import datetime
import ipaddress
import os
import socket
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
CERT, KEY = os.path.join(BASE, "cert.pem"), os.path.join(BASE, "key.pem")


def local_ips():
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    return sorted(ips)


def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kassa")])
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in local_ips()]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    import uvicorn

    if "--http" in sys.argv:
        port = int(os.environ.get("PORT", 8000))
        print("\n  Kassa (HTTP):")
        for ip in local_ips():
            print(f"    http://{ip}:{port}")
        uvicorn.run("main:app", host="0.0.0.0", port=port)
    else:
        port = int(os.environ.get("PORT", 8443))
        ensure_cert()
        print("\n  Kassa (HTTPS) — brauzer ogohlantirsa 'Davom etish' ni bosing:")
        for ip in local_ips():
            print(f"    https://{ip}:{port}")
        uvicorn.run("main:app", host="0.0.0.0", port=port, ssl_certfile=CERT, ssl_keyfile=KEY)
