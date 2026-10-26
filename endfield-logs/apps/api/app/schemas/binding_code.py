from datetime import datetime

from pydantic import BaseModel


BINDING_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
BINDING_CODE_PATTERN = r"^ZMD-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$"


class BindingCodeResponse(BaseModel):
    code: str
    expiresAt: datetime


class BindingCodeAccountResponse(BaseModel):
    accountId: str
    accountDisplayName: str
