"""selector bundle 热更新 API。

抖音页面改版时，编辑 `app/execution/selectors_bundle.json`（或提交给本端点）并
触发 `POST /api/selectors/reload`，即可让后续登录/定位/发送立即用新选择器，
无需重启后端进程。

安全边界：本端点只读当前 bundle / 触发重载，**不接收**任何凭据或脚本；
校验（Pydantic 泛型 dict）只允许 JSON 可序列化的字符串/字符串数组字段，
避免注入任意对象。
"""

from pathlib import Path

from fastapi import APIRouter

from ..execution import selectors as _selectors
from .deps import CurrentUser

router = APIRouter(prefix="/selectors", tags=["selectors"])

# 当前 bundle 文件路径（供展示「改哪个文件」）
_BUNDLE_FILE = Path(__file__).resolve().parent / ".." / "execution" / "selectors_bundle.json"


def _bundle_view() -> dict:
    current = _selectors.all_selectors()
    file_path = str(_BUNDLE_FILE.resolve())
    mtime: float | None = None
    try:
        mtime = _BUNDLE_FILE.stat().st_mtime
    except OSError:
        pass
    return {
        "file_path": file_path,
        "count": len(current),
        "loaded_mtime": mtime,
        "selectors": current,
    }


@router.get("")
def read_selectors(user: CurrentUser) -> dict:
    """返回当前 selector bundle 全量快照（只读，不含任何凭据）。"""
    return _bundle_view()


@router.post("/reload")
def reload_selectors(user: CurrentUser) -> dict:
    """强制重新加载 bundle 文件，返回重载后的全量快照。

    若运维直接编辑了 `selectors_bundle.json`，调用本端点即可生效（无需重启）。
    bundle 默认还会在每次访问时惰性检查 mtime，因此甚至可以不调本端点——
    `POST /reload` 仅为「立刻、显式」地刷新提供入口。
    """
    _selectors.reload_bundle(force=True)
    return _bundle_view()
