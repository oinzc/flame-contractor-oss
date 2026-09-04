"""认证依赖：从 Bearer token 解出当前平台账号(User)与服务端会话(AuthSession)。

token 携带 user_id、session_id 与过期时间；校验顺序：签名合法 → 会话存在 →
会话未被撤销 → 属于该用户 → 未过期。会话状态存在于数据库，因此改密/登出/踢人
会立即让对应 token 失效（这是自签 HMAC 无法做到的，也是可撤销的关键）。
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from ..core.database import SessionDep
from ..core.security import decode_token
from ..models import AuthSession, User


def _resolve(
    request: Request,
    session: Session,
    token: str,
) -> tuple[User, AuthSession]:
    """签名 + 会话表联合校验，返回 (user, auth_session)；任一不满足抛 401/403。"""
    secret_key = request.app.state.settings.secret_key
    decoded = decode_token(token, secret_key)
    if decoded is None:
        raise _unauthorized("登录已失效，请重新登录")
    user_id, session_id, expires_at = decoded
    if int(time.time()) >= expires_at:
        raise _unauthorized("登录已过期，请重新登录")

    auth_session = session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        raise _unauthorized("登录会话不存在，请重新登录")
    if auth_session.revoked_at is not None:
        raise _unauthorized("登录会话已失效，请重新登录")

    user = session.get(User, user_id)
    if user is None:
        raise _unauthorized("账号不存在")
    return user, auth_session


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    session: SessionDep,
) -> User:
    """解析 `Authorization: Bearer <token>`，失败抛 401。所有业务 API 应依赖它。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise _unauthorized("未登录")
    token = auth.removeprefix("Bearer ").strip()
    user, _auth_session = _resolve(request, session, token)
    return user


def get_current_session(request: Request, session: SessionDep) -> AuthSession:
    """与 get_current_user 等同，但额外返回会话，供「我的」页高亮/登出/踢人使用。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise _unauthorized("未登录")
    token = auth.removeprefix("Bearer ").strip()
    _user, auth_session = _resolve(request, session, token)
    return auth_session


def get_current_active_superuser(current_user: CurrentUser) -> User:
    """平台管理员依赖：非 is_superuser 一律 403。"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentSession = Annotated[AuthSession, Depends(get_current_session)]
CurrentSuperUser = Annotated[User, Depends(get_current_active_superuser)]
