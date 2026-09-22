"""仓储：行程快照、版本、消息、长期偏好的读写（SQLAlchemy async）。
阶段四新增：PreferenceRepository（偏好记忆）；PlanRow thread_id 查询（回滚）。"""

import hashlib
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
    ShareRow,
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
    "ShareRepository",
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

    async def get_by_id(self, user_id: int) -> UserRow | None:
        result = await self._session.execute(select(UserRow).where(UserRow.id == user_id))
        return result.scalar_one_or_none()

    async def update_password(self, user_id: int, password_hash: str) -> bool:
        """更新密码哈希；用户不存在返回 False。"""
        from typing import Any, cast

        from sqlalchemy import update
        from sqlalchemy.engine import CursorResult

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
            ),
        )
        return result.rowcount > 0


class ConversationContextRepository:
    """会话内跨轮记忆：以 (user_id, thread_id) 存一份累计 brief。"""

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

    @staticmethod
    def _snapshot_hash(snapshot: str) -> str:
        """快照 sha256（hex），版本去重/一致性校验用。"""
        return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()

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
                snapshot_hash=self._snapshot_hash(snapshot),
                diff_json=diff_json,
                trigger_message_id=trigger_message_id,
            )
        )
        return row

    async def save_snapshot_unique(
        self,
        plan: TripPlan,
        thread_id: str,
        *,
        diff_json: str | None = None,
        trigger_message_id: str | None = None,
        user_id: int | None = None,
    ) -> tuple[PlanRow, int]:
        """内容级去重写入：快照与历史任一版本一致则不再新增版本行。
        回滚/编辑/AI 优化都走这里，避免反复回滚造成重复版本堆积。
        diff_json / trigger_message_id 仅在真正新增版本时写入。
        返回 (行程行, 实际生效版本号)——去重命中时返回历史版本号。
        """
        snapshot = plan.model_dump_json(exclude_computed_fields=True)
        snapshot_hash = self._snapshot_hash(snapshot)
        result = await self._session.execute(
            select(PlanVersionRow.version).where(
                PlanVersionRow.plan_id == plan.plan_id,
                PlanVersionRow.snapshot_hash == snapshot_hash,
                # 哈希命中后仍校验内容，杜绝哈希碰撞误判。
                PlanVersionRow.snapshot_json == snapshot,
            )
        )
        existing = result.scalars().first()
        if existing is not None:
            row = await self._session.get(PlanRow, plan.plan_id)
            if row is not None:
                row.snapshot_json = snapshot
                row.budget_cny = plan.budget_cny
                row.updated_at = datetime.now(UTC)
                return row, int(existing)
            # 主行缺失（历史版本残留而当前行丢失的异常数据）：回退为新增版本。
            version = await self.latest_version_number(plan.plan_id) + 1
            row = await self.save_snapshot(
                plan,
                thread_id,
                version,
                diff_json=diff_json,
                trigger_message_id=trigger_message_id,
                user_id=user_id,
            )
            return row, version
        version = await self.latest_version_number(plan.plan_id) + 1
        row = await self.save_snapshot(
            plan,
            thread_id,
            version,
            diff_json=diff_json,
            trigger_message_id=trigger_message_id,
            user_id=user_id,
        )
        return row, version

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

    async def list_by_user(
        self,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
        q: str | None = None,
    ) -> Sequence[PlanRow]:
        """当前用户的行程列表，按更新时间倒序。
        q 非空时对标题/目的地做模糊匹配（title/destination 冗余列，避免解析快照）。
        """
        stmt = select(PlanRow).where(PlanRow.user_id == user_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((PlanRow.title.like(like)) | (PlanRow.destination.like(like)))
        result = await self._session.execute(
            stmt.order_by(PlanRow.updated_at.desc()).offset(offset).limit(limit)
        )
        return result.scalars().all()

    async def count_by_user(self, user_id: int, q: str | None = None) -> int:
        """当前用户行程总数（可带关键词过滤），分页总数用。"""
        from sqlalchemy import func

        stmt = select(func.count(PlanRow.id)).where(PlanRow.user_id == user_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where((PlanRow.title.like(like)) | (PlanRow.destination.like(like)))
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
        user_id: int | None = None,
    ) -> MessageRow:
        row = MessageRow(
            id=uuid.uuid4().hex,
            user_id=user_id,
            thread_id=thread_id,
            role=role,
            content=content,
            token_json=token_json,
        )
        self._session.add(row)
        return row

    async def list(
        self, thread_id: str, limit: int = 100, user_id: int | None = None
    ) -> list[MessageRow]:
        stmt = select(MessageRow).where(MessageRow.thread_id == thread_id)
        if user_id is not None:
            stmt = stmt.where(MessageRow.user_id == user_id)
        result = await self._session.execute(stmt.order_by(MessageRow.seq).limit(limit))
        return list(result.scalars().all())

    async def list_threads(
        self, user_id: int, limit: int = 50
    ) -> Sequence[tuple[str, datetime, str]]:
        """该用户的会话聚合列表：(thread_id, 最近消息时间, 首条用户消息内容)。
        按 thread 分组取最新消息时间，消息内容取该 thread 第一条 user 消息作摘要。
        """
        from sqlalchemy import func

        latest = (
            select(MessageRow.thread_id, func.max(MessageRow.seq).label("max_seq"))
            .where(MessageRow.user_id == user_id)
            .group_by(MessageRow.thread_id)
            .order_by(func.max(MessageRow.seq).desc())
            .limit(limit)
            .subquery()
        )
        result = await self._session.execute(
            select(MessageRow.thread_id, MessageRow.created_at, MessageRow.content)
            .join(latest, MessageRow.thread_id == latest.c.thread_id)
            .where(
                MessageRow.user_id == user_id,
                MessageRow.seq == latest.c.max_seq,
                MessageRow.role == "assistant",
            )
        )
        threads = list(result.all())
        # 优先 assistant 消息；无 assistant 的 user 会话（异常中断）也纳入。
        seen: set[str] = set()
        ordered: list[tuple[str, datetime, str]] = []
        for thread_id, created_at, content in threads:
            ordered.append((thread_id, created_at, content[:120]))
            seen.add(thread_id)
        # 补齐没有 assistant 消息的 thread（用任意一条 user 消息时间）。
        missing = (
            select(MessageRow.thread_id, MessageRow.created_at, MessageRow.content)
            .where(
                MessageRow.user_id == user_id,
                MessageRow.role == "user",
                ~MessageRow.thread_id.in_(list(seen) or [""]),
            )
            .order_by(MessageRow.seq.desc())
        )
        for row in (await self._session.execute(missing)).all():
            if row.thread_id not in seen:
                ordered.append((row.thread_id, row.created_at, row.content[:120]))
                seen.add(row.thread_id)
        return ordered

    async def delete_thread(self, thread_id: str, user_id: int) -> int:
        """删除该用户某会话全部消息，返回删除条数。"""
        from typing import Any, cast

        from sqlalchemy.engine import CursorResult

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(MessageRow).where(
                    MessageRow.thread_id == thread_id, MessageRow.user_id == user_id
                )
            ),
        )
        return int(result.rowcount or 0)


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
        """加载某 scope 下全部偏好（key -> 解析后的值）。"""
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


class ShareRepository:
    """分享元数据（快照文件存 data/share/，本表记录归属与过期，支撑列表与撤销）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        token: str,
        user_id: int,
        plan_id: str,
        version: int | None,
        expires_at: datetime | None,
    ) -> None:
        self._session.add(
            ShareRow(
                token=token,
                user_id=user_id,
                plan_id=plan_id,
                version=version,
                expires_at=expires_at,
            )
        )

    async def list_by_user(self, user_id: int, limit: int = 100) -> list[ShareRow]:
        """该用户创建的分享（新→旧）。"""
        result = await self._session.execute(
            select(ShareRow)
            .where(ShareRow.user_id == user_id)
            .order_by(ShareRow.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_for_user(self, token: str, user_id: int) -> ShareRow | None:
        """按 token 取归属该用户的分享行（撤销前校验归属）。"""
        result = await self._session.execute(
            select(ShareRow).where(ShareRow.token == token, ShareRow.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def delete(self, token: str, user_id: int) -> bool:
        """删除分享元数据；仅删除归属该用户的行，返回是否实际删除。"""
        from typing import Any, cast

        from sqlalchemy.engine import CursorResult

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ShareRow).where(ShareRow.token == token, ShareRow.user_id == user_id)
            ),
        )
        return result.rowcount > 0
