"""M8-1 文案池候选选取：星期过滤 + 最少最近使用（LRU）轮换。

设计取向：不用纯随机——随机可能连续几天撞同一条文案；LRU 轮换保证候选
均匀出现，同时 last_used_at 只在「实际发送」时写入（consume=True），
预览不消耗轮换顺序。
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from ..models import TemplatePoolItem, utc_now


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def pick_pool_item(
    session: Session,
    template_id: str,
    weekday_index: int,
    *,
    consume: bool = False,
) -> TemplatePoolItem | None:
    """从文案池选一条当天可用候选。

    weekday_index：Python weekday()，0=周一 … 6=周日；
    weekday_mask==0 表示每天不限，否则按位匹配。
    consume=True 时写入 last_used_at（发送路径）；预览传 False。
    无启用且当天匹配的候选时返回 None。
    """
    items = session.exec(
        select(TemplatePoolItem).where(
            TemplatePoolItem.template_id == template_id,
            TemplatePoolItem.enabled,
        )
    ).all()
    candidates = [
        item
        for item in items
        if item.weekday_mask == 0 or (item.weekday_mask >> weekday_index) & 1
    ]
    if not candidates:
        return None

    def sort_key(item: TemplatePoolItem):
        last_used = _as_naive_utc(item.last_used_at) if item.last_used_at else None
        # 从未用过的排最前（False < True），其后按 last_used_at 最早优先，再按 id 稳定排序
        return (last_used is not None, last_used or datetime.min, item.id)

    chosen = min(candidates, key=sort_key)
    if consume:
        chosen.last_used_at = utc_now()
        chosen.updated_at = utc_now()
        session.add(chosen)
        session.commit()
    return chosen
