from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from market_lens.storage.supabase import AuthenticatedUser, SupabaseError, SupabaseRESTClient

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return SupabaseRESTClient().get_user(credentials.credentials)
    except SupabaseError as exc:
        message = str(exc)
        status_code = 401 if "authentication failed" in message.lower() else 503
        raise HTTPException(status_code=status_code, detail=message) from exc
