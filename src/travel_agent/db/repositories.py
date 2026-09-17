"""仓储：行程快照/版本、消息、长期偏好的读写（SQLAlchemy async）。

阶段四新增：PreferenceRepository（偏好记忆）与 PlanRow thread_id 查询（回滚）。
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.db.models import (
    ConversationContextRow,
    MessageRow,
    PlanRow,
    PlanVersionRow,
    UserPreferenceRow,
    UserRow,
)
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.plan import TripPlan

__all__ = [
    "ConversationContextRepository",
    "MessageRepository",
    "PlanRepository",
    "PreferenceRepository",
    "UserRepository",
]


class UserRepository:
    """注册用户（用户名唯一；bcrypt 哈希在路由层完成）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> UserRow | None:
        result = await self._session.execute(select(UserRow).where(UserRow.username == username))
        return result.scalar_one_or_none()

    async def create(self, username: str, password_hash: str) -> UserRow | None:
        """创建用户；用户名已存在返回 None。"""
        existing = await self.get_by_username(username)
        if existing is not None:
            return None
        row = UserRow(username=username, password_hash=password_hash)
        self._session.add(row)
        await self._session.flush()
        return row


class ConversationContextRepository:
    """会话内跨轮记忆：按 (user_id, thread_id) 存一份累计 brief。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, user_id: int, thread_id: str) -> TravelBrief | None:
        result = await self._session.execute(
            select(ConversationContextRow).where(
                ConversationContextRow.user_id == user_id,
                ConversationContextRow.thread_id == thread_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return TravelBrief.model_validate_json(row.brief_json)

    async def save(self, user_id: int, thread_id: str, brief: TravelBrief) -> None:
        """upsert：覆盖 brief_json 并刷新 updated_at。"""
        result = await self._session.execute(
            select(ConversationContextRow).where(
                ConversationContextRow.user_id == user_id,
                ConversationContextRow.thread_id == thread_id,
            )
        )
        row = result.scalar_one_or_none()
        brief_json = brief.model_dump_json()
        if row is None:
            self._session.add(
                ConversationContextRow(user_id=user_id, thread_id=thread_id, brief_json=brief_json)
            )
        else:
            row.brief_json = brief_json
            row.updated_at = datetime.now(UTC)


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_snapshot(
        self,
        plan: TripPlan,
        thread_id: str,
        version: int,
        diff_json: str | None = None,
        trigger_message_id: str | None = None,
        user_id: int | None = None,
    ) -> PlanRow:
        """upsert 行程行并追加一条版本行（同事务提交）。"""
        row = await self._session.get(PlanRow, plan.plan_id)
        # 排除计算字段：快照回读走 model_validate（extra=forbid），计算字段不可作为输入
        snapshot = plan.model_dump_json(exclude_computed_fields=True)
        if row is None:
            row = PlanRow(
                id=plan.plan_id,
                user_id=user_id,
                thread_id=thread_id,
                title=f"{plan.destination} {plan.start_date}~{plan.end_date}",
                destination=plan.destination,
                budget_cny=plan.budget_cny,
                status="active",
                snapshot_json=snapshot,
            )
            self._session.add(row)
        else:
            row.snapshot_json = snapshot
            row.budget_cny = plan.budget_cny
            row.updated_at = datetime.now(UTC)
        self._session.add(
            PlanVersionRow(
                plan_id=plan.plan_id,
                version=version,
                snapshot_json=snapshot,
                diff_json=diff_json,
                trigger_message_id=trigger_message_id,
            )
        )
        return row

    async def get_plan(self, plan_id: str, user_id: int | None = None) -> TripPlan | None:
        if user_id is not None and not await self._owns(plan_id, user_id):
            return None
        row = await self._session.get(PlanRow, plan_id)
        return TripPlan.model_validate_json(row.snapshot_json) if row else None

    async def list_version_numbers(self, plan_id: str) -> list[int]:
        result = await self._session.execute(
            select(PlanVersionRow.version)
            .where(PlanVersionRow.plan_id == plan_id)
            .order_by(PlanVersionRow.version)
        )
        return list(result.scalars())

    async def list_versions(self, plan_id: str) -> Sequence[PlanVersionRow]:
        """全部版本行（版本历史展示：时间戳与变更来源）。"""
        result = await self._session.execute(
            select(PlanVersionRow)
            .where(PlanVersionRow.plan_id == plan_id)
            .order_by(PlanVersionRow.version)
        )
        return result.scalars().all()

    async def get_version(
        self, plan_id: str, version: int, user_id: int | None = None
    ) -> TripPlan | None:
        if user_id is not None and not await self._owns(plan_id, user_id):
            return None
        result = await self._session.execute(
            select(PlanVersionRow.snapshot_json).where(
                PlanVersionRow.plan_id == plan_id, PlanVersionRow.version == version
            )
        )
        snapshot = result.scalar_one_or_none()
        return TripPlan.model_validate_json(snapshot) if snapshot else None

    async def get_thread_id(self, plan_id: str, user_id: int | None = None) -> str | None:
        """获取行程所属会话 ID（回滚时需要）。"""
        if user_id is not None and not await self._owns(plan_id, user_id):
            return None
        result = await self._session.execute(select(PlanRow.thread_id).where(PlanRow.id == plan_id))
        return result.scalar_one_or_none()

    async def latest_version_number(self, plan_id: str) -> int:
        """当前最大版本号（回滚写入新版本时使用）。"""
        versions = await self.list_version_numbers(plan_id)
        return max(versions) if versions else 0

    async def _owns(self, plan_id: str, user_id: int) -> bool:
        result = await self._session.execute(
            select(PlanRow.id).where(PlanRow.id == plan_id, PlanRow.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None

    async def list_by_user(self, user_id: int, limit: int = 100) -> Sequence[PlanRow]:
        """当前用户的行程列表，按更新时间倒序。"""
        result = await self._session.execute(
            select(PlanRow)
            .where(PlanRow.user_id == user_id)
            .order_by(PlanRow.updated_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def delete_plan(self, plan_id: str, user_id: int) -> bool:
        """删除行程及其版本；非本人行程返回 False。"""
        if not await self._owns(plan_id, user_id):
            return False
        await self._session.execute(delete(PlanVersionRow).where(PlanVersionRow.plan_id == plan_id))
        await self._session.execute(delete(PlanRow).where(PlanRow.id == plan_id))
        return True


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        thread_id: str,
        role: str,
        content: str,
        token_json: str | None = None,
    ) -> MessageRow:
        row = MessageRow(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            role=role,
            content=content,
            token_json=token_json,
        )
        self._session.add(row)
        return row

    async def list(self, thread_id: str, limit: int = 100) -> list[MessageRow]:
        result = await self._session.execute(
            select(MessageRow)
            .where(MessageRow.thread_id == thread_id)
            .order_by(MessageRow.seq)
            .limit(limit)
        )
        return list(result.scalars().all())


class PreferenceRepository:
    """长期偏好存储（scope_key 通常为 thread_id 或 'global'）。

    parse_intent 产出 brief 后保存新偏好；后续会话启动时加载并合并进初始 brief。
    值以 JSON 字符串存储，支持 list[str]（偏好/忌口/必去/必避）。
    """

    _PREF_KEYS = ("preferences", "dietary", "must_visit", "avoid", "pace")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, scope_key: str, pref: dict[str, object]) -> None:
        """upsert 偏好（已有则覆盖 value_json）。"""
        for key, value in pref.items():
            if key not in self._PREF_KEYS or value in (None, "", []):
                continue
            value_json = json.dumps(value, ensure_ascii=False)
            existing = await self._session.execute(
                select(UserPreferenceRow).where(
                    UserPreferenceRow.scope_key == scope_key,
                    UserPreferenceRow.pref_key == key,
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                self._session.add(
                    UserPreferenceRow(scope_key=scope_key, pref_key=key, value_json=value_json)
                )
            else:
                row.value_json = value_json
                row.updated_at = datetime.now(UTC)

    async def load(self, scope_key: str) -> dict[str, object]:
        """加载该 scope 下全部偏好（键 -> 解析后的值）。"""
        result = await self._session.execute(
            select(UserPreferenceRow).where(UserPreferenceRow.scope_key == scope_key)
        )
        prefs: dict[str, object] = {}
        for row in result.scalars().all():
            try:
                prefs[row.pref_key] = json.loads(row.value_json)
            except json.JSONDecodeError:
                continue
        return prefs
