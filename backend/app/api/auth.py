import time
from datetime import timedelta
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import Session, select

from ..core.database import SessionDep
from ..core.security import TOKEN_MAX_AGE_DAYS, create_token, hash_password, verify_password
from ..models import AuthSession, User, utc_now
from ..schemas import (
    AuthRead,
    AuthSessionRead,
    AuthSetupRead,
    Message,
    PasswordRecoveryRequest,
    UpdatePassword,
    UserCreateAdmin,
    UserLogin,
    UserRead,
    UserRegister,
    UserUpdateAdmin,
    UserUpdateMe,
)
from .deps import CurrentSession, CurrentSuperUser, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])
_setup_lock = Lock()


def _user_read(user: User) -> UserRead:
    return UserRead.model_validate(user)


def _issue_session(request: Request, session: Session, user: User) -> tuple[str, AuthSession]:
    """创建一条服务端登录会话并签发签名 token。返回 (token, session)。

    token 的 payload 为 `uid:sid:expires_at`（秒级时间戳），sid 指向本会话行；
    会话行保存用户代理/IP 供「我的」页列设备与辨认。撤销/过期统一由 deps 校验。
    """
    expires_at = utc_now() + timedelta(days=TOKEN_MAX_AGE_DAYS)
    auth_session = AuthSession(
        user_id=user.id,
        expires_at=expires_at,
        user_agent=(request.headers.get("user-agent") or "")[:255],
        ip_address=(request.client.host if request.client else None),
    )
    session.add(auth_session)
    session.commit()
    session.refresh(auth_session)
    token = create_token(
        user.id, auth_session.id, request.app.state.settings.secret_key, int(expires_at.timestamp())
    )
    return token, auth_session


def _session_read(auth_session: AuthSession, current_id: str) -> AuthSessionRead:
    read = AuthSessionRead.model_validate(auth_session)
    read.current = auth_session.id == current_id
    return read


@router.post("/register", response_model=AuthRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, session: SessionDep) -> AuthRead:
    """只用于全新安装的一次性管理员初始化，初始化后永久关闭。"""
    with _setup_lock:
        if session.exec(select(User.id)).first() is not None:
            raise HTTPException(status_code=409, detail="系统已完成初始化，请由管理员创建用户")

        user = User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name or payload.username,
            email=payload.email,
            is_superuser=True,
        )
        session.add(user)
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(status_code=409, detail="系统已完成初始化，请直接登录") from None
        session.refresh(user)

    token, _ = _issue_session(request, session, user)
    return AuthRead(token=token, user=_user_read(user))


@router.get("/setup", response_model=AuthSetupRead)
def setup_status(session: SessionDep) -> AuthSetupRead:
    return AuthSetupRead(needs_setup=session.exec(select(User.id)).first() is None)


@router.post("/login", response_model=AuthRead)
def login(payload: UserLogin, request: Request, session: SessionDep) -> AuthRead:
    user = session.exec(select(User).where(User.username == payload.username)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 每次登录都新建会话；此前的会话仍然有效，除非用户主动登出或管理员踢下线。
    token, _ = _issue_session(request, session, user)
    return AuthRead(token=token, user=_user_read(user))


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return _user_read(user)


@router.patch("/me", response_model=UserRead)
def update_me(payload: UserUpdateMe, session: SessionDep, user: CurrentUser) -> UserRead:
    if payload.email:
        existing = session.exec(
            select(User).where(User.email == payload.email, User.id != user.id)
        ).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="邮箱已被其他账号使用")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(user, field, value)
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return _user_read(user)


@router.post("/me/password", response_model=Message)
def change_password(
    payload: UpdatePassword,
    session: SessionDep,
    user: CurrentUser,
    current: CurrentSession,
) -> Message:
    """修改自己的密码：校验旧密码 → 设新密码 → 撤销除当前会话外的所有会话。

    改密后让其它设备立即失效，这是可撤销会话带来的安全收益。
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    user.password_hash = hash_password(payload.new_password)
    user.updated_at = utc_now()
    session.add(user)

    others = session.exec(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.id != current.id,
        )
    ).all()
    for other in others:
        other.revoked_at = utc_now()
        session.add(other)
    session.commit()
    return Message(message="密码已更新，其他设备已退出")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(session: SessionDep, current: CurrentSession) -> Response:
    current.revoked_at = utc_now()
    session.add(current)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions", response_model=list[AuthSessionRead])
def list_sessions(session: SessionDep, user: CurrentUser, current: CurrentSession) -> list[AuthSessionRead]:
    rows = session.exec(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
    ).all()
    return [_session_read(row, current.id) for row in rows]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: str, session: SessionDep, user: CurrentUser
) -> Response:
    row = session.get(AuthSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    row.revoked_at = utc_now()
    session.add(row)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- 管理员：用户管理

@router.get("/users", response_model=list[UserRead])
def list_users(session: SessionDep, _user: CurrentSuperUser) -> list[UserRead]:
    users = session.exec(select(User).order_by(User.created_at)).all()
    return [_user_read(u) for u in users]


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateAdmin, session: SessionDep, _user: CurrentSuperUser
) -> UserRead:
    """由平台管理员创建用户；公开注册在首位管理员初始化后不可用。"""
    if session.exec(select(User).where(User.username == payload.username)).first() is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用")
    if payload.email:
        existing_email = session.exec(select(User).where(User.email == payload.email)).first()
        if existing_email is not None:
            raise HTTPException(status_code=409, detail="邮箱已被其他账号使用")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.username,
        email=payload.email,
        is_superuser=payload.is_superuser,
    )
    session.add(user)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(status_code=409, detail="用户名已被占用") from None
    session.refresh(user)
    return _user_read(user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: str, payload: UserUpdateAdmin, session: SessionDep, _user: CurrentSuperUser
) -> UserRead:
    """管理员重置他人密码 / 调整管理员标记。这是自托管兜底找回密码的途径。"""
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if payload.password is not None:
        target.password_hash = hash_password(payload.password)
    if payload.is_superuser is False and target.is_superuser:
        superusers = session.exec(select(User).where(User.is_superuser.is_(True))).all()
        if len(superusers) <= 1:
            raise HTTPException(status_code=400, detail="至少保留一名平台管理员")
    if payload.is_superuser is not None:
        target.is_superuser = payload.is_superuser
    target.updated_at = utc_now()
    session.add(target)
    # 被改密的用户：其所有会话作废（强制重新登录）。
    if payload.password is not None:
        for row in session.exec(
            select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        ).all():
            row.revoked_at = utc_now()
            session.add(row)
    session.commit()
    session.refresh(target)
    return _user_read(target)


@router.post("/password-recovery", response_model=Message)
def recover_password(payload: PasswordRecoveryRequest, session: SessionDep) -> Message:
    """找回密码入口（自托管、无邮件基础设施）：

    本机优先的定位下，自助重置既无法安全送达重置链接，也容易成为弱口令入口。
    因此这里不签发任何重置 token，统一指向「联系平台管理员（superuser）」。
    无论账号是否存在都返回同一句，避免账号枚举。
    """
    return Message(message="本机版本暂不支持自助找回密码，请联系平台管理员重置")
