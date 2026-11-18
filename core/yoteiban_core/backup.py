"""RSA-OAEP と AES-GCM を使う暗号化バックアップ。"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import env

MAGIC = b"YOTEIBAN-ENC-BACKUP\x00"
FORMAT_VERSION = 1
DEFAULT_PASSPHRASE_ENV = "YOTEIBAN_BACKUP_PASSPHRASE"


class BackupError(ValueError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("暗号化バックアップには cryptography が必要です") from exc
    return hashes, serialization, padding, rsa, AESGCM


def _atomic_write(path: Path, data: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except Exception:
        try: os.unlink(temp_name)
        except OSError: pass
        raise


def snapshot(db_path, out_path, public_key_pem_path):
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    public_key = serialization.load_pem_public_key(Path(public_key_pem_path).read_bytes())
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise BackupError("公開鍵はRSAである必要があります")
    out_path = Path(out_path)
    fd, temp_db_name = tempfile.mkstemp(prefix="yoteiban-snapshot-", suffix=".db", dir=str(out_path.parent if out_path.parent.exists() else None))
    os.close(fd)
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(temp_db_name)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        plaintext = Path(temp_db_name).read_bytes()
        data_key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
        wrapped = public_key.encrypt(data_key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, MAGIC + bytes([FORMAT_VERSION]))
        if len(wrapped) > 65535:
            raise BackupError("RSAラップ鍵の長さが上限を超えています")
        blob = MAGIC + bytes([FORMAT_VERSION]) + struct.pack(">H", len(wrapped)) + wrapped + nonce + ciphertext
        digest = hashlib.sha256(blob).hexdigest().encode("ascii") + b"\n"
        _atomic_write(out_path, blob)
        _atomic_write(Path(str(out_path) + ".sha256"), digest)
        return {"path": str(out_path), "sha256": digest.decode("ascii").strip(), "bytes": len(blob)}
    finally:
        try: os.unlink(temp_db_name)
        except OSError: pass


def _decode_backup(blob):
    prefix = len(MAGIC)
    if len(blob) < prefix + 1 + 2 + 12 + 16 or not blob.startswith(MAGIC):
        raise BackupError("バックアップの形式が不正です")
    version = blob[prefix]
    if version != FORMAT_VERSION:
        raise BackupError(f"未対応のバックアップ形式です: {version}")
    key_len = struct.unpack(">H", blob[prefix + 1:prefix + 3])[0]
    start = prefix + 3
    if key_len <= 0 or len(blob) < start + key_len + 12 + 16:
        raise BackupError("バックアップが途中で切れています")
    return version, blob[start:start + key_len], blob[start + key_len:start + key_len + 12], blob[start + key_len + 12:]


def restore(in_path, private_key_pem_path, out_db_path, passphrase_env=None):
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    in_path, out_db_path = Path(in_path), Path(out_db_path)
    blob = in_path.read_bytes()
    expected = Path(str(in_path) + ".sha256").read_text(encoding="ascii").strip()
    actual = hashlib.sha256(blob).hexdigest()
    if not hmac.compare_digest(expected, actual):
        raise BackupError("SHA-256照合に失敗しました")
    _, wrapped, nonce, ciphertext = _decode_backup(blob)
    password = env(passphrase_env).encode("utf-8") if passphrase_env else None
    private_key = serialization.load_pem_private_key(Path(private_key_pem_path).read_bytes(), password=password)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise BackupError("秘密鍵はRSAである必要があります")
    try:
        data_key = private_key.decrypt(wrapped, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        plaintext = AESGCM(data_key).decrypt(nonce, ciphertext, MAGIC + bytes([FORMAT_VERSION]))
    except Exception as exc:
        raise BackupError("復号に失敗しました（鍵またはバックアップが異なります）") from exc
    out_db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_plain = tempfile.mkstemp(prefix=".yoteiban-restore-", suffix=".db", dir=str(out_db_path.parent))
    os.close(fd)
    temp_target = None
    try:
        Path(temp_plain).write_bytes(plaintext)
        check = sqlite3.connect(temp_plain)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("復元データの整合性検査に失敗しました")
        finally:
            check.close()
        fd, temp_target = tempfile.mkstemp(prefix="." + out_db_path.name + ".", dir=str(out_db_path.parent))
        os.close(fd)
        src, dst = sqlite3.connect(temp_plain), sqlite3.connect(temp_target)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        os.replace(temp_target, out_db_path)
        temp_target = None
        return {"path": str(out_db_path), "sha256": actual}
    finally:
        for path in (temp_plain, temp_target):
            if path:
                try: os.unlink(path)
                except OSError: pass


def rotate(dir, keep_daily, keep_monthly):
    root = Path(dir)
    keep_daily, keep_monthly = int(keep_daily), int(keep_monthly)
    if keep_daily < 0 or keep_monthly < 0:
        raise ValueError("保存数は0以上にしてください")
    files = []
    if root.exists():
        for path in root.iterdir():
            if not path.is_file() or path.name.endswith(".sha256"):
                continue
            try:
                with path.open("rb") as f:
                    recognized = f.read(len(MAGIC)) == MAGIC
            except OSError:
                recognized = False
            if recognized:
                files.append(path)
    dated = []
    for path in files:
        dt = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Tokyo"))
        dated.append((dt, path))
    days = sorted({dt.date() for dt, _ in dated}, reverse=True)[:keep_daily]
    months = sorted({(dt.year, dt.month) for dt, _ in dated}, reverse=True)[:keep_monthly]
    keep = set()
    for day in days:
        keep.add(max(((dt, p) for dt, p in dated if dt.date() == day), key=lambda item: item[0])[1])
    for month in months:
        keep.add(max(((dt, p) for dt, p in dated if (dt.year, dt.month) == month), key=lambda item: item[0])[1])
    removed = []
    for _, path in dated:
        if path in keep:
            continue
        path.unlink(); removed.append(str(path))
        sidecar = Path(str(path) + ".sha256")
        if sidecar.exists():
            sidecar.unlink()
    return {"kept": len(keep), "removed": removed}


def keygen(out, passphrase_env=DEFAULT_PASSPHRASE_ENV):
    hashes, serialization, padding, rsa, AESGCM = _crypto()
    password = env(passphrase_env).encode("utf-8")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password))
    public = private_key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    _atomic_write(out / "backup_private.pem", private, 0o600)
    _atomic_write(out / "backup_public.pem", public, 0o644)
    return {"private_key": str(out / "backup_private.pem"), "public_key": str(out / "backup_public.pem")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="暗号化バックアップ")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot"); snap.add_argument("--db", required=True); snap.add_argument("--out", required=True); snap.add_argument("--public-key", required=True)
    restore_parser = sub.add_parser("restore"); restore_parser.add_argument("--in", dest="input", required=True); restore_parser.add_argument("--private-key", required=True); restore_parser.add_argument("--out-db", required=True); restore_parser.add_argument("--passphrase-env")
    rotate_parser = sub.add_parser("rotate"); rotate_parser.add_argument("--dir", required=True); rotate_parser.add_argument("--keep-daily", type=int, required=True); rotate_parser.add_argument("--keep-monthly", type=int, required=True)
    keygen_parser = sub.add_parser("keygen"); keygen_parser.add_argument("--out", required=True); keygen_parser.add_argument("--passphrase-env", default=DEFAULT_PASSPHRASE_ENV)
    args = parser.parse_args(argv)
    if args.command == "snapshot": result = snapshot(args.db, args.out, args.public_key)
    elif args.command == "restore": result = restore(args.input, args.private_key, args.out_db, args.passphrase_env)
    elif args.command == "rotate": result = rotate(args.dir, args.keep_daily, args.keep_monthly)
    else: result = keygen(args.out, args.passphrase_env)
    print(json.dumps(result, ensure_ascii=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
