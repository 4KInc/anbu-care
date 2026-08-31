#!/usr/bin/env python3
"""Spare photographs of the same document, one per recording.

Document dedupe is keyed on the parent and the IMAGE HASH, and a fresh case
does not reset it, so the same file sent twice is refused as already recorded.
On camera that refusal is correct and ruinous: beat 7 says "nothing was
ingested" in the middle of a beat about a document being ingested. One image
per run-through, rehearsals included, is the only way through that.

So this makes a stack of spares. Not bytes tweaked to move a hash: each is a
plausible second photograph of the SAME piece of paper, held at a slightly
different angle, lit a little differently, focused a little differently, and
saved the way a phone saves. The words on it are identical, because it is the
same document, and that is the point. What changes is only what changes when
somebody picks a sheet up and photographs it again.

    ./.venv/bin/python scripts/make_document_takes.py --count 10
    ./.venv/bin/python scripts/make_document_takes.py \
        --source ~/Desktop/bill_interim_day_four.png --count 6

Every output is checked against every other output AND against everything
already sitting in the target directory, because a spare that collides with a
take from last week is a spare that fails in exactly the situation it exists
for.

BILLS DEDUPE PER CASE and documents dedupe per PARENT, so one bill image
replays across run-throughs while one discharge summary does not. Spares still
matter for bills: photographing the same bill twice inside a single take is
refused, correctly, and that refusal lands in the middle of the money beat.

IMAGES ONLY, deliberately. The reader accepts jpeg, png, webp, heic and heif,
and the inbound classifier accepts audio or image and refuses anything else
rather than guessing. A PDF spare would be a file that fails at the door, which
is worse than no spare at all.
"""

from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

DEFAULT_OUT = Path.home() / "Desktop" / "anbu-demo"


def photograph(source: Image.Image, rng: random.Random) -> Image.Image:
    """The same paper, picked up and photographed again.

    Ranges are deliberately narrow. A spare nobody could read would exercise
    the unreadable path instead, which is a different beat and a worse one.
    """
    image = source.rotate(rng.uniform(-1.1, 1.1), resample=Image.BICUBIC,
                          fillcolor=(255, 255, 255), expand=False)

    # Framed a little tighter or looser, the way a second attempt is.
    margin = rng.randint(6, 20)
    image = image.crop((margin, margin, image.width - margin, image.height - margin))
    image = image.resize((image.width + 2 * margin, image.height + 2 * margin),
                         Image.BICUBIC)

    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.96, 1.09))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.90, 1.06))
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
    return image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="", help="the discharge summary to rephotograph")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="where the spares go")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--start", type=int, default=0,
                    help="first take number; 0 continues past what is already there")
    ap.add_argument("--seed", type=int, default=0, help="0 means genuinely random")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    source = Path(args.source).expanduser() if args.source else out / "discharge_summary.png"
    if not source.exists():
        print(f"no source at {source}. Make one first:\n"
              f"  ./.venv/bin/python scripts/make_documents.py --out {out}")
        return 1

    # Every hash already in the directory, so a new spare cannot collide with
    # one from a previous batch.
    seen: dict[str, str] = {}
    for existing in sorted(out.iterdir()):
        if existing.is_file() and existing.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            seen[hashlib.sha256(existing.read_bytes()).hexdigest()] = existing.name

    start = args.start
    if start <= 0:
        used = [int(p.stem.rsplit("take", 1)[1])
                for p in out.glob(f"{source.stem}_take*{'.jpg'}")
                if p.stem.rsplit("take", 1)[-1].isdigit()]
        start = (max(used) + 1) if used else 2

    rng = random.Random(args.seed or None)
    base = Image.open(source).convert("RGB")
    made: list[Path] = []

    for i in range(args.count):
        number = start + i
        target = out / f"{source.stem}_take{number}.jpg"
        for attempt in range(12):
            image = photograph(base, rng)
            # Quality varies too, so two takes that happened to land on the
            # same geometry still differ in bytes.
            image.save(target, "JPEG", quality=rng.randint(78, 92))
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest not in seen:
                seen[digest] = target.name
                made.append(target)
                print(f"  {target.name:<38} {digest[:16]}  "
                      f"{target.stat().st_size / 1024:.0f} KB")
                break
            if attempt == 11:
                print(f"  {target.name}: could not make one distinct from "
                      f"{seen[digest]}, skipped")
                target.unlink(missing_ok=True)

    print(f"\n{len(made)} new spares in {out}")
    print(f"{len(seen)} distinct images in that directory, so none of them is "
          "a duplicate of another.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
