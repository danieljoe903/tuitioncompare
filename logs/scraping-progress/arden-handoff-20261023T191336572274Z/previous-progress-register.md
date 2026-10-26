# University Tuition Compare — Scraping Progress Register

Updated: 23 October 2026 (Africa/Lagos)
Owner: Daniel Chukwuemeka
Scope: UK universities first, Ireland (IE; user label IR) afterward. International tuition, scholarship rules and deposit policies. EU fee rows are outside the agreed current scope.

## Evidence and counting rules

This register records the user's supplied command outputs and accepted decisions. It is not a fresh query of the live project database. Distinguish audited totals, running totals, source-reviewed rules and confirmed database applications. Unknown counts are **not yet audited**, never zero by assumption.

- Tuition counts refer to active course records with verified **annual international fees**, unless explicitly labelled programme/module pricing.
- Scholarship counts distinguish award rules from courses in their scope. A verified rule does not mean an individual student has been awarded a scholarship.
- Deposit counts refer to university/year policies, not the number of courses or applicants. A verified offer-letter policy can have no public numeric deposit amount.
- Deactivated records are retained and excluded from active-course totals; they are not newly verified fees.
- Preview-only commands do not establish that updates were applied.
- Keep source URLs, academic years, original data, audit/report filenames, pending reasons and eligibility conditions with every update.
- Do not turn absence from a catalogue into proof of discontinuation. Do not infer duplicates solely from similar titles.

## Country queue

| Country | Inventory | Universities worked on in current UK workflow | Not started in current workflow | Status |
|---|---:|---:|---:|---|
| UK | 146, confirmed by user database export | 4 | 142 | Abertay, Aberystwyth, ARU and Arden reviewed with outstanding cases retained |
| Ireland / IE (IR) | Not yet audited | No completed university reconciliation established | Not yet audited | Scheduled after UK; Trinity College Dublin has earlier preview work |

The 142 UK universities are a pending queue, not a list of verified/unverified fees. The complete named inventory has not been supplied in the available material. Do not invent names or assume which school is next.

## UK — Abertay University (UK001)

Status: partially reconciled; paused with unresolved records.

### Tuition

- Last supplied combined audit/workflow: 100 active courses; 83 verified annual international fees; 17 unresolved.
- Verified fee years: 70 for 2026/27 and 13 for 2025/26.
- These are the latest supplied Abertay coverage totals, not a newly executed audit.
- Unresolved groups: 2 TOPUP, 5 UG, 10 PG.

### Scholarships

- Two reported verified rules: undergraduate and postgraduate international awards (IDs 85 and 86 in supplied API outputs).
- Last supplied scope command: UG 37 courses, PG 24 courses — 61 scoped courses in total.
- Do not equate 61 scoped courses with 61 confirmed awards or scholarship payments.
- Number of other applicable awards verified/unverified: not yet audited.
- Remaining issues: course identity, verified fee basis, cohort year, course exclusions and unconfirmed student facts. Residence must not be substituted for nationality.
- Maximum/up-to awards must remain conditional estimates, not guaranteed deductions.

### Deposits

- One 2026/27 university policy source-reviewed in the supplied workflow.
- Rules: normally 50% of net tuition; Nigeria GBP 4,500; full-payment countries AF, BD, IR, PK; full payment for Indian states Gujarat, Haryana, Punjab and Telangana. Individual university overrides may apply.
- Applicability requires the relevant residence/region, visa and self-funding facts; do not apply one country's exception to everybody.
- Confirmed count of saved DepositPolicy rows: not yet audited from a supplied final database export.
- Applicant-specific confirmed numeric deposits: not tracked/audited.
- No currency conversion: UK deposits displayed in GBP.

### Abertay unresolved record IDs

C_ABE001_01; C_ABE001_02; C_UK001_62; C_UK001_65; C_UK001_66; C_UK001_67; C_UK001_68; C_UK001_72; C_UK001_76; C_UK001_77; C_UK001_78; C_UK001_80; C_UK001_81; C_UK001_82; C_UK001_83; C_UK001_86; C_UK001_90.

Reasons include unconfirmed top-up/UG identities, taught-course route/award differences, and research subject/award/intake mapping. Keep the original per-course audit reasons as authoritative. C_UK001_64 was deactivated as a reviewed duplicate of C_UK001_45; no guessed fee was assigned to it.

## UK — Aberystwyth University (UK002)

Status: partially reconciled; ready to pause after final audit. Not fully completed.

### Tuition

- Original inventory: 110 active courses.
- Creative Arts C_UK002_86 deactivated after official last-intake evidence (2024/25). Historic records retained.
- Running total after accepted Astrophysics replacement: **109 active courses; 88 verified annual fees; 21 without verified annual fees**.
- IMPORTANT: Astrophysics preview was shown; its application was instructed, but a final application output was not supplied in the visible record. Consequently 88/21 is a **running total requiring final database audit**. If not applied, the corresponding total remains 87/22.
- C_UK002_92 is part of the annual-fee-unavailable group, but now has reconciled route and source-reviewed conditional programme/module pricing. It is not simply an unscraped course.
- Latest confirmed Agrifood repeat: 21 October 2026, report agrifood-route-20261021T131530650706Z.json; proposed changes 0.
- Latest full 22-course pending export: Pasted text(20261021-010020).txt. All 22 annual source flags were false at that export; Astrophysics was handled afterward.

### Scholarship rules — reported saved scopes

| Rule category, 2027/28 | Rule status | Last reported scoped course count |
|---|---|---:|
| Humanities UG (includes reviewed top-up) | Verified, student eligibility required | 26 |
| Sciences UG | Review required; fee source discrepancy | 20 |
| Humanities taught PG | Verified, student eligibility required | 12 |
| Sciences taught PG | Verified, student eligibility required | 7 |
| MBA PG | Verified, student eligibility required | 4 |
| Humanities research PG — Welsh | Verified, student eligibility required | 1 |

- Reported configured set: **5 verified rule categories and 1 unverified/review-required category**, subject to final DB audit.
- Reported verified scope total: 50 course-rule memberships. This is not a fresh unique-course or student-award count.
- Undergraduate science discrepancy: tuition fee source GBP 23,255; scholarship table gross GBP 23,555. The advertised GBP 4,500 deduction is not automatically verified against the conflicting gross tuition. Astrophysics was not confirmed added to the science scope and remains pending.
- Global Citizens starts for new entrants from 2027/28. Do not transfer it to January 2027 / 2026/27 research cohorts.
- Jan 2027 research scholarship applicability remains pending unless another specific award is verified. This does not mean the university offers no possible funding.
- Biosciences/Parasite Control MRes award classification remains unconfirmed.
- Eligibility: international fee payer, new entrant, academic requirements met, offer received, course application by deadline, no disqualifying external funding. Other combinations require separately reviewed rules.
- Number of all unverified award records and unique courses without applicable verified scholarship rules: not yet audited. Do not subtract scope counts from course totals to invent this number.

### Deposits

- 2027/28 offer-letter policy application was confirmed in the supplied reconciliation workflow: **1 confirmed saved year-policy**.
- 2026/27 offer-letter policy was previewed/referenced by later calculations; final saved-row confirmation is not established by the supplied application logs. Requires audit.
- Public numeric deposit amount: not specified by the verified policy; this is not a scraping failure and is not GBP 0.
- Message: **Deposit amount is specified in your individual offer letter.**
- Distance-learning routes need their own payment/applicability review; do not automatically apply an on-campus pre-CAS policy.
- Individual offer-letter deposit amounts verified: not tracked/audited.

### Annual-fee-unavailable register (21 if Astrophysics applied)

| Course ID | Stored course / issue | Current reason / outcome |
|---|---|---|
| C_ABE002_RES_05 | MRes Media, Communication and Creative Practice | No confirmed exact current course/award match |
| C_ABE002_RES_07 | MSc (Research) Geography | Award mismatch; possible overlap with existing MPhil Geography record; duplicate not proven |
| C_UK002_07 | Criminology and Sociology | Joint-course international fee classification needs confirmation |
| C_UK002_14 | BVSc Veterinary Science | Joint RVC route; 2027/28 programme fee requires primary-source verification |
| C_UK002_27 | MSc Environmental Management | Current course/intake identity not established; historical evidence insufficient |
| C_UK002_28 | MSc Psychology (Conversion) | Online candidate differs from stored campus/duration route; replacement not accepted |
| C_UK002_29 | MA Digital Media Production | No confirmed exact current award/route match |
| C_UK002_36 | Adult Nursing | 2027 international applications held pending funding confirmation in inspected page; applicable fee unconfirmed |
| C_UK002_37 | Mental Health Nursing | Restriction for international applicants ordinarily resident outside UK; applicable fee unconfirmed |
| C_UK002_43 | Modern Languages (French and Spanish) | Separate route vs language combination within existing Modern Languages record unconfirmed |
| C_UK002_52 | MSc Business Psychology | No confirmed exact current course/award match |
| C_UK002_57 | Psychology and Marketing | Joint Psychology/Business international fee band unconfirmed |
| C_UK002_65 | One-year MSc Agrifood Innovation | One-year route unsupported by inspected routes; possible overlap needs confirmation |
| C_UK002_80 | BA History and Heritage | Official scheme found is one-year MA; BA-to-MA replacement and recruitment intake not established |
| C_UK002_82 | BSc Geology | Exact current route unconfirmed; do not substitute Environmental Earth Science automatically |
| C_UK002_92 | MSc Agrifood Innovation (Distance Learning) | D67ND3, 3 years, January 2027 corrected; conditional module/programme prices saved; no fixed annual fee |
| C_UK002_93 | MSc Human Resource Management | Exact current master's route not established |
| C_UK002_94 | MSc Digital Marketing | Exact current master's route not established; undergraduate Digital Marketing is a different award |
| C_UK002_95 | MSc Bioengineering | Exact current route not established |
| C_UK002_96 | MSc Pharmaceutical Sciences | Exact current route not established |
| C_UK002_97 | PhD Politics | Possible overlap with International Politics PhD; separate identity/duplicate not established |

### Agrifood D67ND3 — completed correction

- Course C_UK002_92 retains its ID and three-year duration. January 2027; distance learning; intake months January, May, September.
- Scheme evidence: https://www.aber.ac.uk/en/study-schemes/2026-2027/schemes/scheme/?scheme=AB-D67ND3-MSC-202701
- Pricing source: https://ibersdl.org.uk/registration/fees
- 2026/27: 20-credit online module GBP 1,055; 60-credit MSc dissertation GBP 3,170; conditional upfront full-programme quote GBP 9,500.
- Full-programme quote conditions and changing module-year prices retained. Never divide by duration to fabricate an annual fee.
- 2027/28 TBC column observed: GBP 1,200/module, GBP 3,600/dissertation, GBP 10,800 upfront programme quote. These are not the confirmed 2026/27 prices.
- Fixed scraper selects year by column heading, preserves D67ND3, and excludes volatile route-evidence hashes from business-change comparison while retaining full evidence in reports.

### Confirmed recent applications

- Law C_UK002_25: accepted current-route replacement to LLM International Commercial Law M190, Sept 2027, GBP 22,115; repeat preview 0 changes.
- Law award: GBP 3,000 conditional; net GBP 19,115; PG Humanities scope extended to 12; application confirmed. Repeat verification after that application has not been supplied.
- Creative Arts C_UK002_86: unsupported later intake deactivated; no fee verified by that action.
- Welsh C_ABE002_RES_09: MPhil Welsh Q5562, 1 year, Sept 2027, GBP 20,270; conditional GBP 3,000 award, net GBP 17,270. Apply and unchanged repeat confirmed.
- Education C_UK002_68: PhD Education X3010, Jan 2027, GBP 19,070; Global Citizens not applicable to its cohort; other awards pending. Apply and unchanged repeat confirmed.
- Behaviour Change C_UK002_91: Sept 2027, GBP 23,820; conditional GBP 3,800 award, net GBP 20,020. Apply and unchanged repeat confirmed.
- Astrophysics C_UK002_83: replacement explicitly accepted; preview GBP 23,255 and F510, Sept 2027. Final application must be confirmed in audit before presenting its running count as audited.

## Ireland — earlier work, not completed reconciliation

### Trinity College Dublin

Earlier preview: 209 source fee rows for 2026/27 and 192 database courses; 35 exact-title candidates and 174 unmatched source rows. These are source-row matching counts, not 35 verified/applied tuition records. No complete verified tuition, scholarship or deposit count is established in this register. Ireland work follows the UK queue; confirm the full university inventory before assigning a pending-school total.

## Next steps and final PDF requirements

1. Before the next school, obtain a read-only final DB audit for both worked universities: active/inactive courses, fee basis/year/source verification, ScholarshipRule verification/scope, and DepositPolicy year/verification. Reconcile running totals with that audit.
2. Import the complete named UK inventory (146 universities) and Irish inventory into this register when available. Do not assume a next university from an ID alone.
3. Update this same register after each supplied apply and verification result. Record academic year, amount/basis, rule conditions, source/report reference and pending cause.
4. Keep work complete, partly complete, source-confirmed unavailable, not applicable and awaiting confirmation separate.
5. When scraping is finished, generate the requested downloadable PDF in two sections: UK and Ireland (IE/IR). Include per-university tuition counts, verified/pending scholarship-rule counts and scope, deposit policy status, nonannual pricing, exclusions, duplicates, closures, causes and follow-up actions.
6. Do not present estimates, maximum awards or individual offer-letter amounts as guaranteed charges/discounts. Frontend messaging and final coverage verification remain separate implementation tasks.

No final PDF has been generated yet. This is the maintained working register for that later report.

## Aberystwyth eligibility questions and answer mapping — preserve for frontend integration

These are the existing verified-rule inputs to reuse when the multi-university comparison form is designed. No new frontend implementation is claimed. Present applicable questions dynamically from the saved course/year/rule, rather than displaying every school's questions to every student.

| Profile key / source | Student question | Answer required for current Global Citizens rules |
|---|---|---|
| fee_status | Has the university assessed you as an international fee-paying student? | International; API filter international maps to rule INTL. Do not infer from nationality/residence alone. |
| new_entrant | Will you be a new student starting this programme? | Yes / true |
| academic_conditions_met | Have you met the programme's academic entry requirements? | Yes / true |
| offer_received | Have you received an offer for this programme? | Yes / true |
| applied_by_deadline | Did you submit the course application on or before 1 August 2027 for this 2027/28 rule? | Yes / true. This is the degree application deadline, not a separate scholarship application. |
| external_funding | Are you receiving an external scholarship, funding or bursary from outside Aberystwyth University? | No / false |
| course_id, level, academic_year, study_year | No student question: derive from the verified selected course and comparison year. | Course must be in the reviewed rule scope; year 2027/28; currently modelled study year 1. |
| study_mode and course exclusions | Derive from verified course metadata; clarify only where unknown. | Current Global Citizens rules exclude distance learning, overseas delivery, BVSc Veterinary Science and the two Nursing programmes. |

Answer semantics: Yes=true, No=false, Not sure/unanswered=unknown (omit fact or use an explicit unknown state). Never turn an unanswered question into false. A missing required fact blocks the deduction and produces an eligibility-review message. University confirmation, not a self-reported answer, establishes a student's actual award.

For 2027/28 configured first-year rules: Humanities UG GBP 3,000; Humanities taught PG GBP 3,000; Sciences taught PG GBP 3,800; MBA GBP 3,000; Welsh Humanities MPhil GBP 3,000. UG Sciences advertised GBP 4,500 remains blocked by the GBP 23,255 versus GBP 23,555 fee discrepancy. Do not deduct it merely because a student answers every question Yes.

Global Citizens does not apply to new January 2027 entrants (academic year 2026/27). Keep other awards pending unless their cohort-specific eligibility is verified. Housing benefits are not cash tuition deductions. Stacking needs its own verified combination rules.

Deposit UI: show the verified offer-letter-policy message where applicable. There is no general public numeric Aberystwyth deposit to calculate from nationality or residence. An amount typed by the student would be self-reported and needs a separate provenance label; that input flow has not been implemented. Distance-learning payment requirements remain separately reviewed.

Testing checklist for later integration: eligible verified course; one unanswered fact; external funding=true; no offer; wrong cohort; out-of-scope course; distance-learning course; UG science discrepancy; top-up course routed to the UG award set; no double deduction of stored legacy discounts; offer-letter deposit shown without a fabricated numeric amount.

## Next-school handoff

Continue in UK university-ID order after excluding UK001, UK002, UK003 and UK004, unless Daniel selects another university. Identify the next school from the live DB rather than assuming its name. Collect its university details, all course identities/routes/intakes, international tuition/source rows, scholarships/rules and deposit policies in one read-only export. Include the named UK inventory so the remaining 143 can be tracked individually. Use larger reviewed batches and preserve university-specific question/answer mappings in this register.


## UK — Anglia Ruskin University (UK003): handoff on 22 October 2026

Status: reviewed in part; ready for final database audit and a pause while awaiting university confirmation. This does not mean all fees, scholarships or deposits are verified.

### Evidence and provisional totals

Daniel confirmed applying Computing scholarship/deposit reconciliation and the 15-course unavailable-fee review command. Their final database audit has not yet been supplied. Running coverage: **115 active courses; 98 verified international fees; 15 unresolved international fees; 2 separately restricted courses**. Fees include annual, first-year and full-course bases; do not label all 98 annual. Final audit may supersede these figures.

- Latest supplied Computing scholarship preview: lower UG scope 47 courses; higher UG scope 43; 2027/28 GBP 4,000 deposit scope 89. Subsequent application confirmed by Daniel; a fresh scope audit remains required.
- Previously reported PG scope: 3 MBA courses and 33 ARU-graduate courses. Scope memberships may overlap and must not be summed as unique scholarship recipients.
- Earlier reported 2026/27 GBP 4,000 deposit scope: 2 January 2027 UG courses. Audit saved policies and scope separately by year.
- Last successful supplied API tests: Games Programming, 8 synthetic scenarios. Both UG tiers, missing/negative eligibility, missing tier, APCL, missing visa and sponsored-payment review passed. These tests do not verify awards for real students.
- Politics and Computing deductions were configured after source/rule review. No successful API test for those specific courses has yet been supplied.

### Conditional calculation examples

| Course | Cohort | Gross GBP | Lower award / net GBP | Higher award / net GBP | Deposit |
|---|---|---:|---|---|---|
| Computer Games Programming C_UK003_65 | 2027/28 | 18,825 | 2,000 / 16,825 | 4,000 / 14,825 | 4,000 subject to payment eligibility |
| Politics and International Relations C_UK003_81 | 2027/28 | 18,825 | 2,000 / 16,825 | 4,000 / 14,825 | 4,000 subject to payment eligibility |
| Computing, Peterborough C_UK003_64 | 2027/28 | 19,750 | 2,000 / 17,750 | 4,000 / 15,750 | 4,000 subject to payment eligibility |

Awards are alternatives, not additive. Gross tuition and legacy numeric discount fields are preserved; comparison calculations use verified saved rules. Unknown required answers block deduction. Scholarships do not reduce the fixed deposit.

### Scholarship/deposit outstanding cases

- January 2027 / 2026/27 UG courses C_UK003_12 and C_UK003_61: scholarship applicability pending; cohort-specific deposit scope previously saved.
- Seven January 2027 / 2026/27 MPhil records: C_ARU003_RES_01, C_ARU003_RES_02, C_ARU003_RES_04, C_ARU003_RES_05, C_ARU003_RES_06, C_ARU003_RES_07, C_ARU003_RES_08. International research scholarship and deposit applicability remain unconfirmed; the taught-course GBP 4,000 policy must not be assigned automatically. The GBP 400 Alumni Scholarship's UK/eligible EU restrictions do not verify an INTL deduction.
- Music Therapy bursary: 2027 applicability pending; no separate bursary deduction verified.
- London MPH: fee and scholarship/deposit applicability pending. Other reviewed London courses have conditional GBP 1,000 first-class UK degree entry or GBP 2,000 London alumni rules; do not stack automatically. The London 50% Master's Scholarship Programme excludes international applicants.
- Top-up GBP 1,000 advanced-entry rule: requires confirmed course applicability, entry year, award and funding conditions. APCL adjustments need separate assessment.
- The exact number of unique active courses covered by eligible rules, and every remaining award/deposit gap, must be calculated from the current DB. Missing scope does not prove that a university has no funding or deposit requirement.

### Fifteen unresolved tuition records

| Course ID | Reason |
|---|---|
| C_UK003_18 | Current Writtle BSc Veterinary Nursing route unconfirmed; college qualifications are different awards |
| C_UK003_22 | MSc Data Science identity unconfirmed; possible overlap with verified C_UK003_76 conversion route, not a proven duplicate |
| C_UK003_26 | London MPH 2027/28 international fee explicitly to be confirmed; GBP 19,500 belongs to 2026/27 |
| C_UK003_41 | Historical Marine Biology evidence does not establish a current intake/fee |
| C_UK003_43 | Former Ecology course URL redirects to a subject listing; no verified current route/fee |
| C_UK003_46 | Nursing Mental Health international fee and applicability unconfirmed; UK fee not substituted |
| C_UK003_52 | GBP 9,450 total distance-learning fee appears under UK presentation; international applicability unconfirmed; campus pre-CAS policy not applicable |
| C_UK003_56 | Horticulture capture has three-year part-time route without intake year and 2025/26 UK pricing; current international route/fee unconfirmed |
| C_UK003_66 | Music Performance course URL redirects to a subject listing |
| C_UK003_82 | BA International Relations identity unconfirmed; possible overlap with C_UK003_81 combined route, not a proven duplicate |
| C_UK003_87 | Accelerated blended Early Childhood Studies: applicable international fee/route unconfirmed |
| C_UK003_88 | Blended Social Work: international application/status eligibility and fee unconfirmed; not a blanket nationality exclusion |
| C_UK003_89 | Nursing Children international fee/applicability unconfirmed |
| C_UK003_94 | BA 3D Animation route corrected; applicable international fee unconfirmed |
| C_UK003_104 | January 2027 Global Public Health intake needs 2026/27 fees; published GBP 18,825 is labelled 2027/28 |

### Separately restricted courses

- C_ARU003_RES_03, MRes Life Sciences: captured page states UK students only.
- C_UK003_44, MBChB Medicine: home fee status required at application. Do not infer this from nationality/residence alone.

No courses were deactivated solely because of missing search results, redirects, or similar titles. Original records and audits retained.

### ARU eligibility questions for later frontend integration

| Saved key | Question / meaning | Current rule requirement |
|---|---|---|
| new_entrant | First time joining ARU? | true for Year 1 UG |
| full_time | Full-time study? | true for reviewed standard UG rules |
| entry_year | Which course year are you entering? | 1 for lower/higher; advanced-entry rules use their separate scope |
| apcl | Accredited Prior Certified Learning credit? | false for standard imported deductions; pro-rata assessment otherwise |
| registration_conditions_met | All ARU registration conditions met? | true |
| aru_funding_eligibility_confirmed | Funding/sponsorship/federal loan arrangement eligible? | true |
| aru_ug_award_confirmed | ARU confirmed the UG award? | true |
| aru_ug_award_tier | Which first-year award has ARU confirmed? | lower or higher, never default |
| aru_graduate | ARU graduate? | true for ARU-graduate PG rule |
| aru_pg_entry_requirements_exceeded | ARU confirmed qualifications exceed scholarship entry requirements? | true for relevant PG rules |
| aru_pg_eligibility_confirmed | ARU confirmed PG scholarship eligibility? | true; up-to award remains a labelled maximum estimate |
| aru_london_alumni | Completed a degree at ARU London? | true for the narrow London alumni rule |
| uk_first_class_degree | First-class honours degree from UK university? | true for London entry rule |
| aru_london_award_confirmed | ARU London confirmed the award for this route/intake? | true |
| requires_student_visa | Student visa needed? | true for fixed pre-CAS estimate |
| full_fee_paying | Full-fee-paying international student under ARU payment arrangements? | true |
| sponsored | Sponsor providing sponsorship letter? | false for standard numeric deposit estimate; separate assessment otherwise |

Derive course ID, currency, level, route kind, year and study year from verified selection. Additional advanced-entry keys remain in saved rule JSON and must be retained by final audit. Missing facts stay unknown. A synthetic passing scenario is not university confirmation of an individual's award.

### Handoff evidence

Latest supplied course evidence: remaining-17-evidence-20261022T152141639880Z and four-unidentified-20261022T161108375171Z. Duplicate-pair export confirmed four records and no missing IDs. Politics application and zero-change preview supplied. Games/Health application and zero-change reconciliation supplied. Computing reconciliation application and remaining-15 review application confirmed by Daniel in conversation; final audit requested next.

Next university: select from live UK inventory, excluding UK001/UK002/UK003, in university-ID order. Its identity is not assumed here. Final audit/export must identify it and preserve the complete inventory for named queue tracking.


## ARU final supplied database audit — 22 October 2026

This audit supersedes the provisional ARU totals above. Audit generated: 2026-10-22T22:56:17.354198+00:00. No database changes made by audit. Source: Pasted text(20261022-225751).txt.

| Measure | Audited count |
|---|---:|
| total course records | 115 |
| active courses | 115 |
| inactive courses | 0 |
| verified international fees | 98 |
| pending international fees | 15 |
| restricted international comparisons | 2 |
| scholarship rule records | 7 |
| verified scholarship rule records | 7 |
| unverified scholarship rule records | 0 |
| verified fee courses with conditional award scope | 89 |
| verified fee courses without conditional award scope | 9 |
| deposit policy records | 2 |
| verified deposit policy records | 2 |
| verified fee courses with deposit scope | 86 |
| verified fee courses without deposit scope | 12 |

Verified fees by basis: {"annual_tuition": 94, "full_time_course_fee": 3, "first_year_tuition": 1}. By year: {"2027/28": 89, "2026/27": 9}.

Scholarship scope gaps: seven MPhil courses (RES_01, RES_02, RES_04–RES_08) and January 2027 Law C_UK003_12 / Construction Management C_UK003_61. No individual award is established by a scoped rule.

**Deposit audit follow-up:** strict course-kind matching reports 86 covered verified-fee courses and 12 gaps. Seven are unresolved MPhil research deposits. The other five — C_UK003_01, C_UK003_05, C_UK003_31, C_UK003_36, C_UK003_95 — already occur in the 2027/28 policy course_ids list, but fail the audit course-kind membership check. Earlier source evidence uses legacy `undergraduate` for at least three of these, whereas the policy accepts `undergraduate_taught`. These five are schema/coverage discrepancies requiring current per-source confirmation and correction or alias handling, not proven missing university deposit rules. Do not relabel audited coverage as 91 until the correction and repeat audit are supplied. Runtime deposit calculation for these five also needs verification.

Saved deposit policy scopes: 2026/27 = 2 IDs; 2027/28 = 89 IDs; these scope membership counts differ from validated unique coverage.

## Arden University (UK004) — historical initial handoff

Identified from the supplied database export, not guessed. Source: Pasted text (2)(3).txt. Website: https://arden.ac.uk/. Database inventory: 73 courses (43 UG, 29 PG, 1 TOPUP); 146 tuition rows; 0 tuition source rows; 1 stored scholarship with 0 calculation rules; 0 deposit policy rows. These are stored inventories, not verified current source coverage. All stored intakes September 2026.

Data-quality flags: C_UK004_13 Agriculture and C_UK004_14 Veterinary Nursing are assigned Writtle Campus (Chelmsford), requiring university/campus identity review. Many other rows combine several campuses. Separate UK international campus, online and Berlin offerings; do not copy fees or regional awards across them. Stored GBP 500 deposit and GBP 3,000 regional scholarship are not verified policies.

Official starting sources reviewed: https://arden.ac.uk/international-students/find-a-course ; https://arden.ac.uk/international-students/fees-for-international-students ; https://arden.ac.uk/international-students/scholarships-funding ; https://arden.ac.uk/studying-with-us/fees-and-funding/all-scholarships ; https://arden.ac.uk/terms-and-conditions . Central introductory offer pages contain differing prices/cohort conditions, so programme/campus/intake and terms must be reconciled before import. Central international fee page says deposit amount advised during application; no universal numeric deposit inferred. Source discovery begun; no Arden DB updates applied.

## Named UK inventory from ARU handoff audit

146 inventory entries supplied. Four universities reviewed with outstanding cases; the remaining queue is retained below.

| ID | University | Workflow status |
|---|---|---|
| UK001 | Abertay University | Reviewed in part; outstanding cases |
| UK002 | Aberystwyth University | Reviewed in part; outstanding cases |
| UK003 | Anglia Ruskin University | Reviewed in part; outstanding cases |
| UK004 | Arden University | Reviewed with outstanding cases; see latest Arden entry |
| UK005 | Arts University Bournemouth | Not started |
| UK006 | Arts University Plymouth | Not started |
| UK007 | Aston University | Not started |
| UK008 | Bangor University | Not started |
| UK009 | Bath Spa University | Not started |
| UK010 | BIMM University | Not started |
| UK011 | Birkbeck, University of London | Not started |
| UK012 | Birmingham City University | Not started |
| UK013 | Birmingham Newman University | Not started |
| UK014 | Bournemouth University | Not started |
| UK015 | Brunel University London | Not started |
| UK016 | Buckinghamshire New University | Not started |
| UK017 | Canterbury Christ Church University | Not started |
| UK018 | Cardiff Metropolitan University | Not started |
| UK019 | Cardiff University | Not started |
| UK020 | City St George's, University of London | Not started |
| UK021 | Coventry University | Not started |
| UK022 | Cranfield University | Not started |
| UK023 | De Montfort University | Not started |
| UK024 | Edge Hill University | Not started |
| UK025 | Edinburgh Napier University | Not started |
| UK026 | Falmouth University | Not started |
| UK027 | Glasgow Caledonian University | Not started |
| UK028 | Goldsmiths University of London | Not started |
| UK029 | Harper Adams University | Not started |
| UK030 | Hartpury University | Not started |
| UK031 | Health Sciences University | Not started |
| UK032 | Heriot-Watt University | Not started |
| UK033 | Keele University | Not started |
| UK034 | King's College London | Not started |
| UK035 | Kingston University | Not started |
| UK036 | Lancaster University | Not started |
| UK037 | Leeds Arts University | Not started |
| UK038 | Leeds Beckett University | Not started |
| UK039 | Leeds Trinity University | Not started |
| UK040 | Bishop Grosseteste University | Not started |
| UK041 | Liverpool Hope University | Not started |
| UK042 | Liverpool John Moores University | Not started |
| UK043 | Liverpool School of Tropical Medicine | Not started |
| UK044 | London Business School | Not started |
| UK045 | London Metropolitan University | Not started |
| UK046 | London South Bank University | Not started |
| UK047 | Manchester Metropolitan University | Not started |
| UK048 | Middlesex University | Not started |
| UK049 | Newcastle University | Not started |
| UK050 | Northeastern University London | Not started |
| UK051 | Norwich University of the Arts | Not started |
| UK052 | Nottingham Trent University | Not started |
| UK053 | Oxford Brookes University | Not started |
| UK054 | Queen Margaret University | Not started |
| UK055 | Queen Mary University of London | Not started |
| UK056 | Queen's University Belfast | Not started |
| UK057 | Ravensbourne University London | Not started |
| UK058 | Regent's University London | Not started |
| UK059 | Roehampton University | Not started |
| UK060 | Royal Agricultural University | Not started |
| UK061 | Royal Veterinary College, University of London | Not started |
| UK062 | Sheffield Hallam University | Not started |
| UK063 | SOAS, University of London | Not started |
| UK064 | Southampton Solent University | Not started |
| UK065 | St Mary's University College | Not started |
| UK066 | St Mary's University | Not started |
| UK067 | Stranmillis University College | Not started |
| UK068 | Swansea University | Not started |
| UK069 | Teesside University | Not started |
| UK070 | Robert Gordon University | Not started |
| UK071 | The University of Buckingham | Not started |
| UK072 | The University of Law | Not started |
| UK073 | The University of Northampton | Not started |
| UK074 | The University of Salford | Not started |
| UK075 | The University of Sheffield | Not started |
| UK076 | The University of West London | Not started |
| UK077 | The University of Winchester | Not started |
| UK078 | The University of Worcester | Not started |
| UK079 | University College Birmingham | Not started |
| UK080 | University College London | Not started |
| UK081 | University for the Creative Arts | Not started |
| UK082 | University of Aberdeen | Not started |
| UK083 | University of Bath | Not started |
| UK084 | University of Bedfordshire | Not started |
| UK085 | University of Birmingham | Not started |
| UK086 | University of Bradford | Not started |
| UK087 | University of Brighton International College | Not started |
| UK088 | University of Brighton | Not started |
| UK089 | University of Bristol | Not started |
| UK090 | University of Cambridge | Not started |
| UK091 | University of Chester | Not started |
| UK092 | Chicago Booth School of Business | Not started |
| UK093 | University of Chichester | Not started |
| UK094 | University of Cumbria | Not started |
| UK095 | University of Derby | Not started |
| UK096 | University of Dundee | Not started |
| UK097 | Durham University | Not started |
| UK098 | University of East Anglia | Not started |
| UK099 | University of East London | Not started |
| UK100 | University of Edinburgh | Not started |
| UK101 | University of Essex | Not started |
| UK102 | University of Exeter | Not started |
| UK103 | University of Glasgow | Not started |
| UK104 | University of Gloucestershire | Not started |
| UK105 | University Academy 92 | Not started |
| UK106 | University of Greenwich | Not started |
| UK107 | University of Hertfordshire | Not started |
| UK108 | University of Huddersfield | Not started |
| UK109 | University of Hull | Not started |
| UK110 | University of Kent | Not started |
| UK111 | University of Lancashire | Not started |
| UK112 | University of Leeds | Not started |
| UK113 | University of Leicester | Not started |
| UK114 | University of Lincoln | Not started |
| UK115 | University of Liverpool | Not started |
| UK116 | University of London | Not started |
| UK117 | University of Manchester | Not started |
| UK118 | Northumbria University | Not started |
| UK119 | University of Nottingham | Not started |
| UK120 | University of Oxford | Not started |
| UK121 | University of Plymouth | Not started |
| UK122 | University of Portsmouth | Not started |
| UK123 | University of Reading | Not started |
| UK124 | University of South Wales | Not started |
| UK125 | University of Southampton | Not started |
| UK126 | University of St Andrews | Not started |
| UK127 | University of St Mark & St John | Not started |
| UK128 | Staffordshire University | Not started |
| UK129 | University of Stirling | Not started |
| UK130 | University of Strathclyde | Not started |
| UK131 | University of Suffolk | Not started |
| UK132 | University of Sunderland | Not started |
| UK133 | University of Surrey | Not started |
| UK134 | University of Sussex | Not started |
| UK135 | University of the Arts London | Not started |
| UK136 | University of the Highlands and Islands | Not started |
| UK137 | University of the West of England | Not started |
| UK138 | University of the West of Scotland | Not started |
| UK139 | Ulster University | Not started |
| UK140 | University of Wales Trinity St David | Not started |
| UK141 | University of Warwick | Not started |
| UK142 | University of Westminster | Not started |
| UK143 | University of Wolverhampton | Not started |
| UK144 | University of York | Not started |
| UK145 | Wrexham University | Not started |
| UK146 | York St John University | Not started |


## ARU deposit correction and final supplied audit — 23 October 2026

This update supersedes the earlier 86-covered / 12-gap deposit figures, while preserving the earlier audit narrative above as history. Source: Daniel's supplied final audit and successful calculation checks.

- Five course-kind corrections were applied: C_UK003_01, C_UK003_05, C_UK003_31, C_UK003_36, C_UK003_95; `undergraduate` became `undergraduate_taught`. Repeat preview: zero changes.
- Final supplied totals: 115 active courses; 98 verified international fees; 15 pending; 2 restricted. Seven scholarship rules, all verified; 89 verified-fee courses with conditional award scope and 9 without. Two verified deposit policies; **91 verified-fee courses with deposit scope and 7 without**.
- Verified fee bases: 94 annual; 3 full-time course fees; 1 first-year fee. Cohorts: 89 in 2027/28 and 9 in 2026/27.
- All 20 supplied synthetic calculation scenarios passed for those five corrected course-kind records. These test eligibility guards and calculations, not real student awards.
- Audit: `logs/scraping-progress/aru-handoff-20261023T001020216835Z/aru-final-audit.json`.
- The seven remaining deposit cases are research MPhil cases; no taught-course numeric policy is inferred for them.

<!-- university-audit:UK004:start -->
## UK — Arden University (UK004): latest saved-metadata audit

Generated: 2026-10-23T19:10:15.068495+00:00

Status: reviewed with outstanding cases. This command queried Arden only; earlier university entries retain their original provenance and caveats.

| Measure | Audited count |
|---|---:|
| total course records | 73 |
| active courses | 72 |
| inactive courses | 1 |
| verified international fees | 53 |
| pending international fees | 19 |
| scholarship records | 20 |
| scholarship rule records | 19 |
| verified scholarship rule records | 19 |
| unverified scholarship rule records | 0 |
| verified fee courses with conditional award scope | 39 |
| verified fee courses without conditional award scope | 14 |
| deposit policy records | 1 |
| verified deposit policy records | 1 |
| verified fee courses with individually advised deposit scope | 14 |
| verified fee online courses campus deposit policy not applicable | 39 |
| verified fee courses with pending campus deposit scope | 0 |
| verified numeric deposit amounts | 0 |

Fee bases: {"annual_tuition": 14, "per_level": 23, "total_programme_fee": 16}

Fee years: {"2026/27": 53}

### Outstanding tuition/mapping cases

| Course | Cause / next action |
|---|---|
| C_UK004_13 — BSc (Hons) Agriculture | ARU advertises the matching BSc Agriculture at Writtle. The Arden assignment appears incorrect; no course transfer or fee substitution is approved. Confirm awarding institution and exact award. Do not move the record to ARU or import ARU fees automatically. |
| C_UK004_14 — BSc (Hons) Veterinary Nursing | ARU Writtle advertises veterinary-nursing college/apprenticeship provision. This does not verify the stored BSc qualification or Arden ownership. Confirm awarding institution and exact award. Do not move the record to ARU or import ARU fees automatically. |
| C_UK004_29 — BA (Hons) Business Management with Foundation Year | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. |
| C_UK004_30 — LLB (Hons) Law with Foundation Year | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. |
| C_UK004_31 — BA (Hons) Criminology and Psychology with Foundation Year | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. |
| C_UK004_39 — MSc Data Analytics and Project Management | Historical official award evidence exists; two current URL probes returned 404. Do not silently replace it with already mapped MSc Project Management. Obtain current exact course/intake evidence. Historical awards and 404 responses do not prove closure. |
| C_UK004_47 — BA (Hons) Human Resource Management and Psychology | Exact online route and GBP 5,500 per level are captured; no advertised intake appears in the saved application options. Keep the schedule as evidence only. A confirmed intake is needed before a current comparison or scholarship deduction. |
| C_UK004_48 — BSc (Hons) Health and Care Management with Foundation Year | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. |
| C_UK004_51 — MSc IT Security Management | The old IT Security Management URL redirects to current MSc Cyber Security, already C_UK004_18. Redirects do not prove award equivalence; retain the existing canonical fee/award scope. Review as a possible retirement/replacement; preserve history and the existing canonical course. No retirement applied by this audit. |
| C_UK004_52 — MSc Global Supply Chain Management | The current advertised title is MSc Supply Chain Management and Logistics, already C_UK004_25. No exact Global Supply Chain title/intake was established. Review as a possible retirement/replacement; preserve history and the existing canonical course. No retirement applied by this audit. |
| C_UK004_54 — MA Education | No exact current MA Education intake, mode and price established. A subject label, PG Cert or staff teaching qualification cannot verify this MA. Obtain current exact course/intake evidence. Historical awards and 404 responses do not prove closure. |
| C_UK004_55 — MSc Data Analytics and Information Systems Management | Official Berlin alumni accounts confirm the historical award. They do not establish a current UK intake or price. Both guessed direct URLs returned 404. Obtain current exact course/intake evidence. Historical awards and 404 responses do not prove closure. |
| C_UK004_57 — BSc (Hons) Accounting and Finance with Foundation Year | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. |
| C_UK004_58 — BA (Hons) Business Management (Marketing) with Foundation Year | Exact Marketing foundation pathway found in Berlin. Do not assign Berlin fees or scholarship conditions to the stored UK route. Retain UK comparison as unavailable. Berlin provision cannot establish UK location, tuition or award rules. |
| C_UK004_59 — BA (Hons) Business Management (HRM) with Foundation Year | Exact HRM foundation pathway found in Berlin. Do not assign Berlin fees or scholarship conditions to the stored UK route. Retain UK comparison as unavailable. Berlin provision cannot establish UK location, tuition or award rules. |
| C_UK004_60 — BA (Hons) Business Management (Tourism) with Foundation Year | Exact Tourism foundation pathway found in Berlin. Do not assign Berlin fees or scholarship conditions to the stored UK route. Retain UK comparison as unavailable. Berlin provision cannot establish UK location, tuition or award rules. |
| C_UK004_61 — Foundation Degree Business and Innovation (FdA) | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. This FdA/FdSc is a foundation degree, not a foundation year attached to a BA/BSc honours degree. |
| C_UK004_62 — Foundation Degree Computing and Digital Futures (FdSc) | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. This FdA/FdSc is a foundation degree, not a foundation year attached to a BA/BSc honours degree. |
| C_UK004_63 — Foundation Degree Health and Care Management (FdSc) | Saved Nigeria and Ghana fee responses explicitly state that course fees are not available yet. Retain an unavailable international-fee message. Do not substitute a home, non-foundation or unrelated programme fee. This FdA/FdSc is a foundation degree, not a foundation year attached to a BA/BSc honours degree. |

### Verified-fee courses with scholarship applicability pending

| Course | Saved review |
|---|---|
| C_UK004_04 — BSc (Hons) Computing | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_07 — BA (Hons) Criminology and Psychology | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_08 — BSc (Hons) Digital Marketing | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_11 — BSc (Hons) Project Management | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_12 — BA (Hons) Criminology with Law | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_15 — BA (Hons) Graphic Design | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_32 — BA (Hons) Criminology with Cybercrime | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_33 — BSc (Hons) Policing and Criminal Investigation | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_34 — BSc (Hons) International Hospitality and Tourism Management | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_43 — BSc (Hons) Psychology with Counselling and Mental Health | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_44 — BA (Hons) Business Management and Law | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_45 — BA (Hons) Business Management and Computing | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |
| C_UK004_46 — BA (Hons) Criminology | Criminology is absent from the collected Global Tiers eligible-programme list. No deduction from that offer is approved. Other awards remain unconfirmed. |
| C_UK004_56 — BSc (Hons) Forensic and Criminal Investigation | No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists. |

### Calculation rules, questions and deposit treatment

Verified awards calculate net tuition when required facts are confirmed. Alumni and Global Tiers do not stack; the greatest eligible offer wins. Missing required answers block that award. Full rule conditions and eligibility questions remain in the JSON audit/database snapshot.

Online Alumni facts: arden_alumni_qualifying_degree, arden_alumni_within_five_years, arden_alumni_official_results_received, arden_alumni_advisor_informed, arden_alumni_offer_granted. Common offer, partner, account/status and exclusion checks remain required. Global Tiers country/intake/price guards are preserved.

The verified campus deposit policy advises the individual amount during application; no universal numeric amount is verified. The campus policy does not apply to the reviewed online routes; online payment requirements remain separately unverified.

Manchester top-up early-bird combination remains pending the published arithmetic discrepancy. The LLM has a conditional Alumni deduction; its separate Global Tiers price group remains unconfirmed.

### Audit limitations

- Scope coverage is conditional and does not confirm a real student award.
- Per-level schedules and derived programme totals are not annual tuition.
- A verified individually-advised deposit policy is not a verified numeric deposit.
- Online campus-policy exclusion does not mean no payment is required.
- Missing scholarship scope does not establish that no funding exists.
- Existing numeric discount/deposit fields are reported, not accepted as verified evidence.
- This audit checks saved metadata consistency; it does not refresh websites or rerun API tests.

Expected-count differences: {}


<!-- university-audit:UK004:end -->
