"""
✅ Автобэкап БД через ARQ cron (БЕЗОПАСНАЯ ВЕРСИЯ)
Запускается каждый час
"""
import logging
import subprocess
import re
import gzip
import shutil
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiogram import Bot
from aiogram.types import FSInputFile

from core.config import settings

log = logging.getLogger("backup_db")


async def backup_database_task(ctx):
    """
    ARQ периодическая задача бэкапа
    Вызывается каждый час
    """
    log.info("💾 Starting database backup...")
    
    backup_dir = Path("/app/backups")
    backup_dir.mkdir(exist_ok=True, parents=True)
    
    # ✅ ПРОВЕРКА 1: mysqldump установлен?
    try:
        result = subprocess.run(
            ["which", "mysqldump"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            log.error("❌ mysqldump not installed! Install: apt-get install mysql-client")
            
            # Уведомить админа
            if settings.ADMIN_ID:
                try:
                    bot: Bot = ctx.get("bot")
                    if bot:
                        await bot.send_message(
                            settings.ADMIN_ID,
                            "❌ <b>Backup Error</b>\n\n"
                            "mysqldump not found!\n"
                            "Please install: <code>apt-get install mysql-client</code>",
                            parse_mode="HTML"
                        )
                except Exception:
                    pass
            return
    except Exception as e:
        log.error(f"❌ Cannot check mysqldump: {e}")
        return
    
    # ✅ ПРОВЕРКА 2: Достаточно места на диске?
    try:
        stat = shutil.disk_usage("/app")
        free_gb = stat.free / (1024**3)
        
        if free_gb < 1.0:  # Меньше 1 GB свободно
            log.error(f"❌ Low disk space: {free_gb:.2f} GB free")
            
            if settings.ADMIN_ID:
                try:
                    bot: Bot = ctx.get("bot")
                    if bot:
                        await bot.send_message(
                            settings.ADMIN_ID,
                            f"❌ <b>Backup Skipped</b>\n\n"
                            f"Low disk space: {free_gb:.2f} GB\n"
                            f"Need at least 1 GB free",
                            parse_mode="HTML"
                        )
                except Exception:
                    pass
            return
        
        log.info(f"💾 Disk space OK: {free_gb:.2f} GB free")
    except Exception as e:
        log.warning(f"⚠️ Cannot check disk space: {e}")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"nanoBanana_{timestamp}.sql"
    backup_file_gz = backup_dir / f"nanoBanana_{timestamp}.sql.gz"
    
    try:
        # Парсим DSN
        match = re.match(
            r"mysql\+aiomysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)",
            settings.DB_DSN
        )
        
        if not match:
            log.error("❌ Cannot parse DB_DSN")
            return
        
        user, password, host, port, database = match.groups()
        
        # ✅ ИСПРАВЛЕНИЕ 3: Пароль через файл, а не командную строку
        with NamedTemporaryFile(mode='w', suffix='.cnf', delete=False) as cnf_file:
            cnf_file.write(f"[mysqldump]\n")
            cnf_file.write(f"user={user}\n")
            cnf_file.write(f"password={password}\n")
            cnf_file.write(f"host={host}\n")
            cnf_file.write(f"port={port}\n")
            cnf_path = cnf_file.name
        
        try:
            # Защищаем файл паролей
            os.chmod(cnf_path, 0o600)
            
            log.info(f"🔄 Creating backup...")
            
            # ✅ Безопасная команда без пароля в аргументах
            cmd = [
                "mysqldump",
                f"--defaults-file={cnf_path}",
                "--single-transaction",
                "--routines",
                "--triggers",
                "--quick",  # ✅ Для больших БД
                "--lock-tables=false",  # ✅ Не блокировать
                database
            ]
            
            with open(backup_file, "w") as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=600  # ✅ Увеличено с 300 до 600 сек
                )
            
            if result.returncode != 0:
                log.error(f"❌ mysqldump failed: {result.stderr}")
                
                # Удаляем неполный бэкап
                if backup_file.exists():
                    backup_file.unlink()
                
                return
        
        finally:
            # ✅ Удаляем файл с паролем
            try:
                os.unlink(cnf_path)
            except Exception:
                pass
        
        # Проверяем размер
        size_mb = backup_file.stat().st_size / (1024 * 1024)
        
        # ✅ ПРОВЕРКА 4: Бэкап не пустой?
        if size_mb < 0.1:  # Меньше 100 KB
            log.error(f"❌ Backup too small ({size_mb:.2f} MB) - probably failed")
            backup_file.unlink()
            return
        
        log.info(f"✅ Backup created: {size_mb:.2f} MB")
        
        # ✅ ПРОВЕРКА 5: Хватит ли места для сжатия?
        if stat.free < backup_file.stat().st_size * 0.5:
            log.warning(f"⚠️ Not enough space for compression, sending uncompressed")
            
            # Отправить несжатый
            if settings.ADMIN_ID and size_mb < 50:
                await send_backup_to_admin(ctx, backup_file, size_mb, size_mb, compressed=False)
            
            backup_file.unlink()
            return
        
        # Сжимаем
        log.info(f"🔄 Compressing...")
        with open(backup_file, 'rb') as f_in:
            with gzip.open(backup_file_gz, 'wb', compresslevel=6) as f_out:  # ✅ Уровень 6 - баланс скорости и сжатия
                shutil.copyfileobj(f_in, f_out)
        
        backup_file.unlink()
        
        size_gz_mb = backup_file_gz.stat().st_size / (1024 * 1024)
        compression_ratio = (1 - size_gz_mb / size_mb) * 100
        log.info(f"✅ Compressed: {size_gz_mb:.2f} MB (saved {compression_ratio:.1f}%)")
        
        # Отправить админу
        if settings.ADMIN_ID:
            await send_backup_to_admin(ctx, backup_file_gz, size_mb, size_gz_mb, compressed=True)
        
        # Очистить старые бэкапы
        await cleanup_old_backups(backup_dir, keep_count=24)
        
        log.info("✅ Backup completed")
    
    except subprocess.TimeoutExpired:
        log.error("❌ Backup timeout (>10 min)")
        
        # Удалить неполный бэкап
        if backup_file.exists():
            backup_file.unlink()
    
    except Exception as e:
        log.error(f"❌ Backup error: {e}")
        
        # Удалить неполные файлы
        if backup_file.exists():
            backup_file.unlink()
        if backup_file_gz.exists():
            backup_file_gz.unlink()
        
        # Уведомить админа
        if settings.ADMIN_ID:
            try:
                bot: Bot = ctx.get("bot")
                if bot:
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"❌ <b>Backup Failed</b>\n\n{str(e)[:300]}",
                        parse_mode="HTML"
                    )
            except Exception:
                pass


async def send_backup_to_admin(ctx, backup_file: Path, size_mb: float, size_gz_mb: float, compressed: bool = True):
    """Отправка бэкапа админу"""
    bot: Bot = ctx.get("bot")
    if not bot:
        log.warning("⚠️ Bot not available in context")
        return
    
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if size_gz_mb < 50:
            log.info(f"📤 Sending backup to admin...")
            
            caption = (
                f"💾 <b>Database Backup</b>\n\n"
                f"📅 {timestamp}\n"
            )
            
            if compressed:
                caption += (
                    f"📊 Original: {size_mb:.2f} MB\n"
                    f"📦 Compressed: {size_gz_mb:.2f} MB\n\n"
                )
            else:
                caption += f"📊 Size: {size_mb:.2f} MB\n\n"
            
            caption += "✅ Backup completed"
            
            await bot.send_document(
                settings.ADMIN_ID,
                document=FSInputFile(backup_file),
                caption=caption,
                parse_mode="HTML",
                request_timeout=300
            )
            
            log.info("✅ Backup sent to admin")
        else:
            log.warning(f"⚠️ Backup too large ({size_gz_mb:.2f} MB)")
            
            await bot.send_message(
                settings.ADMIN_ID,
                f"💾 <b>Database Backup</b>\n\n"
                f"📅 {timestamp}\n"
                f"📊 Original: {size_mb:.2f} MB\n"
                f"📦 Compressed: {size_gz_mb:.2f} MB\n\n"
                f"⚠️ Too large for Telegram (>50MB)\n"
                f"📁 Saved locally:\n<code>{backup_file}</code>\n\n"
                f"💡 Use SFTP/SCP to download",
                parse_mode="HTML"
            )
    
    except Exception as e:
        log.error(f"❌ Failed to send backup: {e}")


async def cleanup_old_backups(backup_dir: Path, keep_count: int = 24):
    """Удаление старых бэкапов"""
    try:
        backups = sorted(
            backup_dir.glob("nanoBanana_*.sql.gz"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        if len(backups) <= keep_count:
            log.info(f"📁 Backups: {len(backups)} (keeping all)")
            return
        
        to_delete = backups[keep_count:]
        deleted_count = 0
        freed_mb = 0
        
        for backup in to_delete:
            try:
                size = backup.stat().st_size / (1024 * 1024)
                backup.unlink()
                deleted_count += 1
                freed_mb += size
            except Exception as e:
                log.warning(f"Failed to delete {backup.name}: {e}")
        
        log.info(f"🗑️ Deleted {deleted_count} old backups (freed {freed_mb:.2f} MB, keeping {keep_count})")
    
    except Exception as e:
        log.error(f"❌ Cleanup old backups error: {e}")