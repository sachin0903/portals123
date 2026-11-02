"""Turning a picture into a PNG — for an email, and for an upload type the store never kept.

TWO JOBS, ONE CONVERTER.

* **Email.** A mail client draws PNG, JPEG and GIF inline. Anything else a firm's logo can be stored
  as — SVG, WebP, ICO — arrives as a FILE ATTACHMENT in Outlook (the attachment chip above the
  message), and an SVG is also a broken picture in the body. So a logo or icon in one of those
  types gets a PNG copy for email (``email_png``), stored beside the original at upload and made in
  memory at send when an older upload has none (``app/email/transport.py``). The original is never
  touched: the portals keep drawing the file the firm uploaded.
* **Upload.** GIF, AVIF, BMP, TIFF and HEIC/HEIF are accepted as logos but never stored as
  themselves. The portals cannot draw TIFF or HEIC, a BMP is uncompressed pixels far over the asset
  cap, and an iPhone's HEIC carries the photo's location in its metadata — which must not land in
  the bucket's anonymous-read prefix. Each is stored as the PNG of its first frame instead
  (``to_web``), so the stored types, and the five places that spell the asset-name grammar, stay
  exactly as they were.

SVG is drawn by CairoSVG (``_render_svg``), on the Cairo library the API and worker images already
carry for WeasyPrint. The branding studio also draws an SVG logo's copy in the browser at upload
and sends it with the file (``pkgs/ui`` ``branding/email-copy.ts``) — preferred when present, and
what still makes a copy on a machine without Cairo (a Windows dev box), where ``svg_supported``
is False and an SVG simply has no copy.

Pure: bytes in, bytes out. No storage, no session, no I/O — callers run it with
``asyncio.to_thread``, because decoding an image is CPU work on the event loop otherwise.
"""

from __future__ import annotations

import logging
import re
from functools import cache
from io import BytesIO
from typing import TYPE_CHECKING, Any

from app import storage

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The stored extensions a mail client draws inline, and so may travel in an email as they are.
#: Everything else a branding asset can be (svg, webp, ico) needs an email copy.
MAILABLE_EXTS = frozenset({"png", "jpg", "jpeg", "gif"})
#: The same, as content types — what an attachment may be.
MAILABLE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif"})


#: Picture types a mail client is known NOT to draw inline. For an absolute URL a firm typed in,
#: which may have no extension at all (a CDN path) — only these are refused, so an extension-less
#: URL keeps working exactly as it did.
_UNMAILABLE_EXTS = frozenset(
    {"svg", "svgz", "webp", "ico", "avif", "heic", "heif", "tif", "tiff", "bmp"}
)


def _ext(name_or_url: str) -> str:
    """The lower-case extension of the file a name or URL names ('' when it has none); query and
    fragment ignored."""
    tail = name_or_url.split("#", 1)[0].split("?", 1)[0].rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[1].lower() if "." in tail else ""


def is_mailable(name: str) -> bool:
    """Whether a stored asset ``name`` (which always has an extension) is a type a mail client draws
    inline."""
    return _ext(name) in MAILABLE_EXTS


def is_known_unmailable(url: str) -> bool:
    """Whether ``url`` names a picture type a mail client is known not to draw inline."""
    return _ext(url) in _UNMAILABLE_EXTS


def mailable_type(data: bytes) -> str | None:
    """The content type of ``data`` when its BYTES are a picture a mail client draws inline, else
    None. The name is not enough: a logo saved from a website as `logo.png` is often a WebP, and the
    store keeps it under the name and type the browser gave it."""
    try:
        kind = storage.sniff_document_type(data)
    except ValueError:
        return None
    return kind if kind in MAILABLE_TYPES else None


#: Upload types the branding store has never kept, accepted by storing them as a PNG (or a JPEG —
#: see ``to_web``). Detected from the BYTES (``storage.sniff_document_type``), never from the
#: declared Content-Type: a browser labels a HEIC as ``image/heic``, ``image/heif`` or nothing.
CONVERTED_UPLOAD_TYPES = frozenset(
    {"image/gif", storage.AVIF, "image/bmp", "image/tiff", storage.HEIC, storage.HEIF}
)
_HEIF_TYPES = frozenset({storage.HEIC, storage.HEIF})

#: The box an email copy is fitted inside, aspect kept, never enlarged. The masthead is 28px tall
#: and at most 220px wide (``email/shell.py::logo_wordmark``); 256px tall is 9x that, room for a
#: square mark an author places as a larger badge. 640px wide holds a wide wordmark at the masthead
#: height with retina to spare (a 1740x312 lockup lands at 640x115). Small on purpose: every byte
#: is in every message.
EMAIL_COPY_BOX = (640, 256)

#: A picture larger than this is refused before it is decoded. Pillow's own bomb guard sits at
#: ~179 megapixels and only WARNS from ~89; a branding image never needs a fraction of that, and
#: decoding one is a few hundred megabytes of memory per upload.
MAX_PIXELS = 40_000_000


@cache
def _register_heif() -> None:
    """Teach Pillow HEIC/HEIF, once per process and only when one is actually opened — so a
    process that never sees an iPhone photo never loads the decoder."""
    import pi_heif

    pi_heif.register_heif_opener()


def sniff_convertible(data: bytes) -> str | None:
    """The type of ``data`` when it is one of ``CONVERTED_UPLOAD_TYPES``, else None.

    Read from the magic bytes by the same sniffer the document uploads use. SVG, text, Office files
    and anything unrecognised are all None — this only ever answers "is it a picture we turn into a
    PNG", never "is it a picture"."""
    try:
        kind = storage.sniff_document_type(data)
    except ValueError:
        return None
    return kind if kind in CONVERTED_UPLOAD_TYPES else None


@cache
def _cairosvg() -> Any | None:
    """The CairoSVG module, or None where the Cairo library it drives is not installed — the API
    and worker images carry it (WeasyPrint needs it); a Windows dev box usually does not, and
    importing CairoSVG there raises ``OSError`` rather than ``ImportError``."""
    try:
        import cairosvg
    except (ImportError, OSError):
        logger.warning("CairoSVG is unavailable (no Cairo library): SVG logos get no email copy")
        return None
    return cairosvg


def svg_supported() -> bool:
    """Whether this process can draw an SVG."""
    return _cairosvg() is not None


#: The root `<svg ...>` start tag, after any XML declaration, comment or doctype. Read by regex, not
#: an XML parser: only the size is wanted here, and CairoSVG does the real (hardened) parse.
_SVG_ROOT = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE)
_SVG_PREAMBLE = re.compile(
    rb"\A(?:\s|<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>)*<svg\b", re.S | re.I
)
_PLAIN_LENGTH = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*")


def is_svg(data: bytes) -> bool:
    """Whether ``data`` is an SVG document: an optional UTF-8 BOM, the preamble, then `<svg`."""
    return _SVG_PREAMBLE.match(data.removeprefix(b"\xef\xbb\xbf")[:65536]) is not None


def _svg_attr(tag: bytes, name: str) -> str | None:
    m = re.search(rb"\s" + name.encode() + rb"""\s*=\s*(["'])(.*?)\1""", tag, re.S)
    return m.group(2).decode("utf-8", "replace") if m else None


def _svg_size(data: bytes) -> tuple[float, float] | None:
    """The SVG's own width and height: plain ``width``/``height`` when both are given, else its
    ``viewBox`` (an Illustrator export carries only that). Mirrors ``svgSize`` in the studio's
    ``email-copy.ts``."""
    m = _SVG_ROOT.search(data[:65536])
    if m is None:
        return None
    tag = m.group(0)

    def plain(value: str | None) -> float | None:
        n = _PLAIN_LENGTH.fullmatch(value or "")
        return float(n.group(1)) if n and float(n.group(1)) > 0 else None

    width, height = plain(_svg_attr(tag, "width")), plain(_svg_attr(tag, "height"))
    if width and height:
        return width, height
    box = [p for p in re.split(r"[\s,]+", (_svg_attr(tag, "viewBox") or "").strip()) if p]
    try:
        vw, vh = float(box[2]), float(box[3])
    except (IndexError, ValueError):
        return None
    if len(box) != 4 or vw <= 0 or vh <= 0:
        return None
    if width:
        return width, width * vh / vw
    if height:
        return height * vw / vh, height
    return vw, vh


def _render_svg(data: bytes) -> PILImage:
    """An SVG drawn by CairoSVG as an RGBA ``Image``, at twice the email box (so a trimmed border
    still leaves pixels to spare). Raises ``ValueError`` when it cannot be drawn here.

    ``unsafe=False`` is the safety: CairoSVG then loads ONLY images embedded in the file
    (``data:`` URLs) — never a local file, never a network URL — and refuses XML entities. No
    ``url_fetcher`` is passed, because passing one replaces that safe default."""
    from PIL import Image

    cairosvg = _cairosvg()
    if cairosvg is None:
        raise ValueError("no SVG renderer on this machine")
    box_w, box_h = EMAIL_COPY_BOX[0] * 2, EMAIL_COPY_BOX[1] * 2
    size = _svg_size(data)
    # Bound the long side rather than letting a thin, wide SVG come out tens of thousands of pixels
    # across: fit the drawing to the box by whichever side is the tighter.
    if size and size[0] / size[1] > box_w / box_h:
        target: dict[str, int] = {"output_width": box_w}
    else:
        target = {"output_height": box_h}
    try:
        png = cairosvg.svg2png(bytestring=data, unsafe=False, **target)
    except Exception as exc:
        # CairoSVG raises whatever its parser or Cairo raises; all of it means "cannot draw".
        raise ValueError("SVG could not be drawn") from exc
    with Image.open(BytesIO(png)) as im:
        rgba = im.convert("RGBA")
        rgba.load()
        return rgba


def _first_frame_rgba(data: bytes) -> PILImage:
    """The first frame of ``data`` as an upright RGBA ``Image`` — an SVG drawn by CairoSVG,
    anything else decoded by Pillow. Raises ``ValueError`` when the bytes are not a picture that
    can be read here, or are too large to decode."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if is_svg(data):
        return _render_svg(data)
    if sniff_convertible(data) in _HEIF_TYPES:
        _register_heif()
    try:
        with Image.open(BytesIO(data)) as im:
            width, height = im.size
            if width * height > MAX_PIXELS:
                raise ValueError(f"image is too large ({width}x{height})")
            # EXIF orientation first: a phone photo of a logo is stored sideways and rotated on
            # display. `Image.open` sits on frame 0, so an animated GIF yields its first frame.
            upright = ImageOps.exif_transpose(im) or im
            if upright.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
                # 16-bit and float greyscale (scanner TIFFs) have no direct RGBA conversion:
                # scale to 8 bits first.
                upright = upright.point(lambda v: v / 256).convert("L")
            rgba = upright.convert("RGBA")
            rgba.load()
            return rgba
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, EOFError) as exc:
        raise ValueError("image could not be read") from exc


#: Alpha at or below this counts as empty when trimming an email copy's border — a faint haze
#: left by anti-aliasing or a soft shadow must not hold the border open.
_TRIM_ALPHA = 8


def _trim_transparent_border(rgba: PILImage) -> PILImage:
    """``rgba`` cropped to the pixels that show. The masthead draws a picture at a fixed height
    (28px, ``email/shell.py::logo_wordmark``), so empty canvas round a logo shrinks the logo by the
    same amount: a wordmark inside a square icon file came out a few pixels tall. An opaque picture
    — or one with no empty border — is returned as it is."""
    alpha = rgba.getchannel("A").point(lambda a: 255 if a > _TRIM_ALPHA else 0)
    bbox = alpha.getbbox()
    if bbox is None or bbox == (0, 0, *rgba.size):
        return rgba
    return rgba.crop(bbox)


def to_png(data: bytes, box: tuple[int, int], *, trim: bool = False) -> bytes:
    """``data`` as a PNG fitted inside ``box`` — aspect kept, never enlarged, transparency kept.
    ``trim`` first crops away an empty (transparent) border.

    Raises ``ValueError`` when the bytes are not a picture Pillow can read."""
    from PIL import Image

    rgba = _first_frame_rgba(data)
    if trim:
        rgba = _trim_transparent_border(rgba)
    rgba.thumbnail(box, Image.Resampling.LANCZOS)
    out = BytesIO()
    rgba.save(out, "PNG", optimize=True)
    return out.getvalue()


#: The JPEG qualities ``to_web`` tries, best first, until one fits the store's cap.
_JPEG_QUALITIES = (95, 85, 75, 60)


def to_web(data: bytes, box: tuple[int, int], *, max_bytes: int) -> tuple[bytes, str]:
    """``(bytes, content_type)`` of an upload in a type the branding store keeps.

    A PNG, unless the PNG is over ``max_bytes`` and the picture is fully opaque — a photograph,
    which PNG stores badly — in which case a JPEG, at the highest quality that fits ``max_bytes``
    (a phone photo used as card artwork is a few MB). A transparent picture stays a PNG at any size
    (a JPEG would paint its transparent area black) and the store's own cap then answers for it, as
    it does for a JPEG that fits at no quality.

    Raises ``ValueError`` when the bytes are not a picture Pillow can read."""
    from PIL import Image

    rgba = _first_frame_rgba(data)
    rgba.thumbnail(box, Image.Resampling.LANCZOS)
    out = BytesIO()
    rgba.save(out, "PNG", optimize=True)
    if out.tell() <= max_bytes:
        return out.getvalue(), "image/png"
    # The alpha band's lowest value: anything under 255 is a pixel a JPEG would paint black.
    if rgba.getchannel("A").getextrema()[0] != 255:
        return out.getvalue(), "image/png"
    rgb = rgba.convert("RGB")
    for quality in _JPEG_QUALITIES:
        out = BytesIO()
        # Full chroma at the top quality, as `shell_images` stores a JPEG logo, so lettering stays
        # sharp; the steps below are for photographs, where subsampling costs nothing visible. No
        # `optimize`: on a noisy photograph Pillow's one-shot optimising encoder overruns its
        # buffer into a BytesIO ("broken data stream"), and it saves a few per cent at best.
        rgb.save(out, "JPEG", quality=quality, subsampling=0 if quality >= 95 else 2)
        if out.tell() <= max_bytes:
            break
    return out.getvalue(), "image/jpeg"


def email_png(data: bytes) -> bytes | None:
    """``data`` as the PNG an email carries — its empty border trimmed, fitted inside
    ``EMAIL_COPY_BOX`` — or None.

    Never raises: a picture that cannot be made into an email copy is a missing copy, and the send
    path already knows what to do without one (the icon, or the firm's name in type)."""
    try:
        return to_png(data, EMAIL_COPY_BOX, trim=True)
    except ValueError as exc:
        logger.info("no email copy: %s", exc)
        return None
