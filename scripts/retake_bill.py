"""The same bill, photographed a second time.

The first photo was blurry, so they took another. That is the ordinary thing a
family does, and it used to put the bill on the case twice: dedupe matched on
the image hash, and a retake is a different image. Two bills, twice the money
owed, and a second payment eligible to go out for a debt the hospital issued
once.

This makes that input. Not a byte tweaked to change a hash — a plausible
second photograph: held at a slightly different angle, a little softer, a
little differently lit, and saved as JPEG the way a phone would. The bill
number on it is unchanged, because it is the same piece of paper, and that is
the whole point of the test.

    python scripts/retake_bill.py ~/Desktop/bill_interim_day_three.png

Writes <name>_retaken.jpg beside it.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

# Small enough that the text stays readable, large enough that no pixel of the
# original survives in place. A retake nobody could read would test the
# unreadable path instead, which is a different test.
ROTATION_DEGREES = 0.6
BRIGHTNESS = 1.06
CONTRAST = 0.94
BLUR_RADIUS = 0.6
JPEG_QUALITY = 82


def retake(source: Path, out: Path) -> Path:
    """Photograph the same paper again."""
    image = Image.open(source).convert("RGB")

    # Held at an angle. White fill because the surrounding is a desk under a
    # bright light, not black.
    image = image.rotate(ROTATION_DEGREES, resample=Image.BICUBIC,
                         fillcolor=(255, 255, 255), expand=False)
    # Cropped a hair tighter, the way a second attempt frames it.
    margin = 12
    image = image.crop((margin, margin,
                        image.width - margin, image.height - margin))
    image = image.resize((image.width + 2 * margin, image.height + 2 * margin),
                         Image.BICUBIC)

    image = ImageEnhance.Brightness(image).enhance(BRIGHTNESS)
    image = ImageEnhance.Contrast(image).enhance(CONTRAST)
    image = image.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="the bill image to photograph again")
    parser.add_argument("--out", default="", help="where to write it")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    out = Path(args.out).expanduser() if args.out else \
        source.with_name(f"{source.stem}_retaken.jpg")

    retake(source, out)

    before = hashlib.sha256(source.read_bytes()).hexdigest()
    after = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"original {source.name}: {before[:16]}")
    print(f"retaken  {out.name}: {after[:16]}")
    print(f"different image: {before != after}")
    print("\nSame paper, so the same bill number. The image hash cannot see "
          "that; the bill number can.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
