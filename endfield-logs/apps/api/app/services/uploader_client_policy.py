from __future__ import annotations

import re

from fastapi import Request

from app.core.errors import AppError

UPLOADER_CLIENT_NAME_HEADER = "x-endfield-uploader-name"
UPLOADER_CLIENT_VERSION_HEADER = "x-endfield-uploader-version"
PARSER_VERSION_HEADER = "x-endfield-parser-version"
RULES_VERSION_HEADER = "x-endfield-rules-version"
MIN_UPLOADER_CLIENT_VERSION = "2026.10.15.1"
MIN_PARSER_VERSION = "raw-log-parser-v59"
MIN_RULES_VERSION = "raw-log-parser-v44"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", value)]
    if len(parts) < 3:
        raise ValueError("uploader client version must include year, month, and day")
    return tuple(parts)


def _raw_log_version(value: str) -> int:
    match = re.fullmatch(r"raw-log-parser-v(\d+)", value.strip())
    if match is None:
        raise ValueError("invalid raw log parser version")
    return int(match.group(1))


def require_supported_uploader_client(request: Request) -> None:
    client_version = request.headers.get(UPLOADER_CLIENT_VERSION_HEADER)
    try:
        supported = (
            client_version is not None
            and _version_tuple(client_version) >= _version_tuple(MIN_UPLOADER_CLIENT_VERSION)
        )
    except ValueError:
        supported = False

    if not supported:
        raise AppError(
            status_code=426,
            code="uploader_client_outdated",
            message="当前上传器版本过旧，请下载最新版上传器后重新上传。",
        )


def require_supported_upload_semantics(
    request: Request,
    *,
    parser_version: str,
    rules_version: str,
) -> None:
    parser_header = request.headers.get(PARSER_VERSION_HEADER, "")
    rules_header = request.headers.get(RULES_VERSION_HEADER, "")
    try:
        supported = (
            parser_header == parser_version
            and rules_header == rules_version
            and _raw_log_version(parser_version) >= _raw_log_version(MIN_PARSER_VERSION)
            and _raw_log_version(rules_version) >= _raw_log_version(MIN_RULES_VERSION)
        )
    except ValueError:
        supported = False

    if not supported:
        raise AppError(
            status_code=426,
            code="upload_semantics_outdated",
            message="当前客户端解析规则过旧或版本信息不一致，请强制更新后重新解析并上传。",
        )
