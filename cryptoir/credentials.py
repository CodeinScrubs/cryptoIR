"""Local Windows Credential Manager storage; never plaintext files or CLI secrets."""

import ctypes
import hashlib
import json
import os
from pathlib import Path

from .domain import DataError

# CRED_MAX_CREDENTIAL_BLOB_SIZE in the documented WinCred contract.
MAX_BLOB = 5 * 512
GENERIC, LOCAL_MACHINE, NOT_FOUND = 1, 2, 1168


class CredentialStoreError(DataError):
    pass


class FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class NativeCredential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
                ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", FileTime), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p)]


def supported():
    return os.name == "nt"


def target_name(venue):
    if venue not in ("nobitex", "wallex", "tabdeal"):
        raise CredentialStoreError("unsupported_credential_venue")
    root=str(Path(__file__).resolve().parent.parent).casefold()
    scope=hashlib.sha256(root.encode("utf-8")).hexdigest()[:24]
    return "cryptoIR/"+scope+"/"+venue


def _api():
    if not supported():
        raise CredentialStoreError("windows_credential_manager_required")
    try:
        # Restrict DLL loading to the Windows system directory.
        api=ctypes.WinDLL("Advapi32.dll", use_last_error=True, winmode=0x800)
        api.CredWriteW.argtypes=[ctypes.POINTER(NativeCredential),ctypes.c_uint32]
        api.CredReadW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,
                              ctypes.POINTER(ctypes.POINTER(NativeCredential))]
        api.CredDeleteW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32]
        for name in ("CredWriteW","CredReadW","CredDeleteW"):
            getattr(api,name).restype=ctypes.c_int32
        api.CredFree.argtypes=[ctypes.c_void_p]
        api.CredFree.restype=None
        return api
    except (OSError,AttributeError):
        raise CredentialStoreError("windows_credential_manager_unavailable") from None


def _read_blob(target):
    api=_api()
    pointer=ctypes.POINTER(NativeCredential)()
    if not api.CredReadW(target,GENERIC,0,ctypes.byref(pointer)):
        if ctypes.get_last_error()==NOT_FOUND:
            return None
        raise CredentialStoreError("credential_vault_read_failed")
    try:
        value=pointer.contents
        if (value.Type!=GENERIC or value.TargetName!=target or value.Persist!=LOCAL_MACHINE
                or not 0 < value.CredentialBlobSize <= MAX_BLOB or not value.CredentialBlob):
            raise CredentialStoreError("credential_vault_entry_invalid")
        return ctypes.string_at(value.CredentialBlob,value.CredentialBlobSize)
    finally:
        # Clear our returned copy before releasing the native allocation.
        value=pointer.contents
        if value.CredentialBlob and 0 < value.CredentialBlobSize <= MAX_BLOB:
            ctypes.memset(value.CredentialBlob,0,value.CredentialBlobSize)
        api.CredFree(pointer)


def _write_blob(target,blob):
    if not 0 < len(blob) <= MAX_BLOB:
        raise CredentialStoreError("credential_vault_payload_too_large")
    api=_api()
    buffer=(ctypes.c_ubyte*len(blob)).from_buffer_copy(blob)
    credential=NativeCredential(Type=GENERIC,TargetName=target,
        Comment="cryptoIR local account credentials",CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)),
        Persist=LOCAL_MACHINE,UserName="cryptoIR")
    try:
        if not api.CredWriteW(ctypes.byref(credential),0):
            raise CredentialStoreError("credential_vault_write_failed")
    finally:
        ctypes.memset(buffer,0,len(blob))


def _delete_blob(target):
    api=_api()
    if api.CredDeleteW(target,GENERIC,0):
        return True
    if ctypes.get_last_error()==NOT_FOUND:
        return False
    raise CredentialStoreError("credential_vault_delete_failed")


def _check_scheme(venue,credential):
    allowed={"nobitex":("nobitex_ed25519","nobitex_token"),
             "wallex":("wallex_key",),"tabdeal":("tabdeal_hmac",)}
    if credential.scheme not in allowed[venue]:
        raise CredentialStoreError("stored_credential_venue_mismatch")


def save_credentials(venue,credential):
    from .accounts import Credentials
    target=target_name(venue)
    if not isinstance(credential,Credentials):
        raise CredentialStoreError("invalid_credential_object")
    _check_scheme(venue,credential)
    blob=json.dumps({"schema":1,"venue":venue,"scheme":credential.scheme,
                     "key":credential.key,"secret":credential.secret},separators=(",",":")).encode()
    _write_blob(target,blob)


def load_credentials(venue):
    from .accounts import Credentials, _unique_object
    target=target_name(venue)
    if not supported():
        return None
    blob=_read_blob(target)
    if blob is None:
        return None
    if not 0 < len(blob) <= MAX_BLOB:
        raise CredentialStoreError("stored_credential_payload_invalid")
    try:
        data=json.loads(blob,object_pairs_hook=_unique_object)
        if (not isinstance(data,dict) or set(data)!={"schema","venue","scheme","key","secret"}
                or type(data["schema"]) is not int or data["schema"]!=1 or data["venue"]!=venue):
            raise ValueError()
        credential=Credentials(data["scheme"],data["key"],data["secret"])
        _check_scheme(venue,credential)
        return credential
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise CredentialStoreError("stored_credential_payload_invalid") from None


def credential_status(venue):
    credential=load_credentials(venue)
    return {"kind":"credential_storage_status","venue":venue,"available":supported(),
            "stored":credential is not None,"scheme":credential.scheme if credential else None,
            "storage":"windows_credential_manager","secrets_displayed":False}


def forget_credentials(venue):
    removed=_delete_blob(target_name(venue))
    return {"kind":"credential_storage_removed","venue":venue,"removed":removed,
            "exchange_key_revoked":False}
