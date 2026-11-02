# Client vocabularies

The closed lists on a person, and the value each custodian accepts for the same fact.
The book stores one canonical value (`crm.contacts`). The vendor's word or integer is chosen
at the boundary, and a value with no counterpart is left off the call.

A blank cell means that list has no value for that book row. Values that exist only on one
provider are listed under each table. A title cell that says "not pinned" means the reference
may still hold a code; this page only names codes the client model or the update API states.

The provider columns below are the lists published in the API documents, checked against:

- Seccl: `pkgs/seccl/spec/seccl.postman_collection.json` (Seccl publishes no OpenAPI document). The generated `client.json` schema only records values seen in examples, so it is not the enum.
- Morgan Lloyd: `pkgs/morgan-lloyd/src/morgan_lloyd/vendor_manifest.json`, which matches `pkgs/morgan-lloyd/spec/ml.openapi.json`.
- SEI: `pkgs/sei/spec/sei.openapi.json`. File-drop codes that the API does not list are marked as such.
- CTC illustration words: `docs/morgan-lloyd/ctc-xml-schema.md`.

Where the book sends a different word from the published enum, the section says so.

The book stores a personal fact only when every custodian in the comparison can hold it.
Where one custodian publishes no field for the question (SEI has no employment status), that
custodian does not narrow the list. Title, gender, marital status and employment status are
field types (`title`, `gender`, `marital`, `employment` in
`pkgs/ui/src/lib/forms/vocabularies.ts`). A seeded fact-find does not carry its own copy
of the options: migration `0241` points `title`, `gender`, `pension.maritalStatus` and
`pension.employmentStatus` at those field types. `0239` wrote the same update and changed
no rows, because `documents.form_fields` is force row-level security and that statement
ran as the migrator. A form a firm authored under another code is left alone.
Source of wealth, source of funds, tax relief and the vulnerability lists stay the vocabulary
of the provider that publishes them: the others do not ask the same question.

## Title

Seccl's create-individual parameter publishes this enum, and nothing else: `Dr`, `Professor`,
`Mrs`, `Miss`, `Mr`, `Ms`, `Mx`. The vulnerable-client create and the registered-client update
publish the same list without `Mx`.

The `title` field offers Mr, Mrs, Miss, Ms, Dr and Prof: the titles every custodian can store.
The column stays free text, so a title already on the record outside those six still displays,
and a bare number is a custodian code, so it is not offered as a choice. Enrolment sends
`Prof` as Seccl's word `Professor`, and omits a stored title that is outside the enum (`Mx`
is in the enum, so a stored `Mx` is still sent). Morgan Lloyd's `cam_title` is a wider twelve
plus `Other`. The lookup uppercases the word and drops one trailing full stop, then sends
`Other` (`809130012`) for anything outside that twelve.

SEI does not publish a full title enum. Enrolment `titleId` says the code is system code `451`,
"such as Mr., Mrs., Dr., The Honorable, GEN", and gives no table. The profile update names five
commonly used codes under the same system code: `1` Mr., `2` Mrs., `3` Ms., `4` Dr., `21` Miss.
The file drop carries reference `TITLE` (61 codes). `0` is unset. A code the reference does not
describe stays empty. Enrolment sends `titleId` for the six titles below, and still uses the
stored word inside `mailingLabelName`. A title outside those six is omitted. The SEI codes in
the table that are not in that update list are the
file-drop fixture and its aliases, not an API enum.

| Book   | Seccl published enum | Sent to Seccl | Morgan Lloyd `cam_title` | SEI                                                                                 |
| ------ | -------------------- | ------------- | ------------------------ | ----------------------------------------------------------------------------------- |
| `Mr`   | `Mr`                 | `Mr`          | `809130000` Mr           | Update `1` Mr. File drop `1` `Mr.` → `Mr`                                           |
| `Mrs`  | `Mrs`                | `Mrs`         | `809130002` Mrs          | Update `2` Mrs. File drop `2` `Mrs.` → `Mrs`                                        |
| `Miss` | `Miss`               | `Miss`        | `809130003` Miss         | Update `21` Miss. File drop `21` `Miss`                                             |
| `Ms`   | `Ms`                 | `Ms`          | `809130001` Ms           | Update `3` Ms. File-drop description `Ms.` → `Ms`; the reference code is not pinned |
| `Dr`   | `Dr`                 | `Dr`          | `809130004` Dr           | Update `4` Dr. File-drop description `Dr.` → `Dr`; the reference code is not pinned |
| `Prof` | `Professor`          | `Professor`   | `809130007` Prof         | File drop `12` `Professor` → `Prof`. Not among the five update codes                |

Only on one provider:

| Provider     | Value                                                                                                           | What the book does                                                                                                      |
| ------------ | --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Seccl        | `Mx`                                                                                                            | In the create-individual enum. Not offered. A stored `Mx` is still sent. SEI's file drop aliases `Mx.` to `Mx`          |
| Seccl        | `Professor`                                                                                                     | The enum word. The book stores `Prof` and sends `Professor`                                                             |
| Morgan Lloyd | `809130005` Master, `809130006` Capt, `809130008` Reverend, `809130009` Sir, `809130010` Lord, `809130011` Lady | Not offered. A title already stored is still sent to Morgan Lloyd on its code, and omitted on a Seccl create            |
| Morgan Lloyd | `809130012` Other                                                                                               | Sent when the stored word is outside their twelve                                                                       |
| Morgan Lloyd | `809130013` Major                                                                                               | Present on Get Adviser Contacts only. Not offered, and not sent on create                                               |
| SEI          | `5` `The Honorable` → `The Honourable`                                                                          | File-drop alias. Enrolment names "The Honorable" as an example of system code `451` and does not give its number        |
| SEI          | `28` `H E Shaikh` → `H E Sheikh`                                                                                | File-drop alias                                                                                                         |
| SEI          | `62` Baroness, and the rest of the 61                                                                           | Stored as the reference description                                                                                     |
| SEI          | suffix `1`–`14` (`Jr.` through `CPA`)                                                                           | A different field (`suffix` on the profile update, `suffixId` on enrolment, system code `5044`). The book has no suffix |

## Gender

The column and the `gender` field are Male and Female. Migration `0239` cleared `other` and
`not_specified`.

| Book     | Seccl `gender` | Morgan Lloyd `gendercode` | CTC `<sex>` | SEI                                                                  |
| -------- | -------------- | ------------------------- | ----------- | -------------------------------------------------------------------- |
| `male`   | `Male`         | `1` Male                  | `Male`      | File drop `1`. Enrolment `genderId` `1`. Profile update `gender` `1` |
| `female` | `Female`       | `2` Female                | `Female`    | File drop `2`. Enrolment `genderId` `2`. Profile update `gender` `2` |

Seccl types `gender` as an enum on two requests and does not list the members. The examples
and the generated schema use `Male` and `Female`, which is what enrolment sends. Morgan Lloyd's
option set is exactly those two. SEI enrolment sends `genderId` `1` or `2`. The profile update
sends the same integers on `gender`. SEI documents enrolment `genderId` as no longer shown, so
the profile update is the write that stores it. A file-drop `0` is unset. A value other than
male or female is left off.

## Marital status

| Book       | Seccl `maritalStatus` | Morgan Lloyd `familystatuscode` | CTC `<marital_status>` | SEI reference / `maritalStatusId` | SEI profile update `maritalStatus` |
| ---------- | --------------------- | ------------------------------- | ---------------------- | --------------------------------- | ---------------------------------- |
| `single`   | `Single`              | `1` Single                      | `Single`               | `2` Never Married                 | `1` or `NEVERMARRIED`              |
| `married`  | `Married`             | `2` Married                     | `Married`              | `1` Married                       | `0` or `MARRIED`                   |
| `divorced` | `Divorced`            | `6` Divorced                    | `Divorced`             | `8` Divorced                      | `4` or `DIVORCED`                  |
| `widowed`  | `Widowed`             | `8` Widowed                     | `Widowed`              | `9` Widower and `10` Widowed      | `5` `WIDOWERED` and `6` `WIDOWED`  |

The `marital` field and the column are those four. Seccl publishes `Single`, `Married`,
`Divorced`, `Widowed` on the client object and on create pension client, and that is what the
circumstances update sends. Migration `0239` cleared `civil_partner` and `cohabiting`. The
generated schema has only observed `Single` and `Married`.

Morgan Lloyd's contact option set (`familystatuscode`, and the same set as `cam_familystatuscode`)
is wider: it also has civil partner and cohabiting. Codes `3` and `4` are unused gaps. CTC is a
separate vocabulary.

SEI enrolment sends `maritalStatusId` for the four book values: `1` Married, `2` Never Married
(`single`), `8` Divorced, `10` Widowed. The published list also has `3` Separated,
`7` Partnered and `9` Widower. A retrieve field, `maritalStatusCd`,
gives the same numbers with different example words (`Unmarried`, `Partner`, `Widowered`). The
file drop uses these numbers; `0` is unset and becomes empty. The profile-update field is a
different list: a code or a case-sensitive description, `0 MARRIED`, `1 NEVERMARRIED`,
`2 SEPARATED`, `3 PARTNERED`, `4 DIVORCED`, `5 WIDOWERED`, `6 WIDOWED`. The circumstances update
currently sends the book slug (`single`, `married`, …), which is in neither SEI list. SEI can
accept the call and warn the field off.

Only on one provider:

| Provider           | Value                             | What the book does                                                               |
| ------------------ | --------------------------------- | -------------------------------------------------------------------------------- |
| SEI reference      | `3` Separated                     | Left empty on the way in                                                         |
| SEI enrolment      | `7` Partnered                     | Left empty. Partnered is not one of the four book values                         |
| Morgan Lloyd       | `5` Civil Partner, `7` Cohabiting | Not stored. CTC's words for the same two are `Civil Partnership` and `Partnered` |
| SEI profile update | `2` `SEPARATED`, `3` `PARTNERED`  | The same two statuses, on the update numbering                                   |
| SEI enrolment      | `9` Widower and `10` Widowed      | Both inbound codes become `widowed`                                              |
| CTC                | `Unknown`                         | Allowed on the illustration XML. Nothing on the book is sent as `Unknown`        |
| Morgan Lloyd       | option codes `3` and `4`          | Documented gaps, no label                                                        |
| Seccl              | civil partnership, cohabiting     | No enum member. The book does not store either                                   |

## Employment status

This is whether the person works. Occupation is a separate free-text fact (below).

| Book             | Seccl `employmentStatus` | Morgan Lloyd `cam_employmentstatus` |
| ---------------- | ------------------------ | ----------------------------------- |
| `employed`       | `Employed`               | `809130000` Employed                |
| `self_employed`  | `Self Employed`          | `809130001` Self Employed           |
| `retired`        | `Retired`                | `809130007` Pensioner               |
| `unemployed`     | `Unemployed`             | `809130002` Unemployed              |
| `education`      | `Education`              | `809130003` Student                 |
| `care_under_16s` | `Care Under 16s`         | `809130006` Child Carer             |
| `care_over_16`   | `Care Over 16`           | `809130005` Carer                   |
| `other`          | `Other`                  | `809130008` Other                   |

The `employment` field stores the slug. Its label for `self_employed` is Self-employed.
An older fact-find stored `Self-Employed`, and the write-back still folds that label onto
the slug. Seccl publishes those eight words on the client object and
on create pension client, in this order: `Employed`, `Retired`, `Self Employed`,
`Care Under 16s`, `Care Over 16`, `Education`, `Unemployed`, `Other`. The generated schema has
only observed `Employed`.

SEI has no employment-status field, so it does not narrow this list. The profile update takes
`occupation` as free text, up to 100 characters.

Only on Morgan Lloyd:

| Value                                                                                                                            | Where                                                        | What the book does                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `809130004` Child                                                                                                                | Contact `cam_employmentstatus`                               | No book value. An unmapped status is dropped rather than sent as Other                                                                                                  |
| `809130000` Employed, `809130001` Self-Employed, `809130002` Director, `809130003` Retired, `809130004` Carer, `809130005` Other | Factfind `cam_employmentstatus` (`FactfindEmploymentStatus`) | A second option set on the factfind endpoints. The contact set spells it `Self Employed`; the factfind spells it `Self-Employed`. `employment_status` is not sent there |

## Source of wealth

How the money was accumulated. The column stores Morgan Lloyd's own label, including their
spelling `Inheritence`. The picker is that twelve. `source_of_wealth_detail` is the supporting
statement and goes out as `cam_sourceofwealthstring`. A label that matches sends the integer;
a historical free-text value goes out as the string alone.

| Book label                                 | Morgan Lloyd `cam_sourceofwealth` |
| ------------------------------------------ | --------------------------------- |
| `Salary`                                   | `809130000`                       |
| `Variable Remuneration`                    | `809130001`                       |
| `Investment Income or Sale of Investments` | `809130002`                       |
| `Pension Income`                           | `809130003`                       |
| `Sale of Main Residence`                   | `809130004`                       |
| `Sale of Other Property`                   | `809130005`                       |
| `Business Interests`                       | `809130006`                       |
| `Inheritence` (and a typed `inheritance`)  | `809130007`                       |
| `Gift`                                     | `809130008`                       |
| `Financial Settlements`                    | `809130009`                       |
| `Lottery/Betting/Casino`                   | `809130010`                       |
| `Other`                                    | `809130011`                       |

Seccl has `sourceOfWealth` on a trust stakeholder. The collection describes it as a string,
required when the role is Settlor, with example `Inheritance`. It publishes no enum. SEI has no
source-of-wealth field. Its `initialSourceOfFunds` is the funds list in the next section. The
circumstances update does not put this wealth label into that field.

## Source of funds

Where this payment came from. Distinct from source of wealth. The column is free text. The
picker offers this list, and `source_of_funds_detail` holds the words when the answer is
`Other (please specify)`.

- Personal savings
- Salary or employment income
- Business income
- Sale of property
- Sale of investments (e.g., shares, bonds)
- Loan proceeds
- Gift from family or friends
- Inheritance
- Pension or retirement funds
- Insurance payout
- Transfer from another bank account
- Transfer from another custodian or investment platform
- Payment from a current account
- Proceeds from company sale
- Other (please specify)

SEI's profile update `initialSourceOfFunds` accepts a code or a case-sensitive description:

| Code | Description   |
| ---- | ------------- |
| `0`  | `NONE`        |
| `1`  | `INVESTMENTS` |
| `2`  | `INCOME`      |
| `3`  | `INHERITANCE` |

The labels that name one of those four are sent as the code. The rest are left off.

| Book label                                | `initialSourceOfFunds` |
| ----------------------------------------- | ---------------------- |
| Personal savings                          | `1` INVESTMENTS        |
| Sale of investments (e.g., shares, bonds) | `1` INVESTMENTS        |
| Salary or employment income               | `2` INCOME             |
| Business income                           | `2` INCOME             |
| Pension or retirement funds               | `2` INCOME             |
| Inheritance                               | `3` INHERITANCE        |
| Gift from family or friends               | `3` INHERITANCE        |

Sale of property, loan proceeds, insurance payout, a bank or custodian transfer, a current-account
payment, proceeds from a company sale, and Other have no code. Morgan Lloyd and Seccl have no
source-of-funds option set on the contact. `docs/sei-data-sources.md` names
`crm.contacts.source_of_funds` as the column that corresponds to `initialSourceOfFunds`.

Adding an account owner uses a second field, `sourceOfFunds`, with a shorter enum and no
`NONE`: `1` Investments, `2` Income, `3` Inheritance. A retrieve of compliance details returns
`sourceOfFunds` as a string (`Income`, `Investments` in the examples) and publishes no enum.

## Tax relief

The member's own declaration. The manifest publishes the four integers and points at a Tax
Relief Lookup; the wording in the last column is that lookup, vendored beside the enum. The
contact payload sends the integer. Seccl does not take tax relief on the individual. SEI has no
equivalent field.

| Book                                       | Short label on the record                                                   | Morgan Lloyd `cam_taxrelief` | Vendor's full wording                                                                                                                                  |
| ------------------------------------------ | --------------------------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `uk_relevant_earnings`                     | Has relevant UK earnings chargeable to income tax this tax year             | `809130000`                  | I have relevant UK earnings chargeable to income tax or general earnings from overseas Crown employment subject to UK tax, in this tax year            |
| `uk_resident_this_year`                    | Is or will be UK resident at some time during this year                     | `809130001`                  | I have or will have been resident in the UK at some time during this year                                                                              |
| `overseas_crown_employment_no_uk_earnings` | In overseas Crown employment, with no UK-taxable earnings this tax year     | `809130002`                  | I, or my spouse, are in overseas Crown employment, but for this tax year do not have general earnings from overseas Crown employment subject to UK tax |
| `spouse_overseas_crown_employment`         | Spouse has UK-taxable earnings from overseas Crown employment this tax year | `809130003`                  | My spouse has for this tax year general earnings from overseas Crown employment subject to UK tax                                                      |

## Vulnerability

The flag, the FCA's four drivers (FG21/1), and the 24 characteristics under them.

| Book driver  | Seccl `vulnerability.reason` | Morgan Lloyd `cam_vulnerabilitydrivers` | SEI |
| ------------ | ---------------------------- | --------------------------------------- | --- |
| `health`     | `Health`                     | `809130000` Health                      |     |
| `life_event` | `Life event`                 | `809130001` Life Events                 |     |
| `resilience` | `Resilience`                 | `809130002` Resilience                  |     |
| `capability` | `Capability`                 | `809130003` Capability                  |     |

Seccl publishes that enum on create client. Morgan Lloyd's label for the second driver is
`Life Events`. The send path posts the book slug (`health`, `life_event`, …) in Seccl's
`reason`, which is not the published word. Seccl also takes `isVulnerable` and
`isSupportTemporary`. SEI takes `vulnerableClientFlag` only. The file drop's `VULNERABLE_FLG`
is `1` yes / `2` no, and a blank stays blank.

The 24 characteristics are the same slugs on the book and on Morgan Lloyd's
`cam_vulnerabilitycharacteristics`. Seccl and SEI have no characteristic list. `809130023` is
out of sequence: visual impairment was added later and sits between hearing impairment and
mental health in the vendor's list order.

| Book                       | Morgan Lloyd |
| -------------------------- | ------------ |
| `physical_disability`      | `809130000`  |
| `long_term_illness`        | `809130001`  |
| `hearing_impairment`       | `809130002`  |
| `visual_impairment`        | `809130023`  |
| `mental_health`            | `809130003`  |
| `addiction`                | `809130004`  |
| `low_mental_capacity`      | `809130005`  |
| `retirement`               | `809130006`  |
| `bereavement`              | `809130007`  |
| `income_shock`             | `809130008`  |
| `relationship_breakdown`   | `809130009`  |
| `domestic_abuse`           | `809130010`  |
| `caring_responsibilities`  | `809130011`  |
| `other_circumstances`      | `809130012`  |
| `erratic_income`           | `809130013`  |
| `over_indebtedness`        | `809130014`  |
| `low_savings`              | `809130015`  |
| `low_emotional_resilience` | `809130016`  |
| `low_financial_knowledge`  | `809130017`  |
| `poor_literacy_numeracy`   | `809130018`  |
| `poor_english`             | `809130019`  |
| `poor_digital_skills`      | `809130020`  |
| `learning_difficulties`    | `809130021`  |
| `no_access_to_support`     | `809130022`  |

Morgan Lloyd also has `cam_contactvulnerability`, a different seven-member set. The drivers
above are not mapped onto it.

## FATCA

SEI's certification. Enrolment `certifiedFATCAComplianceId` publishes this table. Seccl and
Morgan Lloyd have no equivalent list. The book collapses two pairs, and enrolment sends a code
only for `w9`, `w8`, `w8_cure` and `not_certified`. `other_individual` and `other_entity` are
refused on the way out, because each one is two SEI codes.

| SEI code | SEI description                                       | Book               |
| -------- | ----------------------------------------------------- | ------------------ |
| `1`      | W-9                                                   | `w9`               |
| `2`      | W-8                                                   | `w8`               |
| `3`      | W-8 Cure                                              | `w8_cure`          |
| `4`      | Other Individual Self Certification Papers            | `other_individual` |
| `5`      | Other Individual Self Certification Cured             | `other_individual` |
| `6`      | Firm has NOT Certified                                | `not_certified`    |
| `7`      | Certified Participating Foreign Financial Institution |                    |
| `8`      | Other Entity Self Certification Papers                | `other_entity`     |
| `9`      | Other Entity Self Certification Cured                 | `other_entity`     |
| `10`     | Pre-existing July 1, 2014 under 1,000,000 or Exempt   |                    |
| `11`     | Pre-existing July 1, 2014 over 1,000,000              |                    |

`us_person` follows the certification on the way in: `1` is a US person, `2` is not.

## Not one of these lists

Occupation is a picker of about ninety-seven jobs plus `Other`, stored as free text, with
`occupation_detail` for Other and for a retired client's previous occupation. Morgan Lloyd has
no occupation field. SEI's `occupation` is free text. It answers a different question from
employment status: a retired GP is `retired` and `GP / Doctor`.

SEI client restriction on the profile update is `0 NONE`, `1 EMPLOYEE`, `2 HIGHLYPRIVATE`.
The book does not store it.
