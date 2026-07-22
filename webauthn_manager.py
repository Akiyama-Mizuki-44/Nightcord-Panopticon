"""
Passkey (WebAuthn) 注册与登录的服务端封装，基于 py_webauthn。
凭证（credential_id / 公钥 / sign_count）持久化在 config.yaml 的 dashboard_auth.passkeys 里，
本模块只负责跟 py_webauthn 打交道，把 bytes 编解码都封装掉，
调用方（app.py）只需要经手 base64url 字符串和 JSON。
"""
import json

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RP_NAME = "Nightcord Panopticon"


def rp_id_from_request(request) -> str:
    """WebAuthn 的 rp_id 必须是当前访问域名（不带端口）。"""
    return request.host.split(":")[0]


def origin_from_request(request) -> str:
    return f"{request.scheme}://{request.host}"


def _descriptors(passkeys: list) -> list:
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(pk["credential_id"])) for pk in (passkeys or [])]


def registration_options(rp_id: str, username: str, existing_passkeys: list):
    """返回 (challenge_b64url, options_json_str)"""
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_name=username,
        user_display_name=username,
        user_id=username.encode("utf-8"),
        exclude_credentials=_descriptors(existing_passkeys) or None,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    return bytes_to_base64url(options.challenge), options_to_json(options)


def verify_registration(credential_json: str, challenge_b64: str, rp_id: str, origin: str) -> dict:
    """成功返回 {credential_id, public_key, sign_count}（均为存 YAML 用的字符串/int）；失败抛异常。"""
    verification = verify_registration_response(
        credential=credential_json,
        expected_challenge=base64url_to_bytes(challenge_b64),
        expected_rp_id=rp_id,
        expected_origin=origin,
    )
    return {
        "credential_id": bytes_to_base64url(verification.credential_id),
        "public_key": bytes_to_base64url(verification.credential_public_key),
        "sign_count": verification.sign_count,
    }


def authentication_options(rp_id: str, existing_passkeys: list):
    """返回 (challenge_b64url, options_json_str)"""
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=_descriptors(existing_passkeys) or None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return bytes_to_base64url(options.challenge), options_to_json(options)


def verify_authentication(credential_json: str, challenge_b64: str, rp_id: str, origin: str, stored_passkey: dict) -> int:
    """成功返回新的 sign_count；失败抛异常。"""
    verification = verify_authentication_response(
        credential=credential_json,
        expected_challenge=base64url_to_bytes(challenge_b64),
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=base64url_to_bytes(stored_passkey["public_key"]),
        credential_current_sign_count=stored_passkey.get("sign_count", 0),
    )
    return verification.new_sign_count


def find_passkey(passkeys: list, credential_id: str):
    return next((p for p in (passkeys or []) if p.get("credential_id") == credential_id), None)


def extract_credential_id(credential) -> str:
    """credential 可能是 dict 或 JSON 字符串，取出其中的 id 字段。"""
    if isinstance(credential, str):
        credential = json.loads(credential)
    return (credential or {}).get("id", "")
