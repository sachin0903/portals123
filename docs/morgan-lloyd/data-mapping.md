# Morgan Lloyd — Data Mapping

How Graphene's data model maps to Morgan Lloyd operations, and the CTC illustration XML schema. Graphene schema is owned by Drizzle in `pkgs/db/src/schema.ts`.

## Where external IDs live

`platform.links` (generic Graphene-id ↔ external-id, keyed by provider) is the home for ML one-shot identifiers:

| ML identifier    | Graphene entity | Stored as                                                                                                                 |
| ---------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------- |
| ContactID        | applicant       | `platform_links(entity='applicant', provider='morgan_lloyd', external_id=contactId)` (or `applicants.platformIdentifier`) |
| Scheme Reference | account         | `platform_links(entity='account', …)` (or `accounts.platformIdentifier`)                                                  |
| Policy Number    | account         | `platform_links` / `accounts.platformIdentifier`                                                                          |
| Beneficiary ID   | beneficiary row | `beneficiaries.mlBeneficiaryId`                                                                                           |
| Transfer ID      | transfer row    | `transfers.mlTransferId`                                                                                                  |

Lifecycle records (valuations, contributions, transfers, beneficiaries, drawdowns) need **new tables** — see [state-machines.md](./state-machines.md). They do not exist today.

## Applicant → Contact / Scheme holder

| ML field                                                | Graphene source                                                           |
| ------------------------------------------------------- | ------------------------------------------------------------------------- |
| cam_title / firstname / middle / lastname               | `applicants.title/firstName/middleName/surname`                           |
| date of birth                                           | `applicants.dob`                                                          |
| gendercode                                              | `applicants.data.gender`                                                  |
| marital status                                          | `applicants.data.maritalStatus` (`pkgs/onboarding/src/models/pension.ts`) |
| cam_ninumber (NINO)                                     | `applicants.nationalIdentifier` (type NINO)                               |
| address / postcode / time-at-address / previous address | `applicants.address` (jsonb) + `applicants.data`                          |
| nationality                                             | `applicants.nationality[]`                                                |
| email                                                   | `applicants.email`                                                        |
| mobilephone                                             | `crm.contacts.mobile`                                                     |
| telephone1                                              | `crm.contacts.phone`, when there is one                                   |
| retirement age                                          | `applicants.data.retirementAge` (50–75)                                   |
| UK relevant individual (Y/N)                            | `applicants.data` (new SIPP field)                                        |
| Pension type (Uncrystallised/Drawdown)                  | `accounts.data`                                                           |

> Fields not in the fixed columns go in the `data` jsonb and are declared in the dynamic `onboarding.fields` table, scoped to the SIPP product (resolution: tenant > product > platform > global). The "Application Fields Required" tab of `ML Account Open Workflow 19.5.26.xlsx` is the authoritative field list.

## Account → Scheme / Investment

| ML field                          | Graphene source                                                 |
| --------------------------------- | --------------------------------------------------------------- |
| schemeType / sippType             | constants `16` / `809130016`                                    |
| provider (Create Investment)      | constant GUID `6ac39921-…`                                      |
| policyNumber                      | `accounts.platformIdentifier`                                   |
| fees (adviser £/%)                | `accounts.fees` (FeeEntry[])                                    |
| bank details (SEI standing order) | `applicants.bankAccount` (sortCode/accountNumber/accountName)   |
| regular/single contributions      | `accounts.payments` (jsonb)                                     |
| withdrawals                       | `accounts.withdrawals` (jsonb)                                  |
| transfer-in                       | `accounts.transfer` (jsonb) + `transfers` table for M transfers |

## Employer contributions → contribution / employer account

`pkgs/onboarding/src/models/pension.ts` → `SchemaPensionEmployerContributions`: name, registrationNumber, accountNumber (8 digits), sortCode (xx-xx-xx), address. Maps to employer contribution records / `cam_createupdateaccount`.

## Beneficiaries / Transfers (N / M)

Today only an `account_role='beneficiary'` value exists — record-level beneficiary/transfer tables are new. Map each beneficiary (name, relationship, % of fund) → `cam_createbeneficiary`; each transfer (provider, plan number, approx value, full/partial, cash/in-specie, crystallised/uncrystallised) → `cam_createtransfer`.

## Roles

`onboarding.accountApplicants.role` (owner / decision_maker / trustee / beneficiary) maps applicants to accounts and into ML participant roles.

---

## CTC bespoke XML v1.6

Request to the CTC calculation engine (vendor spec: `XML_v1.6_Bespoke.pdf`, dated 22/02/2021). Outline:

```
<message>
  <header>
    <message_version>CTC1</message_version>
    <created_date>yyyy-MM-dd HH:mm:ss</created_date>
    <mode>Request</mode>
  </header>
  <content>
    <quotations>                       <!-- non-repeating -->
      <effective_date>yyyy-mm-dd</effective_date>   <!-- M -->
      <output_required>Yes|No</output_required>      <!-- M -->
      <output_reference>… ≤45 chars</output_reference>
      <agent_name>… ≤130 chars</agent_name>
      <client_type>Individual|Company|Trust</client_type>
      <investor>                       <!-- repeating: clients in the illustration -->
        <title/> <forename/> <surname/> <sex>Male|Female</sex>
        <life_type>Applicant|Life Assured|Both|Dependant|None</life_type>
        <marital_status>Single|Married|Civil Partnership|Divorced|Partnered|Widowed|Unknown</marital_status>
        <date_of_birth>yyyy-mm-dd</date_of_birth>
        <exit_age>…</exit_age>         <!-- planned retirement/conversion age -->
      </investor>
      <quotation product_ref="SIPPEN" product_type="Self Invested Personal Pension">  <!-- repeating per product -->
        <quotation_type>Accumulation|Drawdown|Flexi Access Drawdown|SMPI</quotation_type>
        <inflation_adjustment_indicator>Yes|No</inflation_adjustment_indicator>
        <current_max_income/> <next_review_date/> <inc_already_taken/>  <!-- capped drawdown -->
        <plan_stream plan_stream_key="STREAM1">   <!-- repeating per payment type -->
          <contribution_type>Member|Employer|Transfer Value|Existing Fund Value</contribution_type>
          <net_gross>Net|Gross</net_gross>        <!-- when contribution_type=Member -->
          <amount/> <frequency>Single|Weekly|Monthly|Quarterly|Half Yearly|Annually</frequency>
          <crystallised>Yes|No</crystallised>
          <plan_stream_start>…</plan_stream_start> <plan_stream_end>…</plan_stream_end>
        </plan_stream>
        <premium_split>                 <!-- fund allocation per stream -->
          <stream_applicable plan_stream_key="STREAM1"/>
          <fund_split type="COMMPROP?">
            <fund_ref>ISIN</fund_ref> <fund_name/>
            <split_per/> | <split_amount/>   <!-- one of -->
            <rate_of_return/> <management_charge/> <initial_charge/> <generic_fund>Yes|No</generic_fund>
          </fund_split>
        </premium_split>
        <withdrawals>                   <!-- repeating -->
          <basis>Fixed|Maximum GAD|Percentage of Maximum GAD</basis>
          <amount/> <percent/> <frequency>Single|Monthly|Quarterly|Half Yearly|Annually|2 Months|4 Months</frequency>
          <start_date/> <increases><percent/></increases>
          <drawdown_type>Flexible|Capped|UFPLS</drawdown_type>
        </withdrawals>
        <crystallisation>
          <amount/> <frequency/> <drawdown_type>Flexible|Capped</drawdown_type>
          <antytxfcsh>Maximum|Amount|Percentage</antytxfcsh>  <!-- tax-free cash -->
          <allocation_basis>…</allocation_basis> <phasedcash/> <phased_planning_option/>
        </crystallisation>
        <product_fees>…</product_fees>  <!-- when present, ALL fees must be specified (overrides standing data) -->
        <adviser_fees>…</adviser_fees>
        <annuity_basis>…</annuity_basis>
      </quotation>
      <results>…</results>
    </quotations>
  </content>
</message>
```

**Response:** header `<response>SUCCESS|ERROR</response>`; on error, `<errors><error>DESCRIPTION</error></errors>`. Error meanings: _Unknown Product Code_, _Error reading XML message_ (malformed), _An error has occurred…_ (calc/output error), _Specified fund not recognised_ (with the fund ref appended).

> **To complete during implementation:** the `adviser_fees`, `annuity_basis`, `product_fees` (tiered), and `results` element details are on spec pages 19–29 — finish that mapping against `XML_v1.6_Bespoke.pdf`. Map Graphene's illustration inputs (see the "Illustration Fields required" xlsx tab) and fund allocations (`accounts.fees`, model funds) into the structure above.
