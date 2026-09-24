import base64
import io

from PIL import Image, ImageOps


class InvalidImageError(ValueError):
    pass


def prepare_image(data: bytes, max_side: int) -> str:
    """Decode, apply EXIF orientation, downscale and return a JPEG data URL."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise InvalidImageError(f"cannot decode image: {exc}") from exc

    image = image.convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
