"""
✅ Очистка БД через ARQ cron
Запускается каждые 10 минут
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, delete, and_, func, update

from db.engine import SessionLocal
from db.models import Task, Payment

log = logging.getLogger("cleanup_db")


async def cleanup_database_task(ctx):
    """
    ARQ периодическая задача очистки БД
    Вызывается каждые 10 минут
    """
    log.info("🧹 Starting database cleanup...")
    
    try:
        async with SessionLocal() as session:
            now = datetime.utcnow()
            
            # 1. Удалить completed задачи старше 7 дней
            cutoff_completed = now - timedelta(days=7)
            result_completed = await session.execute(
                delete(Task)
                .where(and_(
                    Task.status == "completed",
                    Task.created_at < cutoff_completed
                ))
            )
            deleted_completed = result_completed.rowcount
            
            # 2. Удалить failed задачи старше 3 дней
            cutoff_failed = now - timedelta(days=3)
            result_failed = await session.execute(
                delete(Task)
                .where(and_(
                    Task.status == "failed",
                    Task.created_at < cutoff_failed
                ))
            )
            deleted_failed = result_failed.rowcount
            
            # 3. Пометить зависшие задачи (>1 час) как failed
            cutoff_stuck = now - timedelta(hours=1)
            result_stuck = await session.execute(
                update(Task)
                .where(and_(
                    Task.status.in_(["queued", "processing"]),
                    Task.created_at < cutoff_stuck
                ))
                .values(status="failed")
            )
            marked_failed = result_stuck.rowcount
            
            # 4. Удалить pending платежи старше 24 часов
            cutoff_pending = now - timedelta(hours=24)
            result_pending = await session.execute(
                delete(Payment)
                .where(and_(
                    Payment.status == "pending",
                    Payment.created_at < cutoff_pending
                ))
            )
            deleted_pending = result_pending.rowcount
            
            # 5. Удалить старые completed/cancelled платежи (30 дней)
            cutoff_old_payments = now - timedelta(days=30)
            result_old_payments = await session.execute(
                delete(Payment)
                .where(and_(
                    Payment.status.in_(["completed", "cancelled"]),
                    Payment.created_at < cutoff_old_payments
                ))
            )
            deleted_old_payments = result_old_payments.rowcount
            
            await session.commit()
            
            log.info(
                f"✅ DB Cleanup: "
                f"Tasks(completed:{deleted_completed}, failed:{deleted_failed}, stuck:{marked_failed}), "
                f"Payments(pending:{deleted_pending}, old:{deleted_old_payments})"
            )
            
            # Оптимизация таблиц если удалено много
            total_deleted = deleted_completed + deleted_failed + deleted_pending + deleted_old_payments
            if total_deleted > 100:
                try:
                    await session.execute("OPTIMIZE TABLE tasks")
                    await session.execute("OPTIMIZE TABLE payments")
                    log.info("✅ Tables optimized")
                except Exception as e:
                    log.warning(f"Table optimization skipped: {e}")
            
            # Статистика
            tasks_total = await session.scalar(select(func.count(Task.id)))
            payments_total = await session.scalar(select(func.count(Payment.id)))
            
            log.info(f"📊 DB Stats: Tasks={tasks_total}, Payments={payments_total}")
    
    except Exception as e:
        log.error(f"❌ DB cleanup error: {e}")