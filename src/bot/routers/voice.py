# from __future__ import annotations

# import os
# import tempfile
# import logging
# import time
# from aiogram import Router, F
# from aiogram.types import Message
# from aiogram.fsm.context import FSMContext

# from speech_recognition import Recognizer, AudioFile, UnknownValueError, RequestError
# from pydub import AudioSegment
# from pydub.effects import normalize

# from core.config import settings
# from bot.states import GenStates, CreateStates
# from services.queue import enqueue_generation

# router = Router()
# logger = logging.getLogger("voice")

# # ffmpeg для pydub
# AudioSegment.converter = settings.FFMPEG_PATH


# @router.message(F.voice)
# async def handle_voice_message(message: Message, state: FSMContext):
#     """
#     Обработка голосовых промтов:
#     - /gen: использовать как промт к загруженным фото (GenStates.waiting_prompt)
#     - /create: использовать как промт для генерации без фото (CreateStates.waiting_prompt)
#     - CreateStates.selecting_aspect_ratio: принять голос сразу, AR = None (авто)
#     Всегда редактируем «Распознаю голос…», чтобы оно не оставалось висящим.
#     """
#     user_id = message.from_user.id
#     ogg_path = None
#     wav_path = None

#     current_state = await state.get_state()
#     data = await state.get_data()
#     logger.info(f"[VOICE] user={user_id} state={current_state}")

#     # Блокировки в неподходящих состояниях
#     if current_state == GenStates.uploading_images.state:
#         await message.answer(
#             "⚠️ Сначала загрузите 1–4 фотографии, которые нужно изменить.\n"
#             "После загрузки отправьте голосовое с описанием изменений."
#         )
#         return

#     if current_state in (GenStates.generating.state, CreateStates.generating.state):
#         await message.answer("⏳ Подождите, идёт генерация. После завершения можно отправить новый запрос.")
#         return

#     if current_state == GenStates.final_menu.state:
#         await message.answer(
#             "✅ Генерация завершена.\n"
#             "Напишите, что исправить, или нажмите кнопки ниже.\n"
#             "Для нового изображения — /gen (редактирование) или /create (создание)."
#         )
#         return

#     # Сообщение «распознаю» — обязательно отредактируем/заменим
#     processing_msg = await message.answer("🎙️ Распознаю голос...")

#     try:
#         # Скачать voice из Telegram
#         file = await message.bot.get_file(message.voice.file_id)
#         voice_data = await message.bot.download_file(file.file_path)

#         # Сохранить .ogg
#         with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
#             ogg_file.write(voice_data.getvalue())
#             ogg_path = ogg_file.name

#         # Конвертировать/нормализовать → .wav 16kHz mono
#         audio = AudioSegment.from_file(ogg_path, format="ogg")
#         audio = normalize(audio)
#         if audio.channels > 1:
#             audio = audio.set_channels(1)
#         audio = audio.set_frame_rate(16000)
#         wav_path = ogg_path.replace(".ogg", ".wav")
#         audio.export(wav_path, format="wav", parameters=["-ar", "16000", "-ac", "1"])

#         # Распознавание
#         r = Recognizer()
#         r.energy_threshold = 300
#         r.dynamic_energy_threshold = True
#         r.pause_threshold = 0.5
#         r.non_speaking_duration = 0.3

#         with AudioFile(wav_path) as src:
#             r.adjust_for_ambient_noise(src, duration=0.3)
#             audio_data = r.record(src)

#         text = r.recognize_google(audio_data, language="ru-RU", show_all=False).strip()

#         if not text or len(text) < 2:
#             await processing_msg.edit_text(
#                 "❌ Не удалось распознать голос.\n\n"
#                 "💡 Советы:\n"
#                 "• Говорите чётче и громче\n"
#                 "• Записывайте 2–3 секунды и дольше\n"
#                 "• Избегайте сильного шума"
#             )
#             return

#         # Покажем распознанный промт (редактируем предыдущее сообщение)
#         try:
#             await processing_msg.edit_text(f"🎙️ <b>Распознано:</b>\n\n<i>{text}</i>", parse_mode="HTML")
#         except Exception:
#             # Если редактировать нельзя — отправим отдельным сообщением и удалим «распознаю»
#             await message.answer(f"🎙️ <b>Распознано:</b>\n\n<i>{text}</i>", parse_mode="HTML")
#             try:
#                 await processing_msg.delete()
#             except Exception:
#                 pass

#         # ===== Ветви по состояниям =====
#         # /gen: редактирование фото
#         if current_state == GenStates.waiting_prompt.state:
#             photos = data.get("photos") or []
#             if not photos:
#                 await message.answer("❌ Нет загруженных фото. Используйте /gen и отправьте фото сначала.")
#                 return

#             file_ids = [p["file_id"] for p in photos]
#             wait_msg = await message.answer("⏳ Генерирую...")

#             await state.set_state(GenStates.generating)
#             await state.update_data(
#                 prompt=text,
#                 base_prompt=text,
#                 edits=[],
#                 mode="edit",
#                 wait_msg_id=wait_msg.message_id,
#                 gen_started_at=int(time.time()),
#             )
#             await enqueue_generation(user_id, text, file_ids)
#             return

#         # /create: генерация без фото
#         if current_state == CreateStates.waiting_prompt.state:
#             aspect_ratio = (data.get("aspect_ratio") or None)
#             wait_msg = await message.answer("⏳ Генерирую...")

#             await state.set_state(CreateStates.generating)
#             await state.update_data(
#                 mode="create",
#                 prompt=text,
#                 wait_msg_id=wait_msg.message_id,
#                 gen_started_at=int(time.time()),
#             )
#             await enqueue_generation(user_id, text, [], aspect_ratio=aspect_ratio)
#             return

#         # Голос пришёл пока ждём выбор AR → берём авто (None) и запускаем
#         if current_state == CreateStates.selecting_aspect_ratio.state:
#             aspect_ratio = (data.get("aspect_ratio") or None)
#             wait_msg = await message.answer("⏳ Генерирую...")

#             await state.set_state(CreateStates.generating)
#             await state.update_data(
#                 mode="create",
#                 prompt=text,
#                 wait_msg_id=wait_msg.message_id,
#                 gen_started_at=int(time.time()),
#                 aspect_ratio=aspect_ratio,
#             )
#             await enqueue_generation(user_id, text, [], aspect_ratio=aspect_ratio)
#             return

#         # Нет активной сессии генерации — подскажем команды
#         await message.answer(
#             "ℹ️ Для генерации используйте:\n\n"
#             "• <b>/gen</b> — редактировать фото (загрузите фото, затем скажите промт)\n"
#             "• <b>/create</b> — создать новое изображение (скажите промт сразу)",
#             parse_mode="HTML"
#         )

#     except UnknownValueError:
#         try:
#             await processing_msg.edit_text(
#                 "❌ Не удалось распознать речь.\n\n"
#                 "Попробуйте говорить отчётливее и избегать шума."
#             )
#         except Exception:
#             await message.answer(
#                 "❌ Не удалось распознать речь.\n\n"
#                 "Попробуйте говорить отчётливее и избегать шума."
#             )
#     except RequestError as e:
#         logger.error(f"[VOICE] Google API error: {e}")
#         try:
#             await processing_msg.edit_text("❌ Ошибка сервиса распознавания Google. Попробуйте позже.")
#         except Exception:
#             await message.answer("❌ Ошибка сервиса распознавания Google. Попробуйте позже.")
#     except Exception:
#         logger.exception("[VOICE] Unexpected error")
#         try:
#             await processing_msg.edit_text("❌ Произошла ошибка при обработке голосового. Попробуйте ещё раз.")
#         except Exception:
#             await message.answer("❌ Произошла ошибка при обработке голосового. Попробуйте ещё раз.")
#     finally:
#         # Чистим временные файлы
#         for p in (ogg_path, wav_path):
#             if p and os.path.exists(p):
#                 try:
#                     os.remove(p)
#                 except Exception:
#                     pass


from __future__ import annotations

import os
import tempfile
import logging
import time
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from speech_recognition import Recognizer, AudioFile, UnknownValueError, RequestError
from pydub import AudioSegment
from pydub.effects import normalize

from core.config import settings
from bot.states import GenStates, CreateStates
from services.queue import enqueue_generation

router = Router()
logger = logging.getLogger("voice")

# путь к ffmpeg для pydub
AudioSegment.converter = settings.FFMPEG_PATH


@router.message(F.voice)
async def handle_voice_message(message: Message, state: FSMContext):
    """
    Голосовой промт для всех нужных состояний:
    - GenStates.waiting_prompt: редактирование загруженных фото
    - GenStates.final_menu: добавление правки к результату и регенерация
    - CreateStates.waiting_prompt: генерация без фото
    - CreateStates.selecting_aspect_ratio: голос сразу → AR=auto и генерация
    """
    user_id = message.from_user.id
    ogg_path = None
    wav_path = None

    cur = await state.get_state()
    data = await state.get_data()
    logger.info(f"[VOICE] user={user_id} state={cur}")

    # Блокируем голос там, где это точно не нужно
    if cur == GenStates.uploading_images.state:
        await message.answer(
            "⚠️ Сначала загрузите 1–4 фотографии, которые нужно изменить.\n"
            "После загрузки отправьте голосовое с описанием изменений."
        )
        return
    if cur in (GenStates.generating.state, CreateStates.generating.state):
        await message.answer("⏳ Подождите, идёт генерация. Потом можно отправить новый запрос.")
        return

    # Сообщение «распознаю…» — дальше его отредактируем/удалим
    processing_msg = await message.answer("🎙️ Распознаю голос...")

    try:
        # --- загрузка и подготовка аудио ---
        file = await message.bot.get_file(message.voice.file_id)
        voice_data = await message.bot.download_file(file.file_path)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
            ogg_file.write(voice_data.getvalue())
            ogg_path = ogg_file.name

        audio = AudioSegment.from_file(ogg_path, format="ogg")
        audio = normalize(audio).set_channels(1).set_frame_rate(16000)
        wav_path = ogg_path.replace(".ogg", ".wav")
        audio.export(wav_path, format="wav", parameters=["-ar", "16000", "-ac", "1"])

        # --- распознавание ---
        r = Recognizer()
        r.energy_threshold = 300
        r.dynamic_energy_threshold = True
        r.pause_threshold = 0.5
        r.non_speaking_duration = 0.3

        with AudioFile(wav_path) as src:
            r.adjust_for_ambient_noise(src, duration=0.3)
            audio_data = r.record(src)

        text = r.recognize_google(audio_data, language="ru-RU", show_all=False).strip()
        if not text or len(text) < 2:
            await processing_msg.edit_text(
                "❌ Не удалось распознать голос.\n\n"
                "💡 Говорите чётче, запишите 2–3 секунды и избегайте шума."
            )
            return

        # показать распознанный промт
        try:
            await processing_msg.edit_text(f"🎙️ <b>Распознано:</b>\n\n<i>{text}</i>", parse_mode="HTML")
        except Exception:
            await message.answer(f"🎙️ <b>Распознано:</b>\n\n<i>{text}</i>", parse_mode="HTML")
            try:
                await processing_msg.delete()
            except Exception:
                pass

        # ---------- ВЕТВИ ПО СОСТОЯНИЮ ----------

        # /gen: ждём промт для уже загруженных фото
        if cur == GenStates.waiting_prompt.state:
            photos = data.get("photos") or []
            if not photos:
                await message.answer("❌ Нет загруженных фото. Используйте /gen и отправьте фото сначала.")
                return
            file_ids = [p["file_id"] for p in photos]

            wait_msg = await message.answer("⏳ Генерирую...")
            await state.set_state(GenStates.generating)
            await state.update_data(
                prompt=text,
                base_prompt=text,
                edits=[],
                mode="edit",
                wait_msg_id=wait_msg.message_id,
                gen_started_at=int(time.time()),
            )
            await enqueue_generation(user_id, text, file_ids)
            return

        # ✅ НОВОЕ: голосовые правки после результата /gen
        if cur == GenStates.final_menu.state:
            photos = data.get("photos") or []
            if not photos:
                await message.answer("❌ Не удалось найти исходные изображения. Нажмите «Начать заново».")
                return

            base_prompt = (data.get("base_prompt") or data.get("prompt") or "").strip()
            edits = list(data.get("edits") or [])
            edits.append(text)
            cumulative_prompt = " ".join([base_prompt] + edits).strip()
            if len(cumulative_prompt) > 4000:
                cumulative_prompt = cumulative_prompt[:4000]

            file_ids = [p["file_id"] for p in photos]
            wait_msg = await message.answer("⏳ Генерирую...")

            await state.set_state(GenStates.generating)
            await state.update_data(
                prompt=cumulative_prompt,
                base_prompt=base_prompt,
                edits=edits,
                mode="edit",
                wait_msg_id=wait_msg.message_id,
                gen_started_at=int(time.time()),
            )
            await enqueue_generation(user_id, cumulative_prompt, file_ids)
            return

        # /create: обычное ожидание промта
        if cur == CreateStates.waiting_prompt.state:
            aspect_ratio = data.get("aspect_ratio") or None
            wait_msg = await message.answer("⏳ Генерирую...")
            await state.set_state(CreateStates.generating)
            await state.update_data(
                mode="create",
                prompt=text,
                wait_msg_id=wait_msg.message_id,
                gen_started_at=int(time.time()),
            )
            await enqueue_generation(user_id, text, [], aspect_ratio=aspect_ratio)
            return

        # /create: голос пришёл, пока ждём выбор AR — берём авто
        if cur == CreateStates.selecting_aspect_ratio.state:
            aspect_ratio = data.get("aspect_ratio") or None
            wait_msg = await message.answer("⏳ Генерирую...")
            await state.set_state(CreateStates.generating)
            await state.update_data(
                mode="create",
                prompt=text,
                wait_msg_id=wait_msg.message_id,
                gen_started_at=int(time.time()),
                aspect_ratio=aspect_ratio,
            )
            await enqueue_generation(user_id, text, [], aspect_ratio=aspect_ratio)
            return

        # Нет активной сессии
        await message.answer(
            "ℹ️ Для генерации используйте:\n"
            "• <b>/gen</b> — редактировать фото (загрузите фото, затем скажите промт)\n"
            "• <b>/create</b> — создать новое изображение (скажите промт сразу)",
            parse_mode="HTML",
        )

    except UnknownValueError:
        try:
            await processing_msg.edit_text(
                "❌ Не удалось распознать речь. Попробуйте говорить отчётливее и избегать шума."
            )
        except Exception:
            await message.answer("❌ Не удалось распознать речь. Попробуйте ещё раз.")
    except RequestError as e:
        logger.error(f"[VOICE] Google API error: {e}")
        try:
            await processing_msg.edit_text("❌ Ошибка сервиса распознавания Google. Попробуйте позже.")
        except Exception:
            await message.answer("❌ Ошибка сервиса распознавания Google. Попробуйте позже.")
    except Exception:
        logger.exception("[VOICE] Unexpected error")
        try:
            await processing_msg.edit_text("❌ Ошибка при обработке голосового. Попробуйте ещё раз.")
        except Exception:
            await message.answer("❌ Ошибка при обработке голосового. Попробуйте ещё раз.")
    finally:
        # чистим временные файлы
        for p in (ogg_path, wav_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
