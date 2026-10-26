from __future__ import annotations

import time

import httpx

from uploader_core.battle_payload_builder import payload_builder_diagnostics

UPLOADER_CLIENT_NAME = "EndfieldLogsUploader"
UPLOADER_CLIENT_VERSION = "2026.10.15.1"
SAFE_ACCEPT_ENCODING = "gzip, deflate"


class ApiClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    _REQUEST_RETRY_COUNT = 3
    _REQUEST_RETRY_DELAYS = (0.35, 0.9)

    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        self.base_url = base_url
        self.client = self._new_client(base_url)
        self.session_token: str | None = None

    @staticmethod
    def _new_client(base_url: str) -> httpx.Client:
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        # Do not advertise zstd. Some Windows build environments expose an
        # incomplete `zstandard` namespace (backend binary without package
        # initializer). httpx then advertises zstd but crashes while decoding
        # the first compressed authenticated response.
        return httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept-Encoding": SAFE_ACCEPT_ENCODING},
        )

    def update_base_url(self, base_url: str) -> None:
        self.base_url = base_url
        self.client.close()
        self.client = self._new_client(base_url)

    def set_session_token(self, token: str | None) -> None:
        self.session_token = token

    def _auth_headers(self) -> dict[str, str]:
        if not self.session_token:
            return {}
        return {"Authorization": f"Bearer {self.session_token}"}

    @staticmethod
    def _client_headers() -> dict[str, str]:
        diagnostics = payload_builder_diagnostics()
        headers = {
            "X-Endfield-Uploader-Name": UPLOADER_CLIENT_NAME,
            "X-Endfield-Uploader-Version": UPLOADER_CLIENT_VERSION,
        }
        builder_version = diagnostics.get("payloadBuilderVersion")
        parser_version = diagnostics.get("parserVersion")
        rules_version = diagnostics.get("rulesVersion")
        if builder_version:
            headers["X-Endfield-Payload-Builder-Version"] = builder_version
        if parser_version:
            headers["X-Endfield-Parser-Version"] = parser_version
        if rules_version:
            headers["X-Endfield-Rules-Version"] = rules_version
        return headers

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"请求失败（{response.status_code}）"

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("code") or f"请求失败（{response.status_code}）")
            detail = payload.get("detail")
            if isinstance(detail, dict):
                return str(detail.get("message") or detail.get("code") or f"请求失败（{response.status_code}）")
            if isinstance(detail, str):
                return detail
            message = payload.get("message")
            if isinstance(message, str):
                return message
        return f"请求失败（{response.status_code}）"

    @staticmethod
    def _network_error_message(exc: httpx.TransportError) -> str:
        return f"网络连接中断，请重试：{exc}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict | None = None,
        params: dict | None = None,
        auth: bool = False,
    ) -> dict:
        headers = self._client_headers()
        if auth:
            headers.update(self._auth_headers())
        last_error: httpx.TransportError | None = None
        for attempt in range(self._REQUEST_RETRY_COUNT):
            try:
                response = self.client.request(method, path, json=json_payload, params=params, headers=headers)
                break
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 >= self._REQUEST_RETRY_COUNT:
                    raise ApiClientError(self._network_error_message(exc)) from exc
                time.sleep(self._REQUEST_RETRY_DELAYS[min(attempt, len(self._REQUEST_RETRY_DELAYS) - 1)])
        else:
            raise ApiClientError(self._network_error_message(last_error)) if last_error else ApiClientError("网络请求失败")
        if response.is_error:
            raise ApiClientError(
                self._extract_error_message(response),
                status_code=response.status_code,
            )
        return response.json()

    def healthcheck(self) -> dict:
        return self._request("GET", "/healthz")

    def send_code(self, email: str, purpose: str = "uploader_login") -> dict:
        return self._request(
            "POST",
            "/api/auth/send-code",
            json_payload={"email": email, "purpose": purpose},
        )

    def verify_code(self, email: str, code: str, purpose: str = "uploader_login") -> dict:
        return self._request(
            "POST",
            "/api/auth/verify-code",
            json_payload={"email": email, "purpose": purpose, "code": code},
        )

    def check_email(self, email: str) -> dict:
        return self._request("GET", "/api/auth/check-email", params={"email": email})

    def register_with_password(
        self,
        email: str,
        password: str,
        nickname: str,
        code: str,
        purpose: str = "uploader_login",
    ) -> dict:
        return self._request(
            "POST",
            "/api/auth/register",
            json_payload={
                "email": email,
                "purpose": purpose,
                "password": password,
                "nickname": nickname,
                "code": code,
            },
        )

    def login_with_password(self, email: str, password: str, purpose: str = "uploader_login") -> dict:
        return self._request(
            "POST",
            "/api/auth/login",
            json_payload={"email": email, "purpose": purpose, "password": password},
        )

    def check_nickname(self, nickname: str) -> dict:
        return self._request("GET", "/api/auth/check-nickname", params={"nickname": nickname})

    def complete_profile(self, profile_setup_token: str, nickname: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/complete-profile",
            json_payload={"profileSetupToken": profile_setup_token, "nickname": nickname},
        )

    def auth_me(self) -> dict:
        return self._request("GET", "/api/auth/me", auth=True)

    def get_rotations(self) -> dict:
        return self._request("GET", "/api/rotations", auth=True)

    def get_rotation_catalog(self) -> dict:
        return self._request("GET", "/api/rotations/catalog")

    def logout(self) -> dict:
        return self._request("POST", "/api/auth/logout", auth=True)

    def verify_raw_log_integrity(self, content: str, proof: dict) -> dict:
        return self._request(
            "POST",
            "/api/integrity/verify-raw-log",
            json_payload={"content": content, "proof": proof},
        )

    def preflight_raw_log_upload(self, document: dict) -> dict:
        return self._request("POST", "/api/integrity/preflight-raw-log-upload", json_payload=document)

    def check_duplicate_battle(self, payload: dict) -> dict:
        return self._request(
            "POST",
            "/api/uploader/battles/check-duplicate",
            json_payload=payload,
            auth=True,
        )

    def upload_battle(self, payload: dict) -> dict:
        return self._request(
            "POST",
            "/api/uploader/battles",
            json_payload=payload,
            auth=True,
        )
