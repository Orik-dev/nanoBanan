from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from services.pricing import credits_for_rub
from services.payments import create_topup_payment
from services.users import ensure_user
from db.engine import SessionLocal
from db.models import User
from bot.states import TopupStates
from bot.keyboards import kb_topup_packs, kb_topup_methods, kb_receipt_choice, kb_topup_stars
from services.telegram_safe import safe_answer, safe_edit_text, safe_send_text

router = Router()

# --- helper: безопасное «назад» с фолбэком на новое сообщение ---
async def _edit_or_send(bot, msg, text, reply_markup):
    edited = await safe_edit_text(msg, text, reply_markup=reply_markup)
    if edited is None:
        # не получилось отредактировать — шлём новое
        await safe_send_text(bot, msg.chat.id, text, reply_markup=reply_markup)

# ====== возврат к выбору способа оплаты ======
@router.callback_query(F.data.in_({"back_methods", "back_to_methods"}))
async def back_to_methods(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    await state.clear()
    user = await ensure_user(c.from_user)
    text = (f"Ваш баланс: <b>{user.balance_credits}</b> генераций.\n"
            f"Тариф: 1 генерация — 1 изображение.\n\n"
            "Выберите способ оплаты:")
    await _edit_or_send(c.bot, c.message, text, kb_topup_methods())

# ====== RUB (ЮKassa) ======
@router.callback_query(F.data == "m_rub")
async def method_rub(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    await state.clear()
    await state.set_state(TopupStates.choosing_amount)
    await _edit_or_send(c.bot, c.message, "Выберите сумму для пополнения:", kb_topup_packs())

@router.callback_query(TopupStates.choosing_amount, F.data.startswith("pack_"))
async def choose_pack(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    token = c.data.split("_", 1)[1]
    try:
        rub = int(token)
    except ValueError:
        await _edit_or_send(c.bot, c.message, "Выберите один из доступных пакетов: 149 ₽, 240 ₽ или 540 ₽.", kb_topup_packs())
        return

    cr = credits_for_rub(rub)
    if cr <= 0:
        await _edit_or_send(c.bot, c.message, "Выберите один из доступных пакетов: 149 ₽, 240 ₽ или 540 ₽.", kb_topup_packs())
        return

    await state.update_data(rub=rub, credits=cr)

    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.chat_id == c.from_user.id))).scalar_one()
        already_has_pref = bool(u.email) or bool(u.receipt_opt_out)

    if already_has_pref:
        try:
            url = await create_topup_payment(c.from_user.id, rub)
        except Exception:
            await _edit_or_send(c.bot, c.message, "⚠️ Не удалось создать счёт. Попробуйте позже или выберите другой способ оплаты.", kb_topup_methods())
            await state.clear()
            return

        await _edit_or_send(c.bot, c.message, f"Оплатите по ссылке:\n{url}", None)
        await state.clear()
        return

    await state.set_state(TopupStates.choosing_method)
    await _edit_or_send(c.bot, c.message, f"Сумма: <b>{rub} ₽</b> → {cr} генераций.\nНужен ли чек на e-mail?", kb_receipt_choice())

@router.message(TopupStates.choosing_amount)
async def input_amount(m: Message, state: FSMContext):
    await safe_send_text(m.bot, m.chat.id, "Пожалуйста, выберите один из пакетов: 149 ₽, 240 ₽ или 540 ₽.", reply_markup=kb_topup_packs())

@router.callback_query(TopupStates.choosing_method, F.data == "receipt_skip")
async def receipt_skip(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.chat_id == c.from_user.id))).scalar_one()
        u.receipt_opt_out = True
        await s.commit()

    rub = (await state.get_data())["rub"]
    url = await create_topup_payment(c.from_user.id, rub)
    await _edit_or_send(c.bot, c.message, f"Оплатите по ссылке:\n{url}", None)
    await state.clear()

@router.callback_query(TopupStates.choosing_method, F.data == "receipt_need")
async def receipt_need(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    await state.set_state(TopupStates.waiting_email)
    await _edit_or_send(c.bot, c.message, "Введите e-mail для чека (один раз).", None)

@router.message(TopupStates.waiting_email)
async def waiting_email(m: Message, state: FSMContext):
    email = (m.text or "").strip()

    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.chat_id == m.from_user.id))).scalar_one()
        if email.lower() in {"не нужен", "ненужен", "skip"}:
            u.receipt_opt_out = True
        else:
            if "@" not in email or "." not in email or len(email) < 5:
                await safe_send_text(m.bot, m.chat.id, "Некорректный e-mail. Введите снова или напишите «не нужен».")
                return
            u.email = email
        await s.commit()

    rub = (await state.get_data())["rub"]
    url = await create_topup_payment(m.from_user.id, rub)
    await safe_send_text(m.bot, m.chat.id, f"Оплатите по ссылке:\n{url}\nЕсли потеряете — используйте /buy.")
    await state.clear()

# ====== Stars (XTR) ======
@router.callback_query(F.data == "m_stars")
async def method_stars(c: CallbackQuery, state: FSMContext):
    await safe_answer(c)
    await state.clear()
    await _edit_or_send(c.bot, c.message, "Выберите пакет звёзд ⭐:\n\n", kb_topup_stars())

@router.callback_query(F.data.startswith("stars_"))
async def cb_buy_stars(c: CallbackQuery):
    await safe_answer(c)
    parts = c.data.split("_", 1)
    if len(parts) < 2 or not parts[1].isdigit():
        return

    from services.pricing import credits_for_rub
    stars = int(parts[1])
    cr = credits_for_rub(stars)
    if cr <= 0:
        return

    title = f"{stars} ⭐ → {cr} генераций"
    prices = [LabeledPrice(label=title, amount=stars)]

    try:
        await c.message.delete()
    except TelegramBadRequest:
        pass

    try:
        await c.bot.send_invoice(
            chat_id=c.from_user.id,
            title=title,
            description="NanoBanana — пополнение звёздами",
            payload=f"stars:{stars}",
            provider_token="",
            currency="XTR",
            prices=prices,
        )
    except TelegramForbiddenError:
        pass

@router.pre_checkout_query()
async def stars_pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def stars_success(m: Message):
    payload = m.successful_payment.invoice_payload or ""
    if not payload.startswith("stars:"):
        return
    stars = int(payload.split(":", 1)[1])
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.chat_id == m.from_user.id))).scalar_one()
        cr = credits_for_rub(stars)
        u.balance_credits += cr
        await s.commit()
    await safe_send_text(m.bot, m.chat.id, f"Оплата звёздами прошла ✅ Баланс пополнен на {cr} генераций.")
