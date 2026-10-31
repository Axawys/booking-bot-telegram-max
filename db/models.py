"""Модели БД.

Время записей (starts_at, ends_at, booking_slots.starts_at) хранится как
локальное время бизнеса без часового пояса: запись «на 10:00» означает 10:00
по часам салона. Служебные метки created_at / cancelled_at — в UTC.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Platform(enum.StrEnum):
    TELEGRAM = "telegram"
    MAX = "max"


class BookingStatus(enum.StrEnum):
    PENDING = "pending"  # ждёт подтверждения администратором
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"  # отменена клиентом
    REJECTED = "rejected"  # отклонена администратором


# Записи в этих статусах занимают слоты расписания.
ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


def _str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK со значениями enum (в SQLite нет нативного ENUM)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column(comment="Цена в рублях")
    duration_minutes: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[Platform] = mapped_column(_str_enum(Platform, "platform"))
    external_id: Mapped[int] = mapped_column(BigInteger, comment="ID пользователя в Telegram / MAX")
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())

    bookings: Mapped[list["Booking"]] = relationship(back_populates="client")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        Index("ix_bookings_client_id_starts_at", "client_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    status: Mapped[BookingStatus] = mapped_column(
        _str_enum(BookingStatus, "booking_status"),
        default=BookingStatus.CONFIRMED,
        server_default=BookingStatus.CONFIRMED.value,
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    cancelled_at: Mapped[datetime | None] = mapped_column(
        comment="Когда запись отменена клиентом или отклонена (UTC)"
    )

    client: Mapped[Client] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship()
    slots: Mapped[list["BookingSlot"]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BookingSlot(Base):
    """Слот сетки расписания, занятый записью.

    Запись длительностью N слотов занимает N строк. Уникальное ограничение по
    starts_at гарантирует на уровне БД, что один слот не попадёт в две записи —
    в том числе при одновременных запросах и при пересечении услуг разной
    длительности. При отмене записи её строки удаляются и слоты освобождаются.
    """

    __tablename__ = "booking_slots"
    __table_args__ = (UniqueConstraint("starts_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    starts_at: Mapped[datetime]

    booking: Mapped[Booking] = relationship(back_populates="slots")


class ClosedSlot(Base):
    """Слот, закрытый администратором для новых записей.

    «Закрыть день» = закрыть все слоты дня. Существующие записи не трогаются.
    Запрет записи на закрытое время проверяет сама БД: триггер
    trg_booking_slots_not_closed (миграция «closed slots») не даёт вставить
    строку в booking_slots, если её время есть в closed_slots.
    Внимание: batch-миграция, пересоздающая booking_slots, удалит триггер —
    его нужно создать заново в той же миграции.
    """

    __tablename__ = "closed_slots"

    starts_at: Mapped[datetime] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class AppSetting(Base):
    """Настройки, которые админ меняет из бота (ключ → значение).

    Если ключа нет, используется значение по умолчанию из .env (core.config).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class BannedUser(Base):
    """Пользователь, которому админ закрыл доступ к боту.

    Хранится по (платформа, ID), а не по clients.id: забанить можно и того,
    кто ещё ни разу не записывался.
    """

    __tablename__ = "banned_users"

    platform: Mapped[Platform] = mapped_column(_str_enum(Platform, "platform"), primary_key=True)
    external_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    banned_by: Mapped[int | None] = mapped_column(BigInteger, comment="ID админа")
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class SentReminder(Base):
    """Отправленное напоминание о записи.

    Первичный ключ (booking_id, kind) гарантирует на уровне БД, что одно
    напоминание не уйдёт дважды — даже если задача запланирована повторно
    (например, после перезапуска бота).
    """

    __tablename__ = "sent_reminders"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True, comment="Например, 24h")
    sent_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
