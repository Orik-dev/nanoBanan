from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from services.pricing import PACKS_RUB, credits_for_rub


ASPECT_RATIOS = {
    "21:9": "21:9 (Ultrawide)",
    "16:9": "16:9 (Wide)",
    "4:3": "4:3 (Landscape)",
    "3:2": "3:2 (Landscape)",
    "1:1": "1:1 (Square)",
    "9:16": "9:16 (Portrait)",
    "3:4": "3:4 (Portrait)",
    "2:3": "2:3 (Portrait)",
    "5:4": "5:4 (Flexible)",
    "4:5": "4:5 (Flexible)",
}

def validate_aspect_ratio(ar: str) -> bool:
    """Проверка валидности соотношения сторон"""
    if not ar:
        return False
    return ar in ASPECT_RATIOS

def kb_aspect_ratio_selector() -> InlineKeyboardMarkup:
    """Клавиатура выбора соотношения сторон"""
    buttons = []
    buttons.append([
        InlineKeyboardButton(text="📱 Вертикальное (9:16)", callback_data="ar_9:16"),
        # InlineKeyboardButton(text="3:4", callback_data="ar_3:4"),
    ])
    # Landscape
    # buttons.append([InlineKeyboardButton(text="🖼 Landscape", callback_data="ar_header_landscape")])
    buttons.append([
        # InlineKeyboardButton(text="21:9", callback_data="ar_21:9"),
        InlineKeyboardButton(text="🖼 Горизонтальное (16:9)", callback_data="ar_16:9"),
    ])
    # buttons.append([
    #     InlineKeyboardButton(text="4:3", callback_data="ar_4:3"),
    #     InlineKeyboardButton(text="3:2", callback_data="ar_3:2"),
    # ])
    
    # # Square
    # buttons.append([InlineKeyboardButton(text="⬛️ Square", callback_data="ar_header_square")])
    # buttons.append([InlineKeyboardButton(text="1:1", callback_data="ar_1:1")])
    
    # Portrait
    # buttons.append([InlineKeyboardButton(text="📱 Portrait", callback_data="ar_header_portrait")])
   
    # buttons.append([InlineKeyboardButton(text="2:3", callback_data="ar_2:3")])
    
    # # Flexible
    # buttons.append([InlineKeyboardButton(text="🔄 Flexible", callback_data="ar_header_flexible")])
    # buttons.append([
    #     InlineKeyboardButton(text="5:4", callback_data="ar_5:4"),
    #     InlineKeyboardButton(text="4:5", callback_data="ar_4:5"),
    # ])
    
    # Кнопка "Пропустить" (использовать по умолчанию)
    buttons.append([InlineKeyboardButton(text="⏩ Пропустить", callback_data="ar_skip")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_topup_packs() -> InlineKeyboardMarkup:
    rows = []
    for rub in PACKS_RUB:
        cr = credits_for_rub(rub)
        rows.append([InlineKeyboardButton(text=f"{rub} ₽ → {cr} генераций", callback_data=f"pack_{rub}")])
    # rows.append([InlineKeyboardButton(text="Другая сумма", callback_data="pack_other")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_methods")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def kb_topup_stars() -> InlineKeyboardMarkup:
    rows = []
    for stars in PACKS_RUB:
        cr = credits_for_rub(stars)
        rows.append([
            InlineKeyboardButton(
                text=f"{stars} ⭐ → {cr} генераций",
                callback_data=f"stars_{stars}"
            )
        ])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_methods")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def kb_topup_methods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Карта РФ(₽)", callback_data="m_rub"),
            InlineKeyboardButton(text="⭐️ Звёзды", callback_data="m_stars"),
        ],
    ])


def kb_receipt_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📧 Отправить чек на e-mail", callback_data="receipt_need")],
        [InlineKeyboardButton(text="🙅 Чек не нужен", callback_data="receipt_skip")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_methods")],
    ])


def kb_gen_step_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_images")],
    ])


def kb_final_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Начать заново", callback_data="new_image")],
        [InlineKeyboardButton(text="🔁 Сгенерировать похожее", callback_data="regenerate")],
    ])


def kb_create_image() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать изображение", callback_data="create_image")],
    ])
