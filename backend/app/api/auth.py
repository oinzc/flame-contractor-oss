from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import Session, select

from ..core.database import SessionDep
from ..core.security import create_token, hash_password, verify_password
from ..models import User
from ..schemas import AuthRead, UserLogin, UserRead, UserRegister
from .deps import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_read(user: User) -> UserRead:
    return UserRead.model_validate(user)


@router.post("/register", response_model=AuthRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, session: SessionDep) -> AuthRead:
    existing = session.exec(
        select(User).where(User.username == payload.username)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(status_code=409, detail="用户名已被占用")
    session.refresh(user)

    token = create_token(user.id, request.app.state.settings.secret_key)
    return AuthRead(token=token, user=_user_read(user))


@router.post("/login", response_model=AuthRead)
def login(payload: UserLogin, request: Request, session: SessionDep) -> AuthRead:
    user = session.exec(
        select(User).where(User.username == payload.username)
    ).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(user.id, request.app.state.settings.secret_key)
    return AuthRead(token=token, user=_user_read(user))


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return _user_read(user)
