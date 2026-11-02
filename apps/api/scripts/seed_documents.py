"""Seed sample documents into a local tenant (dev only).

Uploads a few small real PDFs to the private documents bucket (RustFS) and inserts the matching
documents.files + documents.documents + documents.audit_log rows, so the documents surface shows
real, storage-backed data. Idempotent: seeded rows carry metadata {"seed": true} and are cleared
before re-seeding.

Run:  uv run --directory apps/api python scripts/seed_documents.py [tenant_alias]
Requires: RustFS up (pnpm storage:up) + S3_* set in apps/api/.env.local + migrations applied.
"""

import asyncio
import hashlib
import struct
import sys
import zlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import storage
from app.config import settings
from app.ids import new_id

DEFAULT_TENANT_ALIAS = "graphene"

# (display name, document_type/classification, source, format: "pdf" | "image"). ID-verification
# scans are photos of a physical document in real life (phone camera / scanner), not text-native
# PDFs — seeded as images so the doc viewer's image branch (not its PDF branch) actually gets
# exercised, and so the type icon/format label are honest about what these really are.
SAMPLES = [
    ("Suitability Report — Q1 2026", "suitability_report", "generated", "pdf"),
    ("Annual Review Summary 2025", "annual_review", "generated", "pdf"),
    ("Client Agreement (signed)", "client_agreement", "uploaded", "pdf"),
    ("Fact Find", "fact_find", "uploaded", "pdf"),
    ("Pension Transfer Illustration", "illustration", "generated", "pdf"),
    ("ID Verification — Passport", "id_verification", "uploaded", "image"),
    ("ID Verification — Driving Licence", "id_verification", "uploaded", "image"),
]


def _make_pdf(title: str) -> bytes:
    """A minimal, valid single-page PDF (renders in browsers) with `title` drawn on it."""
    safe = title.replace("(", r"\(").replace(")", r"\)")
    text_content = f"BT /F1 24 Tf 72 700 Td ({safe}) Tj ET".encode()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(text_content), text_content),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref_pos)
    return bytes(out)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _make_id_scan_png(card_colour: tuple[int, int, int]) -> bytes:
    """A minimal, valid PNG (no Pillow needed — hand-rolled IHDR/IDAT/IEND) that looks like a
    photographed ID card: a photo silhouette on the left, a few text-line bars on the right, and
    an MRZ-style strip along the bottom. Just enough for the doc viewer's image preview to show
    something recognisable rather than a blank rectangle."""
    w, h = 440, 280
    bg = (250, 246, 238)
    photo = (90, 96, 108)
    line = (150, 150, 150)
    mrz = (40, 40, 45)

    def in_box(x: int, y: int, box: tuple[int, int, int, int]) -> bool:
        x0, y0, x1, y1 = box
        return x0 <= x < x1 and y0 <= y < y1

    photo_box = (32, 32, 168, 216)
    line_boxes = [(196, 56, 396, 70), (196, 92, 356, 106), (196, 128, 336, 142)]
    mrz_boxes = [(24, 232, 416, 244), (24, 250, 416, 262)]

    rows = bytearray()
    for y in range(h):
        rows.append(0)  # filter type: none
        for x in range(w):
            px = bg
            if in_box(x, y, photo_box):
                px = photo
            elif any(in_box(x, y, b) for b in line_boxes):
                px = line
            elif any(in_box(x, y, b) for b in mrz_boxes):
                px = mrz
            elif x < 6 or x >= w - 6 or y < 6 or y >= h - 6:
                px = card_colour  # a coloured border, so different samples look distinct
            rows += bytes(px)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", ihdr)
    png += _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += _png_chunk(b"IEND", b"")
    return png


async def main(tenant_alias: str) -> None:
    if not storage.documents_configured():
        raise SystemExit("S3_DOCUMENTS_BUCKET not configured — set S3_* in apps/api/.env.local")

    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        row = (
            await s.execute(
                text("SELECT id, uid FROM auth.tenants WHERE alias = :a"), {"a": tenant_alias}
            )
        ).one_or_none()
        if row is None:
            raise SystemExit(f"No tenant with alias '{tenant_alias}'")
        tenant_id, _tenant_uid = int(row[0]), row[1]
        actor = (
            await s.execute(
                text(
                    "SELECT id FROM auth.users "
                    "WHERE tenant_id = :t AND email ILIKE '%craig%' LIMIT 1"
                ),
                {"t": tenant_id},
            )
        ).scalar_one_or_none()
        # Client contacts to attach the seeded documents to (round-robin), so each client's
        # Activity tab has a real trail. Empty on a firm with no CRM contacts seeded yet.
        contacts = (
            (
                await s.execute(
                    text("SELECT id FROM crm.contacts WHERE tenant_id = :t ORDER BY id"),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all()
        )

        # Idempotent: drop previously-seeded docs (and their blobs' rows) before re-seeding.
        old_files = (
            (
                await s.execute(
                    text(
                        "SELECT DISTINCT unnest("
                        "  array_remove(ARRAY[original_file_id, pdf_file_id], NULL)) "
                        "FROM documents.documents "
                        "WHERE tenant_id = :t AND metadata->>'seed' = 'true'"
                    ),
                    {"t": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        # Clear seed audit events first: audit_log doesn't cascade on document delete, so without
        # this a re-seed leaves the previous run's events orphaned (no document) on the audit page.
        await s.execute(
            text(
                "DELETE FROM documents.audit_log WHERE tenant_id = :t AND detail->>'seed' = 'true'"
            ),
            {"t": tenant_id},
        )
        await s.execute(
            text(
                "DELETE FROM documents.documents "
                "WHERE tenant_id = :t AND metadata->>'seed' = 'true'"
            ),
            {"t": tenant_id},
        )
        if old_files:
            await s.execute(
                text("DELETE FROM documents.files WHERE id = ANY(:ids)"), {"ids": old_files}
            )

        # A distinct border colour per image sample, purely so they're visually distinguishable
        # from one another when browsing.
        id_scan_colours = [(20, 110, 130), (150, 90, 40)]
        image_i = 0
        for i, (name, dtype, source, fmt) in enumerate(SAMPLES):
            is_image = fmt == "image"
            if is_image:
                data = _make_id_scan_png(id_scan_colours[image_i % len(id_scan_colours)])
                image_i += 1
                ext, content_type = "png", "image/png"
            else:
                data = _make_pdf(name)
                ext, content_type = "pdf", "application/pdf"
            doc_uid = new_id()
            key = storage.upload_key(str(tenant_id), doc_uid, ext)
            await storage.put_document(key, data, content_type)
            file_id = (
                await s.execute(
                    text(
                        "INSERT INTO documents.files "
                        "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                        "VALUES (:t, :b, :k, :ct, :sz, :sha) RETURNING id"
                    ),
                    {
                        "t": tenant_id,
                        "b": settings.s3_documents_bucket,
                        "k": key,
                        "ct": content_type,
                        "sz": len(data),
                        "sha": hashlib.sha256(data).hexdigest(),
                    },
                )
            ).scalar_one()
            # Uploaded ID scans have no separate "PDF rendition" — only a generated document (via
            # the template pipeline) has pdf_file_id; an uploaded image just has original_file_id
            # (the read path already prefers original_file_id, falling back to pdf_file_id only
            # when it's null).
            await s.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, document_type, source, name, status, "
                    "   original_file_id, pdf_file_id, generated_by, metadata) "
                    "VALUES (:uid, :t, :dt, :src, :nm, 'generated', "
                    "   :f, :pf, :by, '{\"seed\": true}'::jsonb)"
                ),
                {
                    "uid": doc_uid,
                    "pf": None if is_image else file_id,
                    "t": tenant_id,
                    "dt": dtype,
                    "src": source,
                    "nm": name,
                    "f": file_id,
                    "by": actor,
                },
            )
            await s.execute(
                text(
                    "INSERT INTO documents.audit_log "
                    "  (tenant_id, document_id, actor_id, action, detail) "
                    "SELECT :t, d.id, :by, 'document.uploaded', '{\"seed\": true}'::jsonb "
                    "FROM documents.documents d WHERE d.uid = :uid"
                ),
                {"t": tenant_id, "by": actor, "uid": doc_uid},
            )
            # Attach to a client + record a send, so the client's Activity tab is populated (the feed
            # reads document_contacts for non-send events, and document.sent's recipient_id for sends).
            if contacts:
                contact_id = int(contacts[i % len(contacts)])
                await s.execute(
                    text(
                        "INSERT INTO documents.document_contacts "
                        "  (document_id, tenant_id, contact_id, role, added_by) "
                        "SELECT d.id, :t, :c, 'recipient', :by "
                        "FROM documents.documents d WHERE d.uid = :uid "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"t": tenant_id, "c": contact_id, "by": actor, "uid": doc_uid},
                )
                await s.execute(
                    text(
                        "INSERT INTO documents.audit_log "
                        "  (tenant_id, document_id, actor_id, action, detail) "
                        "SELECT :t, d.id, :by, 'document.sent', "
                        "  jsonb_build_object('seed', true, 'recipient_id', :c) "
                        "FROM documents.documents d WHERE d.uid = :uid"
                    ),
                    {"t": tenant_id, "by": actor, "c": contact_id, "uid": doc_uid},
                )

    await engine.dispose()
    print(f"Seeded {len(SAMPLES)} documents into tenant '{tenant_alias}' (id {tenant_id}).")


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    alias = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TENANT_ALIAS
    asyncio.run(main(alias))
