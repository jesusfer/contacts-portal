import warnings
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PICTURE_BYTES = 5 * 1024 * 1024
PICTURE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def normalize_picture(upload):
    if not upload.size or upload.size > MAX_PICTURE_BYTES:
        raise ValidationError({"picture": "Picture must be no larger than 5 MiB."})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                if (
                    image.format not in PICTURE_TYPES
                    or upload.content_type != PICTURE_TYPES[image.format]
                ):
                    raise ValidationError(
                        {
                            "picture": "Upload a JPEG, PNG, or WebP image with its matching content type."
                        }
                    )
                if max(image.size) > 4096:
                    raise ValidationError(
                        {"picture": "Picture dimensions must not exceed 4096 pixels."}
                    )
                image.verify()
            upload.seek(0)
            with Image.open(upload) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((512, 512))
                # A fresh image drops EXIF, ICC profiles and other input metadata.
                normalized = Image.new("RGB", image.size)
                normalized.paste(image)
                output = BytesIO()
                normalized.save(output, format="JPEG", quality=85)
        return ContentFile(output.getvalue())
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError({"picture": "Invalid or unsafe image."}) from exc
