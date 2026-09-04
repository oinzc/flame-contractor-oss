"""认证依赖：从 Bearer token 解出当前平台账号(User)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from ..core.database import SessionDep
from ..core.security import decode_token
from ..models import User


def get_current_user(
    request: Request,
    session: SessionDep,
) -> User:
    """解析 `Authorization: Bearer <token>`，失败抛 401。所有业务 API 应依赖它。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth.removeprefix("Bearer ").strip()
    secret_key = request.app.state.settings.secret_key
    user_id = decode_token(token, secret_key)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.exec(select(User).where(User.id == user_id)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
