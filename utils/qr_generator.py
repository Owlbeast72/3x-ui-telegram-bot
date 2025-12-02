# utils/qr_generator.py
from io import BytesIO
import logging
from typing import Union

import qrcode
from PIL import Image

logger = logging.getLogger(__name__)


def generate_qr_image(data: Union[str, bytes]) -> BytesIO:
    """
    Генерирует QR-код из переданных данных.
    
    Args:
        data: Данные для кодирования (строка или байты)
    
    Returns:
        BytesIO объект с изображением QR-кода в формате PNG
    
    Raises:
        ValueError: Если данные пустые
        Exception: При ошибках генерации QR-кода
    """
    if not data:
        raise ValueError("Данные для QR-кода не могут быть пустыми")
    
    try:
        # Создаём QR-код с оптимальными параметрами
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        # Генерируем изображение
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Сохраняем в BytesIO
        bio = BytesIO()
        img.save(bio, 'PNG', optimize=True)
        bio.seek(0)
        
        # Явно закрываем изображение для освобождения памяти
        img.close()
        
        return bio
        
    except Exception as e:
        logger.error(f"Ошибка генерации QR-кода: {e}", exc_info=True)
        # Возвращаем пустой BytesIO в случае ошибки (чтобы не сломать бота)
        empty_bio = BytesIO()
        empty_bio.seek(0)
        return empty_bio
