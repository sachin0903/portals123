-- ============================================================================
-- 0231_integrations_schema -- the `integrations` schema: SEI platform approvals, DSR grants,
-- per-platform feature toggles, job-run history, the SEI<->Intelliflo Office adviser map, and
-- the sei_reports integration engine/webhook's own tables on top (income statements, its
-- migration tracker).
--
-- WHY THIS LANDS HERE, NOW: this schema was built on `feat/porting-integrations-from-platform`
-- (this repo's own admin-only "Integrations" screen), which never merged before `sei_reports`
-- also started writing to the same `integrations.*` tables in the same `portals` database. Its
-- own login (`app` -- the same DML-bound role `apps/api` itself connects as, see
-- `bootstrap/01-roles.sql`) has no CREATE on this database, so its `CREATE SCHEMA IF NOT EXISTS
-- integrations` failed outright ("permission denied for database portals") the first time it
-- tried to self-provision. Content below is adapted, not written fresh, from sei_reports'
-- `src/integrations/migrations/intelliflo/manual/integrations_setup.sql` -- the engine's own
-- from-scratch setup script -- so the two repos agree on one shape instead of drifting into two.
-- Adaptation is mechanical only: the standalone BEGIN/COMMIT is dropped (Alembic already wraps
-- this file in one transaction) and the two accidentally-duplicated blocks in that source
-- (CREATE SCHEMA IF NOT EXISTS appeared twice; the `app_id`/`group_id`/`platform_id` ADD COLUMNs
-- appeared twice) are each collapsed to one. Everything else -- including every defensive IF/
-- EXCEPTION guard -- is kept, because this same file is also sei_reports' own disaster-recovery
-- script for a database that has none of this yet; weakening those guards for our one known
-- environment would just be a second shape to keep in sync.
--
-- WHO THIS GRANTS TO: `app` (set below) becomes a member of `service_role` if it is not
-- already one (it already is, via `01-roles.sql`'s `GRANT service_role TO app`, so that step is
-- a no-op here) -- the engine and the webhook connect as `app` and rely on inheriting
-- `service_role`'s grants without needing `SET ROLE` for ordinary object privileges (only
-- BYPASSRLS-gated behaviour needs the explicit SET ROLE, and this schema's tables carry zero
-- policies under FORCE RLS for exactly that reason -- access control is the admin API's
-- permission check, not a WHERE clause).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- >>> The login the engine and the webhook use (their PG_USER) -- `app`, same as apps/api's own
--     DATABASE_URL login; see the header above. <<<
SELECT set_config('integrations.engine_login', 'app', false);

DO $$
DECLARE engine_login text := current_setting('integrations.engine_login');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION 'The role service_role does not exist in this cluster';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = engine_login) THEN
        RAISE EXCEPTION 'The login % does not exist', engine_login;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'integrations')
       AND NOT has_database_privilege(current_user, current_database(), 'CREATE') THEN
        RAISE EXCEPTION '% may not create a schema in database %. Run this as a login that may, or have an admin run: GRANT CREATE ON DATABASE % TO %;',
            current_user, current_database(), current_database(), current_user;
    END IF;
    -- The engine and the webhook connect as this login and then rely on inheriting service_role's
    -- grants (see header).
    IF NOT pg_has_role(engine_login, 'service_role', 'member') THEN
        BEGIN
            EXECUTE format('GRANT service_role TO %I', engine_login);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE WARNING 'Could not make % a member of service_role (needs an admin). Run: GRANT service_role TO %;', engine_login, engine_login;
        END;
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS integrations;

-- ---------------------------------------------------------------------------- 1. tables, columns and keys
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'integrations' AND t.typname = 'platform_status'
    ) THEN
        CREATE TYPE integrations.platform_status AS ENUM ('pending', 'approved', 'rejected');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS integrations.firms (
    code        text        PRIMARY KEY,
    description text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid
);

CREATE TABLE IF NOT EXISTS integrations.features (
    code        text        PRIMARY KEY,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid
);

-- A DSR code is only unique PER FIRM.
CREATE TABLE IF NOT EXISTS integrations.dsrs (
    code        text NOT NULL,
    firm_id     text NOT NULL REFERENCES integrations.firms(code) ON DELETE RESTRICT,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid,
    PRIMARY KEY (code, firm_id)
);
CREATE INDEX IF NOT EXISTS dsrs_firm_id_idx ON integrations.dsrs (firm_id);

CREATE TABLE IF NOT EXISTS integrations.platforms (
    id               int  PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid              text NOT NULL UNIQUE DEFAULT public.nanoid(12),
    firm_id          text REFERENCES integrations.firms(code) ON DELETE SET NULL,
    platform_name    text,
    tenant_id        text NOT NULL,
    tenant_name      text NOT NULL,
    dsr_codes        text[] NOT NULL DEFAULT '{}',
    status           integrations.platform_status NOT NULL DEFAULT 'pending',
    enabled          boolean NOT NULL DEFAULT false,
    installed        boolean NOT NULL DEFAULT false,
    application_data jsonb,
    actioned_by      uuid,
    justification    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       uuid,
    UNIQUE (firm_id, platform_name, tenant_id),
    CONSTRAINT platforms_justification_on_reject CHECK (
        status != 'rejected' OR (justification IS NOT NULL AND length(btrim(justification)) > 0)
    )
);
CREATE INDEX IF NOT EXISTS platforms_firm_id_idx ON integrations.platforms (firm_id);
CREATE INDEX IF NOT EXISTS platforms_status_idx  ON integrations.platforms (status);

CREATE TABLE IF NOT EXISTS integrations.platform_features (
    platform_id  int  NOT NULL REFERENCES integrations.platforms(id) ON DELETE CASCADE,
    feature_code text NOT NULL REFERENCES integrations.features(code) ON DELETE RESTRICT,
    PRIMARY KEY (platform_id, feature_code)
);

CREATE TABLE IF NOT EXISTS integrations.job_runs (
    id            int  PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid           text NOT NULL UNIQUE DEFAULT public.nanoid(12),
    platform_id   int  NOT NULL REFERENCES integrations.platforms(id) ON DELETE CASCADE,
    run_date      timestamptz NOT NULL DEFAULT now(),
    feature       text NOT NULL,
    status        boolean NOT NULL DEFAULT false,
    error_message text,
    data          jsonb,
    response      jsonb,
    data_date     date
);
CREATE INDEX IF NOT EXISTS job_runs_platform_id_run_date_idx ON integrations.job_runs (platform_id, run_date DESC);

CREATE TABLE IF NOT EXISTS integrations.users (
    advisor_id         text PRIMARY KEY,
    display_name       text,
    login_user_id      text,
    login_phone_number text,
    firm_id            text REFERENCES integrations.firms(code) ON DELETE SET NULL,
    role_id            text,
    role_description   text,
    primary_flg        text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_firm_id_role_id_idx ON integrations.users (firm_id, role_id);

CREATE TABLE IF NOT EXISTS integrations.advisors_map (
    advisor_id_sei   text NOT NULL,
    advisor_id_io    text NOT NULL,
    advisor_name_sei text,
    advisor_name_io  text,
    approved         boolean,
    deleted          boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (advisor_id_sei, advisor_id_io)
);
CREATE INDEX IF NOT EXISTS advisors_map_sei_idx ON integrations.advisors_map (advisor_id_sei) WHERE NOT deleted;

-- Columns the admin screen and the engine each need on top of the base shape above.
ALTER TABLE integrations.platforms    ADD COLUMN IF NOT EXISTS provider_id   text;
ALTER TABLE integrations.platforms    ADD COLUMN IF NOT EXISTS provider_name text;
ALTER TABLE integrations.platforms    ADD COLUMN IF NOT EXISTS app_id        text;
ALTER TABLE integrations.platforms    ADD COLUMN IF NOT EXISTS group_id      text;
ALTER TABLE integrations.advisors_map ADD COLUMN IF NOT EXISTS platform_id   int
    REFERENCES integrations.platforms(id) ON DELETE CASCADE;

-- Foreign keys to auth.users, where it doesn't already have them (it's foundational, always
-- present by this point) -- the `uid` columns above already default to `public.nanoid(12)`
-- directly, portals' own one shape for a uid (0110_nanoid.sql), so there is nothing to backfill
-- here for those.
DO $$
DECLARE
    fk record;
BEGIN
    IF to_regclass('auth.users') IS NOT NULL THEN
        FOR fk IN SELECT * FROM (VALUES
            ('firms', 'updated_by'), ('features', 'updated_by'), ('dsrs', 'updated_by'),
            ('platforms', 'actioned_by'), ('platforms', 'updated_by')
        ) AS t(tbl, col)
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = format('integrations.%I', fk.tbl)::regclass AND conname = fk.tbl || '_' || fk.col || '_fkey'
            ) THEN
                EXECUTE format(
                    'ALTER TABLE integrations.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES auth.users(id) ON DELETE SET NULL',
                    fk.tbl, fk.tbl || '_' || fk.col || '_fkey', fk.col);
            END IF;
        END LOOP;
    END IF;
END $$;

-- ---------------------------------------------------------------------------- 2. the engine's own tables
-- The migration tracker sei_reports' own runner keeps.
CREATE TABLE IF NOT EXISTS integrations.schema_migrations (
    migration_id text PRIMARY KEY,
    executed_at  timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Income statement items already sent to Intelliflo, so a re-run never sends the same item twice.
-- Keyed by IO tenant id + the item's own key from the source data (not by platform id, whose
-- type differs between the engine's schema and this one).
CREATE TABLE IF NOT EXISTS integrations.income_statement_items_sent (
    tenant_id           text        NOT NULL,
    item_key            text        NOT NULL,
    income_statement_id text        NOT NULL,
    sent_at             timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, item_key)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT ON integrations.income_statement_items_sent TO service_role;
    END IF;
END $$;

-- Income statements pushed to Intelliflo. `id` is the reference (nano id) sent to IO; the row is
-- inserted after IO accepts the statement.
CREATE TABLE IF NOT EXISTS integrations.income_statements (
    id         text        NOT NULL PRIMARY KEY,
    tenant_id  text        NOT NULL,
    date_from  date        NOT NULL,
    date_to    date        NOT NULL,
    payload    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by text        NOT NULL DEFAULT 'system',
    -- Intelliflo's own numeric id for the statement (the `id` in the POST /v2/incomestatements
    -- response). `id` above stays the reference we generated and sent; Intelliflo's item routes
    -- (/v2/incomestatements/{id}/items) accept only its numeric id, not the reference.
    io_id      text
);

CREATE INDEX IF NOT EXISTS income_statements_tenant_valid_idx
    ON integrations.income_statements (tenant_id, date_to DESC);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT, INSERT ON integrations.income_statements TO service_role;
    END IF;
END $$;

-- ---------------------------------------------------------------------------- 3. reference data
-- On a re-run row-level security is already forced, which would stop the owner loading the data
-- below; lift it for these tables here. It is switched on again in the security section further
-- down.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['firms', 'features', 'dsrs'] LOOP
        IF (SELECT relforcerowsecurity FROM pg_class WHERE oid = format('integrations.%I', t)::regclass) THEN
            EXECUTE format('ALTER TABLE integrations.%I NO FORCE ROW LEVEL SECURITY', t);
        END IF;
    END LOOP;
END $$;

INSERT INTO integrations.features (code)
VALUES
    ('Valuations'),
    ('Pricing'),
    ('Transactions'),
    ('Reporting'),
    ('Analytics'),
    ('Notifications'),
    ('Deployments'),
    ('Calendar'),
    ('Email'),
    ('Clients')
ON CONFLICT (code) DO NOTHING;

INSERT INTO integrations.firms (code, description)
VALUES
    ('3112', 'Swissential'),
    ('3114', 'UCAP Asset Management (UK) Ltd'),
    ('3110', 'Concentric Financial Services Limited'),
    ('3102', 'Blackfinch Investments Limited'),
    ('3116', 'Chester Rose Financial Planning'),
    ('3106', 'Select Platform Limited'),
    ('3108', 'Alpha Investment Office')
ON CONFLICT (code) DO NOTHING;

INSERT INTO integrations.dsrs (code, firm_id, description)
VALUES
    ('704749', '3102', '1016304, Shouksmith Financial Planning Ltd'),
    ('704752', '3102', '1032068, Chester Rose Hampshire'),
    ('704868', '3102', '1039156, Fintrel Limited'),
    ('409861', '3102', '1040319, Clockswood Wealth'),
    ('704872', '3102', '1044629, Manning & Company (South West) Limited'),
    ('703908', '3102', '114650, Growth Invest'),
    ('703909', '3102', '114694, KW Wealth Planning Limited'),
    ('703910', '3102', '115045, Cestrian Financial Planning'),
    ('703913', '3102', '117931, MJB Partnership Ltd'),
    ('703914', '3102', '121066, Evans Hart Limited'),
    ('703916', '3102', '121153, Avonhurst Investment Brokers'),
    ('703917', '3102', '121212, Aurea Financial Planning'),
    ('703918', '3102', '123456, Direct Investments'),
    ('703919', '3102', '125116, Chalfont Investment Consulta'),
    ('703922', '3102', '125772, Thurlowe-Clarke Limited'),
    ('703923', '3102', '126350, A. G. Shackcloth and Company'),
    ('703924', '3102', '126457, Milsted Langdon Financial Se'),
    ('703928', '3102', '134677, Meldon + Co Ltd'),
    ('703929', '3102', '135202, Craven Street Financial Plan'),
    ('703930', '3102', '135910, MFS Independent Financial Ad'),
    ('703931', '3102', '136414, Evelyn Partners Financial Pl'),
    ('703934', '3102', '137195, Mathews Comfort Financial Se'),
    ('703937', '3102', '138941, Frank Corrigan + Company Lim'),
    ('703938', '3102', '143715, MHA Caves Wealth Ltd'),
    ('703943', '3102', '147876, Financial Advice and Service'),
    ('704808', '3102', '148759, Circle Financial Services Limited'),
    ('703945', '3102', '150317, Perspective (North East) Ltd'),
    ('703946', '3102', '150458, Talis Financial Advisers Ltd'),
    ('703947', '3102', '152689, Greenfields Financial Manage'),
    ('704886', '3102', '153508, One Four Nine Wealth Limited'),
    ('704741', '3102', '153566, Julian Harris Financial Consultants'),
    ('703950', '3102', '154621, Lyndhurst Financial Manageme'),
    ('704257', '3102', '169777, Castlegate Financial Mgmt Ltd'),
    ('703954', '3102', '176527, Mayfield Financial Services'),
    ('704747', '3102', '184591, Quilter Financial Planning Solutions Ltd'),
    ('703959', '3102', '184591, Ripley Wales'),
    ('703963', '3102', '188596, Fairstone Wealth Management'),
    ('703969', '3102', '190203, Deep Blue Financial Ltd'),
    ('703970', '3102', '190353, Phoenix Wealth Management LL'),
    ('703973', '3102', '192396, Radiant Financial Planning L'),
    ('704823', '3102', '192638, The On-Line Partnership Limited'),
    ('704738', '3102', '195522, Finli (FA92) Ltd'),
    ('704736', '3102', '197107, ValidPath Limited'),
    ('703978', '3102', '209534, Baker Hudson Ltd'),
    ('703979', '3102', '210740, Thompson Jenner'),
    ('703980', '3102', '211314, Tees Law'),
    ('704751', '3102', '214036, Policy Services Limited'),
    ('703982', '3102', '215410, John Davies Financial Consul'),
    ('703983', '3102', '216344, Scott Financial Ltd'),
    ('703984', '3102', '216704, AFH Wealth Management'),
    ('703985', '3102', '216704, AFH Wealth Management (IFS U'),
    ('704729', '3102', '217742, Quilter Wealth Ltd'),
    ('703989', '3102', '222033, SW Financial Planning Ltd'),
    ('704759', '3102', '223112, Best Practice IFA Group'),
    ('703992', '3102', '225183, Cambourne Financial Planning'),
    ('703994', '3102', '225538, Landmark IFA'),
    ('703995', '3102', '227247, Benchmark Financial Planning'),
    ('703996', '3102', '228341, Pembroke Financial Services'),
    ('703999', '3102', '229752, Hine Downing Financial Servi'),
    ('704794', '3102', '230342, Abacus Associates'),
    ('704002', '3102', '301672, Aaron Tawny'),
    ('704881', '3102', '304155, Julian Harris Adviser Network Limited'),
    ('704003', '3102', '401135, Absolute Financial Managemen'),
    ('704006', '3102', '409228, McClelland Yarr Financial Se'),
    ('704008', '3102', '409895, Cleddau Financial Services L'),
    ('704009', '3102', '413317, Financial Advice Centre Ltd'),
    ('704010', '3102', '417707, Henwood Court Financial Plan'),
    ('704011', '3102', '419331, Argentis Wealth Management L'),
    ('200177', '3106', '42 Wealth Management'),
    ('200178', '3106', '42 Wealth Service Team'),
    ('704013', '3102', '424784, Price Ferguson Ltd'),
    ('704889', '3102', '428734, MKC Wealth Ltd'),
    ('704885', '3102', '429142, Arcus Wealth'),
    ('704014', '3102', '429142, Corbel Partners Limited'),
    ('704015', '3102', '429458, Paragon Investment Managemen'),
    ('704785', '3106', '42W'),
    ('704016', '3102', '430531, Padstone Financial Managemen'),
    ('704017', '3102', '430771, Cummins Financial Advisers L'),
    ('409860', '3102', '432014, Prydis Wealth Ltd'),
    ('704019', '3102', '433591, Francis Clark Financial Plan'),
    ('704021', '3102', '438796, Thomas And Gentry Independen'),
    ('704022', '3102', '439179, Macleman Wealth Ltd'),
    ('704023', '3102', '440633, City Financial Planning Ltd'),
    ('704024', '3102', '440668, Worldwide Financial Planning'),
    ('704730', '3102', '440703, Quilter Financial Services Ltd'),
    ('704744', '3102', '441359, IWP Financial Planning Limited'),
    ('704030', '3102', '446389, Eastmills Limited'),
    ('704032', '3102', '446579, Fox + Co Financial Managemen'),
    ('704033', '3102', '446589, Prosser Knowles Associates'),
    ('704035', '3102', '449607, Duchy Independent Financial'),
    ('704734', '3102', '449607, Saltus Wealth Partnership Limited'),
    ('704037', '3102', '455444, Anglo Celtic Financial Consu'),
    ('704039', '3102', '456358, Elevation Wealth Management'),
    ('704041', '3102', '457234, Affinity Financial Advisors'),
    ('704043', '3102', '458361, Manning + Company'),
    ('704044', '3102', '458421, Insight Financial Associates'),
    ('704795', '3102', '460094, Ingard Independent Financial Management LLP'),
    ('704046', '3102', '460399, MWA Financial Advice Ltd'),
    ('704047', '3102', '461089, Inspire Wealth Ltd'),
    ('704048', '3102', '461598, 2 Plan Wealth Management'),
    ('704049', '3102', '463179, Courtney Havers LLP'),
    ('704737', '3102', '465124, Sense Network Ltd'),
    ('704050', '3102', '466658, David Winter IFA'),
    ('704051', '3102', '468119, Wealthwide Limited'),
    ('704755', '3102', '468321, Connor Broadley Ltd'),
    ('704053', '3102', '469015, Rose IFA Ltd'),
    ('704745', '3102', '469955, McHardy Private Wealth Ltd'),
    ('704055', '3102', '470448, Bgfp (Financial Planning)'),
    ('704061', '3102', '475973, Fairstone Financial Manageme'),
    ('704065', '3102', '475973, The Goodman Partnership LLP'),
    ('704067', '3102', '476417, BPH Wealth Management LLP'),
    ('704068', '3102', '478217, Wealth Solutions (UK) Limite'),
    ('704070', '3102', '484213, Steven Barton'),
    ('704072', '3102', '487395, Vision Independent Financial'),
    ('704075', '3102', '489484, Carpenter Box Financial Serv'),
    ('704077', '3102', '492448, Keightley Pattison Financial'),
    ('704078', '3102', '493844, Ablestoke Wealth Management'),
    ('704735', '3102', '497604, Quilter Financial Ltd'),
    ('704079', '3102', '500324, Clearwater Financial - Exete'),
    ('704080', '3102', '501867, Astute Investment Planning'),
    ('704088', '3102', '509756, Obsidian Financial Limited'),
    ('704089', '3102', '509886, KD Wright Financial Services'),
    ('704093', '3102', '515456, Coole Bevis Wealth Managemen'),
    ('704095', '3102', '517892, AMG Wealth Solutions LLP'),
    ('704096', '3102', '518813, Clarion Wealth Planning Limi'),
    ('704867', '3102', '525989, Perspective (Gould Financial Planning) Limited'),
    ('704099', '3102', '525994, Blue Wealth (Blue Financial'),
    ('704100', '3102', '526134, Amber River Colchester'),
    ('704103', '3102', '528227, Concise Wealth'),
    ('704746', '3102', '529492, Opus Independent Financial Planning Limited'),
    ('704106', '3102', '529810, True Potential Wealth Manage'),
    ('704109', '3102', '531465, Astute Private Wealth Ltd'),
    ('704111', '3102', '532972, Northfieldfs Limited'),
    ('704112', '3102', '533418, Bruce Bennett T/As Beneficia'),
    ('704113', '3102', '533522, Russell Gibson Financial Man'),
    ('704116', '3102', '542122, Armstrong Watson Financial P'),
    ('704117', '3102', '550013, Taylor Watson Financial Mana'),
    ('704118', '3102', '551177, Finance Shop Limited'),
    ('704253', '3102', '554090, Gemini Financial Planning Ltd'),
    ('704754', '3102', '560260, W.T. Independent Financial Planning Ltd'),
    ('704748', '3102', '564038, Finli (Thames Valley) Ltd'),
    ('704255', '3102', '564126, Thomson Gray Wealth Mgmt Ltd'),
    ('704122', '3102', '564504, Fox Financial Management'),
    ('704124', '3102', '565554, Estate Financial Planning Lt'),
    ('704890', '3102', '574458, Titan Wealth Planning Limited'),
    ('704129', '3102', '578614, Ascot Lloyd'),
    ('704739', '3102', '579543, Finli (Westerham) LLP'),
    ('704132', '3102', '581586, Clarus Wealth Ltd'),
    ('704135', '3102', '586071, Astute Financial Solutions L'),
    ('704733', '3102', '586130, Hoxton Wealth (UK) Ltd'),
    ('704138', '3102', '586897, Calder Financial Ltd'),
    ('704139', '3102', '587645, Focus Financial Planning Ltd'),
    ('704145', '3102', '592550, Pension Savings Investment A'),
    ('704147', '3102', '593699, Otter Financial Services Lim'),
    ('704150', '3102', '596658, Galleon Wealth Management Lt'),
    ('704151', '3102', '601039, JH Family Office'),
    ('704154', '3102', '604534, MKM Financial Management Ltd'),
    ('704740', '3102', '605985, Oculus Wealth Management Limited'),
    ('704160', '3102', '607322, Box Financial Planning'),
    ('704743', '3102', '609361, Elkins Financial Planning Limited'),
    ('704162', '3102', '609361, Smarter Financial Planning'),
    ('704166', '3102', '616019, Bespoke Advice Ltd'),
    ('704742', '3102', '618025, Lyncombe Consultants Limited'),
    ('704169', '3102', '618199, Brunswick Investment Manageme'),
    ('704170', '3102', '622446, People and Business Independ'),
    ('704171', '3102', '624006, Coldrey Wealth Management'),
    ('704172', '3102', '624548, Cherwell Financial Services'),
    ('704731', '3102', '624579, Furnley House Limited'),
    ('704179', '3102', '660094, Jones Financial (Norwich) Lt'),
    ('704756', '3102', '660573, Medical and General Independent Financial Advisers Ltd'),
    ('704180', '3102', '665653, Wren Sterling Financial Plan'),
    ('704186', '3102', '713035, West Country Financial'),
    ('704188', '3102', '715603, Oculus Wealth Management (Co'),
    ('704189', '3102', '715678, Whateley Wealth Management'),
    ('704191', '3102', '719403, Affinity Independent Financi'),
    ('704753', '3102', '726620, Chester Rose Financial Planning Ltd'),
    ('704196', '3102', '746000, Metro Financial Solutions LL'),
    ('704199', '3102', '755327, Arghans Independent Financia'),
    ('704201', '3102', '771110, Ian D Campbell Independent F'),
    ('704202', '3102', '772483, Prosperity IFA'),
    ('704203', '3102', '773427, EMET Financial Services Ltd'),
    ('704207', '3102', '787328, Berry + Oak Limited'),
    ('704732', '3102', '789217, Campbell & McConnachie'),
    ('704208', '3102', '790551, Sanctuary Wealth Planning'),
    ('704210', '3102', '794622, Bright Future Financial Ltd'),
    ('704211', '3102', '797296, Stallard Wealth Management L'),
    ('704758', '3102', '802430, Finli (PDB) Ltd'),
    ('704212', '3102', '804598, Simply Advice Ltd'),
    ('704213', '3102', '807675, Rutherford Hughes Ltd'),
    ('704216', '3102', '814844, HF Wealth Management'),
    ('704217', '3102', '815929, DFM Financial Planning'),
    ('704218', '3102', '825023, Mayfair International Privat'),
    ('704220', '3102', '827035, The Wealth Harbour Limited'),
    ('704221', '3102', '828050, Ethical Investments (SW) Ltd'),
    ('704222', '3102', '830057, Penguin Wealth Planners Limi'),
    ('704871', '3102', '836465, ValidPath Financial Limited'),
    ('409871', '3102', '837821, Stadden Forbes Financial'),
    ('704223', '3102', '841219, Howlett Reid Wealth Manageme'),
    ('704224', '3102', '844011, Raeburn, Christie Clark + Wa'),
    ('704225', '3102', '911770, Towan IFA Limited'),
    ('704228', '3102', '916506, Forest Financial Planning'),
    ('704229', '3102', '917403, Thomson Tyndall'),
    ('704230', '3102', '930215, Oculus Wealth Management (So'),
    ('704233', '3102', '935113, Oculus Wealth Management (Ne'),
    ('704238', '3102', '947126, Responsible Wealth'),
    ('704239', '3102', '951050, Blackthorn Financial Service'),
    ('704243', '3102', '966139, London Financial Consultants'),
    ('704246', '3102', '979187, Your Vision Wealth Managemen'),
    ('704247', '3102', '985418, Pyrford Financial Planning'),
    ('704249', '3102', '991017, Inco Financial Limited'),
    ('704250', '3102', '992948, Riverglen Financial Associat'),
    ('704251', '3102', '997021, Richard Armitage Wealth Mana'),
    ('704820', '3106', 'AFH Independent Financial Services Limited'),
    ('409810', '3106', 'AFS Advocate Fin Serv Ltd'),
    ('58417997', '3106', 'Ahmad42, Shahbano'),
    ('58417998', '3106', 'AhmadIFA, Shahbano'),
    ('58417999', '3106', 'Ahmadfirmuser, Shahbano'),
    ('409814', '3106', 'Alchemy Financial Limited'),
    ('200148', '3108', 'Alpha Investment Office'),
    ('409812', '3106', 'CPN Investment Management LLP'),
    ('704772', '3106', 'Cannizaro Wealth Management (MGM) Limited'),
    ('409663', '3106', 'Chester Rose'),
    ('409855', '3106', 'Chester Rose Hampshire'),
    ('704870', '3106', 'Countrywide Estate Planning'),
    ('409818', '3106', 'Cradle Overseas Pensions Ltd'),
    ('704815', '3108', 'Custody Offshore for Alpha'),
    ('704765', '3106', 'Custody Offshore for Select'),
    ('704814', '3108', 'Custody Onshore for Alpha'),
    ('704764', '3106', 'Custody Onshore for Select'),
    ('58307201', '3102', 'Daley_UAT, Alyssa'),
    ('409817', '3106', 'Excaliber Associates Ltd'),
    ('409834', '3106', 'Gateway Investment Services Ltd'),
    ('58377999', '3106', 'Gaunt, Andy'),
    ('409816', '3106', 'Haldon Financial Ltd'),
    ('409737', '3106', 'Harvest Associates Limited'),
    ('409665', '3106', 'Howard Financial Services'),
    ('409823', '3106', 'IAM Financial Services Ltd'),
    ('409819', '3106', 'Invest Southwest Limited'),
    ('704787', '3106', 'Kliskey Independent Financial Services Limited'),
    ('704760', '3108', 'LeifBridge'),
    ('704771', '3106', 'MIM'),
    ('409655', '3106', 'Marlborough MPS'),
    ('409666', '3106', 'Marlborough Personal Portfolios'),
    ('200119', '3106', 'Marlborough Service Team'),
    ('409815', '3106', 'Medical & Professional Inv LLP'),
    ('409822', '3106', 'Michael John Knott'),
    ('409821', '3106', 'Molyneux Associates Ltd'),
    ('703609', '3102', 'Non Advised'),
    ('409664', '3106', 'One Financial Solutions'),
    ('704259', '3106', 'One Knightsbridge Capital LLP'),
    ('409813', '3106', 'Orme Financial Services Ltd'),
    ('409835', '3106', 'Personal Wealth Mgt (S Wales) Ltd'),
    ('409811', '3106', 'Policy Services Limited'),
    ('409820', '3106', 'Right Advice Limited'),
    ('704784', '3106', 'SLP'),
    ('409649', '3106', 'Select Platform'),
    ('704717', '3106', 'Select Platform Direct'),
    ('704718', '3106', 'Select Service Team'),
    ('58331943', '3108', 'Sharma, Dhruv'),
    ('409859', '3106', 'Symphony Financial Advisers Limited'),
    ('704804', '3106', 'Vea Wealth Management Ltd'),
    ('409744', '3106', 'WFO')
ON CONFLICT (code, firm_id) DO NOTHING;

-- The features the engine runs that the base reference data above does not list.
INSERT INTO integrations.features (code, description) VALUES
    ('IncomeStatements',     'Create the monthly income statement in Intelliflo'),
    ('IncomeStatementItems', 'Send daily income statement items to Intelliflo')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------------- 4. mark the engine's own migrations as applied
-- So the engine and the webhook skip them (they would otherwise try to re-run them with rights
-- they may not have).
INSERT INTO integrations.schema_migrations (migration_id) VALUES
    ('001_integrations_schema.sql'),
    ('002_integrations_reference_data.sql'),
    ('003_integrations_engine_features.sql'),
    ('011_income_statement_items_sent.sql'),
    ('012_income_statements.sql'),
    ('013_income_statements_io_id.sql')
ON CONFLICT (migration_id) DO NOTHING;

-- ---------------------------------------------------------------------------- 5. security: row-level security, grants, audit
-- RLS: service_role only, zero policies (aml's old sync_state pattern). Access control is the
-- admin API's permission check, not a WHERE clause. FORCE binds the table owner too, so the
-- engine works as service_role without needing SET ROLE for the grants below.
DO $$
DECLARE t text;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT USAGE, CREATE ON SCHEMA integrations TO service_role;   -- CREATE: the engine's own tables live here too
        FOR t IN SELECT unnest(ARRAY['firms','features','dsrs','platforms','platform_features','job_runs','users','advisors_map'])
        LOOP
            EXECUTE format('ALTER TABLE integrations.%I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE integrations.%I FORCE ROW LEVEL SECURITY', t);
            EXECUTE format('GRANT ALL ON integrations.%I TO service_role', t);
        END LOOP;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA integrations TO service_role;
    END IF;
END $$;

-- No audit triggers on the integrations tables. audit.record() writes an audit.log row whose
-- tenant_id has a foreign key to auth.tenants, but these tables' `tenant_id` is the Intelliflo-side
-- tenant (a vendor id), so the insert fails and the write with it. A database that was created with
-- the triggers has them dropped by 0279 and 0281.

-- ---------------------------------------------------------------------------- 6. the engine's access to its own tables
GRANT SELECT, INSERT ON integrations.schema_migrations TO service_role;

-- Hand the engine's own tables to service_role (as the engine itself would have created them)
-- since this migration runs as `migrator`, which is already a member (01-roles.sql).
DO $$
BEGIN
    IF pg_has_role(current_user, 'service_role', 'member') THEN
        ALTER TABLE integrations.schema_migrations OWNER TO service_role;
        ALTER TABLE integrations.income_statements OWNER TO service_role;
        ALTER TABLE integrations.income_statement_items_sent OWNER TO service_role;
    END IF;
END $$;

-- ---------------------------------------------------------------------------- 7. portals tables the engine reads
-- platform.accounts / platform.links are foundational portals tables (Valuations and the income
-- items need them) -- granted unconditionally since both always exist by this point.
GRANT USAGE ON SCHEMA platform TO service_role;
GRANT SELECT ON platform.accounts, platform.links TO service_role;
