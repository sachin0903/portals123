-- ============================================================================
-- 0110_nanoid.sql -- nanoid() in the database, so a `uid` has ONE shape whoever mints it.
--
-- `app/ids.py` is the application's minter: nanoid, length 12, over the 62 alphanumeric
-- alphabet (`A-Za-z0-9`), no prefix. Every `uid` column is `text NOT NULL UNIQUE`, so the
-- application has always been able to satisfy them -- but only the application. A row inserted by
-- anything else (a backfill in a migration, a provider pipeline writing as `book_writer`, an
-- operator in psql) had to remember to mint one by hand, and the expressions that grew up to do
-- that had drifted into three shapes: `encode(gen_random_bytes(8), 'hex')` in the merge
-- procedures, prefixed hex (`cnv_…`) in messaging, and the real thing in Python. Two of those are
-- not nanoid and one of them is not even the same LENGTH, so "what does a uid look like" had no
-- single answer and a column default could not be written without picking one.
--
-- This is the function, so the answer is `nanoid(12)` everywhere and `0113_uid_defaults` can say
-- so once for every column. The application keeps minting its own: `ids.new_id()` stays the path
-- every INSERT in `app/` uses, because a value the application chose is a value it can log,
-- return and test against, where a value the database chose has to be read back. The default is
-- the floor under the writers that are not the application, not a replacement for it.
--
-- Source: https://github.com/viascom/nanoid-postgres (Apache 2.0), vendored verbatim apart from
-- three deliberate changes:
--   * the script's own BEGIN/COMMIT are removed -- Alembic already runs each migration in a
--     transaction, and a COMMIT here would end it half way through the upgrade;
--   * the functions are created in `public` explicitly rather than relying on the migrator's
--     search_path, because a column DEFAULT resolves the function at DDL time and a default that
--     resolved against the wrong schema is a broken INSERT nobody sees until a row is written;
--   * `CREATE EXTENSION IF NOT EXISTS pgcrypto` is kept but is a no-op here -- pgcrypto is
--     already installed (the merge procedures have used `gen_random_bytes` since 0065).
--
-- The two DROPs the upstream script performs are deliberately NOT carried over: they exist to
-- upgrade an older nanoid installation, and this database has none -- while a DROP of a function a
-- column default depends on would be refused and roll the whole migration back.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- A compact, URL-friendly random identifier. `size` symbols drawn from `alphabet`, with an
-- optional prefix that does not count towards the size.
CREATE OR REPLACE FUNCTION public.nanoid(
    size int DEFAULT 21,
    -- OURS, not nanoid's: its default set minus `_` and `-`. Both are URL-safe but not safe
    -- everywhere a uid ends up -- `app/lake/holdings.py` and `app/platform_entities.py` interpolate
    -- one into a SQL identifier behind an `^[A-Za-z0-9_]+$` guard that refuses `-`. Every caller
    -- passes `size` alone, so this default IS the platform's alphabet; `app/ids.py` mints the same
    -- 62 and `tests/test_public_ids.py` reads this signature to keep the two in step.
    alphabet text DEFAULT '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
    additionalBytesFactor float DEFAULT 1.6,
    prefix text DEFAULT ''
)
    RETURNS text
    LANGUAGE plpgsql
    VOLATILE
    -- PL/pgSQL takes a new snapshot when it evaluates a volatile expression, which is forbidden
    -- inside a parallel worker ("cannot start commands during a parallel operation").
    PARALLEL UNSAFE
AS
$$
DECLARE
    alphabetLength int;
    cutoff         int;
    step           int;
BEGIN
    IF size IS NULL OR size < 1 THEN
        RAISE EXCEPTION 'The size must be defined and greater than 0!';
    END IF;

    IF alphabet IS NULL OR length(alphabet) = 0 OR length(alphabet) > 256 THEN
        RAISE EXCEPTION 'The alphabet can''t be undefined, zero or bigger than 256 symbols!';
    END IF;

    IF additionalBytesFactor IS NULL OR additionalBytesFactor < 1 THEN
        RAISE EXCEPTION 'The additional bytes factor can''t be less than 1!';
    END IF;

    alphabetLength := length(alphabet);
    -- Random bytes are 0-255. `byte % alphabetLength` would make some symbols likelier whenever
    -- 256 is not a multiple of the alphabet length, so bytes at or above `cutoff` are rejected
    -- instead and every symbol keeps an equal chance. This is LIVE for our own alphabet, not
    -- merely a guard for a caller passing its own: 256 % 62 = 8, so the cutoff is 248 and roughly
    -- 3% of bytes are redrawn. It was dead at 64 symbols, which is why the margin below matters.
    cutoff := 256 - (256 % alphabetLength);
    -- On average 256/cutoff bytes are needed per symbol; the factor is the margin for an unlucky
    -- run of rejections. Capped at 1024 because that is gen_random_bytes' limit per call.
    step := cast(least(1024, ceil(additionalBytesFactor * 256 * size / cutoff)) AS int);

    RETURN coalesce(prefix, '') || public.nanoid_optimized(size, alphabet, cutoff, step);
END
$$;

-- The generator itself. Validates only what it must to be sure of terminating: the loop's one exit
-- is reached after a symbol has been appended, so a zero size or an empty alphabet would spin for
-- ever. Everything else is the caller's business -- `nanoid()` above is the checked door.
CREATE OR REPLACE FUNCTION public.nanoid_optimized(
    size int,
    alphabet text,
    cutoff int,
    step int
)
    RETURNS text
    LANGUAGE plpgsql
    VOLATILE
    PARALLEL UNSAFE
AS
$$
DECLARE
    idBuilder      text := '';
    counter        int  := 0;
    bytes          bytea;
    randomByte     int;
    alphabetArray  text[];
    alphabetLength int;
BEGIN
    IF size IS NULL OR size < 1 THEN
        RAISE EXCEPTION 'The size must be defined and greater than 0!';
    END IF;

    IF alphabet IS NULL OR length(alphabet) = 0 THEN
        RAISE EXCEPTION 'The alphabet can''t be undefined or zero!';
    END IF;

    IF cutoff IS NULL OR cutoff < 1 OR step IS NULL OR step < 1 THEN
        RAISE EXCEPTION 'The cutoff and step must be defined and greater than 0!';
    END IF;

    alphabetArray := regexp_split_to_array(alphabet, '');
    alphabetLength := array_length(alphabetArray, 1);

    LOOP
        bytes := gen_random_bytes(step);
        FOR counter IN 0..step - 1
            LOOP
                randomByte := get_byte(bytes, counter);
                IF randomByte < cutoff THEN
                    idBuilder := idBuilder || alphabetArray[(randomByte % alphabetLength) + 1];
                    IF length(idBuilder) = size THEN
                        RETURN idBuilder;
                    END IF;
                END IF;
            END LOOP;
    END LOOP;
END
$$;

COMMENT ON FUNCTION public.nanoid(int, text, float, text) IS
  'A public identifier. Call it as nanoid(12) -- the platform-wide shape, matching '
  'app/ids.py::new_id (12 symbols of the 62 alphanumerics -- nanoid''s default set '
  'minus `_` and `-` -- no prefix). '
  'Vendored from https://github.com/viascom/nanoid-postgres (Apache 2.0).';
